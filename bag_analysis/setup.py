from setuptools import find_packages, setup

package_name = 'bag_analysis'

setup(
    name=package_name,
    version='0.0.1',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Roland Arsenault',
    maintainer_email='roland@ccom.unh.edu',
    description=('Two-stage post-deployment bag analysis: extract rosbag2 '
                 'to SQLite, then render a Tier-1 report.'),
    license='BSD-3-Clause',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'bag_to_sqlite = bag_analysis.cli.bag_to_sqlite:main',
            'sqlite_to_report = bag_analysis.cli.sqlite_to_report:main',
            'bag_to_xtf = bag_analysis.cli.bag_to_xtf:main',
        ],
    },
)
