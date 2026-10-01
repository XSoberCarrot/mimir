# Unitree Go2 — High-Level API Reference (ROS 2)

Reference for controlling the Go2 through Unitree's official high-level ("sport mode") services, plus the other official services, state topics and setup guidance.

**Sources** (read from the robot `unitree@192.168.0.164`, 2026-10-01):

| Source | Version |
|---|---|
| `/home/unitree/unitree_sdk2` — `include/unitree/robot/go2/**` | sport API `1.0.0.1` |
| `/home/unitree/unitree_ros2` — `CHANGELOG.md`, `README.md`, `example/src/src/common/ros2_sport_client.cpp`, `cyclonedds_ws/src/unitree/**/msg` | `v0.3.0` (commit `3ff13ea`, 2025-10-18) |
| Official docs | <https://support.unitree.com/home/en/developer/sports_services> |

> Which commands actually work depends on the Go2 **firmware** version. Everything below is "supported by the SDK"; test each one on the robot before relying on it.

---

## 1. How a sport command is sent

Sport mode is request/response. Publish a `unitree_api/msg/Request` to **`/api/sport/request`**; replies come back on `/api/sport/response`.

```
Request:          header.identity.api_id   ← the command ID (tables below)
                  parameter                ← JSON string ("" when the command takes none)
```

From a terminal (robot standing, open space, remote in hand):

```bash
source /opt/ros/humble/setup.bash && source ~/test_ws/go2_nav_ws/install/setup.bash

# Command with no parameter
ros2 topic pub --once /api/sport/request unitree_api/msg/Request \
  "{header: {identity: {api_id: 1061}}, parameter: ''}"

# Command with a bool flag
ros2 topic pub --once /api/sport/request unitree_api/msg/Request \
  "{header: {identity: {api_id: 2049}}, parameter: '{\"data\": true}'}"

# Command with x/y/z values (e.g. Move)
ros2 topic pub --once /api/sport/request unitree_api/msg/Request \
  "{header: {identity: {api_id: 1008}}, parameter: '{\"x\": 0.2, \"y\": 0.0, \"z\": 0.0}'}"
```

From C++ / Python, build the same message (see `ros2_sport_client.cpp` for every command):

```cpp
unitree_api::msg::Request req;
req.header.identity.api_id = 1015;           // SpeedLevel
req.parameter = R"({"data": 1})";
req_pub->publish(req);                        // topic: /api/sport/request
```

---

## 2. Sport API — all Go2 commands (`ROBOT_SPORT_SERVICE_NAME = "sport"`)

**Parameter formats** below are taken from `ros2_sport_client.cpp`. "—" means send `parameter: ''`.

Safety: 🟢 safe for normal use · 🟡 changes how it moves; test in open space · 🔴 acrobatic or risky; needs space, a flat floor and a charged battery. Don't use in the demo.

### 2.1 Posture and basic control

| ID | Function | Parameter | What it does | |
|---|---|---|---|---|
| 1001 | `Damp()` | — | Damping mode: all motors go limp. **The robot collapses if it's standing.** Emergency stop. | 🔴 |
| 1002 | `BalanceStand()` | — | Balance stand: holds its body pose and accepts `Euler` adjustments. | 🟢 |
| 1003 | `StopMove()` | — | Stops the current motion; parameters go back to defaults. | 🟢 |
| 1004 | `StandUp()` | — | Stands up with locked joints (stiff stand). | 🟢 |
| 1005 | `StandDown()` | — | Lies down with locked joints. | 🟢 |
| 1006 | `RecoveryStand()` | — | Gets up from lying down or a fall. | 🟢 |
| 1007 | `Euler(roll, pitch, yaw)` | `{"x": roll, "y": pitch, "z": yaw}` (rad) | Tilts or twists the **body** while the feet stay planted. Use in BalanceStand. Our `go2_target_pose_controller` clamps to roll/pitch ±0.75 and yaw ±0.6 rad. | 🟢 |
| 1008 | `Move(vx, vy, vyaw)` | `{"x": vx, "y": vy, "z": vyaw}` (m/s, m/s, rad/s, body frame) | Velocity walking. **This is what our nav stack uses** (`go2_base.cpp` turns `/cmd_vel` into `Move`). | 🟢 |
| 1009 | `Sit()` | — | Sits down. | 🟢 |
| 1010 | `RiseSit()` | — | Stands back up from sitting. | 🟢 |
| 1015 | `SpeedLevel(level)` | `{"data": -1 \| 0 \| 1}` | Speed range: slow / normal / fast. | 🟡 |
| 1027 | `SwitchJoystick(flag)` | `{"data": true/false}` | Turns handling of the wireless remote's joystick on or off. | 🟡 |
| 1028 | `Pose(flag)` | `{"data": true/false}` | Pose mode: the body follows the joystick in place. | 🟢 |
| 2054 | `AutoRecoverSet(flag)` | `{"data": true/false}` | Turns automatic get-up after a fall on or off. | 🟢 |
| 2055 | `AutoRecoverGet(&flag)` | — (reply: `{"data": bool}`) | Reads that setting. | 🟢 |
| 2058 | `SwitchAvoidMode()` | — | Toggles obstacle-avoidance mode. | 🟡 |

