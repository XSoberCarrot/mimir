# Mimir — Setup & Run Guide

How to set up Mimir from a fresh clone and run it: the nutrition assistant, the benchmarks, and the Go2 robot demo.
(The previous README, with architecture and results, is in `README_previous.md`.)

```
nutri_graph  →  nutri_rag  →  nutri-atlas  →  robot (Go2)
(KB + GAT)      (RAG + LLM)    (agent + tools)   (ZMQ bridge → ROS 2 / Nav2)
```

| Machine | Runs |
|---|---|
| **Operator PC** (Linux, NVIDIA GPU) | LLM server, knowledge base, `robot_assistant.py` |
| **Robot** (Unitree Go2 onboard PC, ROS 2 **Foxy**) | Nav2 / SLAM / camera stack, ZMQ bridge |

### Inference only, or evaluation too?

Steps are tagged:

- **[inference]** needed to run the nutrition assistant and the robot demo
- **[eval]** only needed for benchmarks or reproducing paper results; skip these for inference only
- **[optional]** extra features, off by default

| If you only want… | Do these sections |
|---|---|
| Nutrition assistant | 1.1, 1.2 (USDA + SR Legacy), 1.3 (steps 1, 2, 5), 1.4, then §2.1–2.2 |
| Robot demo | the above + 1.5, §3, §4 |
| Benchmarks | everything, including all **[eval]** steps |

---

## 1. Operator PC setup

### 1.1 Clone and create the environment

```bash
git clone git@github.com:XSoberCarrot/mimir.git
cd mimir
git checkout dev

conda env create -f environment.yml      # creates env "mimir" (Python 3.10)
conda activate mimir

# Not in environment.yml:
pip install onnxruntime-gpu              # [inference] robot demo detector (or: pip install onnxruntime)
pip install "lm-eval[api]"               # [eval] NutriBench benchmarks only
```

- `environment.yml` uses **CUDA 12.4** (`pytorch-cuda=12.4` and the `torch-2.5.0+cu124` PyG wheels). If your CUDA is different, change both before creating the env. Mismatched PyG wheels install fine but crash at runtime with `undefined symbol`. Check with:
  ```bash
  python -c "import torch_scatter, torch_sparse; print('PyG OK')"
  ```
- Python **3.10+** is required (the code uses `X | None` type syntax).
- Ignore `nutri_graph/requirements.txt`; it has an outdated CPU wheel URL. `environment.yml` covers it.

### 1.2 Download external data (not in git)

All of these go **inside the repo folder** at the paths shown. They are gitignored.

| Tag | Data | How to get it | Put it at | Used by |
|---|---|---|---|---|
| **[inference]** | USDA FoodData Central (Foundation Foods) | `cd nutri_graph && python scripts/download_data.py` (uses `kagglehub`; set up a Kaggle token at `~/.kaggle/kaggle.json` if it asks) | `nutri_graph/data/raw/` (done by the script) | the knowledge base (required) |
| **[inference]** | USDA SR Legacy (ASCII) | Manual download from USDA FoodData Central → *SR Legacy*, ASCII format; unzip | `nutri_graph/data/SR-Leg_ASC/` (must contain `FOOD_DES.txt`, `NUT_DATA.txt`, `NUTR_DEF.txt`) | extra foods in the KB. `build_kb.py` skips it if missing, but the KB is much smaller. |
| **[eval]** / **[optional]** | PFoodReq: recipe KG | `git clone https://github.com/hugochan/PFoodReq`, then download its data from the OneDrive link in `PFoodReq/README.md` | `PFoodReq/data/recipe_kg/recipe_kg.json` | recipe layer: the PFoodReq benchmark, recipe nodes in the GAT, and the off-by-default `MEAL_COMPOSE_MODE=on` |
| **[eval]** | PFoodReq: QA data | same download as above | `PFoodReq/data/kbqa_data/` | PFoodReq benchmark only |
| **[eval]** | HealthyFoodSubs | `git clone https://github.com/jloe2911/HealthyFoodSubs` | `HealthyFoodSubs/Input Data/final_substitution.csv` | substitution supervision when training the GAT (reproducing paper results) |

The text-embedding model (`Qwen/Qwen3-Embedding-0.6B`) and the NutriBench dataset are downloaded automatically from Hugging Face on first use.

### 1.3 Build the knowledge base and embeddings (run in this order)

