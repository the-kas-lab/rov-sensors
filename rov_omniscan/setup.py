import os
from glob import glob
from setuptools import setup

package_name = 'rov_omniscan'

setup(
    name=package_name,
    version='0.0.0',
    packages=[package_name, 'omniscan_mock'],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), glob(os.path.join('launch', '*launch.[pxy][yma]*'))),
        (os.path.join('share', package_name, 'config'), glob(os.path.join('config', '*'))),
    ],
    install_requires=['setuptools', 'numpy'],
    zip_safe=True,
    maintainer='Gesem Mejia',
    maintainer_email='gesemgudino@gmail.com',
    description='ROS 2 driver, waterfall display and mock for the Cerulean Omniscan 450 FS sonar',
    license='TODO: License declaration',
    entry_points={
        'console_scripts': [
            'omniscan450_node = rov_omniscan.omniscan450_node:main',
            'sonar_waterfall_node = rov_omniscan.sonar_waterfall_node:main',
            'omniscan_mock = omniscan_mock.__main__:main',
        ],
    },
)
