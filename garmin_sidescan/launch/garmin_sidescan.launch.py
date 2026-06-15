"""
Example launch for the Garmin GCV sidescan driver.

Neutral defaults only: no ROS namespace, ``frame_id`` ``garmin_sidescan``, and the
node's own safe defaults (transmit OFF at startup). A platform launch is expected
to wrap this to set the namespace, frame prefix, the GCV's address, and the local
multicast interface.
"""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    gcv_ip_arg = DeclareLaunchArgument(
        'gcv_ip', default_value='172.16.3.0',
        description='GCV unit IP (GCV-10 ships as 172.16.3.196)')
    iface_ip_arg = DeclareLaunchArgument(
        'iface_ip', default_value='',
        description='Local NIC IP to join the imagery multicast on '
                    '(set on multi-homed hosts)')
    frame_id_arg = DeclareLaunchArgument(
        'frame_id', default_value='garmin_sidescan',
        description='Base frame; channels publish <frame_id>_port / '
                    '_starboard / _down and are oriented via TF')

    return LaunchDescription([
        gcv_ip_arg,
        iface_ip_arg,
        frame_id_arg,
        Node(
            package='garmin_sidescan',
            executable='garmin_sidescan',
            name='garmin_sidescan',
            output='screen',
            parameters=[{
                'gcv_ip': LaunchConfiguration('gcv_ip'),
                'iface_ip': LaunchConfiguration('iface_ip'),
                'frame_id': LaunchConfiguration('frame_id'),
            }],
        ),
    ])
