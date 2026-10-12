"""
Sonoptix ECHO multibeam sonar -> ROS 2 -> Foxglove.

Starts the sonar driver, the fan image node and foxglove_bridge; optionally the
mock sonar (no hardware needed) and the Foxglove desktop app. Import
config/sonoptix_echo_foxglove_layout.json once in Foxglove; it remembers it.

Run after `source ~/ros2_ws/src/rov-sensors/env.sh`:

    ros2 launch rov_sonoptix sonoptix_echo.launch.py use_mock:=true       # no sonar needed
    ros2 launch rov_sonoptix sonoptix_echo.launch.py                      # real sonar on the ROV
    ros2 launch rov_sonoptix sonoptix_echo.launch.py range_m:=15 use_foxglove_app:=true
"""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node

MOCK_API_PORT = '8001'  # the mock's default: the Omniscan mock already uses 8000


def generate_launch_description():
    use_mock         = LaunchConfiguration('use_mock')
    host             = LaunchConfiguration('host')
    range_m          = LaunchConfiguration('range_m')
    operation_mode   = LaunchConfiguration('operation_mode')
    use_bridge       = LaunchConfiguration('use_bridge')
    use_foxglove_app = LaunchConfiguration('use_foxglove_app')
    bridge_port      = LaunchConfiguration('bridge_port')

    return LaunchDescription([
        DeclareLaunchArgument('use_mock', default_value='false',
                              description='Start the mock sonar on this machine and connect to it'),
        DeclareLaunchArgument('host', default_value='192.168.2.42',
                              description='Sonar IP (ignored when use_mock:=true)'),
        DeclareLaunchArgument('range_m', default_value='6.0',
                              description='Range (m): 3, 6, 9, 12, 15, 20, 25, 30, 40, 50, 60, 70, 80, '
                                          '90, 100, 125, 150, 175 or 200'),
        DeclareLaunchArgument('operation_mode', default_value='1',
                              description='0 = AUTO, 1 = HF (700 kHz), 2 = LF (400 kHz), 3 = LFLR'),
        DeclareLaunchArgument('use_bridge', default_value='true',
                              description='Launch foxglove_bridge (websocket server for Foxglove)'),
        DeclareLaunchArgument('use_foxglove_app', default_value='false',
                              description='Best-effort auto-launch the Foxglove desktop app, pre-connected'),
        DeclareLaunchArgument('bridge_port', default_value='8765',
                              description='foxglove_bridge websocket port'),

        # Mock sonar: REST API on MOCK_API_PORT (its default) plus RTSP on 8554.
        # The driver retries its connection, so start order doesn't matter.
        # Started directly (not via `ros2 run`, which can leave it running after Ctrl-C).
        Node(
            package='rov_sonoptix',
            executable='sonoptix_mock',
            name='sonoptix_mock',
            arguments=['--host', '127.0.0.1'],
            output='screen',
            condition=IfCondition(use_mock),
        ),

        Node(
            package='rov_sonoptix',
            executable='sonoptix_echo_node',
            name='sonoptix_echo',
            output='screen',
            parameters=[{
                'host': PythonExpression(["'127.0.0.1' if '", use_mock, "' == 'true' else '", host, "'"]),
                'api_port': PythonExpression([MOCK_API_PORT, " if '", use_mock, "' == 'true' else 8000"]),
                'range_m': PythonExpression(['float(', range_m, ')']),  # '15' must still be a double
                'operation_mode': PythonExpression(['int(', operation_mode, ')']),
            }],
        ),

        Node(
            package='rov_sonoptix',
            executable='sonoptix_fan_node',
            name='sonoptix_fan',
            output='screen',
        ),

        Node(
            package='foxglove_bridge',
            executable='foxglove_bridge',
            name='foxglove_bridge',
            output='screen',
            parameters=[{'port': bridge_port}],
            condition=IfCondition(use_bridge),
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
