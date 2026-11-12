# Target architecture: sensor-driven body over MQTT

Status: draft · As of: 2026-11-11 · Branch: `motion-framework`

## 1. Goal

Sensors running on the same Pi or on other devices (e.g. ESP32),
including a camera with a Coral Edge TPU for image recognition,
should be able to influence the PiCrawler's behaviour without being coupled
to the servo control. New sensors and external controllers
(Node-RED, Home Assistant, LLM) should be connectable without having to
change the motion core.

### Principles

1. **Sensors report facts, not commands.** A sensor publishes
   "distance = 12 cm", not "stop".
2. **One place decides.** The arbiter turns sensor facts into commands
   according to priorities.
3. **A single process talks to the servos.** Only the `MotionExecutor`
   calls `Picrawler`.
4. **The core stays free of MQTT.** `picrawler/` has no dependency on
   MQTT and remains testable without hardware.
5. **When in doubt, stop.** If a connection drops, an invalid message
   arrives or a timeout expires, the body goes into a safe state.

### Non-goals

- Control loops above 20 Hz over MQTT (e.g. balancing with an IMU). Such
  control loops run locally in the executor.
- Individual gait frames as a real-time stream over the network.
- Navigation, mapping, multiple robots (evaluate ROS 2 for that later).

## 2. Components

```
 Sensor nodes                 Arbiter                     Body-Service (Pi)
┌──────────────┐ sensor/*  ┌──────────────┐  cmd/*   ┌──────────────────────────┐
│ ultrasonic   │ ────────► │ Rules        │ ───────► │ services.body (Adapter)  │
│ imu          │           │ Priorities   │          │   Validation, Dedupe     │
│ vision+Coral │           │              │ ◄─────── │   Watchdog               │
│ remote       │           └──────────────┘ state/*  │ picrawler.executor       │
└──────────────┘                                     │   MotionExecutor thread  │
                                                     │ picrawler.Picrawler      │
                                                     └──────────────────────────┘
```

| Component | Location | Responsibility | Must not |
|---|---|---|---|
| `picrawler` (core) | this repo | Kinematics, gaits, servos | import MQTT, start threads |
| `picrawler.executor` | this repo | Execute commands one after another, interruptible between frames | network access |
| `services.body` | this repo, own process | MQTT ↔ executor, validation, watchdog, status | drive servos bypassing the executor |
| `services.arbiter` | this repo, own process | Sensor facts → commands by priority, heartbeat | pass through `cmd/*` from sensors |
| Sensor nodes | anywhere | Publish measurements | publish under `cmd/*` |
| `vision-service` | Pi with camera + Coral | Camera, video stream (local only), inference on the TPU, publish detections | decide behaviour, send commands |
| `web-service` | Pi | Login, stream proxy, object log, emergency stop, admin (Bluetooth, vision mode, services) | drive servos or camera directly |
| `gamepad-service` | Pi | Read Bluetooth controller, publish inputs | publish under `cmd/*` |

Package structure:

```
picrawler/
  kinematics.py
  gaits.py
  picrawler.py        # + iter_action()
  executor.py         # new
services/             # one process per service: python -m services.<name> --config …
  common/             # config.py, mqtt.py (paho wrapper), schema.py (payload validation)
  body/               # adapter.py (BodyAdapter), __main__.py
  arbiter/            # arbiter.py (rules, wander), __main__.py
  sensor_ultrasonic/  # __main__.py
services/vision/      # camera + Coral, see section 7
  __main__.py
  camera.py           # picamera2: main stream + lores stream
  stream.py           # MJPEG over HTTP
  inference.py        # TPU models, only the newest frame each time
  imprint.py          # learn custom objects
services/web/         # web interface, see section 8
  __main__.py
  app.py              # aiohttp: routes, login, SSE, stream proxy
  sightings.py        # detections → sightings (object log)
  store.py            # SQLite
  bluetooth.py        # BlueZ over D-Bus
  system.py           # services/system via polkit
  static/             # HTML, CSS, JS, without CDN
services/gamepad/     # Bluetooth controller via evdev
tests/
```

## 3. Core extensions

### 3.1 `Picrawler.iter_action(name, steps=1)`

Generator that yields the frames of an action one by one. The logic for the
alternating leg pairs (`stand_position`) is carried forward exactly as
in `do_action`. `do_action` is switched over to it internally and keeps its
behaviour:

