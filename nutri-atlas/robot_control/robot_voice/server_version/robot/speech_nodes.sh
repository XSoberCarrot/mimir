#!/bin/bash
# Robot speech nodes for the server-side agent:
#   speech_bridge.py    ZMQ PULL :5562 -> ROS /tts
#   tts_stream_node.py  /tts -> ElevenLabs (or tts_cache/) -> Go2 speaker
# tts_stream_node.py imports Chang's speech_processor build (read-only; it holds the ElevenLabs key).
# Logs and tts_cache/ stay in this folder. Can start before or after everything.launch.py.
# Usage: ./speech_nodes.sh start | stop | status
HERE=$(cd "$(dirname "$0")" && pwd)
WS=/home/unitree/Chang/demo_proj/project_legged_robot/ros2_code/install
LOG=$HERE/logs
NODES="speech_bridge tts_node"
export SPEECH_PORT=${SPEECH_PORT:-5562}

running() { [ -f "$LOG/$1.pid" ] && kill -0 "$(cat "$LOG/$1.pid")" 2> /dev/null; }

case "$1" in
start)
    if running speech_bridge || running tts_node; then
        echo "already running ($0 status). Stop first: $0 stop"; exit 1
    fi
    # another ElevenLabs node would play every sentence a second time
    # (the keyless tts_node from everything.launch.py is harmless and not listed here)
    if pgrep -af "tts_stream_node.py|$WS/speech_processor/lib/speech_processor/tts_node" | grep -v pgrep; then
        echo "another speech node (above) is running, e.g. ~/Chang/nutriatlas_voice. Stop it first."; exit 1
    fi
    if ss -ltn "sport = :$SPEECH_PORT" | grep -q LISTEN; then
        echo "port $SPEECH_PORT is in use"; exit 1
    fi
    mkdir -p "$LOG" "$HERE/tts_cache"
    # clean ROS environment: the go2_nav_ws speech_processor (no API key) must not shadow Chang's
    export PATH=$(echo "$PATH" | tr ':' '\n' | grep -v miniconda3 | paste -sd:)
    unset PYTHONPATH AMENT_PREFIX_PATH COLCON_PREFIX_PATH CONDA_PREFIX CONDA_DEFAULT_ENV
    source ~/unitree_ros2/setup.sh > /dev/null      # foxy + CycloneDDS on eth0 (reaches the Go2 audio hub)
    source "$WS/setup.bash"
    export ROS_LOG_DIR=$LOG/ros PYTHONUNBUFFERED=1 PLAYBACK_PAD=${PLAYBACK_PAD:-1.0}
    cd "$HERE"                                      # tts_node creates tts_cache/ in the working directory
    nohup /usr/bin/python3 "$HERE/speech_bridge.py" --port "$SPEECH_PORT" > "$LOG/speech_bridge.log" 2>&1 < /dev/null &
    echo $! > "$LOG/speech_bridge.pid"
    nohup /usr/bin/python3 "$HERE/tts_stream_node.py" > "$LOG/tts_node.log" 2>&1 < /dev/null &
    echo $! > "$LOG/tts_node.pid"
    disown -a
    sleep 3
    for n in $NODES; do
        running "$n" || { echo "$n failed to start, last lines of $LOG/$n.log:"; tail -n 20 "$LOG/$n.log"; exit 1; }
    done
    echo "speech nodes started (ZMQ :$SPEECH_PORT, pad ${PLAYBACK_PAD}s). logs: $LOG"
    ;;
stop)
    for n in $NODES; do
        running "$n" && kill "$(cat "$LOG/$n.pid")" && echo "stopped $n"
        rm -f "$LOG/$n.pid"
    done
    ;;
status)
    for n in $NODES; do
        if running "$n"; then echo "$n: running (pid $(cat "$LOG/$n.pid"))"; else echo "$n: stopped"; fi
    done
    ;;
*)  echo "usage: $0 start|stop|status"; exit 1 ;;
esac
