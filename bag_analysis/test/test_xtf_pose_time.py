"""
Tests that bag_to_xtf looks each ping's pose up at the ping's own time.

The track (see ``xtf_fakes.Track``) turns at 15 deg/s and moves at 4 m/s,
sampled at 10 Hz. Pings sit at fractional positions between samples, and
the bag lists each /tf sample after the pings that need it, as the real bag
does, so several pings wait at once. The expected pose of each ping is
computed from the two bracketing samples with an interpolation written for
the test, not from tf2.

Drop counters (``no_tf``, ``tf_gap``, ``tf_late``, ``tf_mismatch``) count
pings, a port and a starboard message paired on their stamps; ``unpaired``
and ``malformed`` count messages.
"""

import datetime as _dt
import re
from types import SimpleNamespace

from bag_analysis import tf_lookup
from bag_analysis.cli import bag_to_xtf
from bag_analysis.xtf.geo import ecef_pose_to_geo, quaternion_to_matrix
import numpy as np
import pytest
from xtf_fakes import (
    base_args, FRAME, PORT_TOPIC, read_ping_headers, single_ping_messages,
    STBD_TOPIC, Track, track_messages, two_frame_messages)

_NS = 1_000_000_000


def _run(tmp_path, monkeypatch, messages, *extra, name='out.xtf'):
    monkeypatch.setattr(
        bag_to_xtf, 'iter_messages', lambda *a, **k: iter(messages))
    bag = tmp_path / 'bag'
    bag.mkdir(exist_ok=True)
    out = tmp_path / name
    return bag_to_xtf.main(base_args(bag, out, *extra)), out


def _wrap(d):
    return (d + 180.0) % 360.0 - 180.0


def _dropped(report):
    lines = re.findall(r'dropped [^:\n]*: (.*)', report)
    return {k: int(v) for line in lines
            for k, v in re.findall(r'(\w+)=(\d+)', line)}


def _check_pose(ping, geo):
    lat, lon, _alt, heading, pitch, roll = geo
    assert abs(ping['lat'] - lat) < 1e-8      # about a millimetre
    assert abs(ping['lon'] - lon) < 1e-8
    assert abs(_wrap(ping['heading'] - heading)) < 1e-3
    assert abs(_wrap(ping['pitch'] - pitch)) < 1e-3
    assert abs(_wrap(ping['roll'] - roll)) < 1e-3


def _spy_queue(monkeypatch):
    """Record the length of the hold queue each time it is looked at."""
    depths = []
    original = bag_to_xtf._resolve

    def spy(held, *a, **k):
        depths.append(len(held))
        return original(held, *a, **k)
    monkeypatch.setattr(bag_to_xtf, '_resolve', spy)
    return depths


def test_pose_is_the_one_at_the_ping_time(tmp_path, monkeypatch, capsys):
    track = Track(70, flip={5, 17, 18, 40})
    messages, stamps = track_messages(track, 60)
    depths = _spy_queue(monkeypatch)
    rc, out = _run(tmp_path, monkeypatch, messages, '--no-split')
    assert rc == 0, capsys.readouterr().out
    pings = read_ping_headers(out)
    assert len(pings) == 60
    for stamp, ping in zip(stamps, pings):
        _check_pose(ping, track.geo_at(stamp))
    assert max(depths) >= 4               # several pings held at once
    # The track really is hard on a stale pose: one sample of staleness is
    # far outside the tolerances above.
    h_ping = track.geo_at(stamps[10])[3]
    h_sample = track.geo_at(track.stamps[10])[3]
    assert abs(_wrap(h_ping - h_sample)) > 0.4


def test_altitude_is_the_one_read_with_the_ping(tmp_path, monkeypatch):
    track = Track(70)
    messages, _ = track_messages(track, 60)
    rc, out = _run(tmp_path, monkeypatch, messages, '--no-split')
    assert rc == 0
    for n, ping in enumerate(read_ping_headers(out)):
        assert ping['altitude'] == pytest.approx(3.0 + n * 0.01, abs=1e-4)