```python
def do_action(self, motion_name, step=1, speed=50):
    for frame in self.iter_action(motion_name, step):
        self.do_step(frame, speed=speed)
```

Unknown names raise `KeyError` instead of printing something. `do_action`
catches this and, for compatibility, still prints "No such action".

### 3.2 `MotionExecutor`

```python
class Priority(IntEnum):
    GOAL = 0      # remote control, missions, LLM
    REFLEX = 1    # sensor reactions, e.g. evasion
    SAFETY = 2    # stop, watchdog, emergency stop

@dataclass(frozen=True)
class Command:
    kind: Literal["action", "frame", "stop", "sit"]
    name: str | None = None
    steps: int = 1
    speed: int = 50
    frame: list[list[float]] | None = None
    priority: Priority = Priority.GOAL
    id: str | None = None

class MotionExecutor:
    def __init__(self, crawler, on_state=None): ...
    def start(self) -> None: ...
    def submit(self, cmd: Command) -> bool: ...   # False = rejected
    def stop(self, priority=Priority.SAFETY) -> None: ...
    def shutdown(self, sit=True) -> None: ...
    @property
    def state(self) -> ExecutorState: ...
```

Rules:

- Exactly one worker thread owns `crawler`. No other code calls it.
- Between two frames it checks whether to abort. The
  reaction time is therefore at most the duration of one frame (depends on
  `speed`, measure on the device).
- A command with **higher** priority aborts the running one and clears all
  waiting commands with lower priority.
- A command with **equal** priority is appended to the end. The queue
  has a fixed upper limit (default 8), beyond that it is rejected (`submit` → False).
- A command with **lower** priority is rejected while the lockout period
  after a higher-priority command is running (default 1 s). This way
  "forward" cannot immediately cancel a stop that was just triggered.
- Errors in the worker do not end the thread. They are reported as state `error`,
  followed by a stop.

States:

```
idle ──submit──► running ──done──► idle
  ▲                 │
  │            preempt/stop
  │                 ▼
  └──────────── stopping ──► idle
            error ──► (stop) ──► idle
```

`on_state(ExecutorState)` is called on every transition and provides
`status`, `command_id`, `name`, `priority`, `queue_len` and `error`.

## 4. MQTT interface

Prefix: `crawler/<robot_id>/` (default `robot_id = 1`). All payloads are
JSON in UTF-8 and contain `ts` (Unix time in seconds, float).

### 4.1 Topics

| Topic | Direction | QoS | Retain |
|---|---|---|---|
| `sensor/<sensor>/<measurement>` | Sensor → Arbiter | 0 | no |
| `sensor/<sensor>/status` | Sensor → | 1 | yes (LWT `offline`) |
| `cmd/action` | Arbiter → Body | 1 | **never** |
| `cmd/frame` | Arbiter → Body | 1 | **never** |
| `cmd/stop` | any authorized party → Body | 1 | **never** |
| `cmd/heartbeat` | Arbiter (or UI) → Body | 0 | no |
| `state/motion` | Body → | 1 | yes |
| `state/legs` | Body → | 0 | yes |
| `status` | Body → | 1 | yes (LWT `offline`) |
| `event/rejected` | Body → | 1 | no |
| `event/watchdog` | Body → | 1 | no |
| `arbiter/status` | Arbiter → | 1 | yes (LWT `offline`) |

The body only accepts GOAL commands if a `cmd/heartbeat` arrived in the last
second. Otherwise the watchdog would abort them immediately anyway.
REFLEX commands and `cmd/stop` do not need a heartbeat.

The body service ignores retained messages on `cmd/*` (`msg.retain == True`)
and reports them via `event/rejected`.

### 4.2 Payloads

`cmd/action`
```json
{"id": "b1f3…", "name": "forward", "steps": 2, "speed": 70, "priority": "goal", "ts": 1762900000.1}
```

`cmd/frame`
```json
{"id": "…", "frame": [[45,45,-50],[45,0,-50],[45,0,-50],[45,45,-50]], "speed": 60, "priority": "goal", "ts": …}
```

`cmd/stop`
```json
{"id": "…", "sit": false, "reason": "obstacle", "ts": …}
```

`state/motion`
```json
{"status": "running", "command_id": "b1f3…", "name": "forward", "priority": "goal", "queue_len": 0, "ts": …}
```

`sensor/ultrasonic/distance`
```json
{"cm": 12.4, "ts": …}
```

