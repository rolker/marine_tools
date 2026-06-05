from glob import glob

from setuptools import find_packages, setup

package_name = 'kongsberg_em_bridge'

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
    description=('Decode a Kongsberg .all UDP stream (Raw Range and Angle 78) '
                 'from an M3 multibeam into marine_acoustic_msgs/SonarDetections.'),
    license='BSD-3-Clause',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'kongsberg_em_bridge = kongsberg_em_bridge.node:main',
            'replay = kongsberg_em_bridge.replay:main',
        ],
    },
)
