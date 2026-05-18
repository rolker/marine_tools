"""
Launch the ZDA serial bridge.

Defaults: ``/dev/ttyS2`` @ 9600 baud, talker ID ``GP``. The node subscribes
to a relative topic ``utc_time`` — by default the launch leaves it relative
so the package is portable across vehicle namespaces. A BizzyBoat-side
launch (or any other caller) is expected to pass
``utc_time_topic:=/bizzy/sensors/sbg/utc_time`` (or the equivalent for its
namespace).
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description() -> LaunchDescription:
    device_arg = DeclareLaunchArgument('device', default_value='/dev/ttyS2')
    baud_arg = DeclareLaunchArgument('baud', default_value='9600')
    talker_arg = DeclareLaunchArgument('talker_id', default_value='GP')
    # Relative default so the launch file is portable across vehicle
    # namespaces. Absolute topics ignore any enclosing namespace, so
    # hard-coding ``/bizzy/...`` here would silently subscribe to the
    # wrong topic when this package is dropped on another vehicle.
    utc_topic_arg = DeclareLaunchArgument(
        'utc_time_topic', default_value='utc_time')
    # Safety knobs — surfacing as launch args lets operators tune them
    # via `ros2 launch ... arg:=value` without editing this file.
    # See node.py for the meaning of each (clock_utc_status semantics,
    # sync-requirement rationale, cold-start grace).
    min_utc_status_arg = DeclareLaunchArgument(
        'min_utc_status', default_value='2')
    require_utc_sync_arg = DeclareLaunchArgument(
        'require_utc_sync', default_value='false')
    startup_grace_arg = DeclareLaunchArgument(
        'startup_grace_sec', default_value='5.0')

    return LaunchDescription([
        device_arg,
        baud_arg,
        talker_arg,
        utc_topic_arg,
        min_utc_status_arg,
        require_utc_sync_arg,
        startup_grace_arg,
        Node(
            package='zda_serial_bridge',
            executable='zda_serial_bridge',
            name='zda_serial_bridge',
            output='screen',
            # Wrap non-string LaunchConfiguration values in
            # ParameterValue with an explicit value_type. Bare
            # substitutions reach rclpy as strings; on Jazzy they
            # currently coerce via YAML (so the launch works today),
            # but YAML coercion has surprising edges (e.g. ``on`` /
            # ``off`` become bool) and stricter rclpy versions will
            # raise InvalidParameterTypeException. ``device`` and
            # ``talker_id`` are genuinely strings and don't need a
            # wrapper.
            parameters=[{
                'device': LaunchConfiguration('device'),
                'baud': ParameterValue(
                    LaunchConfiguration('baud'), value_type=int),
                'talker_id': LaunchConfiguration('talker_id'),
                'min_utc_status': ParameterValue(
                    LaunchConfiguration('min_utc_status'), value_type=int),
                'require_utc_sync': ParameterValue(
                    LaunchConfiguration('require_utc_sync'),
                    value_type=bool),
                'startup_grace_sec': ParameterValue(
                    LaunchConfiguration('startup_grace_sec'),
                    value_type=float),
            }],
            remappings=[
                ('utc_time', LaunchConfiguration('utc_time_topic')),
            ],
        ),
    ])
