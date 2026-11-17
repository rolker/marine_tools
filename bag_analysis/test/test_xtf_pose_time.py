"""
Tests that bag_to_xtf looks each ping's pose up at the ping's own time.

The track (see ``xtf_fakes.Track``) turns at 15 deg/s and moves at 4 m/s,
sampled at 10 Hz. Pings sit at fractional positions between samples, and
the bag lists each /tf sample after the pings that need it, as the real bag
does. The expected pose of each ping is computed from the two bracketing
samples with an interpolation written for the test, not from tf2.
"""

from bag_analysis.cli import bag_to_xtf
import numpy as np
from xtf_fakes import (
    base_args, read_ping_headers, Track, track_messages)


def _run(tmp_path, monkeypatch, messages, *extra, name='out.xtf'):
    monkeypatch.setattr(
        bag_to_xtf, 'iter_messages', lambda *a, **k: iter(messages))
    bag = tmp_path / 'bag'
    bag.mkdir(exist_ok=True)
    out = tmp_path / name
    return bag_to_xtf.main(base_args(bag, out, *extra)), out


def _wrap(d):
    return (d + 180.0) % 360.0 - 180.0


def test_pose_is_the_one_at_the_ping_time(tmp_path, monkeypatch, capsys):
    track = Track(70, flip={5, 17, 18, 40})
    messages, stamps = track_messages(track, 60)
    rc, out = _run(tmp_path, monkeypatch, messages, '--no-split')
    assert rc == 0, capsys.readouterr().out
    pings = read_ping_headers(out)
    assert len(pings) == 60
    for stamp, ping in zip(stamps, pings):
        lat, lon, _alt, heading, pitch, roll = track.geo_at(stamp)
        assert abs(ping['lat'] - lat) < 1e-8      # about a millimetre
        assert abs(ping['lon'] - lon) < 1e-8
        assert abs(_wrap(ping['heading'] - heading)) < 1e-3
        assert abs(_wrap(ping['pitch'] - pitch)) < 1e-3
        assert abs(_wrap(ping['roll'] - roll)) < 1e-3
    # The track really is hard on a stale pose: one sample of staleness is
    # far outside the tolerances above.
    h0 = track.geo_at(stamps[10])[3]
    h1 = track.geo_at(track.stamps[10])[3]
    assert abs(_wrap(h0 - h1)) > 0.4
    assert np.isfinite(h0)
