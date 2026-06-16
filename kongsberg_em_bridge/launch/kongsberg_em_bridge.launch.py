# Copyright 2026 Center for Coastal and Ocean Mapping & NOAA-UNH Joint
# Hydrographic Center, University of New Hampshire
#
# SPDX-License-Identifier: BSD-3-Clause

"""Launch the kongsberg_em_bridge node feeding cube_bathymetry detections."""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('bind_port', default_value='20002'),
        DeclareLaunchArgument('frame_id', default_value='m3'),
        DeclareLaunchArgument('namespace', default_value=''),
        DeclareLaunchArgument(
            'save_all_dir', default_value='',
            description='Directory to record received datagrams as a timestamped '
                        'Kongsberg .all file; empty disables recording.'),
        DeclareLaunchArgument(
            'save_all_max_seconds', default_value='0.0',
            description='Roll the .all recording to a fresh file after this many '
                        'wall-clock seconds; 0 disables the time trigger.'),
        DeclareLaunchArgument(
            'save_all_max_bytes', default_value='0',
            description='Roll the .all recording to a fresh file after this many '
                        'bytes; 0 disables the size trigger.'),
        Node(
            package='kongsberg_em_bridge',
            executable='kongsberg_em_bridge',
            name='kongsberg_em_bridge',
            namespace=LaunchConfiguration('namespace'),
            parameters=[{
                'bind_port': LaunchConfiguration('bind_port'),
                'frame_id': LaunchConfiguration('frame_id'),
                'save_all_dir': LaunchConfiguration('save_all_dir'),
                'save_all_max_seconds': LaunchConfiguration('save_all_max_seconds'),
                'save_all_max_bytes': LaunchConfiguration('save_all_max_bytes'),
            }],
            output='screen',
        ),
    ])
