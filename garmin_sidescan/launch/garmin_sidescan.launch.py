"""
Launch the Garmin GCV sidescan driver, namespaced for BizzyBoat.

Topics land under ``/<namespace>/sensors/sidescan/...`` to match the
``/<ns>/sensors/<sensor>/...`` convention used by the other sensor bridges.
The sound-speed watchdog defaults to the BizzyBoat AML SVS topic.
"""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, GroupAction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node, PushRosNamespace


def generate_launch_description():
    frame_prefix = LaunchConfiguration('frame_prefix')
    gcv_ip = LaunchConfiguration('gcv_ip')
    iface_ip = LaunchConfiguration('iface_ip')

    return LaunchDescription([
        DeclareLaunchArgument('frame_prefix', default_value='bizzy/'),
        DeclareLaunchArgument('gcv_ip', default_value='172.16.3.0'),
        DeclareLaunchArgument(
            'iface_ip', default_value='',
            description='Local NIC IP to join the imagery multicast on'),

        GroupAction(actions=[
            PushRosNamespace('sensors/sidescan'),
            Node(
                package='garmin_sidescan',
                executable='garmin_sidescan',
                name='garmin_sidescan',
                parameters=[{
                    'gcv_ip': gcv_ip,
                    'iface_ip': iface_ip,
                    'frame_id': [frame_prefix, 'gcv_sonar'],
                    # SAFE default: do not transmit on startup.
                    'transmit_on_startup': False,
                    'sound_speed_safety_enabled': True,
                    'sound_speed_topic': '/bizzy/sensors/sound_speed/sound_speed',
                }],
                respawn=True,
                respawn_delay=2.0,
                emulate_tty=True,
            ),
        ]),
    ])