### 2.2 Gaits (walking modes)

On current Go2 firmware, each gait has **its own command**. The old `SwitchGait(1011)` is gone (see §3).

| ID | Function | Parameter | What it does | |
|---|---|---|---|---|
| 1061 | `StaticWalk()` | — | Static walk: slow and stable. | 🟡 |
| 1062 | `TrotRun()` | — | Running trot: fast. | 🟡 |
| 1063 | `EconomicGait()` | — | Energy-saving walk. | 🟡 |
| 2045 | `FreeWalk()` | — | "Free" / agile walk (newer firmware). | 🟡 |
| 2049 | `ClassicWalk(flag)` | `{"data": true/false}` | Classic walk on/off. | 🟡 |
| 2051 | `CrossStep(flag)` | `{"data": true/false}` | Cross-step gait on/off. | 🟡 |
| 2048 | `FreeAvoid(flag)` | `{"data": true/false}` | Walks with built-in obstacle avoidance. | 🟡 |
| 2046 | `FreeBound(flag)` | `{"data": true/false}` | Bounding gait. | 🔴 |
| 2047 | `FreeJump(flag)` | `{"data": true/false}` | Jumping gait. | 🔴 |
| 2050 | `WalkUpright(flag)` | `{"data": true/false}` | Walks on its hind legs. | 🔴 |

### 2.3 Tricks and gestures

| ID | Function | Parameter | What it does | |
|---|---|---|---|---|
| 1016 | `Hello()` | — | Wave / greet. | 🟢 |
| 1017 | `Stretch()` | — | Stretch. | 🟢 |
| 1020 | `Content()` | — | "Happy" gesture. | 🟢 |
| 1022 | `Dance1()` | — | Dance routine 1. | 🟡 |
| 1023 | `Dance2()` | — | Dance routine 2. | 🟡 |
| 1029 | `Scrape()` | — | Bow / scrape gesture. | 🟢 |
| 1036 | `Heart()` | — | Draws a heart with its front paw. | 🟢 |
| 1030 | `FrontFlip()` | — | Front flip. | 🔴 |
| 1031 | `FrontJump()` | — | Jumps forward. | 🔴 |
| 1032 | `FrontPounce()` | — | Pounces forward. | 🔴 |
| 2041 | `LeftFlip()` | — | Side flip. | 🔴 |
| 2043 | `BackFlip()` | — | Back flip. | 🔴 |
| 2044 | `HandStand(flag)` | `{"data": true/false}` | Handstand on its front legs. | 🔴 |

---

## 3. Removed / no longer supported IDs

Official list from `unitree_ros2` v0.2.0 **BREAKING CHANGE**. Sending these does nothing on current firmware.

