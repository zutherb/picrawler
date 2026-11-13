# rl_coral: sim-to-real RL gait on the Coral Edge TPU

A residual gait policy for the PiCrawler. A CPG trot (2 Hz) does the walking.
On top of it, a small MLP adds up to ±15 mm per foot axis, based on the
command, visual proprioception and the last two actions. The policy is trained
with PPO in PyBullet, exported as a full-int8 TFLite model and run at 50 Hz on
the robot.

```
camera 30 fps ──> vision_proprioception_tpu0.py ──┐ [roll, pitch, yaw rate, obstacle], [vx, wz]
                  flow 64x48 + SSD on pci:0        │
                                                   v
run_picrawler_dual_tpu.py, 50 Hz: observation (32) ─> int8 policy on pci:1 ─> 12 offsets
                                  CPG trot + offsets ─> SunFounder IK ─> Picrawler.do_step()
```

| File | Runs on | What it does |
|---|---|---|
| `common.py` | everywhere | Data contract: observation layout, CPG, body→leg frames, quantization |
| `picrawler.urdf` | desktop | Geometry from `picrawler/kinematics.py` (C 33, A 48, B 78 mm), torso 0.55 kg |
| `sim_env.py` | desktop | `PiCrawlerVisionEnv`: 50 Hz, 4 substeps, domain randomization, vision delay |
| `train_and_export_int8.py` | desktop | PPO → Keras → int8 TFLite → `edgetpu_compiler` |
| `vision_proprioception_tpu0.py` | Pi | `VisionStateTracker`: optical flow, floor edge, SSD on TPU 0, target following |
| `run_picrawler_dual_tpu.py` | Pi | 50 Hz loop, policy on TPU 1, guards, sits down on exit |
| `occupancy_mapper_2d.py` | Pi | 2D log-odds grid from leg odometry + flow, floor edge + ultrasonic |
| `demo_kitchen.py` | desktop | Kitchen demo: records camera, detections and the rolling map; MP4 + replay for the web mock |

## Data contract

Observation `(1, 32)` float32. It is quantized to int8 with one scale, so
every value stays in [-1, 1].

| Index | Content |
|---|---|
| 0:2 | `sin(phase)`, `cos(phase)`, 2 Hz |
| 2:4 | `vx_cmd`, `wz_cmd` in [-1, 1] (1 = 0.08 m/s, 0.6 rad/s), after the obstacle slow-down |
| 4:8 | `vis_roll`, `vis_pitch` (rad, high-passed, τ 0.5 s), `flow_yaw` (rad/s, clipped ±1), `obstacle_dist` (free distance / 1 m) |
| 8:20 | last action a(t-1) |
| 20:32 | action a(t-2) |

Action `(1, 12)` in [-1, 1]. It holds (dx, dy, dz) per leg in the body frame
(x right, y forward, z up), scaled by 15 mm. The offsets are added to the stance
`[[60,60,-50], [-60,60,-50], [-60,-60,-50], [60,-60,-50]]` (leg order RF, LF,
LR, RR) plus the CPG. Each leg's signs are then flipped into its SunFounder leg
frame. Below 15 cm to an obstacle no forward command gets through, in simulation
and on the robot alike.

## Train and export (desktop)

```bash
pip install -r rl_coral/requirements-train.txt
python -m rl_coral.train_and_export_int8 --timesteps 2000000 --envs 8
# -> rl_coral/models/gait_policy_ppo.zip, gait_policy_int8.tflite, representative_obs.npy
```

The script checks the Keras copy against PyTorch (difference must be below
1e-4). It also checks the int8 model against float, with the error reported in
mm.

`edgetpu_compiler` exists only for x86-64 Linux. If it is installed, the script
calls it. Otherwise it prints the command, for example to run in a Debian
container:

```bash
edgetpu_compiler -s -o rl_coral/models rl_coral/models/gait_policy_int8.tflite
# -> gait_policy_int8_edgetpu.tflite; the log must say every operation maps to the Edge TPU
```

On macOS, pybullet 3.2.7 does not build with current Xcode (a zlib `fdopen`
macro). Remove the `#define fdopen(fd, mode) NULL` line in
`examples/ThirdPartyLibs/zlib/zutil.h` of the sdist, then `pip install .`.

## Run on the robot

