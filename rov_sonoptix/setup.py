import os
from glob import glob
from setuptools import setup

package_name = 'rov_sonoptix'

setup(
    name=package_name,
    version='0.0.0',
    packages=[package_name, 'sonoptix_mock'],
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
    description='ROS 2 driver, fan display and mock for the Sonoptix ECHO multibeam imaging sonar',
    license='TODO: License declaration',
    entry_points={
        'console_scripts': [
            'sonoptix_echo_node = rov_sonoptix.sonoptix_echo_node:main',
            'sonoptix_fan_node = rov_sonoptix.sonoptix_fan_node:main',
            'sonoptix_mock = sonoptix_mock.__main__:main',
        ],
    },
)