def test_ping_in_bag_order_before_any_transform_arrives(
        tmp_path, monkeypatch, capsys):
    track = Track(70)
    messages, stamps = track_messages(track, 60)
    first_tf = next(i for i, m in enumerate(messages) if m[0] == '/tf')
    assert first_tf > 0         # a ping is recorded before the first /tf
    rc, out = _run(tmp_path, monkeypatch, messages, '--no-split')
    assert rc == 0
    assert len(read_ping_headers(out)) == 60


def test_ping_before_the_first_sample_is_no_tf(
        tmp_path, monkeypatch, capsys):
    track = Track(70)
    skip = {0, 1, 2}
    messages, stamps = track_messages(track, 60)
    messages = [m for m in messages if not (
        m[0] == '/tf' and m[1].transforms[0].header.stamp.nanosec
        + m[1].transforms[0].header.stamp.sec * _NS
        in {track.stamps[k] for k in skip})]
    rc, out = _run(tmp_path, monkeypatch, messages, '--no-split')
    report = capsys.readouterr().out
    assert rc == 0
    # pings 1 and 2 (indices 0 and 1) are before sample 3: two pings
    assert _dropped(report)['no_tf'] == 2
    pings = read_ping_headers(out)
    assert len(pings) == 58
    for stamp, ping in zip(stamps[2:], pings):
        _check_pose(ping, track.geo_at(stamp))


def test_ping_after_the_last_sample_is_tf_late(
        tmp_path, monkeypatch, capsys):
    track = Track(50)       # samples 0..49; pings 49..60 need sample 50+
    messages, stamps = track_messages(track, 60)
    rc, out = _run(tmp_path, monkeypatch, messages, '--no-split')
    report = capsys.readouterr().out
    assert rc == 0
    late = [s for s in stamps if s > track.stamps[-1]]
    assert _dropped(report)['tf_late'] == len(late)
    pings = read_ping_headers(out)
    assert len(pings) == 60 - len(late)
    for stamp, ping in zip(stamps[:len(pings)], pings):
        _check_pose(ping, track.geo_at(stamp))


def test_gap_longer_than_the_limit_drops_only_its_pings(
        tmp_path, monkeypatch, capsys):
    track = Track(70)
    skip = set(range(20, 30))           # samples 19 and 30: 1.1 s apart
    messages, stamps = track_messages(track, 60)
    stamp_set = {track.stamps[k] for k in skip}
    messages = [m for m in messages if not (
        m[0] == '/tf' and m[1].transforms[0].header.stamp.sec * _NS
        + m[1].transforms[0].header.stamp.nanosec in stamp_set)]
    rc, out = _run(tmp_path, monkeypatch, messages, '--no-split')
    report = capsys.readouterr().out
    assert rc == 0
    inside = [s for s in stamps
              if track.stamps[19] < s < track.stamps[30]]
    assert len(inside) == 11
    assert _dropped(report)['tf_gap'] == 11
    kept = [s for s in stamps if s not in inside]
    pings = read_ping_headers(out)
    assert len(pings) == len(kept)
    for stamp, ping in zip(kept, pings):
        _check_pose(ping, track.geo_at(stamp, skip))


def test_gap_shorter_than_the_limit_is_interpolated(
        tmp_path, monkeypatch, capsys):
    track = Track(70)
    skip = set(range(40, 45))           # samples 39 and 45: 0.6 s apart
    messages, stamps = track_messages(track, 60)
    stamp_set = {track.stamps[k] for k in skip}
    messages = [m for m in messages if not (
        m[0] == '/tf' and m[1].transforms[0].header.stamp.sec * _NS
        + m[1].transforms[0].header.stamp.nanosec in stamp_set)]
    rc, out = _run(tmp_path, monkeypatch, messages, '--no-split')
    assert rc == 0
    assert _dropped(capsys.readouterr().out)['tf_gap'] == 0
    pings = read_ping_headers(out)
    assert len(pings) == 60
    for stamp, ping in zip(stamps, pings):
        _check_pose(ping, track.geo_at(stamp, skip))


def test_max_tf_gap_option_moves_the_limit(tmp_path, monkeypatch, capsys):
    track = Track(70)
    skip = set(range(20, 30))
    messages, _ = track_messages(track, 60)
    stamp_set = {track.stamps[k] for k in skip}
    messages = [m for m in messages if not (
        m[0] == '/tf' and m[1].transforms[0].header.stamp.sec * _NS
        + m[1].transforms[0].header.stamp.nanosec in stamp_set)]
    rc, _ = _run(tmp_path, monkeypatch, messages, '--max-tf-gap', '1.2')
    assert rc == 0
    assert _dropped(capsys.readouterr().out)['tf_gap'] == 0


