"""
Sequential rosbag2 reader.

Yields (topic, deserialized_msg, t_ns) tuples for every message in a
rosbag2 directory. Message types are resolved dynamically via
rosidl_runtime_py.utilities.get_message from the bag's metadata, so
any package on the ROS path can be deserialized — there is no
compile-time list of supported types.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

from rclpy.serialization import deserialize_message
import rosbag2_py
from rosidl_runtime_py.utilities import get_message


def _detect_storage_id(bag_path: Path) -> str:
    """Pick the storage backend for the bag's file format."""
    if any(bag_path.glob('*.mcap')):
        return 'mcap'
    if any(bag_path.glob('*.db3')):
        return 'sqlite3'
    raise FileNotFoundError(
        f'No .mcap or .db3 files found in {bag_path}; cannot determine '
        f'rosbag2 storage backend.'
    )


def open_reader(
    bag_path: Path,
) -> tuple[rosbag2_py.SequentialReader, dict[str, str]]:
    """
    Open a rosbag2 directory for sequential reading.

    Returns the open reader plus a {topic: msg_type_name} map. The
    caller drives the read loop via reader.has_next / reader.read_next.
    """
    storage_id = _detect_storage_id(bag_path)
    storage_options = rosbag2_py.StorageOptions(
        uri=str(bag_path), storage_id=storage_id,
    )
    converter_options = rosbag2_py.ConverterOptions(
        input_serialization_format='cdr',
        output_serialization_format='cdr',
    )
    reader = rosbag2_py.SequentialReader()
    reader.open(storage_options, converter_options)

    topic_types = {
        t.name: t.type for t in reader.get_all_topics_and_types()
    }
    return reader, topic_types


def iter_messages(
    bag_path: Path,
    *,
    topics: list[str] | None = None,
    on_unresolved: str = 'warn',
) -> Iterator[tuple[str, Any, int]]:
    """
    Yield (topic, msg, t_ns) for every message in the bag.

    `topics`, if given, is a whitelist applied via the storage filter so
    only those topics are deserialized.

    Topics whose message type can't be resolved on the ROS path
    (e.g. a custom package not built into the current overlay) are
    skipped. With `on_unresolved='warn'` a single warning is printed
    per unresolved type the first time it's seen; with 'silent', nothing
    is printed. The pipeline never crashes on a missing type.
    """
    reader, topic_types = open_reader(bag_path)

    if topics is not None:
        reader.set_filter(rosbag2_py.StorageFilter(topics=topics))

    msg_classes: dict[str, Any] = {}
    unresolved: set[str] = set()
    while reader.has_next():
        topic, data, t_ns = reader.read_next()
        msg_type_name = topic_types[topic]

        if msg_type_name in unresolved:
            continue

        msg_class = msg_classes.get(msg_type_name)
        if msg_class is None:
            try:
                msg_class = get_message(msg_type_name)
            except (ModuleNotFoundError, ValueError) as exc:
                unresolved.add(msg_type_name)
                if on_unresolved == 'warn':
                    print(
                        f'  warn: skipping {msg_type_name} '
                        f'(unresolved: {exc})',
                        flush=True,
                    )
                continue
            msg_classes[msg_type_name] = msg_class

        msg = deserialize_message(data, msg_class)
        yield topic, msg, t_ns
