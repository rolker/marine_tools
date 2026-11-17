"""
Tests for the contacts_to_geojson exporter.

The first half covers the bag-free logic: dlog timestamp parsing, log-line
extraction, the ISO-8601 helper, and the Contact -> GeoJSON Feature
shaping. The second half runs the exporter on a synthetic bag stream (a
moving, turning track) to check that log entries are located at their own
time, never from the latest transform.
"""

import datetime as _dt
import json
import math
from types import SimpleNamespace

from bag_analysis.cli import contacts_to_geojson
from bag_analysis.cli.contacts_to_geojson import (
    _contact_feature,
    _iso,
    _parse_log_ts,
    _read_log_entries,
)
import pytest
from xtf_fakes import T0_NS, Track


def test_parse_log_ts_with_seconds():
    dt = _parse_log_ts('2026-06-29 10:30:45 -04:00')
    assert dt is not None
    # -04:00 means 14:30:45 UTC.
    assert dt.astimezone(_dt.timezone.utc).hour == 14
    assert dt.utcoffset() == _dt.timedelta(hours=-4)


def test_parse_log_ts_minute_precision():
    dt = _parse_log_ts('2026-06-29 10:30 -04:00')
    assert dt is not None
    assert dt.second == 0
    assert dt.astimezone(_dt.timezone.utc).hour == 14


def test_parse_log_ts_offset_without_colon():
    with_colon = _parse_log_ts('2026-06-29 10:30 -04:00')
    without = _parse_log_ts('2026-06-29 10:30 -0400')
    assert with_colon is not None and without is not None
    assert with_colon.timestamp() == without.timestamp()


def test_parse_log_ts_t_separator():
    dt = _parse_log_ts('2026-06-29T10:30 -04:00')
    assert dt is not None
    assert dt.astimezone(_dt.timezone.utc).hour == 14


def test_parse_log_ts_rejects_garbage():
    assert _parse_log_ts('not a timestamp') is None
    assert _parse_log_ts('2026-06-29 10:30') is None  # no offset


def test_iso_epoch_zero():
    assert _iso(0) == '1970-01-01T00:00:00+00:00'


def test_iso_known_instant():
    # 2026-06-29 14:30:00 UTC.
    epoch = _dt.datetime(
        2026, 6, 29, 14, 30, tzinfo=_dt.timezone.utc).timestamp()
    assert _iso(epoch) == '2026-06-29T14:30:00+00:00'


def test_read_log_entries(tmp_path):
    log = tmp_path / 'log.md'
    log.write_text(
        '# 2026-06-29 — gabby log\n'
        '\n'
        'Deployment issue: pending\n'
        '**2026-06-29 10:30 -04:00** — em-dash entry\n'
        '**2026-06-29 10:31:05 -0400** - hyphen entry, seconds\n'
        'a plain narrative line, not an entry\n'
        '**bogus-ts** — malformed timestamp, skipped\n'
    )
    entries = _read_log_entries(log)
    assert len(entries) == 2
    (t0, text0), (t1, text1) = entries
    assert text0 == 'em-dash entry'
    assert text1 == 'hyphen entry, seconds'
    # File order preserved; second entry is 65 s after the first.
    assert t1 > t0
    assert (t1 - t0) == 65 * 1_000_000_000


def test_read_log_entries_empty(tmp_path):
    log = tmp_path / 'empty.md'
    log.write_text('# header only\n\nno entries here\n')
    assert _read_log_entries(log) == []


def _make_contact(lon, lat, alt, *, sec=0, nanosec=0):
    return SimpleNamespace(
        geo_pose=SimpleNamespace(
            position=SimpleNamespace(
                longitude=lon, latitude=lat, altitude=alt)),
        header=SimpleNamespace(
            stamp=SimpleNamespace(sec=sec, nanosec=nanosec),
            frame_id='sonar_waterfall'),
        id='contact-7',
        source='operator',
        existence_probability=0.8,
        classification=[
            SimpleNamespace(description='rock'),
            SimpleNamespace(description=''),
        ],
        status=2,
        origin_kind=1,
        note='marked at survey line 3',
        shape=SimpleNamespace(
            type=1,
            dimensions=SimpleNamespace(x=1.5, y=2.0, z=0.5)),
    )


