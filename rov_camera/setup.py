import os
from glob import glob
from setuptools import setup

package_name = 'rov_camera'

setup(
    name=package_name,
    version='0.0.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), glob(os.path.join('launch', '*launch.[pxy][yma]*'))),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Gesem Mejia',
    maintainer_email='gesemgudino@gmail.com',
    description='ROS 2 node that receives the BlueROV camera stream from BlueOS',
    license='TODO: License declaration',
    entry_points={
        'console_scripts': [
            'bluerov_camera_node = rov_camera.bluerov_camera_node:main',
        ],
    },
)