```bash
pip install -r rl_coral/requirements-robot.txt
sudo systemctl stop picrawler.target           # body, vision and sensors hold servos, camera and TPU
python -m rl_coral.vision_proprioception_tpu0 --model ssd_mobilenet_v2_coco_quant_postprocess_edgetpu.tflite \
    --labels coco_labels.txt                   # check signs: tilt right -> roll +, nose up -> pitch +
python -m rl_coral.run_picrawler_dual_tpu --model ssd_...edgetpu.tflite --labels coco_labels.txt
python -m rl_coral.run_picrawler_dual_tpu --no-camera --cmd 0.5 0 --seconds 10    # bench test, robot lifted
```

The robot walks towards `--target` (default `sports ball`). Without a target it
trots in place. `--cmd VX WZ` gives a fixed command instead.

Ctrl-C or SIGTERM returns the feet to the stance and sits down. The loop prints
its rate, invoke and servo times and overruns every 5 s. Off the robot,
`--dry-run` uses the fake `robot_hat` from `tests/`.

## 2D map while walking

```bash
python -m rl_coral.run_picrawler_dual_tpu --map --ultrasonic --model ... --labels ... --map-save /tmp/map.png
ssh -L 8090:127.0.0.1:8090 pi@picrawler     # then open http://localhost:8090/map.mjpg
```

`OccupancyMapper2D` keeps a 200 × 200 grid with 2 cm cells (4 × 4 m), with the
start pose in the middle facing up.

- **Pose.** Each control step it rigidly fits the stance feet of two steps,
  using the commanded CPG plus the policy offsets. The motion of those feet
  is the body's motion reversed. Slip factors scale the result: 0.8 for
  translation and 0.9 for rotation, both measured in simulation; calibrate
  them on the real floor. The heading is blended 70/30 with `flow_yaw`.
- **Scan.** Each camera frame becomes a fan of 31 rays across the field of
  view. The floor-edge bands of the camera reach 1 m. Rays within ±7.5° also
  use the ultrasonic distance, up to 1.5 m. Each ray applies -0.4 log-odds
  to the free cells (Bresenham) and +0.85 at the hit, clamped to ±4.
- **Tilt gate.** Frames with |roll| or |pitch| above 0.12 rad are skipped.
- **Output.** `get_probability_map()` returns an RGB image: free light,
  walls dark, path blue, footprint teal, heading orange.

The pose is dead reckoning: the map drifts with it. In simulation that came
to about 3 cm per metre walked.

## Kitchen demo

```bash
python -m rl_coral.demo_kitchen      # -> rl_coral/models/kitchen_demo.mp4 and kitchen_demo.zip (about 1 min)
python -m services.web --config web.toml --fake --demo rl_coral/models/kitchen_demo.zip
```

The crawler walks a simulated kitchen with the trained policy. The MP4 puts
three views side by side:

- the camera, with the floor edge and the detector's boxes;
- the rolling map, with camera rays in teal, ultrasonic rays in orange and
  detected objects in yellow;
- the real kitchen from above.

The web mock replays the recording in the real UI, in a loop. The Remote page
shows the video with boxes and builds the Surroundings map live. The Live page
shows the boxes too.

The simulated sensors behave like the robot's:

- The ultrasonic sensor sits 5 cm up and passes over the bowl.
- The detector only knows COCO classes, so the bin stays unnamed.
- Object distances come from the bottom edge of each box (`locate`).

## Things to know

- **Only one TPU on our board.** The HatDrive passes a single PCIe lane, so
  only `pci:0` shows up (see `spikes/coral`). Run the policy with
  `--policy-device cpu --policy rl_coral/models/gait_policy_int8.tflite`. With
  7k parameters it takes about 0.1 ms on the CPU. Sharing `pci:0` with the SSD
  would swap models in and out of the TPU's memory on every call.
- **No safety net from the MQTT stack.** This loop bypasses arbiter, body
  interlock, heartbeat and `cmd/stop`. Its own guards:
  - the obstacle slow-down;
  - a stop on more than 0.45 rad of tilt held for 0.2 s;
  - zero command when vision is stale for more than 0.5 s;
  - it refuses to start while the services run.
- **Servo timing.** `do_step(speed=100)` makes one interpolation step in
  robot_hat, about 10 ms of the 20 ms budget. Watch the `servo` and `overruns`
  numbers.
- **Calibrate before trusting the vision features.**
  - The defaults for field of view, height and tilt are estimates for 4:3.
  - Roll and pitch come from frame-to-frame motion, so they are relative.
    The simulator high-passes the true angles the same way.
- **Torque.** The URDF uses 1.8 N·m and 8.5 rad/s per joint as specified.
  Hobby servos of this size deliver roughly 0.2 N·m. If the policy relies on
  strength the robot does not have, lower `effort` in the URDF and retrain.