def test_two_links_with_different_rates_and_a_hole_in_the_slow_one(
        tmp_path, monkeypatch, capsys):
    track = Track(70)
    messages, stamps = track_messages(track, 60, child='base')
    offset = (0.3, -0.2, 0.1)
    slow = []
    for k in range(0, 70, 5):           # every 0.5 s
        if 20 <= k <= 45:               # a hole: samples 15 and 50 remain
            continue
        tf = track.tf_msg(k, child=FRAME, parent='base')
        t = tf.transforms[0]
        t.transform.translation.x, t.transform.translation.y, \
            t.transform.translation.z = offset
        t.transform.rotation.x = t.transform.rotation.y = 0.0
        t.transform.rotation.z, t.transform.rotation.w = 0.0, 1.0
        slow.append(('/tf', tf, track.stamps[k] + 250_000_000))
    messages = sorted(messages + slow, key=lambda m: m[2])
    rc, out = _run(tmp_path, monkeypatch, messages, '--no-split')
    report = capsys.readouterr().out
    assert rc == 0
    in_hole = [s for s in stamps
               if track.stamps[15] < s < track.stamps[50]]
    ok = [s for s in stamps if s not in in_hole]
    assert _dropped(report)['tf_gap'] == len(in_hole) > 0
    pings = read_ping_headers(out)
    assert len(pings) == len(ok)
    for stamp, ping in zip(ok, pings):
        pos, quat = track.pose_at(stamp)
        # the sonar sits at a fixed lever arm from the interpolated base
        sonar = pos + quaternion_to_matrix(*quat) @ np.array(offset)
        _, _, _, heading, pitch, roll = track.geo_at(stamp)
        lat, lon, *_ = ecef_pose_to_geo(tuple(sonar), tuple(quat))
        assert abs(ping['lat'] - lat) < 1e-8
        assert abs(ping['lon'] - lon) < 1e-8
        assert abs(_wrap(ping['heading'] - heading)) < 1e-3


def test_late_stamped_sample_leaves_other_pings_alone(
        tmp_path, monkeypatch, capsys):
    track = Track(70)
    messages, stamps = track_messages(track, 60, late_samples=(20,))
    # sample 20 is recorded after sample 21
    order = [m[1].transforms[0].header.stamp.sec * _NS
             + m[1].transforms[0].header.stamp.nanosec
             for m in messages if m[0] == '/tf']
    assert order.index(track.stamps[20]) > order.index(track.stamps[21])
    rc, out = _run(tmp_path, monkeypatch, messages, '--no-split')
    assert rc == 0
    pings = read_ping_headers(out)
    assert len(pings) == 60
    # A ping released before the late sample arrives is looked up in the
    # wider bracket (documented in tf_lookup); the others are exact.
    for stamp, ping in zip(stamps, pings):
        if track.stamps[19] < stamp < track.stamps[21]:
            continue
        _check_pose(ping, track.geo_at(stamp))


def test_end_time_keeps_the_pings_still_waiting(
        tmp_path, monkeypatch, capsys):
    track = Track(70)
    messages, stamps = track_messages(track, 60)
    end = stamps[40] + 10_000_000
    rc, out = _run(tmp_path, monkeypatch, messages, '--no-split',
                   '--end-time', f'{end / _NS:.6f}')
    report = capsys.readouterr().out
    assert rc == 0
    pings = read_ping_headers(out)
    assert len(pings) == 41
    for stamp, ping in zip(stamps, pings):
        _check_pose(ping, track.geo_at(stamp))
    # the first ping past the end is counted, as before; nothing after it
    assert 'port msgs=42 starboard msgs=41' in report
    assert _dropped(report) == {
        'no_tf': 0, 'tf_gap': 0, 'tf_late': 0, 'tf_evicted': 0, 'tf_mismatch': 0,
        'malformed': 0, 'unpaired': 0}


