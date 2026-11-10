#!/bin/bash
# Robot microphone input for speak_assistant.py --mic: PageUp push-to-talk -> Google STT -> port 5561
# (the server connects here with its --robot-ip). Asks for the sudo password (the PageUp key hook needs
# root). Arguments go to mic_input.py.
# Usage: ./mic.sh
#        ./mic.sh --seconds 4 --print-only          # test: 4 s recording, print only, no PageUp
HERE=$(cd "$(dirname "$0")" && pwd)
PY=/home/unitree/miniconda3/envs/go2-agent-demo/bin/python   # has google-cloud-speech, pynput, sounddevice
exec sudo PYNPUT_BACKEND_KEYBOARD=uinput PYNPUT_BACKEND_MOUSE=dummy PYTHONWARNINGS=ignore::FutureWarning \
    "$PY" "$HERE/mic_input.py" "$@"
