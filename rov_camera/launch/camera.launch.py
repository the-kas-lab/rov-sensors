"""
BlueROV camera (BlueOS stream) -> ROS 2 -> Foxglove.

    ros2 launch rov_camera camera.launch.py url:=rtsp://192.168.2.2:8554/<name>   # RTSP from BlueOS
    ros2 launch rov_camera camera.launch.py udp_port:=5600                        # BlueOS UDP stream
    ros2 launch rov_camera camera.launch.py use_mock:=true                           # no robot: laptop webcam
    ros2 launch rov_camera camera.launch.py use_mock:=true mock_device:=/dev/video2  # another webcam

UDP only arrives if BlueOS sends it to this computer's IP (its default target is
192.168.2.1); RTSP works from any IP. Copy the RTSP URL from BlueOS > Video Streams.
"""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    use_mock    = LaunchConfiguration('use_mock')
    mock_device = LaunchConfiguration('mock_device')
    url         = LaunchConfiguration('url')
    udp_port    = LaunchConfiguration('udp_port')
    max_width   = LaunchConfiguration('max_width')
    publish_raw = LaunchConfiguration('publish_raw')
    use_bridge  = LaunchConfiguration('use_bridge')
    bridge_port = LaunchConfiguration('bridge_port')

    return LaunchDescription([
        DeclareLaunchArgument('use_mock', default_value='false',
                              description='Stream a local webcam (needs ffmpeg) instead of the robot camera'),
        DeclareLaunchArgument('mock_device', default_value='/dev/video0',
                              description='Webcam (V4L2 device) used when use_mock:=true'),
        DeclareLaunchArgument('url', default_value='',
                              description='RTSP URL from BlueOS > Video Streams; empty = use udp_port'),
        DeclareLaunchArgument('udp_port', default_value='5600',
                              description='Local port receiving the BlueOS RTP/H.264 stream (when url is empty)'),
        DeclareLaunchArgument('max_width', default_value='0',
                              description='Downscale to this width before publishing; 0 = full resolution'),
        DeclareLaunchArgument('publish_raw', default_value='false',
                              description='Also publish uncompressed camera/image_raw'),
        DeclareLaunchArgument('use_bridge', default_value='true',
                              description='Launch foxglove_bridge (websocket server for Foxglove)'),
        DeclareLaunchArgument('bridge_port', default_value='8765',
                              description='foxglove_bridge websocket port'),

        # Mock camera: encode the webcam exactly the way BlueOS sends the robot camera
        # (H.264 over RTP/UDP, payload 96), so the node runs its real receive/decode path.
        ExecuteProcess(
            cmd=['ffmpeg', '-hide_banner', '-loglevel', 'error',
                 '-f', 'v4l2', '-input_format', 'mjpeg', '-video_size', '1280x720', '-framerate', '30',
                 '-i', mock_device,
                 '-c:v', 'libx264', '-preset', 'ultrafast', '-tune', 'zerolatency',
                 '-pix_fmt', 'yuv420p', '-g', '30', '-payload_type', '96',
                 '-f', 'rtp', ['rtp://127.0.0.1:', udp_port]],
            output='screen',
            condition=IfCondition(use_mock),
        ),

        Node(
            package='rov_camera',
            executable='bluerov_camera_node',
            name='bluerov_camera',
            output='screen',
            parameters=[{
                # The mock always streams over UDP, so ignore any url then.
                'url': ParameterValue(
                    PythonExpression(["'' if '", use_mock, "' == 'true' else '", url, "'"]), value_type=str),
                'udp_port': ParameterValue(udp_port, value_type=int),
                'max_width': ParameterValue(max_width, value_type=int),
                'publish_raw': ParameterValue(publish_raw, value_type=bool),
            }],
        ),

        Node(
            package='foxglove_bridge',
            executable='foxglove_bridge',
            name='foxglove_bridge',
            output='screen',
            parameters=[{'port': ParameterValue(bridge_port, value_type=int)}],
            condition=IfCondition(use_bridge),
        ),
    ])
