"""
Tests for bag_analysis.tf_lookup.

The tracker is only worth having if it agrees with tf2, so several tests
feed the same transforms to a bare tf2 ``Buffer`` and compare. Those pin the
tf2 behaviour that the module relies on, as installed here.
"""

import random
from types import SimpleNamespace

from bag_analysis import tf_lookup
from bag_analysis.tf_lookup import (
    NO_TF, READY, TF_EVICTED, TF_GAP, TF_LATE, TfChain, TfChainError, TfMismatch,
    WAIT_CHAIN, WAIT_LATE)
from builtin_interfaces.msg import Time as TimeMsg
from geometry_msgs.msg import TransformStamped
import pytest
from rclpy.duration import Duration
import rclpy.time
from tf2_ros import Buffer, TransformException

S = 1_000_000_000


def tf(parent, child, ns, x=0.0, q=(0.0, 0.0, 0.0, 1.0)):
    t = TransformStamped()
    t.header.stamp = TimeMsg(sec=ns // S, nanosec=ns % S)
    t.header.frame_id = parent
    t.child_frame_id = child
    t.transform.translation.x = x
    (t.transform.rotation.x, t.transform.rotation.y,
     t.transform.rotation.z, t.transform.rotation.w) = q
    return t


def chain(cache=600.0, gap=1.0):
    return TfChain('earth', cache, gap)


def feed(c, *transforms, static=False):
    c.feed(transforms, static=static, authority='test')


def bare_ok(buffer, parent, child, ns):
    try:
        buffer.lookup_transform(parent, child, rclpy.time.Time(nanoseconds=ns))
        return True
    except TransformException:
        return False


def test_exact_sample_passes_whatever_the_neighbours_are():
    c = chain(gap=1.0)
    feed(c, tf('earth', 'a', 100 * S), tf('earth', 'a', 160 * S))
    assert c.check('a', 100 * S) == READY
    assert c.check('a', 160 * S) == READY
    assert c.check('a', 130 * S) == TF_GAP


def test_gap_limit_is_inclusive():
    c = chain(gap=1.0)
    feed(c, tf('earth', 'a', 100 * S), tf('earth', 'a', 101 * S),
         tf('earth', 'a', 101 * S + 1))
    assert c.check('a', 100 * S + S // 2) == READY      # exactly 1.0 s apart
    assert c.check('a', 101 * S + 1) == READY


def test_gap_just_over_limit_is_dropped():
    c = chain(gap=1.0)
    feed(c, tf('earth', 'a', 100 * S), tf('earth', 'a', 101 * S + 1))
    assert c.check('a', 100 * S + 1) == TF_GAP


def test_before_first_after_last_and_unknown_frame():
    c = chain()
    feed(c, tf('earth', 'a', 100 * S), tf('earth', 'a', 101 * S))
    assert c.check('a', 100 * S - 1) == NO_TF
    assert c.check('a', 101 * S + 1) == WAIT_LATE
    assert c.check('ghost', 100 * S) == WAIT_CHAIN
    assert c.settle(WAIT_LATE) == TF_LATE
    assert c.settle(WAIT_CHAIN) == NO_TF
    assert c.settle(READY) == READY
    assert c.settle(TF_GAP) == TF_GAP


def test_time_zero_or_negative_is_never_looked_up():
    c = chain()
    feed(c, tf('earth', 'a', 0), tf('earth', 'a', 1 * S))
    assert c.check('a', 0) == NO_TF
    assert c.check('a', -5) == NO_TF


def test_frame_that_is_earth_is_ready():
    assert chain().check('earth', 5 * S) == READY
    assert chain().check('/earth', 5 * S) == READY


def test_leading_slash_is_ignored_like_tf2():
    c = chain()
    feed(c, tf('/earth', '/a', 100 * S), tf('/earth', '/a', 101 * S))
    assert c.check('/a', 100 * S + S // 2) == READY
    assert c.check('a', 100 * S + S // 2) == READY
    assert c.lookup('a', 100 * S + S // 2)[0] == (0.0, 0.0, 0.0)
    assert bare_ok(c.buffer, 'earth', 'a', 100 * S)


def test_two_links_with_different_rates_and_one_gap():
    c = chain(gap=1.0)
    # fast link every 0.1 s, slow link every 0.5 s with one hole of 3 s
    for k in range(0, 100):
        feed(c, tf('b', 'sonar', 100 * S + k * S // 10))
    for k in (0, 1, 2, 3, 10, 11, 12):
        feed(c, tf('earth', 'b', 100 * S + k * S // 2))
    assert c.check('sonar', 100 * S + 3 * S // 10 + 1) == READY
    assert c.check('sonar', 101 * S + 1) == READY       # slow link bracket 0.5 s
    assert c.check('sonar', 103 * S) == TF_GAP           # inside the hole
    assert c.check('sonar', 105 * S + S // 4) == READY
    assert c.check('sonar', 106 * S + 1) == WAIT_LATE


def test_definite_failure_wins_over_a_wait_on_another_link():
    c = chain(gap=1.0)
    feed(c, tf('b', 'sonar', 100 * S), tf('b', 'sonar', 110 * S))
    feed(c, tf('earth', 'b', 100 * S), tf('earth', 'b', 105 * S))
    assert c.check('sonar', 107 * S) == TF_GAP     # sonar link: 10 s apart
    feed(c, tf('earth', 'b', 108 * S))
    assert c.check('sonar', 107 * S) == TF_GAP


def test_chain_incomplete_waits_and_then_resolves():
    c = chain()
    feed(c, tf('b', 'sonar', 100 * S), tf('b', 'sonar', 101 * S))
    assert c.check('sonar', 100 * S + 1) == WAIT_CHAIN
    feed(c, tf('earth', 'b', 100 * S), tf('earth', 'b', 101 * S))
    assert c.check('sonar', 100 * S + 1) == READY


def test_static_link_is_exempt_from_the_gap_rule():
    c = chain(gap=1.0)
    feed(c, tf('earth', 'b', 100 * S, x=2.0), static=True)
    feed(c, tf('b', 'sonar', 100 * S), tf('b', 'sonar', 100 * S + S // 2))
    assert c.check('sonar', 100 * S + 1) == READY
    assert c.lookup('sonar', 100 * S + 1)[0][0] == pytest.approx(2.0)


def test_identical_static_repeats_are_fine():
    c = chain()
    for _ in range(3):
        feed(c, tf('earth', 'b', 0), static=True)
    assert c.check('b', 5 * S) == READY


@pytest.mark.parametrize('what, transform', [
    ('not_finite', tf('earth', 'a', 100 * S, x=float('nan'))),
    ('zero_quaternion', tf('earth', 'a', 100 * S, q=(0.0, 0.0, 0.0, 0.0))),
    ('self_transform', tf('a', 'a', 100 * S)),
    ('empty_frame_id', tf('earth', '', 100 * S)),
    ('empty_frame_id', tf('', 'a', 100 * S)),
])
def test_transforms_tf2_rejects_are_not_tracked(what, transform):
    c = chain()
    feed(c, transform)
    assert c.rejected[what] == 1
    assert c.check('a', 100 * S) == WAIT_CHAIN
    bare = Buffer(cache_time=Duration(seconds=600.0))
    bare.set_transform(transform, 'test')
    assert not bare_ok(bare, 'earth', 'a', 100 * S)


def test_a_quaternion_that_is_not_unit_length_is_accepted_by_both():
    c = chain()
    feed(c, tf('earth', 'a', 100 * S, q=(0.0, 0.0, 0.0, 2.0)))
    assert c.check('a', 100 * S) == READY
    assert bare_ok(c.buffer, 'earth', 'a', 100 * S)


def test_data_older_than_the_cache_is_rejected_like_tf2():
    c = chain(cache=10.0)
    feed(c, tf('earth', 'a', 110 * S))
    feed(c, tf('earth', 'a', 100 * S))              # exactly the cutoff: kept
    feed(c, tf('earth', 'a', 100 * S - 1))          # one ns older: rejected
    assert c.rejected['older_than_cache'] == 1
    assert c.check('a', 100 * S) == READY
    assert c.check('a', 100 * S - 1) == NO_TF
    assert bare_ok(c.buffer, 'earth', 'a', 100 * S)
    assert not bare_ok(c.buffer, 'earth', 'a', 100 * S - 1)


def test_out_of_order_stamps_are_kept_sorted():
    c = chain()
    feed(c, tf('earth', 'a', 102 * S), tf('earth', 'a', 100 * S),
         tf('earth', 'a', 101 * S), tf('earth', 'a', 101 * S))
    assert c.check('a', 100 * S + 1) == READY
    assert c.check('a', 101 * S + 1) == READY
    assert c._stamps['a'].items == [100 * S, 101 * S, 102 * S]


def test_re_parenting_is_an_error():
    c = chain()
    feed(c, tf('earth', 'a', 100 * S))
    with pytest.raises(TfChainError, match='two parents'):
        feed(c, tf('map', 'a', 101 * S))


def test_re_parenting_a_static_frame_is_an_error():
    c = chain()
    feed(c, tf('earth', 'a', 0), static=True)
    with pytest.raises(TfChainError, match='two parents'):
        feed(c, tf('map', 'a', 0), static=True)


@pytest.mark.parametrize('first_static', [True, False])
def test_frame_in_both_tf_and_tf_static_is_an_error(first_static):
    c = chain()
    feed(c, tf('earth', 'a', 100 * S), static=first_static)
    with pytest.raises(TfChainError, match='both /tf and /tf_static'
                       if first_static is False else
                       'both /tf_static and /tf'):
        feed(c, tf('earth', 'a', 101 * S), static=not first_static)


def test_a_loop_is_an_error():
    c = chain()
    feed(c, tf('b', 'a', 100 * S), tf('a', 'b', 100 * S))
    with pytest.raises(TfChainError, match='loop'):
        c.check('a', 100 * S)


def test_lookup_after_a_ready_verdict_that_tf2_refuses_is_a_mismatch(
        monkeypatch):
    c = chain()
    feed(c, tf('earth', 'a', 100 * S))

    def refuse(*a, **k):
        raise TransformException('forced')
    monkeypatch.setattr(c.buffer, 'lookup_transform', refuse)
    assert c.check('a', 100 * S) == READY
    with pytest.raises(TfMismatch, match='forced'):
        c.lookup('a', 100 * S)


def test_lookup_returns_the_interpolated_pose():
    c = chain()
    feed(c, tf('earth', 'a', 100 * S, x=1.0), tf('earth', 'a', 101 * S, x=3.0))
    pos, quat = c.lookup('a', 100 * S + S // 4)
    assert pos[0] == pytest.approx(1.5)
    assert quat == (0.0, 0.0, 0.0, 1.0)


def test_stamp_ns():
    assert tf_lookup.stamp_ns(SimpleNamespace(sec=3, nanosec=7)) == 3 * S + 7


@pytest.mark.parametrize('seed', range(8))
def test_agrees_with_tf2_on_random_streams(seed):
    """Random order, duplicates, old data and pruning: tracker == Buffer."""
    rng = random.Random(seed)
    cache = rng.choice([3.0, 10.0, 600.0])
    c = TfChain('earth', cache, 1e6)    # no gap limit: compare tf2's answer
    bare = Buffer(cache_time=Duration(seconds=cache))
    base = 1000 * S
    for _ in range(300):
        jitter = rng.choice([0, 0, 0, 1, 5, -3, -15, 40])
        newest = max(c._stamps['a'].items[-1] if 'a' in c._stamps
                     and len(c._stamps['a']) else base, base)
        ns = max(1, newest + jitter * S // 4 + rng.choice([0, 0, 1, -1]))
        t = tf('earth', 'a', ns, x=float(ns % 97))
        feed(c, t)
        # the bare buffer sees everything; the tracker only what tf2 keeps
        bare.set_transform(t, 'test')
    items = c._stamps['a'].items[c._stamps['a'].start:]
    lo, hi = items[0], items[-1]
    probes = set(items)
    probes.update(rng.randint(lo - 5 * S, hi + 5 * S) for _ in range(200))
    probes.update(s + d for s in items[:20] for d in (-1, 1))
    for ns in sorted(p for p in probes if p > 0):
        verdict = c.check('a', ns)
        ok = bare_ok(bare, 'earth', 'a', ns)
        assert (verdict in (READY, TF_GAP)) == ok, (seed, ns, verdict)
        if ok:
            c.lookup('a', ns)


def test_a_loop_is_an_error_whatever_the_links_before_it_say():
    c = chain()
    feed(c, tf('b', 'a', 100 * S), tf('a', 'b', 100 * S))
    # Before the first sample a definite NO_TF used to be returned first.
    with pytest.raises(TfChainError, match='loop'):
        c.check('a', 50 * S)


def test_a_link_with_no_sample_yet_makes_the_chain_wait():
    c = chain()
    feed(c, tf('earth', 'a', 100 * S), tf('a', 'b', 100 * S))
    c._stamps['a'].items.clear()            # as if its stamps were gone
    assert c.check('b', 100 * S) == WAIT_CHAIN


def test_the_buffer_and_the_tracker_share_one_cache_length_in_ns():
    # round() gives 10.000000001 s, truncation 10.000000000 s: a sample the
    # tracker keeps must not be one tf2 has refused.
    c = chain(cache=10.0000000006)
    feed(c, tf('earth', 'a', 110 * S + 1))
    feed(c, tf('earth', 'a', 100 * S))
    assert c.rejected['older_than_cache'] == 0
    assert c.check('a', 100 * S) == READY
    assert bare_ok(c.buffer, 'earth', 'a', 100 * S)


@pytest.mark.parametrize('cache, gap', [
    (1e30, 1.0), (0.0, 1.0), (-1.0, 1.0), (600.0, 1e-10), (600.0, 0.0)])
def test_option_values_the_buffer_cannot_take_are_refused(cache, gap):
    with pytest.raises(ValueError):
        TfChain('earth', cache, gap)


def test_a_stamp_fed_twice_is_counted():
    c = chain()
    feed(c, tf('earth', 'a', 100 * S, x=1.0))
    feed(c, tf('earth', 'a', 100 * S, x=2.0))
    feed(c, tf('earth', 'a', 101 * S))
    assert c.rejected['duplicate_stamp'] == 1
    assert c.check('a', 100 * S) == READY      # tf2 kept one, tracker one


def test_a_sample_the_cache_dropped_is_tf_evicted_not_no_tf():
    c = chain(cache=10.0)
    feed(c, tf('earth', 'a', 100 * S), tf('earth', 'a', 105 * S),
         tf('earth', 'a', 112 * S), tf('earth', 'a', 125 * S))
    assert c.check('a', 100 * S) == TF_EVICTED
    assert c.check('a', 104 * S) == TF_EVICTED
    assert c.check('a', 105 * S) == READY
    assert c.settle(TF_EVICTED) == TF_EVICTED
    other = chain(cache=10.0)
    feed(other, tf('earth', 'b', 112 * S))
    assert other.check('b', 100 * S) == NO_TF   # nothing was ever dropped