def test_start_time_window_is_on_the_stamp(tmp_path, monkeypatch, capsys):
    track = Track(70)
    messages, stamps = track_messages(track, 60)
    start = stamps[10] + 1_000_000
    rc, out = _run(tmp_path, monkeypatch, messages, '--no-split',
                   '--start-time', f'{start / _NS:.6f}')
    report = capsys.readouterr().out
    assert rc == 0
    assert 'trimmed: 22 pings' in report
    assert len(read_ping_headers(out)) == 49


def test_max_pings_leaves_the_counts_clean(tmp_path, monkeypatch, capsys):
    track = Track(70)
    messages, _ = track_messages(track, 60)
    rc, out = _run(tmp_path, monkeypatch, messages, '--max-pings', '5')
    report = capsys.readouterr().out
    assert rc == 0
    assert len(read_ping_headers(out)) == 5
    dropped = _dropped(report)
    assert dropped['unpaired'] == 0
    assert dropped['tf_late'] == dropped['no_tf'] == 0


def test_max_pings_does_not_count_the_halves_read_ahead(
        tmp_path, monkeypatch, capsys):
    track = Track(70)
    messages, _ = track_messages(track, 60)
    # Record every starboard half 0.05 s after its port half, so that while
    # the last wanted ping waits for its transforms, later pings are read
    # as lone port halves.
    messages = sorted(
        ((t, m, r + 50_000_000 if t == STBD_TOPIC else r)
         for t, m, r in messages), key=lambda e: e[2])
    rc, out = _run(tmp_path, monkeypatch, messages, '--max-pings', '5')
    report = capsys.readouterr().out
    assert rc == 0
    assert len(read_ping_headers(out)) == 5
    assert _dropped(report)['unpaired'] == 0


def test_a_chain_that_never_comes_costs_one_wait_not_the_bag(
        tmp_path, monkeypatch, capsys):
    track = Track(70)
    messages, _ = track_messages(track, 60)
    messages = [m for m in messages if m[0] != '/tf']
    depths = _spy_queue(monkeypatch)
    rc, out = _run(tmp_path, monkeypatch, messages, '--tf-wait', '2')
    report = capsys.readouterr().out
    assert rc == 1                       # nothing could be written
    assert _dropped(report)['no_tf'] == 60
    assert not out.exists()
    # 2 s of bag time at 20 messages/s, plus the one being read
    assert max(depths) <= 45


def test_a_bad_stamp_does_not_flush_the_queue(tmp_path, monkeypatch, capsys):
    track = Track(70)
    messages, stamps = track_messages(track, 60)
    future = 4_000_000_000 * _NS // 1_000_000_000   # a stamp far in the future
    for topic, msg, _ in messages:
        if topic in (PORT_TOPIC, STBD_TOPIC) and (
                msg.header.stamp.sec * _NS + msg.header.stamp.nanosec
                == stamps[30]):
            msg.header.stamp = SimpleNamespace(sec=future, nanosec=0)
    rc, out = _run(tmp_path, monkeypatch, messages, '--no-split')
    report = capsys.readouterr().out
    assert rc == 0
    assert _dropped(report)['tf_late'] == 1
    good = [s for i, s in enumerate(stamps) if i != 30]
    pings = read_ping_headers(out)
    assert len(pings) == 59
    for stamp, ping in zip(good, pings):
        _check_pose(ping, track.geo_at(stamp))


def test_a_lookup_that_fails_after_ready_is_counted_apart(
        tmp_path, monkeypatch, capsys):
    track = Track(70)
    messages, stamps = track_messages(track, 60)
    original = tf_lookup.TfChain.lookup
    refused = set(stamps[::3])          # every third ping

    def sometimes(self, frame, lookup_ns):
        if lookup_ns in refused:
            raise tf_lookup.TfMismatch('forced')
        return original(self, frame, lookup_ns)
    monkeypatch.setattr(tf_lookup.TfChain, 'lookup', sometimes)
    rc, out = _run(tmp_path, monkeypatch, messages, '--no-split')
    dropped = _dropped(capsys.readouterr().out)
    assert rc == 0
    assert dropped['tf_mismatch'] == 20     # a ping, not a message
    assert len(read_ping_headers(out)) == 40
    assert dropped['no_tf'] == 0 and dropped['tf_gap'] == 0


