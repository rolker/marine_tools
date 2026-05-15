from glob import glob

from setuptools import find_packages, setup

package_name = 'zda_serial_bridge'

setup(
    name=package_name,
    version='0.0.1',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        # Python-launch only; broaden this glob (e.g. 'launch/*.launch.*')
        # if a future contributor adds YAML or XML launch files.
        ('share/' + package_name + '/launch', glob('launch/*.launch.py')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Roland Arsenault',
    maintainer_email='roland@ccom.unh.edu',
    description=('Emit NMEA $ZDA time/date sentences to a serial port '
                 'driven by an SBG SbgUtcTime topic.'),
    license='BSD-3-Clause',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'zda_serial_bridge = zda_serial_bridge.node:main',
        ],
    },
)
