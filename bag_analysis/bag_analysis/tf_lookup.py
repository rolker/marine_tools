"""
Stamped transform lookup for the bag tools, as tf2 is meant to be used.

A tool that georeferences recorded data needs the pose at the data's own
time. tf2 answers that only once it has transforms on both sides of the
time, and it answers by interpolating across a gap of any length, without
saying how long the gap was. :class:`TfChain` wraps a tf2 ``Buffer`` and
keeps, for every dynamic frame, the stamps of the transforms the Buffer
accepted, so that a caller can ask first (:meth:`TfChain.check`) and look
up only when the answer is sound:

* ``ready``: every dynamic link of the chain has a sample at the time, or
  one before and one after it no further apart than the gap limit;
* ``wait_chain`` / ``wait_late``: transforms past the time (or part of the
  chain) have not been read yet; the caller holds the data and asks again
  after reading more;
* ``no_tf``: the time is before a link's first sample;
* ``tf_evicted``: the time is before the oldest sample the cache still
  holds, and older samples were dropped from it, so the answer is lost to
  the cache length, not to the bag (raise the cache length);
* ``tf_gap``: the two samples around the time are further apart than the
  gap limit.

Static links never limit the answer. There is no fallback to the latest
transform.

The tracker holds exactly the samples the Buffer holds because every
transform goes through :meth:`TfChain.feed`, which applies tf2's own
rejection rules first and hands the Buffer only what it accepts (the Python
``Buffer`` does not report a rejected insert). The rules were checked
against the installed tf2 in ``test_tf_lookup.py``. Two things tf2 does that
this module does not copy are treated as errors instead: a frame that gets a
second parent, and a frame that arrives both on ``/tf`` and on
``/tf_static`` (tf2 then replaces one cache by the other). Both raise
:class:`TfChainError`.

A transform that arrives after a transform with a later stamp for the same
frame is stored in order, but a datum already answered from the wider
bracket is not revisited.
"""

from __future__ import annotations

import bisect
from collections import Counter
import math

from rclpy.duration import Duration
import rclpy.time
from tf2_ros import Buffer, TransformException

READY = 'ready'
NO_TF = 'no_tf'
TF_GAP = 'tf_gap'
TF_EVICTED = 'tf_evicted'
TF_LATE = 'tf_late'
WAIT_CHAIN = 'wait_chain'
WAIT_LATE = 'wait_late'

NS_PER_S = 1_000_000_000

# Longest cache length accepted (s): Duration holds nanoseconds in an int64
# (about 9.2e9 s), and a cache of more than a few months is a typo.
MAX_CACHE_S = 1.0e7

# Verdicts a caller must act on at once, whatever is still to come.
DEFINITE = (NO_TF, TF_GAP, TF_EVICTED)
WAITS = (WAIT_CHAIN, WAIT_LATE)


class TfChainError(RuntimeError):
    """The transform tree changed shape in a way this tool does not support."""


class TfMismatch(RuntimeError):
    """tf2 refused a lookup that the tracker had called ready."""


def stamp_ns(stamp) -> int:
    """Return nanoseconds since epoch from a builtin_interfaces/Time."""
    return stamp.sec * NS_PER_S + stamp.nanosec


def _strip(frame: str) -> str:
    """Return a frame id the way tf2 stores it (no leading slash)."""
    return frame.lstrip('/')


class _Stamps:
    """Sorted stamps (ns) of one frame; the front is dropped by index."""

    __slots__ = ('items', 'start', 'evicted')

    def __init__(self) -> None:
        self.items: list[int] = []
        self.start = 0
        self.evicted = False    # a stamp was dropped from the front

    def __len__(self) -> int:
        return len(self.items) - self.start

    @property
    def newest(self) -> int | None:
        return self.items[-1] if len(self) else None

    def drop_before(self, cutoff: int) -> None:
        """Forget stamps below ``cutoff``."""
        start = bisect.bisect_left(self.items, cutoff, self.start)
        if start > self.start:
            self.evicted = True
        self.start = start
        if self.start > 4096 and self.start * 2 > len(self.items):
            del self.items[:self.start]
            self.start = 0

    def add(self, value: int) -> bool:
        """
        Insert ``value``; return False if that stamp was already held.

        A stamp is not stored twice.
        """
        items = self.items
        if not len(self) or value > items[-1]:
            items.append(value)
            return True
        i = bisect.bisect_left(items, value, self.start)
        if i < len(items) and items[i] == value:
            return False
        items.insert(i, value)
        return True