def test_contact_feature_basic_shaping():
    msg = _make_contact(-71.38, 42.97, 48.5, sec=100, nanosec=0)
    feat = _contact_feature(msg, bag_t_ns=999)
    assert feat['type'] == 'Feature'
    assert feat['geometry']['type'] == 'Point'
    # GeoJSON is [lon, lat, alt].
    assert feat['geometry']['coordinates'] == [-71.38, 42.97, 48.5]
    props = feat['properties']
    assert props['feature_type'] == 'contact'
    assert props['id'] == 'contact-7'
    assert props['status'] == 2
    assert props['note'] == 'marked at survey line 3'
    assert props['classification'] == ['rock']  # empty entry filtered out
    assert props['dim_x_m'] == 1.5
    # header stamp (sec=100) wins over the bag time.
    assert props['t_epoch'] == 100.0


def test_contact_feature_omits_nan_altitude():
    msg = _make_contact(-71.38, 42.97, float('nan'))
    feat = _contact_feature(msg, bag_t_ns=0)
    assert feat['geometry']['coordinates'] == [-71.38, 42.97]
    assert len(feat['geometry']['coordinates']) == 2


def test_contact_feature_falls_back_to_bag_time():
    # Zero header stamp -> _stamp_ns returns 0 (falsy) -> bag time used.
    msg = _make_contact(-71.38, 42.97, 1.0, sec=0, nanosec=0)
    feat = _contact_feature(msg, bag_t_ns=5_000_000_000)
    assert math.isclose(feat['properties']['t_epoch'], 5.0)


# --- the exporter end to end, with a synthetic bag -------------------------

_FRAME = 'bizzy/base_link'
_CONTACTS = '/operator/sonar_waterfall/contacts'
_NS = 1_000_000_000


def _run(tmp_path, monkeypatch, messages, log_lines, *extra):
    monkeypatch.setattr(
        contacts_to_geojson, 'iter_messages',
        lambda *a, **k: iter(messages))
    bag = tmp_path / 'bag'
    bag.mkdir(exist_ok=True)
    out = tmp_path / 'out.geojson'
    argv = ['--bag', str(bag), '--output', str(out), *extra]
    if log_lines is not None:
        log = tmp_path / 'log.md'
        log.write_text('\n'.join(log_lines) + '\n')
        argv += ['--log', str(log)]
    rc = contacts_to_geojson.main(argv)
    features = json.loads(out.read_text())['features'] if out.exists() else None
    return rc, features


def _entry(t_ns, text):
    when = _dt.datetime.fromtimestamp(t_ns / 1e9, tz=_dt.timezone.utc)
    return f'**{when:%Y-%m-%d %H:%M:%S} +00:00** — {text}'


def _track(n=100, **kw):
    # Start 0.35 s past a whole second, so whole-second log entries fall
    # between two samples (at 0.65 of the interval) and not on one.
    return Track(n, start_ns=T0_NS + 350_000_000, flip={7, 8, 30}, **kw)


def _tf_messages(track, skip=()):
    return [('/tf', track.tf_msg(k, child=_FRAME), track.stamps[k])
            for k in range(len(track.stamps)) if k not in skip]


def test_log_entry_is_located_at_its_own_time(tmp_path, monkeypatch):
    track = _track()
    t = T0_NS + 4 * _NS          # between samples 36 and 37
    rc, feats = _run(tmp_path, monkeypatch, _tf_messages(track),
                     [_entry(t, 'first'), _entry(t + 3 * _NS, 'second')])
    assert rc == 0
    assert [f['properties']['geo_status'] for f in feats] == ['exact'] * 2
    for feat, when in zip(feats, (t, t + 3 * _NS)):
        lat, lon, *_ = track.geo_at(when)
        lon_out, lat_out = feat['geometry']['coordinates']
        assert abs(lat_out - lat) < 1e-8 and abs(lon_out - lon) < 1e-8
    # The latest transform (what the old fallback would have used) is
    # metres away, so the check above is not vacuous.
    last = track.geo_at(track.stamps[-1])
    assert abs(last[0] - track.geo_at(t)[0]) > 1e-5


