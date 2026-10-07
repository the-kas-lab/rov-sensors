"""
Omniscan 450 FS sonar -> ROS 2 -> Foxglove.

Starts the sonar driver, the waterfall image node and foxglove_bridge; optionally
the mock sonar (no hardware needed) and the Foxglove desktop app. Import
config/omniscan450_foxglove_layout.json once in Foxglove; it remembers it.

Run after `source ~/ros2_ws/src/rov-sensors/env.sh` (puts the .venv with brping /
fastapi / matplotlib on PYTHONPATH):

    ros2 launch rov_omniscan omniscan450.launch.py use_mock:=true      # no sonar needed
    ros2 launch rov_omniscan omniscan450.launch.py                     # real sonar on the ROV
    ros2 launch rov_omniscan omniscan450.launch.py use_mock:=true use_foxglove_app:=true
"""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node


def generate_launch_description():
    use_mock         = LaunchConfiguration('use_mock')
    host             = LaunchConfiguration('host')
    range_m          = LaunchConfiguration('range_m')
    num_results      = LaunchConfiguration('num_results')
    gain_index       = LaunchConfiguration('gain_index')
    use_bridge       = LaunchConfiguration('use_bridge')
    use_foxglove_app = LaunchConfiguration('use_foxglove_app')
    bridge_port      = LaunchConfiguration('bridge_port')

    return LaunchDescription([
        DeclareLaunchArgument('use_mock', default_value='false',
                              description='Start the mock sonar on this machine and connect to it'),
        DeclareLaunchArgument('host', default_value='192.168.2.92',
                              description='Sonar IP (ignored when use_mock:=true)'),
        DeclareLaunchArgument('range_m', default_value='10.0', description='Scan length (m)'),
        DeclareLaunchArgument('num_results', default_value='600', description='Samples per ping'),
        DeclareLaunchArgument('gain_index', default_value='-1', description='0-7, -1 = auto'),
        DeclareLaunchArgument('use_bridge', default_value='true',
                              description='Launch foxglove_bridge (websocket server for Foxglove)'),
        DeclareLaunchArgument('use_foxglove_app', default_value='false',
                              description='Best-effort auto-launch the Foxglove desktop app, pre-connected'),
        DeclareLaunchArgument('bridge_port', default_value='8765',
                              description='foxglove_bridge websocket port'),

        # Mock sonar: Ping protocol on 51200 plus its HTTP API on 8000.
        # The driver retries its connection, so start order doesn't matter.
        # Started directly (not via `ros2 run`, which can leave it running after Ctrl-C).
        Node(
            package='rov_omniscan',
            executable='omniscan_mock',
            name='omniscan_mock',
            arguments=['--host', '127.0.0.1'],
            output='screen',
            condition=IfCondition(use_mock),
        ),

        Node(
            package='rov_omniscan',
            executable='omniscan450_node',
            name='omniscan450',
            output='screen',
            parameters=[{
                'host': PythonExpression(["'127.0.0.1' if '", use_mock, "' == 'true' else '", host, "'"]),
                'range_m': PythonExpression(['float(', range_m, ')']),  # '20' must still be a double
                'num_results': num_results,
                'gain_index': gain_index,
            }],
        ),

        Node(
            package='rov_omniscan',
            executable='sonar_waterfall_node',
            name='sonar_waterfall',
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
