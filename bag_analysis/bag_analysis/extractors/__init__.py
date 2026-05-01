"""
Per-message-type extractors that flatten ROS messages to flat dicts
suitable for parquet rows.

Dispatch is by canonical "pkg/msg/Type" string. Unknown types fall
through to a JSON-string fallback so unsupported topics don't crash
the pipeline — they get a single `json` column with `repr(msg)` and
the topic still ends up in parquet for downstream introspection.

Adding a new extractor:
    1. Drop a module under `extractors/` with `extract(msg) -> dict`.
    2. Register it in EXTRACTORS by canonical type string.
"""

from __future__ import annotations

from typing import Any, Callable

from . import (
    battery,
    behavior_tree,
    diagnostics,
    heartbeat,
    imu,
    mavros_state,
    navsatfix,
    odometry,
    rcout,
    sbg,
    twist,
    udp_bridge,
)


Extractor = Callable[[Any], dict[str, Any]]


EXTRACTORS: dict[str, Extractor] = {
    'diagnostic_msgs/msg/DiagnosticArray': diagnostics.extract,
    'geometry_msgs/msg/TwistStamped': twist.extract,
    'marine_interfaces/msg/Heartbeat': heartbeat.extract,
    'mavros_msgs/msg/RCOut': rcout.extract,
    'mavros_msgs/msg/State': mavros_state.extract,
    'nav2_msgs/msg/BehaviorTreeLog': behavior_tree.extract,
    'nav_msgs/msg/Odometry': odometry.extract,
    'sbg_driver/msg/SbgEkfNav': sbg.extract_ekf_nav,
    'sbg_driver/msg/SbgGpsHdt': sbg.extract_gps_hdt,
    'sbg_driver/msg/SbgGpsPos': sbg.extract_gps_pos,
    'sbg_driver/msg/SbgGpsVel': sbg.extract_gps_vel,
    'sbg_driver/msg/SbgStatus': sbg.extract_status,
    'sensor_msgs/msg/BatteryState': battery.extract,
    'sensor_msgs/msg/Imu': imu.extract,
    'sensor_msgs/msg/NavSatFix': navsatfix.extract,
    'udp_bridge_interfaces/msg/BridgeInfo':
        udp_bridge.extract_bridge_info,
    'udp_bridge_interfaces/msg/TopicStatisticsArray':
        udp_bridge.extract_topic_statistics_array,
}


def _json_fallback(msg: Any) -> dict[str, Any]:
    """Last-resort representation for messages with no registered extractor."""
    return {'json': repr(msg)}


def extract(msg_type: str, msg: Any) -> dict[str, Any]:
    """Flatten a ROS message into a parquet-friendly dict."""
    fn = EXTRACTORS.get(msg_type)
    if fn is None:
        return _json_fallback(msg)
    return fn(msg)