| ID | Macro | Old function | Note for our code |
|---|---|---|---|
| 1011 | `ROBOT_SPORT_API_ID_SWITCHGAIT` | `SwitchGait()` | Why the teleop **`i`** key does nothing. Use the §2.2 gaits instead. |
| 1012 | `ROBOT_SPORT_API_ID_TRIGGER` | `Trigger()` | |
| 1013 | `ROBOT_SPORT_API_ID_BODYHEIGHT` | `BodyHeight()` | Why `go2_target_pose_controller_v2` says height adjustment is "not working". |
| 1014 | `ROBOT_SPORT_API_ID_FOOTRAISEHEIGHT` | `FootRaiseHeight()` | Teleop `[` key. |
| 1018 | `ROBOT_SPORT_API_ID_TRAJECTORYFOLLOW` | `TrajectoryFollow()` | |
| 1019 | `ROBOT_SPORT_API_ID_CONTINUOUSGAIT` | `ContinuousGait()` | Teleop `;` key. |
| 1021 | `ROBOT_SPORT_API_ID_WALLOW` | `Wallow()` | Teleop `,` key. |
| 1024 | `ROBOT_SPORT_API_ID_GETBODYHEIGHT` | — | |
| 1025 | `ROBOT_SPORT_API_ID_GETFOOTRAISEHEIGHT` | — | |
| 1026 | `ROBOT_SPORT_API_ID_GETSPEEDLEVEL` | — | |

> The `sport_model.hpp` copies in `~/test_ws/legged_robot_ros2/ros2_code/ws/*` and the teleop's `ROBOT_SPORT_API_IDS` still list these old IDs. The SDK's `go2/sport/sport_api.hpp` is the up-to-date list.

---

## 4. Reading the current mode and gait

Subscribe to **`sportmodestate`** (high rate) or **`lf/sportmodestate`** (low rate). Message type: `unitree_go/msg/SportModeState`.

```bash
ros2 topic echo --once /sportmodestate      # or /lf/sportmodestate
```

```
TimeSpec   stamp
uint32     error_code
IMUState   imu_state            # quaternion[4], gyroscope[3], accelerometer[3], rpy[3], temperature
uint8      mode                 # see table
float32    progress             # 1 while a dance/action is running
uint8      gait_type            # see table
float32    foot_raise_height
float32[3] position             # odometry position
float32    body_height
float32[3] velocity
float32    yaw_speed
float32[4] range_obstacle
int16[4]   foot_force
float32[12] foot_position_body
float32[12] foot_speed_body
```

| `mode` | Meaning | | `gait_type` | Meaning |
|---|---|---|---|---|
| 0 | idle, default stand | | 0 | idle |
| 1 | balanceStand | | 1 | trot |
| 2 | pose | | 2 | run |
| 3 | locomotion | | 3 | climb stair |
| 4 | reserve | | 4 | forwardDownStair |
| 5 | lieDown | | 9 | adjust |
| 6 | jointLock | | | |
| 7 | damping | | | |
| 8 | recoveryStand | | | |
| 9 | reserve | | | |
| 10 | sit | | | |
| 11 | frontFlip | | | |
| 12 | frontJump | | | |
| 13 | frontPounce | | | |

(Enums from the `unitree_ros2` README. Newer gaits such as FreeWalk / ClassicWalk may report values not listed here; check by echoing the topic after switching.)

---

## 5. Other official Go2 services

All services use the same `Request`/`Response` pattern with topics `/api/<service>/request` and `/api/<service>/response`. ✅ means a ROS 2 example in `unitree_ros2` uses that topic. ⚪ means the topic follows the pattern but I haven't seen a ROS 2 example for it (the SDK2 C++ client exists).

### 5.1 `robot_state`: services on the robot ✅ `/api/robot_state/request`

| ID | Function | Purpose |
|---|---|---|
| 1001 | `ServiceSwitch(name, on/off, &status)` | Start or stop a built-in service by name |
| 1002 | `SetReportFreq(interval, duration)` | Set the state-report rate |
| 1003 | `ServiceList(&list)` | List built-in services and their status |

Example: `unitree_ros2/example/src/src/go2/go2_robot_state_client.cpp`

### 5.2 `motion_switcher`: high-level controller on/off ✅ `/api/motion_switcher/request`