### 4.3 Validation (`schema.py`)

Messages that violate one of the following rules are discarded and reported
with a reason via `event/rejected`. Nothing is executed in that case.

- `name` ∈ fixed configured list (default: all built-in gaits).
- `steps` ∈ 1–10, `speed` ∈ 1–100.
- `frame`: exactly 4 × 3 numbers, each coordinate in the range x 0–120,
  y −120–150, z −100–130 mm (configurable).
- `priority` ∈ {`goal`, `reflex`}. `safety` exists only internally or via `cmd/stop`.
- `ts` may be at most 2 s old (protection against stale messages).
  Prerequisite: the clocks are synchronized via NTP.
- `id` must not have occurred in the last 60 s (protection against
  duplicate delivery with QoS 1).

## 5. Safety

| Case | Reaction |
|---|---|
| Connection to the broker lost | `stop(SAFETY)`, sit down after 5 s without connection |
| `cmd/heartbeat` missing for longer than 1 s while a GOAL movement is running | `stop(SAFETY)` |
| Retained command received | ignore and report |
| Invalid message | ignore and report, movement continues. Exception `cmd/stop`: always executed, even with a stale `ts` or broken content |
| Exception in the executor | report `error`, then stop |
| Process end (SIGINT/SIGTERM) | `shutdown(sit=True)`, `status` = `offline` |
| Start | `status` = `online`, state `idle`, **no** automatic movement |

Broker:

- Username and password mandatory, TLS recommended as soon as devices other than
  localhost are involved.
- ACLs: sensor users may only write `crawler/+/sensor/<own sensor>/#`.
  Only the arbiter and the user interface may write `crawler/+/cmd/#`.
  Only the body service may write `state/#`, `status` and `event/#`.

## 6. Arbiter

Rules are defined declaratively in the configuration and evaluated by
priority. First rule:

```toml
[[rule]]
name = "obstacle_stop"
when = "sensor/ultrasonic/distance"
condition = "cm < 15"
then = { kind = "stop", reason = "obstacle" }
priority = "safety"
cooldown_s = 1.0
```

Measurements are debounced, e.g. by a median over the last 3 values,
so that a single outlier does not trigger a stop. If a sensor is `offline`,
its rules are considered "unknown", and GOAL commands that depend on it
are rejected.

## 7. Vision service (camera + Coral TPU)

### 7.1 Responsibilities

- Is the **only process** that owns the camera and the Coral TPU.
- Uses `picamera2` to capture two streams simultaneously: a **main stream**
  for the video (e.g. 1280×720) and a **lores stream** at the
  model's input size (e.g. 300×300 or 320×320) for the TPU.
- Delivers the video as **MJPEG over HTTP**, only on `127.0.0.1`. Login,
  drawn boxes and everything for the browser are handled by the web-service
  (section 8).
- Publishes detections as **facts** under `sensor/vision/*`. What the
  robot does in response is decided by the arbiter (section 6).

### 7.2 Pipeline

```
picamera2 ─┬─ main  ─► MJPEG encoder ─► HTTP /stream.mjpg
           └─ lores ─► [newest frame] ─► TPU ─► post-processing ─► MQTT
```

- Stream and inference run in **separate threads**. If inference is
  slow or fails, the stream keeps running.
- Inference always fetches only the **newest** frame. There is no
  queue in which old frames pile up, hence no growing
  latency.
- Detections are published at a rate limited to at most 10 Hz. The crawler
  cannot react faster with its step cycles anyway.

### 7.3 Models (compiled for the Edge TPU, INT8)

| Mode | Model (starting point) | Result |
|---|---|---|
| `detect` | SSD MobileNet v2 or EfficientDet-Lite0 (COCO) | Boxes + classes (person, cat, sports ball, …) |
| `pose` | MoveNet Lightning | 17 body keypoints of one person |
| `imprint` | MobileNet embedding + weight imprinting | custom, self-taught object classes |
| `off` | – | stream only, TPU unused |

One mode is always active. Switching reloads the model (measure duration on the
device). The start mode is set in `vision.toml`; switching is done via
`vision/mode`.

### 7.4 Topics

