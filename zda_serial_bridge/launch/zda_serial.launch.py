"""
Launch the ZDA serial bridge.

Defaults: ``/dev/ttyS2`` @ 9600 baud, talker ID ``GP``. The node subscribes
to a relative topic ``utc_time`` — the caller is expected to remap or
namespace it to e.g. ``/bizzy/sensors/sbg/utc_time``.
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    device_arg = DeclareLaunchArgument('device', default_value='/dev/ttyS2')
    baud_arg = DeclareLaunchArgument('baud', default_value='9600')
    talker_arg = DeclareLaunchArgument('talker_id', default_value='GP')
    utc_topic_arg = DeclareLaunchArgument(
        'utc_time_topic', default_value='/bizzy/sensors/sbg/utc_time')

    return LaunchDescription([
        device_arg,
        baud_arg,
        talker_arg,
        utc_topic_arg,
        Node(
            package='zda_serial_bridge',
            executable='zda_serial_bridge',
            name='zda_serial_bridge',
            output='screen',
            parameters=[{
                'device': LaunchConfiguration('device'),
                'baud': LaunchConfiguration('baud'),
                'talker_id': LaunchConfiguration('talker_id'),
            }],
            remappings=[
                ('utc_time', LaunchConfiguration('utc_time_topic')),
            ],
        ),
    ])