```bash
conda activate mimir

# 1. [inference] Knowledge base (USDA + SR Legacy)          → nutri_graph/data/nutri_kb.duckdb
cd nutri_graph && python scripts/build_kb.py && cd ..

# 2. [inference] Food text embeddings (GPU recommended)     → nutri_rag/data/embeddings/food_*.npy
cd nutri_rag && python scripts/build_embeddings.py && cd ..

# 3. [eval] Add PFoodReq recipes to the KB (needs step 2)   → recipe tables in nutri_kb.duckdb
cd nutri_graph && python scripts/build_recipe_kb.py && cd ..

# 4. [eval] Substitution train/test split (needs HealthyFoodSubs) → nutri_graph/data/subs_*.csv
cd nutri_graph && python scripts/split_subs.py && cd ..

# 5. [inference] Train the GAT (GPU if available)           → nutri_graph/outputs/embeddings/, nutri_graph/models/
cd nutri_graph && python scripts/train_GAT.py && cd ..

# 6. [eval] Recipe text embeddings (needs step 3)           → nutri_rag/data/embeddings/recipe_*.npy
cd nutri_rag && python scripts/build_recipe_embeddings.py && cd ..
```

- Run the `nutri_graph` scripts from inside `nutri_graph/`, because they use relative paths.
- **Inference only:** run steps **1 → 2 → 5**. `train_GAT.py` still works without steps 3–4; it just trains without recipe nodes and substitution supervision. Those embeddings are fine for the assistant, but they won't exactly reproduce the paper's GAT.
- **Reproducing results:** run all six steps in order (3 and 4 must come before 5).
- **Optional recipe suggestions:** steps 3 and 6 are also needed if you turn on recipe composition in the assistant (`MEAL_COMPOSE_MODE=on`; off by default, and the robot demo doesn't use it).

### 1.4 LLM server (llama.cpp + Qwen3.5-9B) — [inference]

**Build llama.cpp.** `start_server.sh` expects the binary at `~/softwares/llama.cpp/llama-server`. Use CUDA **12.8+**; 12.1 fails with `Unsupported gpu architecture 'compute_120'`.

```bash
mkdir -p ~/softwares && cd ~/softwares
git clone https://github.com/ggml-org/llama.cpp
cmake llama.cpp -B llama.cpp/build -DBUILD_SHARED_LIBS=OFF -DGGML_CUDA=ON
#   without sudo / nvcc not on PATH: add  -DCMAKE_CUDA_COMPILER=/usr/local/cuda-13.0/bin/nvcc
cmake --build llama.cpp/build --config Release -j --clean-first --target llama-server
cp llama.cpp/build/bin/llama-server llama.cpp/llama-server
~/softwares/llama.cpp/llama-server --version
```

**Download the model and the vision adapter.** The vision adapter (`mmproj`) is required for VLM detection and image input.

```bash
python -c "
from huggingface_hub import snapshot_download
snapshot_download(repo_id='unsloth/Qwen3.5-9B-GGUF',
                  local_dir='$HOME/work/atlas/unsloth/Qwen3.5-9B-GGUF',
                  allow_patterns=['*UD-Q4_K_XL*', '*UD-IQ2_M*', 'mmproj-BF16.gguf'])
"
```
For inference you only need **one** model file plus `mmproj-BF16.gguf`. **[eval]** The model-sweep benchmark (`run_model_sweep.py`) uses every quantization in the folder; download them all with `allow_patterns=['*.gguf']` (~3–6 GB each).

> ⚠️ `nutri_rag/scripts/start_server.sh` has **hardcoded paths**:
> - line 15 (default model): `/home/boxun/work/atlas/unsloth/Qwen3.5-9B-GGUF/Qwen3.5-9B-UD-IQ2_M.gguf`
> - line 16 (vision adapter): `/home/boxun/work/atlas/unsloth/Qwen3.5-9B-GGUF/mmproj-BF16.gguf`
>
> On another machine, edit both lines. Note that the default model is the small **IQ2_M** version. To use a different one, pass its path as the first argument (see §2.1).

### 1.5 YOLO weights — [inference] (robot demo only)

```bash
mkdir -p nutri-atlas/weights && cd nutri-atlas/weights
yolo export model=yolo11n.pt format=onnx imgsz=640     # downloads yolo11n.pt, writes yolo11n.onnx
cd ../..
```

The detector loads `nutri-atlas/weights/yolo11n.onnx` with onnxruntime. Install `onnxruntime` (§1.1) even if you only use `--detector vlm`, because the detector module imports it either way.

### 1.6 NutriBench benchmark prerequisite — [eval]

`run_bench.py` and `run_baseline.py` change into a folder **next to** the repo: `../qwen_test/lm-evaluation-harness`. Create it once:

```bash
mkdir -p ../qwen_test/lm-evaluation-harness
```

---

## 2. Running: nutrition only (no robot)

### 2.1 Start the LLM server (keep this terminal open) — [inference]

```bash
cd nutri_rag
bash scripts/start_server.sh                                   # default model (IQ2_M), port 8080
# or choose the model:
bash scripts/start_server.sh $HOME/work/atlas/unsloth/Qwen3.5-9B-GGUF/Qwen3.5-9B-UD-Q4_K_XL.gguf
```
This serves an OpenAI-compatible API at `http://localhost:8080/v1`. The port is fixed at 8080, and "thinking" is disabled (`enable_thinking:false`).

### 2.2 Interactive nutrition assistant — [inference]

```bash
cd nutri_rag && python scripts/demo_assistant.py
```

### 2.3 Benchmarks — [eval]

```bash
cd nutri_rag

# NutriBench (needs LLM server + §1.6). Modes v0–v5, nutrients carb|protein|fat|energy
python scripts/run_bench.py --mode v3 --nutrient protein --limit 200 --concurrent 3
python scripts/run_all_bench.py --modes v0 v1 v3 --nutrients carb protein --limit 1000

# Quantization sweep (starts/stops the server itself; don't run start_server.sh at the same time)
python scripts/run_model_sweep.py --limit 20

# PFoodReq (no LLM server needed for the default --ablation no_llm)
python scripts/run_pfoodreq_bench.py
```
Results go to `nutri_rag/results/`. For PFoodReq, results go to `results/` relative to the folder you run from.

---

## 3. Robot setup (Unitree Go2, ROS 2 Foxy)

### 3.1 Navigation stack: `~/test_ws/go2_nav_ws` on the robot

This is **not part of this repo**. It lives on the robot, with a git-tracked source mirror on the operator PC at `/home/boxun/work/go2_nav_src`. It contains `go2_ros2_toolbox` (go2_core, SLAM, Nav2 config), `realsense_zmq` (camera → ZMQ streams on ports 5557/5558) and `speech_processor`. Our local changes are:

- **`go2_core/src/go2_base.cpp`:** a `gait_api_id` parameter (default **1061 StaticWalk**). StopMove is no longer sent on zero velocity.
- **`go2_navigation/config/navigate_plan_first.xml`:** a behavior tree that plans before following, passed in `go2_navigation/launch/go2_nav2.launch.py`.
- **`go2_navigation/config/nav2_params.yaml`:** `xy_goal_tolerance: 0.5`.

To change something: edit it in `go2_nav_src`, copy the changed files to the robot at the same paths under `~/test_ws/go2_nav_ws/src/`, then build **on the robot**:
```bash
cd ~/test_ws/go2_nav_ws
source /opt/ros/foxy/setup.bash
colcon build --packages-select <package>       # e.g. go2_core, go2_navigation
```

### 3.2 ZMQ bridge on the robot

Copy the bridge from this repo to the robot, and install pyzmq there:
```bash
# operator PC
scp nutri-atlas/robot_control/robot_side/zmq_bridge_real/zmq_bridge_node_working_v2.py \
    unitree@<ROBOT_IP>:/home/unitree/test_ws/atlas/zmq_communication/
# robot
pip3 install pyzmq
```
The bridge works with Python 3.8 (Foxy). It stores detected objects in `~/detected_objects.json` on the robot.

> **Keep these equal:** the bridge's `_ARRIVAL_THRESHOLD_M` must equal Nav2's goal checker `xy_goal_tolerance` (`nav2_params.yaml`), and both must be ≥ the FollowPath (DWB) `xy_goal_tolerance`. All three are currently **0.5**. Change them together; a mismatch causes false "failed" results or shaking near the goal.

### 3.3 Landmarks

Named locations are in `nutri-atlas/robot_control/config/landmarks.yaml` on the **operator PC**. It is read when `robot_assistant.py` starts, so restart after editing.
```yaml
landmarks:
  Kitchen:
    x: -16.0
    y: 10.76
    yaw_rad: -0.75        # optional: heading on arrival
    description: "Kitchen area"
```
To record a pose: copy `nutri-atlas/robot_control/robot_side/coordinates_record.py` to the robot, drive the robot to the spot, then on the robot run
```bash
source ~/test_ws/go2_nav_ws/install/setup.bash
python3 coordinates_record.py --output landmarks_record.json
```
Press **Enter** to record and `q` to quit. Then copy `x`, `y` and `yaw_rad` from the JSON into `landmarks.yaml`. Keep landmarks ≥ ~0.6 m from furniture, because Nav2 keeps the robot ~0.55 m away from obstacles.

---

## 4. Running: robot demo (real world)

### 4.1 Network check

- The operator PC and the robot must be on the same Wi-Fi subnet (`192.168.0.x`).
- **Get the robot's IP on the robot** with `hostname -I`, and use the `192.168.0.x` address. Ignore `192.168.123.x` (Unitree internal) and `172.17.x.x` (Docker). The address can change after a reboot.
- From the operator PC: `ping <ROBOT_IP>`.
- The robot must accept TCP **5555** (bridge), **5557** and **5558** (camera).

### 4.2 On the robot (2 terminals)

```bash
# Terminal 1: Go2 driver, SLAM, Nav2, RViz, RealSense + ZMQ camera streams
cd ~/test_ws/go2_nav_ws
source install/setup.bash
ros2 launch ./everything.launch.py
#   change gait live:     ros2 param set /go2_base gait_api_id 1062   (1061 = StaticWalk, default)

# Terminal 2: ZMQ bridge (port 5555)
cd ~/test_ws/go2_nav_ws && source install/setup.bash
cd ~/test_ws/atlas/zmq_communication
python3 zmq_bridge_node_working_v2.py
#   options: --port 5555 --spin-kp 1.5 --spin-threshold-deg 15 --move-kp 0.8 --move-threshold-m 0.05
```

Check the robot is localized in RViz before sending goals. Gait IDs are listed in `nutri-atlas/robot_control/robot_side/GO2_SPORT_API.md`.

### 4.3 On the operator PC (2 terminals)

```bash
conda activate mimir

# Terminal 1: LLM server
cd nutri_rag && bash scripts/start_server.sh

# Terminal 2: robot assistant
cd nutri-atlas/robot_control
python robot_assistant.py --robot-ip <ROBOT_IP> --detection-mode real                  # YOLO detector (default)
python robot_assistant.py --robot-ip <ROBOT_IP> --detection-mode real --detector vlm   # open-vocabulary, uses the LLM's vision
```

On startup, the assistant clears the previous session's detected objects. Example requests:
```
Go to the reception.
Go to the kitchen and check if there is any food.
Go to the reception and look for objects on the way.
What do you see right now?
Remember what you see.
Turn left 90 degrees.
I ate a burger for lunch, what should I eat for dinner?
```
Type `exit` to quit. The system prompt and the landmarks are loaded at startup, so restart after changing either.

**Optional standalone detector windows** (live YOLO view, auto-registration). These are not required; the assistant runs detection itself:
```bash
cd nutri-atlas/robot_control/tools
python seperate_detector_real_world.py --robot-ip <ROBOT_IP>        # press Enter to push detections
python seperate_detector_real_world_auto.py --robot-ip <ROBOT_IP>   # automatic; --targets person chair --stable-conf 0.6
```

### 4.4 Settings worth knowing

| Setting | Where | Default |
|---|---|---|
| Robot port | `--robot-port` / `ROBOT_PORT` | 5555 |
| Navigation reply timeout (client) | `NAV_TIMEOUT_MS` (navigate / spin tools) | 60000 ms |
| Labels to keep when registering objects | `INTEREST_OBJECTS` (comma list) | all |
| Bridge "no progress" failure | `_NAV_STALL_S` in the bridge | 30 s |
| Bridge navigation timeout | `_NAV_TIMEOUT` in the bridge | 120 s |

> Long trips in StaticWalk (e.g. start → kitchen) can take more than 60 s. If the assistant reports `No reply from robot within 60s` while the robot is still walking, start it with `NAV_TIMEOUT_MS=130000 python robot_assistant.py ...`.

---

## 5. Simulation — [optional]

Simulation uses a different bridge and an extra object server, in an autonomy-stack + Unity simulator workspace:
- **Bridge:** `nutri-atlas/robot_control/robot_side/zmq_bridge_simulation/zmq_bridge_node.py` (`--port 5555`, `--objects-file ...`).
- **Object server:** `zmq_bridge_simulation/zmq_object_server.py` (port 5556).
- **Launcher:** `nutri-atlas/robot_control/system_simulation_with_zmq.sh`. Copy it to the simulator workspace root; it starts the simulator and the bridge, but not the object server.
- **Operator side:** `python robot_assistant.py --robot-ip <SIM_IP>`, where `--detection-mode sim` is the default.

---

## 6. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `No reply from robot within 10s` at startup | Wrong IP, bridge not running, or network blocked. Check `hostname -I` on the robot and `ping`. |
| Navigation "fails" with `No progress … for 30s` and the robot never moved | Check the Nav2 logs on the robot (`~/.ros/log/bt_navigator_*`, `controller_server_*`). A landmark too close to obstacles is a common cause. |
| `Trajectory Hits Obstacle` in the Nav2 log at the start of a trip | The start pose is too close to an obstacle. Move that landmark into open space. |
| Robot shakes near the goal | Bridge and Nav2 position tolerances differ (see §3.2). |
| Gait falls back to default | Check `ros2 param get /go2_base gait_api_id` and the `Gait set:` lines in the launch output. |
| `undefined symbol` importing torch_scatter | PyG wheels don't match torch/CUDA (see §1.1). |
| `run_bench.py` fails with `FileNotFoundError` on `qwen_test` | Create `../qwen_test/lm-evaluation-harness` (see §1.6). |
| Detector error about `yolo11n.onnx` | Export the weights (see §1.5). |
