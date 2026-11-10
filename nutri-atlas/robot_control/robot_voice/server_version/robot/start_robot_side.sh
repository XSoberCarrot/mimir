#!/bin/bash
# Robot Terminal 2: speech nodes (background) + ZMQ bridge (foreground). Ctrl+C stops everything.
# Run after everything.launch.py (Terminal 1). Other arguments go to the bridge, e.g. --spin-threshold-deg 15
# Usage: ./start_robot_side.sh            # speech nodes + bridge
#        ./start_robot_side.sh --mic      # also the microphone (mic.sh, asks for the sudo password first);
#                                         # its lines are shown with a [mic] prefix
HERE=$(cd "$(dirname "$0")" && pwd)
NAV_WS=${NAV_WS:-/home/unitree/test_ws/go2_nav_ws}
BRIDGE_DIR=${BRIDGE_DIR:-/home/unitree/test_ws/atlas/zmq_communication}

MIC=0
if [ "$1" = --mic ]; then MIC=1; shift; fi
if [ $MIC = 1 ]; then
    sudo -v || exit 1          # ask for the password now: mic.sh runs in the background and cannot ask
fi

"$HERE/speech_nodes.sh" start || exit 1
trap '"$HERE/speech_nodes.sh" stop' EXIT

if [ $MIC = 1 ]; then
    # mic.sh runs as root (sudo), so this script cannot kill it: --exit-with makes it stop when this script ends
    "$HERE/mic.sh" --exit-with $$ < /dev/null > >(sed -u 's/^/[mic] /') 2>&1 &
fi

source "$NAV_WS/install/setup.bash"
cd "$BRIDGE_DIR"
python3 zmq_bridge_node_working_v2.py "$@"