def test_transforms_tf2_rejects_are_reported(tmp_path, monkeypatch, capsys):
    track = Track(70)
    messages, _ = track_messages(track, 60)
    bad = track.tf_msg(5)
    bad.transforms[0].transform.translation.x = float('nan')
    messages.insert(3, ('/tf', bad, messages[3][2]))
    rc, _ = _run(tmp_path, monkeypatch, messages, '--no-split')
    assert rc == 0
    assert 'transforms rejected or re-fed: not_finite=1' in capsys.readouterr().out


@pytest.mark.parametrize('bad_tree, message', [
    ('reparent', 'two parents'),
    ('both', 'both /tf_static and /tf'),
])
def test_unsupported_tree_ends_the_run_with_exit_1(
        tmp_path, monkeypatch, capsys, bad_tree, message):
    track = Track(70)
    messages, _ = track_messages(track, 60)
    if bad_tree == 'reparent':
        messages.append(('/tf', track.tf_msg(5, parent='map'), 10**18))
    else:
        messages.insert(0, ('/tf_static', track.tf_msg(0), 0))
    rc, out = _run(tmp_path, monkeypatch, messages, '--no-split')
    captured = capsys.readouterr()
    assert rc == 1
    assert message in captured.err
    assert not out.exists()
    assert not list(tmp_path.glob('*.partial'))


# --- the stamp-to-transmit offset -------------------------------------------

def _written_time(ping):
    y, mo, d, h, mi, s, hs = ping['time']
    return _dt.datetime(y, mo, d, h, mi, s, hs * 10000,
                        tzinfo=_dt.timezone.utc)


