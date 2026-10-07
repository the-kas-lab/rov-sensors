"""
All ROV sensors -> ROS 2 -> one Foxglove bridge.

Includes the sonar launch (rov_omniscan) and the camera launch (rov_camera) with
their own bridges turned off, then starts a single foxglove_bridge. Import
config/rov_foxglove_layout.json once in Foxglove for the combined view.

Run after `source ~/ros2_ws/src/rov-sensors/env.sh`:

    ros2 launch rov_bringup rov.launch.py use_omniscan_mock:=true use_camera_mock:=true use_foxglove_app:=true  # no robot
    ros2 launch rov_bringup rov.launch.py use_camera_mock:=true                   # real sonar, laptop webcam as camera
    ros2 launch rov_bringup rov.launch.py camera_url:=rtsp://192.168.2.2:8554/<name> use_foxglove_app:=true
    ros2 launch rov_bringup rov.launch.py use_camera:=false                       # sonar only
"""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    use_omniscan_mock = LaunchConfiguration('use_omniscan_mock')
    use_camera_mock  = LaunchConfiguration('use_camera_mock')
    camera_mock_device = LaunchConfiguration('camera_mock_device')
    use_sonar        = LaunchConfiguration('use_sonar')
    use_camera       = LaunchConfiguration('use_camera')
    sonar_host       = LaunchConfiguration('sonar_host')
    camera_url       = LaunchConfiguration('camera_url')
    camera_udp_port  = LaunchConfiguration('camera_udp_port')
    use_foxglove_app = LaunchConfiguration('use_foxglove_app')
    bridge_port      = LaunchConfiguration('bridge_port')

    return LaunchDescription([
        DeclareLaunchArgument('use_omniscan_mock', default_value='false',
                              description='Run the mock Omniscan sonar on this machine instead of the real one'),
        DeclareLaunchArgument('use_camera_mock', default_value='false',
                              description='Stream a local webcam (camera_mock_device) instead of the robot camera'),
        DeclareLaunchArgument('camera_mock_device', default_value='/dev/video0',
                              description='Webcam used as the camera mock (when use_camera_mock:=true)'),
        DeclareLaunchArgument('use_sonar', default_value='true', description='Start the sonar'),
        DeclareLaunchArgument('use_camera', default_value='true', description='Start the camera'),
        DeclareLaunchArgument('sonar_host', default_value='192.168.2.92', description='Sonar IP'),
        DeclareLaunchArgument('camera_url', default_value='',
                              description='RTSP URL from BlueOS > Video Streams; empty = UDP on camera_udp_port'),
        DeclareLaunchArgument('camera_udp_port', default_value='5600',
                              description='Port receiving the BlueOS RTP/H.264 stream (when camera_url is empty)'),
        DeclareLaunchArgument('use_foxglove_app', default_value='false',
                              description='Best-effort auto-launch the Foxglove desktop app, pre-connected'),
        DeclareLaunchArgument('bridge_port', default_value='8765', description='foxglove_bridge websocket port'),

        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                PathJoinSubstitution([FindPackageShare('rov_omniscan'), 'launch', 'omniscan450.launch.py'])),
            launch_arguments={'use_mock': use_omniscan_mock, 'host': sonar_host,
                              'use_bridge': 'false', 'use_foxglove_app': 'false'}.items(),
            condition=IfCondition(use_sonar),
        ),

        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                PathJoinSubstitution([FindPackageShare('rov_camera'), 'launch', 'camera.launch.py'])),
            launch_arguments={'use_mock': use_camera_mock, 'mock_device': camera_mock_device,
                              'url': camera_url, 'udp_port': camera_udp_port,
                              'use_bridge': 'false'}.items(),
            condition=IfCondition(use_camera),
        ),

        Node(
            package='foxglove_bridge',
            executable='foxglove_bridge',
            name='foxglove_bridge',
            output='screen',
            parameters=[{'port': ParameterValue(bridge_port, value_type=int)}],
        ),

        ExecuteProcess(
            # env -u: terminals inside VS Code export ELECTRON_RUN_AS_NODE=1, which makes the
            # Foxglove (Electron) binary start as plain Node and reject its own flags.
            cmd=['env', '-u', 'ELECTRON_RUN_AS_NODE', 'foxglove-studio', '--no-sandbox', [
                'foxglove://open?ds=foxglove-websocket&ds.url=ws://localhost:', bridge_port, '/'
            ]],
            output='screen',
            condition=IfCondition(use_foxglove_app),
        ),
    ])
