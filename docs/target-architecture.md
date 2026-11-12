# Target architecture: sensor-driven body over MQTT

Status: draft · As of: 2026-11-12 · Branch: `motion-framework`

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
 Sensors / inputs               Arbiter                   Body service (Pi)
┌────────────────────┐ sensor/* ┌──────────────┐  cmd/*  ┌──────────────────────────┐
│ ultrasonic         │ ───────► │ Rules        │ ──────► │ services.body (adapter)  │
│ gamepad            │          │ Manual       │         │   validation, watchdog   │
│ vision + Coral     │          │ Wander       │ ◄────── │   battery (ADC A4)       │
│ web remote         │          │ Heartbeat    │ state/* │ picrawler.executor       │
└────────────────────┘          └──────────────┘         │   MotionExecutor thread  │
                                                         │ picrawler.Picrawler      │
 USB mic ─► sensor_speech ─► agent ──── cmd/action, ────►│                          │
            (whisper.cpp)    (Gemma)    cmd/stop         └──────────────────────────┘
                               └─► agent/reply ─► voice (Piper)

 Observers: web (UI, admin, MQTT debug, OLED mirror) · display (OLED, works without MQTT)
 Clients:   browser, iPhone/iPad app (same HTTP API), found via avahi or the robot's hotspot
```

All services run on the Pi and talk through the local Mosquitto broker
(per-role users and ACLs, section 4). Sensors on other devices (e.g. ESP32)
connect to the same broker with the `sensor` role.

| Component | Location | Responsibility | Must not |
|---|---|---|---|
| `picrawler` (core) | this repo | Kinematics, gaits, servos | import MQTT, start threads |
| `picrawler.executor` | this repo | Execute commands one after another, interruptible between frames | network access |
| `services.body` | own process | MQTT ↔ executor, validation, watchdog, status, battery protection | drive servos bypassing the executor |
| `services.arbiter` | own process | Sensor facts → commands by priority, heartbeat, Manual (gamepad, web remote), Wander | pass through `cmd/*` from sensors |
| `services.sensor_ultrasonic` | own process | Publish distances | publish under `cmd/*` |
| `services.sensor_gamepad` | own process | Read any evdev controller, publish normalized inputs (section 8.8) | publish under `cmd/*` |
| Other sensor nodes | anywhere | Publish measurements | publish under `cmd/*` |
| `services.vision` | Pi with camera + Coral | Camera, MJPEG stream (127.0.0.1 only), inference on the TPU, publish detections (section 7) | decide behaviour, send commands |
| `services.web` | own process | Login, live page, stream proxy, object log, remote, emergency stop, admin, MQTT debug, OLED mirror (section 8) | drive servos or camera directly |
| `services.sensor_speech` | own process | Microphone, VAD, whisper.cpp client, publish transcripts (section 8a) | send commands |
| `services.agent` | own process | Wake word, stop fast path, language model with tools; sends goals as `cmd/action` / `cmd/stop` (section 8a) | bypass the arbiter's reflexes or the body's lockout |
| `services.voice` | own process | Speak replies with Piper | — |
| `services.display` | own process | OLED status from `/proc` and `/sys`, battery from MQTT, publish `display/frame` (section 12) | depend on MQTT for its basic picture |
| `netwatch` | Ansible `network` role | Wi-Fi hotspot fallback, state in `/run/picrawler/network.json` (section 12) | — |
| iOS app | `apps/ios` | Remote control via the web service's HTTP API (section 12) | talk MQTT or reach the servos another way |

Package structure:

```
picrawler/
  kinematics.py
  gaits.py
  picrawler.py        # Picrawler, iter_action()
  executor.py         # MotionExecutor
services/             # one process per service: python -m services.<name> --config …
  common/             # config.py, mqtt.py (paho wrapper), schema.py (payload validation), metrics.py
  body/               # adapter.py (BodyAdapter), battery.py, __main__.py
  arbiter/            # arbiter.py (rules, Manual, Wander), __main__.py
  sensor_ultrasonic/  # __main__.py
  sensor_gamepad/     # gamepad.py (normalization), __main__.py (evdev), section 8.8
  vision/             # camera + Coral, see section 7
    camera.py         # picamera2: main stream + lores stream; test card for --fake
    stream.py         # MJPEG over HTTP, 127.0.0.1 only
    inference.py      # Edge TPU detector (pycoral / tflite_runtime / ai_edge_litert)
    __main__.py       # imprint.py (learn custom objects) is planned
  web/                # web interface, see section 8
    app.py            # aiohttp: routes, login, SSE, stream proxy
    auth.py           # scrypt, sessions, rate limit; hashpw.py prints a hash
    state.py          # MQTT messages → status snapshot, log, SSE pushes
    monitor.py        # recent MQTT traffic for the debug page
    sightings.py      # detections → sightings (object log)
    store.py          # SQLite
    bluetooth.py      # bluetoothctl
    system.py         # units/journal/power via systemctl + polkit, system info
    static/           # HTML, CSS, JS, without CDN or inline scripts (CSP)
  sensor_speech/      # audio.py (mic + VAD), asr.py (whisper-server client)
  agent/              # agent.py, brains.py (remote, LiteRT-LM, keywords), tools.py, world.py
  voice/              # Piper TTS
  display/            # sysinfo.py, render.py (128x64 1-bit), splash.py, assets/
apps/
  ios/                # SwiftUI app, XcodeGen project.yml
deploy/ansible/       # roles: base, network, mosquitto, storage, picrawler
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
| `event/stop` | Body → | 1 | no (`{"reason", "sit"}`, for the object log) |
| `state/battery` | Body → | 1 | yes (`{"present", "voltage", "percent", "level"}`, every 5 s) |
| `event/battery` | Body → | 1 | no (level changes) |
| `sensor/gamepad/state` | gamepad → | 0 | no (`{"x", "y", "buttons"}`, x/y in −1..1, up is negative) |
| `sensor/gamepad/status` | gamepad → | 1 | yes (LWT `offline`, plus `connected`, `name`) |
| `web/status` | web → | 1 | yes (LWT `offline`) |
| `display/frame` | display → web | 0 | yes (`{"w", "h", "data"}`, 1-bit pixels base64, only when the picture changes) |
| `remote/state` | web → arbiter | 0 | no (`{"x", "y", "speed"?, "action"?, "user"}`, while a remote control is held) |

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

### 7.7 Implementation status

- Implemented: modes `detect` and `off`, MJPEG stream on `127.0.0.1:8081`
  (`/stream.mjpg`, `/snapshot.jpg`), status every 2 s, detections limited
  to `max_rate_hz`. Default model: the Coral test model SSD MobileNet v2
  (COCO, 300×300), downloaded by Ansible.
- `pose` and `imprint` are accepted by the UI but answered with an `error`
  in `sensor/vision/status` until they are built (M7).
- The TPU route is not fixed yet (spike M-V0); `inference.py` tries
  pycoral, tflite_runtime and ai_edge_litert with the `libedgetpu` delegate.
  If none loads, the service streams and reports `tpu: false`.
- picamera2's `RGB888` is B, G, R in memory; the lores frame is flipped to
  RGB before inference.

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
                                     └─ bluetoothctl   ──► Bluetooth controller
```

- The vision-service now listens only on `127.0.0.1`. From outside, the
  stream is reachable exclusively via the web-service (with login).
- Live data (log, status, Bluetooth scan) goes to the browser via **Server-Sent Events**.
  This is simpler than WebSockets and sufficient, because
  commands from the browser arrive as normal POST requests.
- Technology: `aiohttp` (one package for HTTP, SSE and the stream proxy, also available as
  `python3-aiohttp` via apt), BlueZ via `bluetoothctl` (no extra Python
  dependency; arguments are passed as lists, MAC addresses validated), frontend as
  static HTML/CSS/JS **without a build step and without CDN**. The page must
  work without internet.

### 8.2 Pages

| Page | Role | Content |
|---|---|---|
| **Live** | viewer | Video, drawn boxes (toggleable), status bar (vision online/TPU/FPS/inference time, body status, gamepad connected), **emergency stop button** (always visible), live log of the latest events |
| **Log** | viewer | Object log with filter (label, time range, minimum score), CSV export |
| **Admin** | admin | Bluetooth controller, vision mode, services, system |
| **Remote** | admin | Remote control: video, virtual joystick (mouse/touch), WASD/arrow keys, a gamepad on the viewing computer (browser Gamepad API), action buttons, speed slider, big STOP. Sends stick facts on `remote/state` while a control is held |
| **MQTT** | admin | Debug view: topic tree of everything under `crawler/<id>/` (counts, retained marker, live flash) on the left, messages of the selected topic or subtree on the right (time with ms, QoS, size, JSON formatted), filter, pause, clear. Read-only, no publishing |

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
- This includes a separate **gamepad-service** (`services/sensor_gamepad/`)
  that reads the connected controller via `evdev` and publishes under
  `sensor/gamepad/*`. Button mapping and priority: section 8.8.
- **Gamepad input** panel: live stick values and names of pressed buttons,
  to adapt the mapping to any controller.

**Vision**: switch mode (`detect`/`pose`/`imprint`/`off`) via
`vision/mode`; imprinting (hold an object in front of the camera, assign a name,
learn, delete learned classes).

**Services**: status of all `picrawler-*` units, restart of individual services,
latest log lines from the journal.

**System**: CPU usage, load, memory, disk, CPU temperature, throttling
(`vcgencmd get_throttled`), memory per service (systemd `MemoryCurrent`),
refreshed every 2 s; shutdown and reboot (with confirmation dialog).
Shutdown makes the crawler sit down first. The battery is shown in the
header of every page and as a tile on the live page.

**Metrics history**: no Prometheus on the Pi (RAM and SD card writes for a
single robot). The admin page reads `/proc` directly. For history over days,
install `node_exporter` on the Pi (~15 MB RAM) and run Prometheus/Grafana on
another machine that scrapes it.

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
  - MQTT user `ui`: reads everything under the robot's prefix (for the MQTT
    debug page), writes only `cmd/action`, `cmd/stop`, `cmd/heartbeat`,
    `vision/mode`, `agent/ask` and its own status.
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

### 8.8 Manual control (gamepad)

Decided: the gamepad takes **precedence over autonomy**, but stays below the
safety layer.

- `services.sensor_gamepad` picks up any device with two stick axes and
  joystick/gamepad buttons (deliberately loose, generic Bluetooth pads often
  report `BTN_TRIGGER`/`BTN_THUMB` instead of `BTN_SOUTH`). `device_name`
  narrows it down. It publishes facts only.
- The web remote page (section 8.2) is a second source on `remote/state`
  with the same stick convention plus an optional one-shot `action` and a
  `speed`. The freshest source drives. A source silent for 1 s counts as
  centered, so a closed browser tab stops the crawler after the current
  step (dead man).
- The arbiter's `Manual` behaviour turns them into goal commands, one at a
  time while the body is idle: stick up/down → `forward`/`backward`,
  left/right → `turn left`/`turn right` (threshold 0.5), button edges →
  actions from `[manual] buttons`, `stop_button` → `cmd/stop`.
- While the pad was used within `hold_s` (3 s), `Wander` pauses.
- The obstacle stop keeps working. It has `when_motion = ["forward"]`, so it
  only fires while walking forward: in front of a wall the crawler can still
  turn or back away instead of being locked out by repeated stops.
- Tested pads: none on the device yet. SteelSeries Nimbus is the target; if
  its button names differ, read them on the admin page and set
  `picrawler_manual_buttons`.

## 8a. Voice and agent

Offline voice control and a language-model agent. Everything runs on the Pi;
an optional stronger model on a machine in the home network is used when it
is reachable. No internet.

```
USB mic ─► sensor_speech ─(sensor/speech/text)─► agent ─(cmd/action, cmd/stop)─► body
           VAD + whisper.cpp                      │  Gemma 4 E2B (LiteRT-LM)
                                                  │  tools: walk, turn, do_action,
web UI ──────────(agent/ask)──────────────────────┤         stop, look, distance, search
                                                  └─(agent/reply)─► voice (Piper) ─► speaker
```

| Service | Job |
|---|---|
| `whisper` | whisper.cpp `whisper-server` on 127.0.0.1:8082, German, model loaded once |
| `sensor_speech` | USB microphone (PyAudio), energy VAD with adaptive noise floor, sends each utterance to whisper, publishes the text. Mutes itself while `voice/speaking` |
| `agent` | wake word filter, stop fast path, one request at a time, brains in order: `[remote]` (OpenAI-compatible, e.g. llama-server/Ollama on a Mac), `[litert]` (Gemma 4 E2B), keyword parser |
| `voice` | Piper TTS, plays replies with `aplay`, switches on the Robot HAT amplifier |

Decisions:

- **Model:** Gemma 4 E2B via LiteRT-LM on the CPU (~1.4 GB RAM, ~9 tok/s
  decode, ~99 tok/s prefill per the Raspberry Pi benchmark). Native tool
  calling; LiteRT-LM runs the Python tool functions itself. Qwen 3.5 2B is
  the alternative (slower, tool calling not documented for LiteRT).
- **Speech-to-text: whisper.cpp**, not Gemma's audio input. Gemma 4 E2B
  accepts audio, but no Pi benchmark or LiteRT-LM audio documentation for
  the Pi was found; Google's own Pi demo uses a separate ASR.
- **Wake word in the transcript** ("Crawler, …") instead of a wake-word
  model: no extra model, whisper only runs when the VAD hears speech.
  Plain room talk is transcribed but neither acted on nor logged.
- **Stop never waits for the model:** "stopp", "halt" etc. trigger
  `cmd/stop` directly and drop queued requests.
- **Tools do the loops, the model decides:** `search` turns step by step
  until the label is detected; `walk forward` refuses below 20 cm. Commands
  are plain goals, so the arbiter's obstacle stop and the body's lockout
  stay in charge; while the agent is busy, wander pauses.
- **CPU budget:** agent and whisper run with `Nice=10`, the agent with
  `CPUQuota=300%`, leaving a core for body, vision and the stream. Active
  cooling is required.
- **Language:** the model answers in the language of the request; fixed
  replies come from a table (`[agent] language = "de"`).

Topics: `sensor/speech/text` `{"text", "language", "duration_s", "asr_s"}`,
`sensor/speech/status`, `agent/ask` `{"text", "user"}` (web UI, admin),
`agent/reply` `{"text", "request", "source", "brain", "seconds"}`,
`agent/status` (retained, `busy`), `voice/speaking` `{"speaking"}`,
`voice/status`.

Not measured yet (milestone M-A1): end-to-end latency on the Pi, tool-call
accuracy of Gemma 4 E2B with these tools, whisper base vs small for German,
CPU contention with vision running.

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
| M5 ✅ (device test pending) | vision-service: stream + `detect` + status | Stream in the browser with login, detections under `sensor/vision/detections`, stream keeps running when the TPU is unplugged |
| M6 | Follow me | Crawler follows a person, stops after 2 s of loss and at an obstacle |
| M7 | `pose` gestures + imprinting | Raise arm → stand up, wave → wave, learned object is found |
| M-W1 ✅ (device test pending) | web-service: login, live page, object log, emergency stop (can be developed independently of M5 with `--fake`) | Sighting tests green, stream and log in the browser, emergency stop stops the crawler, viewer cannot access admin pages |
| M-W2 ✅ (device test pending) | Admin: Bluetooth, vision mode, services, system + gamepad-service | Controller can be paired via the page and reconnects by itself after reboot, crawler controllable via gamepad, ultrasonic stop also takes effect then |
| M-A1 ✅ (device test pending) | Voice and agent: sensor_speech, whisper, agent (Gemma 4 E2B), voice | "Crawler, such die Katze" turns until a cat is detected and answers by voice; "stopp" stops within one frame; latency and tool-call accuracy recorded here |

## 12. Hardware (decided)

- **Raspberry Pi 5, 8 GB.** No H.264 hardware encoder, hence MJPEG for
  the stream (section 7).
- **Pineboards HatDrive! AI**: M.2 NVMe (2230/2242) plus an M.2 E-key
  slot for the Coral, behind an ASMedia PCIe Gen 2 switch on the Pi's
  single lane.
  - **Coral Dual Edge TPU** in the E-key slot. Each of its two TPUs needs
    its own PCIe lane; the HatDrive passes one, so expect **one usable TPU**
    (`/dev/apex_0`). One is plenty here (~10 ms per frame at ≤ 10 Hz).
    Using both would need the Hat AI! Dual (no NVMe).
  - Coral setup: 4K-page kernel, `dtparam=pciex1`, gasket driver, udev rule
    (group `apex`, the user `picrawler` is in it). See `spikes/coral/`.
  - **No booting from the NVMe** behind the switch: the system stays on the
    SD card. The `storage` Ansible role moves everything that writes or is
    large to the NVMe: `/opt/picrawler` (code, venv, whisper.cpp, Piper),
    `/var/lib/picrawler` (models, calibration, agent cache),
    `/var/lib/picrawler-web` (log database), the journal, Mosquitto data,
    the apt cache and the swap file; `/tmp` goes to RAM. The SD card keeps
    `/`, `/boot`, `/etc`, `/usr`. Robot units have `RequiresMountsFor`, so
    without the NVMe they do not start instead of writing to the SD card.
  - Next step if power cuts become a problem: read-only root on the SD card
    (overlay), now possible because nothing robot-related writes there.
- **Original PiCrawler camera module** via the Pi 5 adapter cable
  (22-pin to 15-pin), accessed via libcamera/`picamera2`.
- **Battery**: two Li-ion cells in series (2S, 6.0-8.4 V) on the Robot HAT,
  measured on ADC A4 through a 20k/10k divider (V = A4/4095 x 3.3 x 3;
  `get_battery_voltage()` is avoided, it is broken in some robot_hat
  versions). The **body service** reads it every 5 s while holding the same
  lock the executor holds for servo frames, because both talk to the HAT's
  microcontroller over I2C and must not interleave. It publishes
  `state/battery` (median of the last readings, charge from a 2S Li-ion
  curve). Levels change only after holding 20 s, so the sag while walking
  does not count: `low` below 7.0 V (warning), `critical` below 6.6 V (sit
  down, refuse motion commands except stop). Charge percentages are rough;
  check the voltage against a multimeter once.
- **OLED status display**: AZDelivery 0.96" 128x64, SSD1306, I2C address
  0x3C, on the Robot HAT's I2C bus (no conflict with the HAT at 0x14).
  `services.display` reads `/proc` and `/sys` only (no MQTT, so it also helps
  when everything else is down): header with Wi-Fi bars, the full IP
  alternating every 3 s with the CPU temperature (kept on screen from 75 °C,
  marked HOT) and a Bluetooth icon (crossed out = off/no adapter, plain = on,
  inverted = device connected; from `/sys/class/bluetooth` and rfkill);
  below, bars for CPU (all cores), RAM, SD card and NVMe (when mounted).
  With MQTT (user `display`, reads only `state/battery`) a battery bar comes
  first; a critical battery keeps "BATTERY LOW" on screen. When the NVMe
  is mounted, SD card and NVMe share the last row as two half bars.
  Thresholds (`picrawler_display_thresholds`, default CPU 80/95 %, RAM
  80/92 %, storage 80/95 %, battery 20/5 % or the body's low/critical
  level, temperature 70/80 °C) show as icons because the display has no
  colors: no icon = ok, outlined warning triangle = warning, triangle
  flashing filled/outlined = critical; the temperature is inverted from the
  warning level and flashes when critical.
- **Boot and shutdown splash**: the robot from the project artwork
  (1 bit, white shell lit, blue parts dithered, 50 px high, centered) with
  a blinking state icon below it, ↻ while booting and ⏻ while shutting
  down. A standard-library-only script on the SD card
  (`/usr/local/lib/picrawler/oled-splash.py`) writes pre-rendered frames
  (icon on / off, rendered by Ansible with `services.display.splash`)
  straight to `/dev/i2c-1`:
  - `picrawler-splash.service` starts right after the i2c-dev module loads
    (`DefaultDependencies=no`, before `sysinit.target`), long before the
    NVMe and the robot services, and blinks until the display service
    starts (`Conflicts=`) or for 3 minutes at most.
  - `picrawler-splash-shutdown.service` starts at halt/poweroff/reboot once
    the display service has stopped and blinks until systemd terminates it
    at the very end; it leaves the icon on. The OLED keeps its picture
    while powered, so on a reboot the logo stays visible throughout.
  - A missing display never fails the boot. Bootloader and the first kernel
    seconds cannot be shown without a kernel framebuffer driver; not done.
- **Wi-Fi hotspot fallback** (`network` role): at home the Pi joins the
  known Wi-Fi. If none is connected 45 s after boot (later: 20 s after
  losing it), `picrawler-netwatch` brings up the NetworkManager profile
  `picrawler-hotspot`: SSID `PiCrawler-<robot_id>`, WPA2 with
  `vault_hotspot_psk`, 2.4 GHz channel 6, shared IPv4 with DHCP, the Pi at
  **10.42.0.1** (web UI `https://10.42.0.1:8080`). While nobody is
  connected, every 10 minutes it takes the hotspot down and lets
  NetworkManager try the known networks for 40 s; with a phone connected it
  never switches. State in `/run/picrawler/network.json`: the OLED shows a
  radio-mast icon and the hotspot IP, the admin page a Network tile.
  Bluetooth PAN was considered and rejected: ~1-2 Mbit/s is too little for
  the video stream.
- **iPhone/iPad app** (`apps/ios`, SwiftUI): remote control in the look of
  the web Remote page, same HTTP API (login, CSRF, `/api/remote`,
  `/api/stop`, SSE, MJPEG). Finds the robot via Bonjour (`_picrawler._tcp`,
  announced by avahi) or joins its hotspot (`NEHotspotConfiguration`);
  pins the self-signed certificate on first use.
- Open: whether the HatDrive! AI and the Robot HAT fit together
  mechanically, and the extra power draw of the NVMe on battery.

## 13. Open questions

- How long does a frame actually take at typical `speed`? This determines
  whether the reaction time of a stop is sufficient.
- Should Home Assistant or similar be connected in addition to the own web interface (section 8)?
- Does the SteelSeries Nimbus pair with BlueZ and show up in evdev? If not,
  any other Bluetooth pad works with the same code (section 8.8).
- How long does a model switch (`detect` ↔ `pose`) take on the TPU?
- Do the HatDrive! AI and the Robot HAT fit together mechanically, and how
  much does the NVMe add to the power draw on battery (section 12)?