@pytest.mark.parametrize('offset', [0.05, -0.05, 0.0, 0.1])
def test_offset_moves_the_lookup_and_the_written_time_together(
        tmp_path, monkeypatch, capsys, offset):
    track = Track(70)
    messages, stamps = track_messages(track, 60)
    rc, out = _run(tmp_path, monkeypatch, messages, '--no-split',
                   '--stamp-to-transmit-offset', str(offset))
    report = capsys.readouterr().out
    assert rc == 0, report
    assert f'stamp-to-transmit offset: {offset:g} s' in report
    offset_ns = round(offset * _NS)
    pings = read_ping_headers(out)
    assert len(pings) == 60
    for stamp, ping in zip(stamps, pings):
        _check_pose(ping, track.geo_at(stamp - offset_ns))
        sent = _dt.datetime.fromtimestamp(
            (stamp - offset_ns) / 1e9, tz=_dt.timezone.utc)
        assert _written_time(ping) == sent.replace(
            microsecond=sent.microsecond // 10000 * 10000)
        raw = _dt.datetime.fromtimestamp(stamp / 1e9, tz=_dt.timezone.utc)
        assert ping['clock'] == (
            raw.hour, raw.minute, raw.second, raw.microsecond // 10000)


def test_offset_does_not_change_the_window_or_the_pairing(
        tmp_path, monkeypatch, capsys):
    track = Track(70)
    messages, stamps = track_messages(track, 60)
    start = stamps[10] + 1_000_000
    rc, out = _run(tmp_path, monkeypatch, messages, '--no-split',
                   '--stamp-to-transmit-offset', '0.2',
                   '--start-time', f'{start / _NS:.6f}')
    report = capsys.readouterr().out
    assert rc == 0
    assert 'trimmed: 22 pings' in report
    first = read_ping_headers(out)[0]
    # the first ping in the window is ping 11, written at stamp - 0.2 s
    sent = _dt.datetime.fromtimestamp(
        (stamps[11] - 200_000_000) / 1e9, tz=_dt.timezone.utc)
    assert _written_time(first) == sent.replace(
        microsecond=sent.microsecond // 10000 * 10000)


def test_part_names_follow_the_written_time(tmp_path, monkeypatch, capsys):
    track = Track(70)
    messages, stamps = track_messages(track, 60)
    rc, _ = _run(tmp_path, monkeypatch, messages,
                 '--stamp-to-transmit-offset', '-2', '--split-size', '0.02')
    assert rc == 0
    parts = sorted(p.name for p in tmp_path.glob('out_*.xtf'))
    assert len(parts) >= 2
    first_written = _dt.datetime.fromtimestamp(
        (stamps[0] + 2 * _NS) / 1e9, tz=_dt.timezone.utc)
    assert parts[0] == f'out_{first_written:%Y%m%dT%H%M%SZ}.xtf'
    raw_label = _dt.datetime.fromtimestamp(
        stamps[0] / 1e9, tz=_dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    assert raw_label not in parts[0]


@pytest.mark.parametrize('value', ['nan', 'inf', '-inf', '7200', '-7200'])
def test_offset_must_be_finite_and_sane(tmp_path, monkeypatch, value):
    with pytest.raises(SystemExit) as exc:
        _run(tmp_path, monkeypatch, [], '--stamp-to-transmit-offset=' + value)
    assert exc.value.code == 2


# --- options ------------------------------------------------------------------

def test_max_tf_age_is_a_usage_error_naming_the_new_option(
        tmp_path, monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        _run(tmp_path, monkeypatch, [], '--max-tf-age', '60')
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert '--max-tf-gap' in err and 'no longer exists' in err


def test_max_tf_age_cannot_be_reached_by_a_prefix(
        tmp_path, monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        _run(tmp_path, monkeypatch, [], '--max-tf', '60')
    assert exc.value.code == 2


@pytest.mark.parametrize('extra', [
    ['--max-tf-gap', '0'],
    ['--max-tf-gap', 'nan'],
    ['--tf-wait', '1'],                           # not above the gap limit
    ['--tf-wait', '3', '--max-tf-gap', '3'],
    ['--tf-cache', '5'],                          # not above the wait
    ['--tf-cache', '6.5', '--stamp-to-transmit-offset', '-1'],
    ['--tf-cache', 'inf'],
    ['--tf-cache', '1e30'],                       # beyond what Duration holds
    ['--max-tf-gap', '1e-10'],                    # rounds to 0 ns
    ['--start-time', 'nan'],
    ['--end-time', 'inf'],
    ['--start-time', 'yesterday'],
])
def test_wait_gap_and_cache_are_checked_against_each_other(
        tmp_path, monkeypatch, extra):
    with pytest.raises(SystemExit) as exc:
        _run(tmp_path, monkeypatch, [], *extra)
    assert exc.value.code == 2


# --- a ping is one unit: a dropped half takes its mate with it ---------------

def _frame_hole_pings(track):
    """
    Three pings; the middle two are each missing a transform for one channel.

    Ping A (starboard frame has no sample 12) loses its starboard half; ping B
    (port frame has no sample 13) loses its port half, and its starboard half
    is fine. A and B are 0.14 s apart, well inside the pairing tolerance, so
    a surviving half that stays pending would pair with the other ping's
    opposite channel.
    """
    s = track.stamps
    before = (s[5] + 30_000_000, s[5] + 30_000_000)
    a = (s[11] + 80_000_000, s[11] + 80_000_000)
    b = (s[13] + 20_000_000, s[13] + 20_000_000)
    after = (s[20] + 30_000_000, s[20] + 30_000_000)
    return [before, a, b, after]


def test_a_ping_with_one_unresolvable_half_is_dropped_whole(
        tmp_path, monkeypatch, capsys):
    track = Track(40)
    pings = _frame_hole_pings(track)
    messages = two_frame_messages(
        track, pings, drop_port_samples={13}, drop_stbd_samples={12})
    rc, out = _run(tmp_path, monkeypatch, messages, '--no-split',
                   '--max-tf-gap', '0.15')
    report = capsys.readouterr().out
    assert rc == 0, report
    written = read_ping_headers(out)
    # Only the two untouched pings are written; the surviving half of each
    # broken ping must not pair with the other broken ping's other half.
    assert len(written) == 2, report
    assert _dropped(report)['tf_gap'] == 2     # one per ping, not per message
    assert _dropped(report)['unpaired'] == 0
    for ping, (port_ns, _) in zip(written, (pings[0], pings[3])):
        _check_pose(ping, track.geo_at(port_ns))


def test_negative_offset_waits_for_the_transforms_it_looks_up(
        tmp_path, monkeypatch, capsys):
    # The lookup is 6 s after the stamp, so the transforms it needs are
    # recorded more than the default 5 s of bag time after the ping. The
    # wait is counted from the lookup time, so no ping is called late.
    track = Track(140)
    messages, stamps = track_messages(track, 60)
    rc, out = _run(tmp_path, monkeypatch, messages, '--no-split',
                   '--stamp-to-transmit-offset', '-6')
    report = capsys.readouterr().out
    assert rc == 0, report
    assert _dropped(report)['tf_late'] == 0
    pings = read_ping_headers(out)
    assert len(pings) == 60
    for stamp, ping in zip(stamps, pings):
        _check_pose(ping, track.geo_at(stamp + 6 * _NS))


def test_negative_offset_with_no_transforms_is_still_tf_late(
        tmp_path, monkeypatch, capsys):
    # The cause is still named: the transforms past the lookup never come.
    track = Track(70)                      # ends at 6.9 s
    messages, stamps = track_messages(track, 60)
    rc, _ = _run(tmp_path, monkeypatch, messages, '--no-split',
                 '--stamp-to-transmit-offset', '-6')
    report = capsys.readouterr().out
    assert rc == 0
    assert _dropped(report)['tf_late'] > 0


def test_pings_held_at_end_time_settle_without_reading_to_the_bag_end(
        tmp_path, monkeypatch, capsys):
    track = Track(70)
    messages, stamps = track_messages(track, 60)
    # /tf stops after sample 38; the pings up to the end time at ping 40 wait
    # for samples that never come, and only sonar messages follow.
    messages = [m for m in messages if not (
        m[0] == '/tf' and m[1].transforms[0].header.stamp.sec * _NS
        + m[1].transforms[0].header.stamp.nanosec > track.stamps[38])]
    consumed = []

    def reader(*a, **k):
        for m in messages:
            consumed.append(1)
            yield m
    monkeypatch.setattr(bag_to_xtf, 'iter_messages', reader)
    bag = tmp_path / 'bag'
    bag.mkdir()
    end = stamps[40] + 10_000_000
    rc = bag_to_xtf.main(base_args(
        bag, tmp_path / 'out.xtf', '--no-split', '--tf-wait', '1.2',
        '--end-time', f'{end / _NS:.6f}'))
    report = capsys.readouterr().out
    assert rc == 0
    assert _dropped(report)['tf_late'] > 0
    assert len(consumed) < len(messages) - 10


# Integer-nanosecond boundaries of the XTF hundredths. The written time is
# the truncation of the instant to 1/100 s, worked out here by hand from
# 1_750_000_000 s since the epoch = 2025-06-15 15:06:40 UTC exactly
# (20254 days = 1_749_945_600 s, plus 54_400 s = 15 h 6 min 40 s).
_T0_S = 1_750_000_000
_BOUNDARY = [
    # id, stamp ns past _T0_S, offset (s), written time, ComputerClock
    ('just_under_a_hundredth', 9_999_999, '0',
     (15, 6, 40, 0), (15, 6, 40, 0)),
    ('on_a_hundredth', 10_000_000, '0',
     (15, 6, 40, 1), (15, 6, 40, 1)),
    ('just_under_a_second', 999_999_999, '0',
     (15, 6, 40, 99), (15, 6, 40, 99)),
    ('just_under_a_minute', 59_999_999_999, '0',
     (15, 7, 39, 99), (15, 7, 39, 99)),
    ('offset_of_one_ns_crosses_back', 1_000_000_000, '1e-9',
     (15, 6, 40, 99), (15, 6, 41, 0)),
    ('negative_offset_crosses_forward', 999_999_999, '-2e-9',
     (15, 6, 41, 0), (15, 6, 40, 99)),
]


@pytest.mark.parametrize(
    'stamp_past, offset, written, clock',
    [c[1:] for c in _BOUNDARY], ids=[c[0] for c in _BOUNDARY])
def test_hundredths_are_cut_from_the_integer_nanoseconds(
        tmp_path, monkeypatch, capsys, stamp_past, offset, written, clock):
    messages = single_ping_messages(_T0_S * _NS + stamp_past)
    rc, out = _run(tmp_path, monkeypatch, messages, '--no-split',
                   '--stamp-to-transmit-offset=' + offset)
    assert rc == 0, capsys.readouterr().out
    ping, = read_ping_headers(out)
    assert ping['time'] == (2025, 6, 15, *written)
    assert ping['clock'] == clock