| Topic | Direction | QoS | Retain | Payload |
|---|---|---|---|---|
| `sensor/vision/detections` | vision → | 0 | no | `{"frame_ts": …, "objects": [{"label": "person", "score": 0.87, "box": [x0, y0, x1, y1]}]}` (box normalized to 0–1) |
| `sensor/vision/pose` | vision → | 0 | no | `{"frame_ts": …, "score": 0.7, "keypoints": [[x, y, score], …]}` (17 points, COCO order) |
| `sensor/vision/status` | vision → | 1 | yes (LWT `offline`) | `{"status": "online", "tpu": true, "mode": "detect", "fps": 18.2, "inference_ms": 9.5}` |
| `vision/mode` | Arbiter/UI → vision | 1 | never | `{"id": "…", "mode": "pose", "ts": …}` |

Imprinting (show an object, assign a name, learn) is operated via the
admin page of the web-service (section 8.4). The learned classes are stored under
`/var/lib/picrawler/vision/`.

### 7.5 Behaviour in the arbiter

New rules, all with priority `goal` unless stated otherwise:

- **Follow me** (`detect`, label `person`, highest score):
  - Box centre more than 0.15 away from the image centre → `turn left` or
    `turn right`
  - otherwise, as long as the box height is < 0.5 → `forward`
  - otherwise → stay put
  - Person not seen for more than 2 s → `stop`
  - New command only once `state/motion` reports `idle` again. This way
    the queue is not flooded with commands.
- **Gestures** (`pose`): wrist above the head → `stand`, waving (sideways
  movement of the wrist over 1 s) → `wave`. Each gesture must be detected in 3
  consecutive frames to avoid false triggers.
- **Search** (`imprint`): turn slowly until the learned object is detected,
  then approach as in "Follow me".
- The ultrasonic stop (`safety`) always overrides all vision rules.
- `sensor/vision/status` is `offline` or `tpu: false` → all
  vision rules are considered "unknown" and trigger nothing.

### 7.6 Constraints

- **Software risk:** The official `pycoral` only supports up to
  Python 3.9, Raspberry Pi OS Bookworm has 3.11. Which path is taken is
  decided by spike M-V0 (section 11, `spikes/coral/`):
  1. Community build (e.g. `pycoral-pi5`)
  2. `tflite-runtime` or LiteRT with the Edge TPU delegate (`libedgetpu`),
     possibly with a separate Python via `uv`
  3. Inference in a container with an older Debian. The camera stays
     outside, and the frames are handed over locally (additional service).
- **Without TPU:** The service still starts, delivers the stream and reports
  `tpu: false`. No inference on the CPU as a silent fallback, so that the CPU
  stays free for motion and stream.
- **Power:** `libedgetpu` with standard clock instead of maximum clock. Measure consumption
  and voltage drops under load on the battery (servos + TPU +
  camera + stream simultaneously).
- **Privacy:** no recording by default, stream only on the LAN with
  login, no identification of persons.

## 8. Web interface (web-service)

Separate service `services/web/`, decoupled from the vision-service. Reason: the
vision-service should only own the camera and TPU, and admin functions such as
Bluetooth need different permissions than the camera. If the interface crashes,
stream generation and detection keep running, and vice versa.

### 8.1 Structure

```
Browser ──HTTP──► web-service :8080 ─┬─ /stream.mjpg ──► vision-service 127.0.0.1:8081 (Proxy)
   ▲                                 ├─ MQTT (user ui): sensor/#, state/#, status, event/#
   └──── Server-Sent Events ─────────┤                cmd/stop, cmd/action, vision/mode
                                     └─ BlueZ (D-Bus)  ──► Bluetooth controller
```

- The vision-service now listens only on `127.0.0.1`. From outside, the
  stream is reachable exclusively via the web-service (with login).
- Live data (log, status, Bluetooth scan) goes to the browser via **Server-Sent Events**.
  This is simpler than WebSockets and sufficient, because
  commands from the browser arrive as normal POST requests.
- Technology: `aiohttp` (one package for HTTP, SSE and the stream proxy, also available as
  `python3-aiohttp` via apt), BlueZ via `dbus-fast`, frontend as
  static HTML/CSS/JS **without a build step and without CDN**. The page must
  work without internet.

### 8.2 Pages

| Page | Role | Content |
|---|---|---|
| **Live** | viewer | Video, drawn boxes (toggleable), status bar (vision online/TPU/FPS/inference time, body status, gamepad connected), **emergency stop button** (always visible), live log of the latest events |
| **Log** | viewer | Object log with filter (label, time range, minimum score), CSV export |
| **Admin** | admin | Bluetooth controller, vision mode, services, system |

