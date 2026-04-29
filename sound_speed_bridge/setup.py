from glob import glob

from setuptools import find_packages, setup

package_name = 'sound_speed_bridge'

setup(
    name=package_name,
    version='0.0.1',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', glob('launch/*.launch.py')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Roland Arsenault',
    maintainer_email='roland@ccom.unh.edu',
    description=('Serial-to-ROS bridge for real-time sound-speed sensors '
                 'with optional UDP fan-out.'),
    license='BSD-3-Clause',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'sound_speed_bridge = sound_speed_bridge.node:main',
        ],
    },
)
