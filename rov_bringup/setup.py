import os
from glob import glob
from setuptools import setup

package_name = 'rov_bringup'

setup(
    name=package_name,
    version='0.0.0',
    packages=[],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), glob(os.path.join('launch', '*launch.[pxy][yma]*'))),
        (os.path.join('share', package_name, 'config'), glob(os.path.join('config', '*'))),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Gesem Mejia',
    maintainer_email='gesemgudino@gmail.com',
    description='Launches all ROV sensors (sonar + camera) with one Foxglove bridge',
    license='TODO: License declaration',
)
