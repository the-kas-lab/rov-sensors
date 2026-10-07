# Set up a shell for this project:  source ~/ros2_ws/src/rov-sensors/env.sh
#
# ROS 2 node scripts run with the system /usr/bin/python3 (colcon's
# --symlink-install writes that into their shebang), which can't see packages
# installed in .venv. Putting the venv's site-packages on PYTHONPATH lets the
# nodes import brping / matplotlib / fastapi no matter how they were built.

_rov_omniscan_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source /opt/ros/humble/setup.bash
source "$_rov_omniscan_dir/.venv/bin/activate"
export PYTHONPATH="$_rov_omniscan_dir/.venv/lib/python3.10/site-packages${PYTHONPATH:+:$PYTHONPATH}"

_ws_setup="$_rov_omniscan_dir/../../install/setup.bash"
[ -f "$_ws_setup" ] && source "$_ws_setup"
unset _rov_omniscan_dir _ws_setup