| ID | Function | Purpose |
|---|---|---|
| 1001 | `CheckMode(&form, &name)` | Which motion controller is active (empty name = none) |
| 1002 | `SelectMode(nameOrAlias)` | Activate a motion controller |
| 1003 | `ReleaseMode()` | **Turn off** the high-level controller (needed before low-level `/lowcmd` control) |
| 1004 / 1005 | `SetSilent(bool)` / `GetSilent(&bool)` | Silent setting |

> ⚠️ `ReleaseMode()` turns off sport mode: the robot stops balancing, and **sport commands stop working**. The official `go2_stand_example` calls it before low-level control. Only use it with the robot lying down or hung up.

### 5.3 `obstacles_avoid`: built-in obstacle avoidance ⚪

| ID | Function | Purpose |
|---|---|---|
| 1001 | `SwitchSet(enable)` | Avoidance on/off |
| 1002 | `SwitchGet(&enable)` | Read the switch |
| 1003 | `Move(x, y, yaw)` | Velocity move with avoidance |
| 1004 | `UseRemoteCommandFromApi(flag)` | Take movement commands from the API instead of the remote |
| — | `MoveToAbsolutePosition(x, y, yaw)` / `MoveToIncrementPosition(x, y, yaw)` | Helpers built on the above (SDK2 C++ client) |

### 5.4 `vui`: volume and lights ⚪ (ROS 2 examples use `/api/voice/...`)

| ID | Function | Purpose |
|---|---|---|
| 1001 / 1002 | `SetSwitch(int)` / `GetSwitch(&int)` | Voice/UI switch |
| 1003 / 1004 | `SetVolume(level)` / `GetVolume(&level)` | Speaker volume |
| 1005 / 1006 | `SetBrightness(level)` / `GetBrightness(&level)` | Light brightness |

Example: `unitree_sdk2/example/go2/go2_vui_client.cpp`

### 5.5 `videohub`: front camera ⚪

| ID | Function | Purpose |
|---|---|---|
| 1001 | `GetImageSample(&bytes)` | Get one JPEG frame from the built-in front camera |

Example: `unitree_sdk2/example/go2/go2_video_client.cpp`

### 5.6 `uwbswitch` (UTrack): UWB follow-me ⚪

| ID | Function | Purpose |
|---|---|---|
| 1001 / 1002 | `SwitchSet(enable)` / `SwitchGet(&enable)` | UWB tracking on/off |
| 1003 | `IsTracking(&bool)` | Whether it's currently tracking |

### 5.7 `config`: on-robot key/value config ⚪

| ID | Function | Purpose |
|---|---|---|
| 1001 | `Set(name, content)` | Write a config entry |
| 1002 | `Get(name, &content)` | Read a config entry |
| 1003 | `Del(name)` | Delete a config entry |
| 1004 | `Meta(name, &meta)` | Config metadata |

---

## 6. State and control topics

| Topic | Type | Direction | Content |
|---|---|---|---|
| `/sportmodestate`, `/lf/sportmodestate` | `unitree_go/SportModeState` | robot → you | Mode, gait, odometry, feet (§4) |
| `/lowstate`, `/lf/lowstate` | `unitree_go/LowState` | robot → you | 20 motor states, IMU, battery (BMS), foot force, `power_v`/`power_a` |
| `/wirelesscontroller` | `unitree_go/WirelessController` | robot → you | `lx ly rx ry` in [-1, 1] + `keys` bitmask |
| `/utlidar/cloud` | `sensor_msgs/PointCloud2` | robot → you | Built-in L1 lidar (frame `utlidar_lidar`) |
| `/utlidar/robot_pose` | `geometry_msgs/PoseStamped` | robot → you | Robot pose; our `go2_base.cpp` publishes it as TF `odom → base_link` |
| `/api/sport/request` / `response` | `unitree_api/Request` / `Response` | you → robot | Sport commands (§2) |
| `/lowcmd` | `unitree_go/LowCmd` | you → robot | Per-motor `q, dq, tau, kp, kd`. **Only with sport mode released (§5.2).** |

