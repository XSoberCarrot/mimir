# Spoken replies: model on the server, voice on the robot

The robot says every answer of `robot_assistant.py` out loud. The LLM stays on the server; only the
reply text goes to the robot.

```
server: speak_assistant.py (= robot_assistant.py + speak) --ZMQ PUSH text--> robot :5562
robot : speech_bridge.py (:5562 -> /tts) -> tts_stream_node.py (ElevenLabs) -> Go2 speaker
```

| Folder | Runs on | Files |
|---|---|---|
| `nutri-atlas/robot_control/` | operator PC | `speak_assistant.py`: wraps `robot_assistant.py` unchanged, same arguments (`--no-voice` = printed only) |
| `robot/` | Go2 | `speech_bridge.py`, `tts_stream_node.py` (copy from `nutriatlas_voice`), `speech_nodes.sh`, `start_robot_side.sh` |

The robot side uses Chang's `speech_processor` build (read-only, it has the ElevenLabs key) and
does not change `~/Chang/nutriatlas_voice`. Port 5562 is used instead of 5560, so the start order
does not matter (`everything.launch.py` holds 5560 with a speech bridge that cannot speak).

Requirements: the robot needs internet (ElevenLabs); the server must reach the robot on TCP 5562.

## Deploy to the robot (once, and after every change in `robot/`)

```bash
# on the server
ssh unitree@<ROBOT_IP> 'mkdir -p ~/test_ws/atlas/robot_voice'
scp robot/* unitree@<ROBOT_IP>:~/test_ws/atlas/robot_voice/
```

Optional: copy the existing audio cache so sentences already generated are not paid for again
(robot: `cp ~/Chang/nutriatlas_voice/tts_cache/*.mp3 ~/test_ws/atlas/robot_voice/tts_cache/`).

## Run

Robot (2 terminals):

```bash
# Terminal 1: Go2 driver, SLAM, Nav2, camera (unchanged)
cd ~/test_ws/go2_nav_ws && source install/setup.bash
ros2 launch ./everything.launch.py

# Terminal 2: speech nodes + ZMQ bridge. Ctrl+C stops both.
cd ~/test_ws/atlas/robot_voice && ./start_robot_side.sh
#   bridge options are passed on: ./start_robot_side.sh --spin-threshold-deg 15
```

Server (2 terminals):

```bash
conda activate mimir
# Terminal 1: LLM server (unchanged)
cd nutri_rag && bash scripts/start_server.sh

# Terminal 2: assistant with voice (instead of robot_assistant.py)
cd nutri-atlas/robot_control
python speak_assistant.py --robot-ip <ROBOT_IP> --detection-mode real --detector vlm
python speak_assistant.py --robot-ip <ROBOT_IP> --detection-mode real --detector vlm --no-voice   # printed only
```

`nutri-atlas/robot_control/speak_assistant.py` is the one to run (next to `robot_assistant.py`).

Type the question as before. Each spoken text is printed as `[speak] ...`.

Options (environment variables on the server):

| Variable | Default | Meaning |
|---|---|---|
| `SPEAK_STREAM` | `first` | `first`: first sentence spoken while the LLM is still writing, rest at the end. `each`: every sentence when finished. `off`: whole answer at the end |
| `SPEAK_PORT` | `5562` | robot port (or `--speak-port`) |

On the robot, `PLAYBACK_PAD` (default 1.0 s) is the wait after each sentence; 0.2 s lost sentences.

## Test the voice alone

```bash
# robot
cd ~/test_ws/atlas/robot_voice && ./speech_nodes.sh start      # status | stop
# server
python -c "import zmq; s=zmq.Context().socket(zmq.PUSH); s.connect('tcp://<ROBOT_IP>:5562'); s.send_string('Hello, I am ready.'); s.close(linger=2000)"
```

Logs on the robot: `~/test_ws/atlas/robot_voice/logs/speech_bridge.log` (text received) and
`logs/tts_node.log` (generation and playback).

## If something goes wrong

| Symptom | Check |
|---|---|
| `[speak]` printed, robot silent, nothing in `speech_bridge.log` | Server cannot reach :5562: wrong `--robot-ip`, or speech nodes not running (`./speech_nodes.sh status`) |
| Text in `speech_bridge.log`, nothing in `tts_node.log` | ROS environment: both nodes must be started by `speech_nodes.sh` |
| `tts_node.log`: generation error | No internet on the robot (ElevenLabs) |
| `speech_nodes.sh start` refuses: another speech node | `~/Chang/nutriatlas_voice` nodes are running; stop them (`./services.sh stop speech` or `./tts_stream_nodes.sh stop` there). Two nodes = every sentence twice |
| Two voices / doubled sentences | Same as above |
