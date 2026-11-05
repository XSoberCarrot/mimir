#!/bin/bash
# Robot Terminal 2: speech nodes (background) + ZMQ bridge (foreground). Ctrl+C stops both.
# Run after everything.launch.py (Terminal 1). Arguments go to the bridge, e.g. --spin-threshold-deg 15
HERE=$(cd "$(dirname "$0")" && pwd)
NAV_WS=${NAV_WS:-/home/unitree/test_ws/go2_nav_ws}
BRIDGE_DIR=${BRIDGE_DIR:-/home/unitree/test_ws/atlas/zmq_communication}

"$HERE/speech_nodes.sh" start || exit 1
trap '"$HERE/speech_nodes.sh" stop' EXIT

source "$NAV_WS/install/setup.bash"
cd "$BRIDGE_DIR"
python3 zmq_bridge_node_working_v2.py "$@"