The browser draws the boxes onto a canvas over the video, using the data
from `sensor/vision/detections`. This keeps the stream unchanged, and no
second, edited video stream has to be encoded on the Pi.

### 8.3 Object log

Detections arrive at up to 10 Hz. Writing one entry per frame
would be unreadable. The web-service therefore aggregates them into **sightings**:

- **Start:** A label is detected in 3 consecutive frames with
  score ≥ `min_score` (default 0.5).
- **End:** The label has not been detected for 2 s.
- Several objects with the same label count as one sighting; the count
  is recorded as `max_count`. There is no tracking of individual
  objects.
- Entries: `start`, `end`, `label`, `max_score`, `max_count`, `mode`
  (`detect`/`imprint`). Gestures from `pose` appear as separate entries
  (`gesture: wave`), as do body events (`event/rejected`, stops
  caused by the ultrasonic sensor).
- Storage in SQLite under `/var/lib/picrawler/web/log.db`, retention
  by default 7 days or at most 10 000 entries.
- **No storing of images** (in line with 7.6 Privacy). Optionally
  enableable: a small thumbnail per sighting, off by default.

The aggregation lives in the web-service and not in the vision-service, because
the arbiter needs the raw per-frame data.

### 8.4 Admin functions

**Bluetooth controller**
- Start/stop scan (runs at most 30 s), show found devices
  live, sort gamepads to the top based on the device class.
- Pair → trust (`Trusted`, so that the controller later reconnects
  by itself) → connect. For pairing, the web-service registers
  a BlueZ agent with `NoInputNoOutput`. This is sufficient for common gamepads
  (Xbox, PlayStation, 8BitDo).
- List of paired devices with status (connected, battery if reported),
  disconnect, remove.
- This includes a separate **gamepad-service** (`services/gamepad/`) that reads the
  connected controller via `evdev` and publishes under `sensor/gamepad/*`.
  How the buttons are mapped to commands and how
  manual control is prioritized relative to autonomy will be defined in a
  separate section. The ultrasonic stop (`safety`) also applies
  during manual control.

**Vision**: switch mode (`detect`/`pose`/`imprint`/`off`) via
`vision/mode`; imprinting (hold an object in front of the camera, assign a name,
learn, delete learned classes).

**Services**: status of all `picrawler-*` units, restart of individual services,
latest log lines from the journal.

**System**: CPU temperature, throttling (`vcgencmd get_throttled`),
battery voltage if available, shutdown and reboot (with
confirmation dialog). Shutdown makes the crawler sit down first.

### 8.5 Security

- **Login** with session cookie (`HttpOnly`, `SameSite=Strict`). Passwords
  as scrypt hash in `web.toml`, set via Ansible Vault.
- **Two roles:** `viewer` (video, log, emergency stop) and `admin` (additionally
  everything from 8.4). The emergency stop is available to every logged-in person.
- All modifying requests only via POST with CSRF token.
- LAN only. HTTPS optional with own certificate. Without HTTPS,
  password and cookie travel in plain text over the Wi-Fi. This is
  acceptable on your own network, but must be explicitly confirmed in the configuration (like
  `mosquitto_allow_plaintext_remote`).
- **Least privilege:** The service runs as its own user
  `picrawler-web` and gets only what it needs:
  - Group `bluetooth`, so that it may control BlueZ via D-Bus.
  - A **polkit rule** that allows exactly restart/status of the
    `picrawler-*` units as well as shutdown/reboot of the system.
    No `sudo`, no shell commands with input from the browser.
  - MQTT user `ui` (ACL already exists in Ansible).
- Failed logins are rate-limited (e.g. 5 attempts per minute
  per IP).

### 8.6 Configuration (`web.toml`)

```toml
[http]
listen = "0.0.0.0:8080"
allow_plaintext = true          # confirm explicitly when running without HTTPS
vision_stream = "http://127.0.0.1:8081/stream.mjpg"

[users.admin]
role = "admin"
password_hash = "scrypt$…"      # from Ansible Vault

[log]
min_score = 0.5
start_frames = 3
end_after_s = 2.0
retention_days = 7
max_entries = 10000
thumbnails = false
```

### 8.7 Tests

- Aggregation into sightings: start after 3 frames, end after 2 s,
  short dropouts extend the sighting, `max_count`/`max_score`.
