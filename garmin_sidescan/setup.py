from glob import glob

from setuptools import find_packages, setup

package_name = 'garmin_sidescan'

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
    description=('ROS 2 driver for the Garmin GCV-10/20 sidescan sonar: '
                 'imagery decode, transmit control, sound-speed safety.'),
    license='BSD-3-Clause',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'garmin_sidescan = garmin_sidescan.node:main',
        ],
    },
)