class TfChain:
    """
    A tf2 Buffer plus the bookkeeping to look up poses only when sound.

    ``max_gap_s`` is the largest allowed interval between the two samples
    around a lookup time, on every dynamic link of the chain.
    """

    def __init__(self, earth_frame: str, cache_s: float,
                 max_gap_s: float) -> None:
        """Create the Buffer (cache length ``cache_s``) and an empty tracker."""
        self.earth = _strip(earth_frame)
        self.cache_ns = round(cache_s * NS_PER_S)
        self.max_gap_ns = round(max_gap_s * NS_PER_S)
        if not 0 < cache_s <= MAX_CACHE_S:
            raise ValueError(
                f'cache length {cache_s:g} s is outside (0, {MAX_CACHE_S:g}]')
        if self.max_gap_ns < 1:
            raise ValueError(
                f'gap limit {max_gap_s:g} s rounds to 0 ns; it must be at '
                'least 1 ns')
        # The tracker and the Buffer must prune against the same integer
        # number of nanoseconds (Duration(seconds=) would truncate).
        self.buffer = Buffer(cache_time=Duration(nanoseconds=self.cache_ns))
        self.rejected: Counter = Counter()
        self._parent: dict[str, str] = {}
        self._static: set[str] = set()
        self._stamps: dict[str, _Stamps] = {}

    # -- feeding ----------------------------------------------------------
    def feed(self, transforms, *, static: bool, authority: str) -> None:
        """Offer a message's transforms; raise TfChainError on a bad tree."""
        for tr in transforms:
            self._feed_one(tr, static, authority)

    def _feed_one(self, tr, static: bool, authority: str) -> None:
        child, parent = _strip(tr.child_frame_id), _strip(tr.header.frame_id)
        if not child or not parent:
            self.rejected['empty_frame_id'] += 1
            return
        if child == parent:
            self.rejected['self_transform'] += 1
            return
        t, q = tr.transform.translation, tr.transform.rotation
        values = (t.x, t.y, t.z, q.x, q.y, q.z, q.w)
        if not all(math.isfinite(v) for v in values):
            self.rejected['not_finite'] += 1
            return
        if not any(q_ for q_ in values[3:]):
            self.rejected['zero_quaternion'] += 1
            return
        self._check_tree(child, parent, static)
        if static:
            self.buffer.set_transform_static(tr, authority)
            self._static.add(child)
            self._parent[child] = parent
            return
        s_ns = stamp_ns(tr.header.stamp)
        stamps = self._stamps.setdefault(child, _Stamps())
        newest = stamps.newest
        if newest is not None:
            if s_ns + self.cache_ns < newest:
                self.rejected['older_than_cache'] += 1
                return
            # tf2 prunes against the newest stamp from before this insert.
            stamps.drop_before(newest - self.cache_ns)
        if not stamps.add(s_ns):
            # tf2 replaces the value silently and the tracker cannot tell
            # whether it differs; say that it happened.
            self.rejected['duplicate_stamp'] += 1
        self._parent[child] = parent
        self.buffer.set_transform(tr, authority)

    def _check_tree(self, child: str, parent: str, static: bool) -> None:
        known = self._parent.get(child)
        if known is not None and known != parent:
            raise TfChainError(
                f'frame "{child}" has two parents, "{known}" and "{parent}"; '
                'a transform tree that is re-parented during the bag is not '
                'supported')
        if static and child in self._stamps:
            raise TfChainError(
                f'frame "{child}" arrives on both /tf and /tf_static; '
                'tf2 then keeps only the one that came last, so poses '
                'would be wrong. Fix the recording or remove one publisher')
        if not static and child in self._static:
            raise TfChainError(
                f'frame "{child}" arrives on both /tf_static and /tf; '
                'tf2 then keeps only the one that came last, so poses '
                'would be wrong. Fix the recording or remove one publisher')

    # -- asking -----------------------------------------------------------
    def check(self, frame: str, lookup_ns: int) -> str:
        """Say whether ``earth -> frame`` can be looked up at ``lookup_ns``."""
        frame = _strip(frame)
        if lookup_ns <= 0:
            return NO_TF        # tf2 reads time 0 as "the latest"
        if frame == self.earth:
            return READY
        # Walk the whole chain before any verdict is returned, so a loop is
        # found whatever the links before it say.
        definite = None
        late = missing = False
        seen = {frame}
        cur = frame
        while cur != self.earth:
            parent = self._parent.get(cur)
            if parent is None:
                missing = True
                break
            if cur not in self._static and definite is None:
                verdict = self._check_link(cur, lookup_ns)
                if verdict in DEFINITE:
                    definite = verdict
                elif verdict == WAIT_CHAIN:
                    missing = True
                elif verdict == WAIT_LATE:
                    late = True
            if parent in seen:
                raise TfChainError(
                    f'the transform tree has a loop through "{parent}"')
            seen.add(parent)
            cur = parent
        if definite is not None:
            return definite
        if missing:
            return WAIT_CHAIN
        return WAIT_LATE if late else READY

    def _check_link(self, child: str, lookup_ns: int) -> str:
        stamps = self._stamps.get(child)
        if stamps is None or not len(stamps):
            return WAIT_CHAIN
        items, start = stamps.items, stamps.start
        i = bisect.bisect_left(items, lookup_ns, start)
        if i < len(items) and items[i] == lookup_ns:
            return READY                    # a sample at the time itself
        if i == start:
            # before the first sample held; was an older one evicted?
            return TF_EVICTED if stamps.evicted else NO_TF
        if i == len(items):
            return WAIT_LATE                # past the newest sample
        if items[i] - items[i - 1] > self.max_gap_ns:
            return TF_GAP
        return READY

    @staticmethod
    def settle(verdict: str) -> str:
        """Turn a wait into the answer when nothing more will be read."""
        if verdict == WAIT_CHAIN:
            return NO_TF
        if verdict == WAIT_LATE:
            return TF_LATE
        return verdict

    def lookup(self, frame: str, lookup_ns: int):
        """
        Return ``((x, y, z), (qx, qy, qz, qw))`` of ``frame`` in earth.

        Call after :meth:`check` said ``ready``; a refusal by tf2 then means
        the tracker and tf2 disagree and raises :class:`TfMismatch`.
        """
        try:
            tf = self.buffer.lookup_transform(
                self.earth, _strip(frame),
                rclpy.time.Time(nanoseconds=lookup_ns))
        except TransformException as exc:
            raise TfMismatch(
                f'tf2 refused earth -> {frame} at {lookup_ns} ns after the '
                f'tracker called it ready: {exc}') from exc
        t = tf.transform.translation
        q = tf.transform.rotation
        return (t.x, t.y, t.z), (q.x, q.y, q.z, q.w)