- Permissions: viewer cannot reach any admin route, POST without CSRF token is
  rejected, emergency stop works for viewer.
- Bluetooth with a fake BlueZ object: scan, pairing, pairing errors
  are shown instead of swallowed.
- Development on a Mac without a Pi: `python -m services.web --fake` delivers a
  test image instead of the stream and generates random detections.

## 9. Configuration (`body.toml`)

```toml
robot_id = "1"

[mqtt]
host = "localhost"
port = 1883
username = "body"
password_file = "/etc/picrawler/mqtt.pass"
tls = false

[executor]
queue_max = 8
lockout_s = 1.0

[watchdog]
heartbeat_timeout_s = 1.0
sit_after_disconnect_s = 5.0

[limits]
steps_max = 10
actions = ["stand", "sit", "forward", "backward", "turn left", "turn right", "wave"]
```

## 10. Tests

- **Executor** (without hardware, with `tests/fake_robot_hat.py`): order,
  abort by higher priority between frames, upper limit of the
  queue, lockout period, errors in the worker, `shutdown` sits down.
- **Schema**: all rejection reasons from 4.3.
- **Adapter**: with a fake MQTT client: ignore retained commands,
  heartbeat timeout, connection loss, dedupe.
- **Integration** (optional, local Mosquitto): message to
  `sensor/ultrasonic/distance` with `cm = 5` → stop within one
  frame duration.
- **On the device**: measure the reaction time of a stop at `speed` 40/70/100 and
  record it here.

## 11. Milestones

| # | Content | Acceptance |
|---|---|---|
| M1 ✅ | `iter_action`, `MotionExecutor` | Existing tests green, executor tests green, `do_action` behaves unchanged |
| M2 ✅ (device test pending) | `services.body`: adapter + schema + watchdog | Adapter tests green, body service starts without movement, stop via MQTT works on the device |
| M3 ✅ (device test pending) | `services.arbiter` + `services.sensor_ultrasonic`, optional `[wander]` | Robot walks forward and stops in front of an obstacle (< 15 cm) |
| M4 | Further sensors / external control | New sensor connected without changes to `picrawler/` |
| M-V0 | Spike Coral software (independent of M1–M3, can start immediately) | SSD MobileNet v2 runs on the TPU on the target Pi, measured inference time and chosen installation path are recorded here |
| M5 | vision-service: stream + `detect` + status | Stream in the browser with login, detections under `sensor/vision/detections`, stream keeps running when the TPU is unplugged |
| M6 | Follow me | Crawler follows a person, stops after 2 s of loss and at an obstacle |
| M7 | `pose` gestures + imprinting | Raise arm → stand up, wave → wave, learned object is found |
| M-W1 | web-service: login, live page, object log, emergency stop (can be developed independently of M5 with `--fake`) | Sighting tests green, stream and log in the browser, emergency stop stops the crawler, viewer cannot access admin pages |
| M-W2 | Admin: Bluetooth, vision mode, services, system + gamepad-service | Controller can be paired via the page and reconnects by itself after reboot, crawler controllable via gamepad, ultrasonic stop also takes effect then |

## 12. Hardware (decided)

- **Raspberry Pi 5, 8 GB.** No H.264 hardware encoder, hence MJPEG for
  the stream (section 7).
- **Coral Edge TPU as M.2 card over PCIe.** Requires a 4K-page kernel,
  `dtparam=pciex1`, gasket driver and a udev rule (group `apex`). The
  user `picrawler` needs the group `apex`. Setup and measurements:
  `spikes/coral/README.md`.
- **Original PiCrawler camera module** via the Pi 5 adapter cable
  (22-pin to 15-pin), accessed via libcamera/`picamera2`.
- Open: whether the M.2 board and Robot HAT fit together mechanically.

## 13. Open questions

- Which sensors run on which device? If all are attached to the Pi, an
  in-process event bus is enough at first, and MQTT only comes with M2.
- Where does the broker run (on the Pi itself or another machine on the network)?
- How long does a frame actually take at typical `speed`? This determines
  whether the reaction time of a stop is sufficient.
- Should Home Assistant or similar be connected in addition to the own web interface (section 8)?
- Which controller exactly? Button mapping and pairing depend on it.
- Manual control via gamepad: separate priority `manual` between `goal` and
  `reflex`, or does it switch off autonomy completely?
- How long does a model switch (`detect` ↔ `pose`) take on the TPU?
