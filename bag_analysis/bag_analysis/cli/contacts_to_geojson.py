r"""
Export operator-marked sonar contacts (+ deployment log entries) to GeoJSON.

Two feature sources land in one ``FeatureCollection`` so they overlay on the
same map (QGIS, web, CAMP):

* **contacts** — ``marine_interfaces/msg/Contact`` from the operator waterfall
  (default topic ``/operator/sonar_waterfall/contacts``). Each contact already
  carries a ``geo_pose`` (WGS84 lat/lon/alt) filled in by the waterfall viewer,
  so it becomes a ``Point`` directly — no TF needed.

* **log entries** — timestamped lines from a deployment markdown log
  (``**<ts>** — <text>``, the format the ``dlog`` helper writes). These are
  *text*, not inherently spatial, so each is georeferenced to the boat's
  position at the entry's timestamp by composing the bag's TF tree offline
  (``earth`` → ``--track-frame``) — the same offline-TF approach
  ``bag_to_xtf`` uses. Operators often write a log entry right when they mark a
  waterfall contact, so placing both on the track makes them directly
  comparable. An entry that can't be georeferenced (outside the bag's TF span,
  or no transform) is still emitted, with ``"geometry": null`` and a
  ``geo_status`` property, so nothing is silently dropped.

GeoJSON has no native time type, so timestamps live in ``properties`` as
ISO-8601 (``time``) plus epoch seconds (``t_epoch``). Coordinates are
``[lon, lat]`` (+ ``alt`` when available) in WGS84 per RFC 7946.

Usage::

    ros2 run bag_analysis contacts_to_geojson \\
        --bag ~/data/logs/operator/2026-06-29/bags/operator_2026-06-29T09.28.14 \\
        --log .../docs/logs/2026/2026-06-29_salmon_logs.md \\
        --output ~/share/contacts/2026-06-29.geojson

``--log`` is optional; omit it to export contacts only.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
from pathlib import Path
import re
import sys

from rclpy.duration import Duration
import rclpy.time
from tf2_ros import Buffer

from .bag_to_xtf import _lookup_pose, _stamp_ns
from ..reader import iter_messages
from ..xtf.geo import ecef_pose_to_geo

_DEFAULT_CONTACTS = '/operator/sonar_waterfall/contacts'
_DEFAULT_TRACK_FRAME = 'bizzy/base_link'
_DEFAULT_EARTH_FRAME = 'earth'
_NS_PER_S = 1_000_000_000

# A deployment-log entry written by dlog.sh:  **2026-06-29 10:30 -04:00** — text
# Seconds are optional (dlog writes minute precision); the offset may carry a
# colon (``-04:00``) or not (``-0400``). The separator is an em dash or hyphen.
_LOG_ENTRY_RE = re.compile(
    r'^\*\*\s*'
    r'(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?\s*[+-]\d{2}:?\d{2})'
    r'\s*\*\*\s*[—-]+\s*'
    r'(?P<text>.*\S)\s*$'
)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description='Export operator sonar contacts (+ georeferenced log '
                    'entries) from a rosbag2 to GeoJSON.')
    p.add_argument('--bag', required=True, type=Path,
                   help='rosbag2 directory (the one with metadata.yaml)')
    p.add_argument('--output', required=True, type=Path,
                   help='Output .geojson path')
    p.add_argument('--log', type=Path, default=None,
                   help='Deployment markdown log to georeference and include')
    p.add_argument('--contacts-topic', default=_DEFAULT_CONTACTS)
    p.add_argument('--track-frame', default=_DEFAULT_TRACK_FRAME,
                   help='Boat frame georeferenced against --earth-frame for '
                        'log entries (default: %(default)s)')
    p.add_argument('--earth-frame', default=_DEFAULT_EARTH_FRAME,
                   help='ECEF root frame (default: %(default)s)')
    p.add_argument('--max-tf-age', type=float, default=5.0,
                   help='Max age (s) of a fallback "latest" TF for a log '
                        'entry before it is marked stale (default: %(default)s)')
    p.add_argument('--tf-cache', type=float, default=6.0 * 3600.0,
                   help='TF buffer cache length (s); must span the bag so log '
                        'entries anywhere in it resolve (default: %(default)s)')
    return p


def _parse_log_ts(ts: str) -> _dt.datetime | None:
    """Parse a dlog timestamp string to an aware datetime, or None."""
    ts = ts.strip().replace('T', ' ')
    for fmt in ('%Y-%m-%d %H:%M:%S %z', '%Y-%m-%d %H:%M %z'):
        try:
            return _dt.datetime.strptime(ts, fmt)
        except ValueError:
            continue
    return None


def _iso(epoch_s: float) -> str:
    return _dt.datetime.fromtimestamp(
        epoch_s, tz=_dt.timezone.utc).isoformat()


def _contact_feature(msg, bag_t_ns: int) -> dict:
    """Build a Point Feature from a marine_interfaces/msg/Contact."""
    gp = msg.geo_pose.position
    coords = [float(gp.longitude), float(gp.latitude)]
    # Only carry altitude when it is finite and meaningful.
    if gp.altitude == gp.altitude:  # not NaN
        coords.append(float(gp.altitude))

    stamp_ns = _stamp_ns(msg.header.stamp) or bag_t_ns
    classification = [
        getattr(c, 'description', None) or getattr(c, 'classification', '')
        for c in msg.classification
    ]
    props = {
        'feature_type': 'contact',
        'id': msg.id,
        'source': msg.source,
        'existence_probability': float(msg.existence_probability),
        'classification': [c for c in classification if c],
        'status': int(msg.status),
        'origin_kind': int(msg.origin_kind),
        'note': msg.note,
        'frame_id': msg.header.frame_id,
        'shape_type': int(msg.shape.type),
        'dim_x_m': float(msg.shape.dimensions.x),
        'dim_y_m': float(msg.shape.dimensions.y),
        'dim_z_m': float(msg.shape.dimensions.z),
        'time': _iso(stamp_ns / 1e9),
        't_epoch': stamp_ns / 1e9,
    }
    return {
        'type': 'Feature',
        'geometry': {'type': 'Point', 'coordinates': coords},
        'properties': props,
    }


def _read_log_entries(path: Path) -> list[tuple[int, str]]:
    """Parse (epoch_ns, text) tuples from a dlog-format markdown log."""
    entries = []
    for line in path.read_text().splitlines():
        m = _LOG_ENTRY_RE.match(line)
        if not m:
            continue
        dt = _parse_log_ts(m.group('ts'))
        if dt is None:
            continue
        entries.append((int(dt.timestamp() * 1e9), m.group('text')))
    return entries


def _log_feature(t_ns: int, text: str, buffer: Buffer, args,
                 max_tf_age_ns: int) -> dict:
    """Georeference one log entry against earth->track_frame at its time."""
    stamp = rclpy.time.Time(nanoseconds=t_ns).to_msg()
    pose, status = _lookup_pose(
        buffer, args.track_frame, args.earth_frame, stamp, max_tf_age_ns)
    geometry = None
    if pose is not None:
        lat, lon, _alt, _hdg, _pitch, _roll = ecef_pose_to_geo(pose[0], pose[1])
        geometry = {'type': 'Point', 'coordinates': [lon, lat]}
    return {
        'type': 'Feature',
        'geometry': geometry,
        'properties': {
            'feature_type': 'log_entry',
            'text': text,
            'geo_status': status,   # exact | approx | stale | missing
            'time': _iso(t_ns / 1e9),
            't_epoch': t_ns / 1e9,
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if not args.bag.exists():
        print(f'bag not found: {args.bag}', file=sys.stderr)
        return 2

    want_track = args.log is not None
    buffer = Buffer(cache_time=Duration(seconds=args.tf_cache))
    topics = [args.contacts_topic]
    if want_track:
        topics += ['/tf', '/tf_static']

    contacts: list[dict] = []
    for topic, msg, t_ns in iter_messages(args.bag, topics=topics):
        if topic == '/tf':
            for tr in msg.transforms:
                buffer.set_transform(tr, 'contacts_to_geojson')
        elif topic == '/tf_static':
            for tr in msg.transforms:
                buffer.set_transform_static(tr, 'contacts_to_geojson')
        elif topic == args.contacts_topic:
            contacts.append(_contact_feature(msg, t_ns))

    features = list(contacts)
    log_stats = {'exact': 0, 'approx': 0, 'stale': 0, 'missing': 0}
    if want_track:
        max_tf_age_ns = int(args.max_tf_age * _NS_PER_S)
        for t_ns, text in _read_log_entries(args.log):
            feat = _log_feature(t_ns, text, buffer, args, max_tf_age_ns)
            log_stats[feat['properties']['geo_status']] += 1
            features.append(feat)

    features.sort(key=lambda f: f['properties']['t_epoch'])
    collection = {'type': 'FeatureCollection', 'features': features}

    args.output.parent.mkdir(parents=True, exist_ok=True)
    partial = args.output.with_name(args.output.name + '.partial')
    partial.write_text(json.dumps(collection, indent=2))
    partial.replace(args.output)

    n_log = sum(log_stats.values())
    print(f'wrote {len(contacts)} contacts'
          + (f' + {n_log} log entries '
             f'({log_stats["exact"]} located, {log_stats["approx"]} approx, '
             f'{log_stats["stale"]} stale, {log_stats["missing"]} unlocated)'
             if want_track else '')
          + f' -> {args.output}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