def test_before_first_after_last_and_gap(tmp_path, monkeypatch, capsys):
    track = _track()
    skip = set(range(20, 60))                  # a 4 s hole in the samples
    lines = [
        _entry(T0_NS, 'before'),               # first sample is 0.35 s later
        _entry(T0_NS + 4 * _NS, 'in the gap'),
        _entry(T0_NS + 8 * _NS, 'fine'),
        _entry(T0_NS + 20 * _NS, 'after'),
    ]
    rc, feats = _run(tmp_path, monkeypatch, _tf_messages(track, skip), lines)
    assert rc == 0
    status = {f['properties']['text']: f['properties']['geo_status']
              for f in feats}
    assert status == {'before': 'no_tf', 'in the gap': 'tf_gap',
                      'fine': 'exact', 'after': 'tf_late'}
    for f in feats:
        assert (f['geometry'] is None) == (
            f['properties']['geo_status'] != 'exact')
    out = capsys.readouterr().out
    assert '1 located, 1 no_tf, 1 tf_gap, 1 tf_late, 0 tf_mismatch' in out


def test_max_tf_gap_widens_the_bracket(tmp_path, monkeypatch):
    track = _track()
    lines = [_entry(T0_NS + 4 * _NS, 'in the gap')]
    skip = set(range(20, 60))
    rc, feats = _run(tmp_path, monkeypatch, _tf_messages(track, skip), lines,
                     '--max-tf-gap', '10')
    assert rc == 0
    assert feats[0]['properties']['geo_status'] == 'exact'


def test_frame_missing_from_the_bag_is_no_tf(tmp_path, monkeypatch):
    rc, feats = _run(tmp_path, monkeypatch, [],
                     [_entry(T0_NS, 'nothing')])
    assert rc == 0
    assert feats[0]['properties']['geo_status'] == 'no_tf'


def test_max_tf_age_is_rejected_and_names_the_new_option(
        tmp_path, monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        _run(tmp_path, monkeypatch, [], None, '--max-tf-age', '5')
    assert exc.value.code == 2
    assert '--max-tf-gap' in capsys.readouterr().err


@pytest.mark.parametrize('value', ['0', '-1', 'nan', 'inf'])
def test_max_tf_gap_must_be_positive_and_finite(
        tmp_path, monkeypatch, value):
    with pytest.raises(SystemExit) as exc:
        _run(tmp_path, monkeypatch, [], None, '--max-tf-gap=' + value)
    assert exc.value.code == 2


def test_re_parented_frame_stops_the_run(tmp_path, monkeypatch, capsys):
    track = _track()
    messages = _tf_messages(track)
    messages.append(('/tf', track.tf_msg(50, child=_FRAME, parent='map'),
                     track.stamps[50]))
    rc, feats = _run(tmp_path, monkeypatch, messages,
                     [_entry(T0_NS + 4 * _NS, 'x')])
    assert rc == 1
    assert feats is None
    assert 'two parents' in capsys.readouterr().err


def test_frame_on_tf_and_tf_static_stops_the_run(
        tmp_path, monkeypatch, capsys):
    track = _track()
    messages = [('/tf_static', track.tf_msg(0, child=_FRAME), 0)]
    messages += _tf_messages(track)
    rc, feats = _run(tmp_path, monkeypatch, messages,
                     [_entry(T0_NS + 4 * _NS, 'x')])
    assert rc == 1
    assert 'both /tf_static and /tf' in capsys.readouterr().err


def test_contacts_alone_need_no_transforms(tmp_path, monkeypatch):
    contact = _make_contact(-71.38, 42.97, 1.0, sec=100)
    rc, feats = _run(tmp_path, monkeypatch, [(_CONTACTS, contact, 0)], None)
    assert rc == 0
    assert [f['properties']['feature_type'] for f in feats] == ['contact']
