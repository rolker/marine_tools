"""
Example launch for sound_speed_bridge with the AML SVS sensor.

Defaults: AML parser, /dev/ttyS1, no UDP fan-out (ROS topic only). Boat-specific
launch files are expected to override `udp_hosts`, `udp_ports`, and
`udp_formats` to point UDP output at downstream consumers (M3, QINSy, etc.).
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    device_arg = DeclareLaunchArgument('device', default_value='/dev/ttyS1')
    baud_arg = DeclareLaunchArgument('baud', default_value='9600')
    frame_id_arg = DeclareLaunchArgument(
        'frame_id', default_value='sound_speed_sensor')

    return LaunchDescription([
        device_arg,
        baud_arg,
        frame_id_arg,
        Node(
            package='sound_speed_bridge',
            executable='sound_speed_bridge',
            name='sound_speed_bridge',
            output='screen',
            parameters=[{
                'device': LaunchConfiguration('device'),
                'baud': LaunchConfiguration('baud'),
                'parser': 'aml',
                'frame_id': LaunchConfiguration('frame_id'),
            }],
        ),
    ])