Request / Response messages:

```
Request:  header{ identity{ int64 id, int64 api_id }, lease{ int64 id }, policy{ int32 priority, bool noreply } }
          string parameter        # JSON
          uint8[] binary
Response: header{ identity{ id, api_id }, status{ int32 code } }   # code 0 = success
          string data             # JSON reply (e.g. AutoRecoverGet → {"data": true})
          int8[] binary
```

---

## 7. Official setup guidance (from the `unitree_ros2` README)

1. **Network:** connect by Ethernet and set the PC's interface to static `192.168.123.99/24`. The robot's internal network is `192.168.123.x`.
2. **DDS:** `unitree_ros2/setup.sh` sets `RMW_IMPLEMENTATION=rmw_cyclonedds_cpp` and a `CYCLONEDDS_URI` that names that network interface (e.g. `enp3s0`); edit the interface name. Use `setup_local.sh` (loopback) for simulation, or `setup_default.sh` for no specific interface.
3. **Check it works:** `source ~/unitree_ros2/setup.sh && ros2 topic list`, then `ros2 topic echo /sportmodestate`.
4. **Build the examples:** `cd ~/unitree_ros2/example && colcon build`, then run e.g. `./install/unitree_ros2_example/bin/read_motion_state`.
5. **Supported:** Ubuntu 22.04 + Humble (recommended) or 20.04 + Foxy.

**Official examples on the robot:**

| Path | What it shows |
|---|---|
| `unitree_ros2/example/src/src/go2/go2_sport_client.cpp` | High-level sport commands (ROS 2) |
| `unitree_ros2/example/src/src/go2/go2_stand_example.cpp` | Low-level stand (releases sport mode first) |
| `unitree_ros2/example/src/src/go2/go2_robot_state_client.cpp` | robot_state service |
| `unitree_ros2/example/src/src/read_motion_state.cpp` | Reading `sportmodestate` |
| `unitree_ros2/example/src/src/read_low_state.cpp` | Reading `lowstate` |
| `unitree_ros2/example/src/src/read_wireless_controller.cpp` | Reading the remote |
| `unitree_ros2/example/src/src/record_bag.cpp` | Recording a rosbag |
| `unitree_sdk2/example/go2/` | C++ (non-ROS) versions: `go2_sport_client`, `go2_low_level`, `go2_trajectory_follow`, `go2_video_client`, `go2_vui_client`, `go2_robot_state_client`, `go2_stand_example` |

**Official docs:**
- Sport services: <https://support.unitree.com/home/en/developer/sports_services>
- Basic (low-level) services: <https://support.unitree.com/home/en/developer/Basic_services>
- Remote controller: <https://support.unitree.com/home/en/developer/Get_remote_control_status>
- Repos: <https://github.com/unitreerobotics/unitree_ros2>, <https://github.com/unitreerobotics/unitree_sdk2>

---

## 8. How this maps to our demo stack

| Our component | What it sends | Notes |
|---|---|---|
| `go2_nav_ws/.../go2_core/src/go2_base.cpp` | `/cmd_vel` → `Move` (1008); all-zero `/cmd_vel` → `StopMove` (1003) | Never changes gait, so the robot walks in whatever gait it's currently in |
| `zmq_bridge_node_working_v2.py` | `/cmd_vel` (spin / move / final heading) | Goes through `go2_base` like Nav2 |
| `go2_teleop_ctrl_keyboard` | Raw IDs with `{"x","y","z"}` params | `i` (1011), `[` (1014), `;` (1019), `,` (1021) are removed IDs (§3); gaits that take a flag need `{"data": ...}`, not `{"x","y","z"}` |
| `go2_target_pose_controller_v2` | `Move` → `BalanceStand` → `Euler` | `BodyHeight` (1013) removed (§3) |

To set a gait for the demo, send one §2.2 command (e.g. `ClassicWalk {"data": true}` or `StaticWalk`) after the robot stands up. Then confirm it stays in effect after a `StopMove` by echoing `gait_type` (§4).
