"""
Tests for the contacts_to_geojson exporter's pure helpers.

The georeferencing path (offline TF over a rosbag2) needs a bag and is
exercised in the field; these unit tests cover the bag-free logic: dlog
timestamp parsing, log-line extraction, the ISO-8601 helper, and the
Contact -> GeoJSON Feature shaping.
"""

import datetime as _dt
import math
from types import SimpleNamespace

from bag_analysis.cli.contacts_to_geojson import (
    _contact_feature,
    _iso,
    _parse_log_ts,
    _read_log_entries,
)


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
