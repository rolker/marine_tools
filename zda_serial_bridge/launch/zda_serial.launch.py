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
