# Services

Each service is its own process, started with
`python -m services.<name> --config <file>.toml`. On the Pi, systemd does
this (units from `deploy/ansible`). Background: `docs/target-architecture.md`.

| Service | Job | MQTT user |
|---|---|---|
| `services.body` | the only process with access to the servos: validates commands, `MotionExecutor`, watchdog | `body` |
| `services.arbiter` | sensor readings → commands by rules, heartbeat, gamepad control, optional "wander" | `arbiter` |
| `services.sensor_ultrasonic` | measures and publishes the distance | `sensor-ultrasonic` |
| `services.sensor_gamepad` | reads any Bluetooth/USB gamepad via evdev, publishes stick and buttons | `sensor-gamepad` |
| `services.vision` | camera, MJPEG stream on 127.0.0.1:8081, object detection on the Coral | `sensor-vision` |
| `services.web` | login, live video with boxes, object log, emergency stop, admin (Bluetooth, vision mode, services, metrics) | `ui` |
| `services.common` | config, MQTT connection, message validation | – |

The logic of each service lives in a class without networking
(`BodyAdapter`, `Arbiter`); `__main__.py` only connects it to MQTT. That is
why all tests run without a broker and without hardware:
`python3 -m unittest discover -s tests -t .`

## First test on the Pi (by hand)

Prerequisite: Ansible has run once (`picrawler_services_start: false`).

```bash
sudo -u picrawler -s
cd /opt/picrawler/src
V=/opt/picrawler/venv/bin/python

# 1. Body alone: the servos move to the start position, then no movement
$V -m services.body --config /etc/picrawler/body.toml

# 2. In a second terminal: arbiter and sensor
$V -m services.arbiter --config /etc/picrawler/arbiter.toml
$V -m services.sensor_ultrasonic --config /etc/picrawler/sensor-ultrasonic.toml
```

Watch the traffic (password from `/etc/picrawler/mqtt/arbiter.pass`):

```bash
mosquitto_sub -h 127.0.0.1 -u arbiter -P "$(cat /etc/picrawler/mqtt/arbiter.pass)" -t 'crawler/#' -v
```

Hold a hand in front of the sensor: `cmd/stop` with `"reason": "obstacle"`
appears.

To try wandering: set `enabled = true` under `[wander]` in
`/etc/picrawler/arbiter.toml` and restart the arbiter (permanently via the
Ansible variable `picrawler_wander_enabled`). The crawler walks forward,
turns away below `clear_cm` and stops below 15 cm. **The first time, keep
your hand on the emergency stop:** `Ctrl+C` in the body terminal makes it
sit down.

If everything works, set `picrawler_services_start: true` in Ansible.

## Web UI

On the Pi: `https://<pi>:8080` (self-signed certificate, accept it once).
Users come from `picrawler_web_users` in Ansible; create a password hash with

```bash
python3 -m services.web.hashpw
```

Roles: `viewer` sees video and log and may press STOP, `admin` additionally
gets the admin page. `Esc` on any page also sends a stop.

Pairing a controller: Admin → "Search", put the pad into pairing mode,
"Pair". The "Gamepad input" panel shows the button names for
`picrawler_manual_buttons`.

## On a Mac without a Pi

The web UI alone, without broker, Bluetooth or systemd (random detections,
fake gamepad, test card video):

```bash
pip install aiohttp
python3 -m services.web.hashpw            # paste the hash below
cat > /tmp/web.toml <<'TOML'
[http]
listen = "127.0.0.1:8080"
[users.admin]
role = "admin"
password_hash = "scrypt$..."
[log]
path = "/tmp/picrawler-log.db"
[system]
units = ["picrawler-body.service"]
TOML
python3 -m services.web --config /tmp/web.toml --fake   # then open http://127.0.0.1:8080
```

The whole stack:

With a local broker (e.g. `brew install mosquitto`, then
`mosquitto -p 1883`) and configs without a user:

```bash
printf 'robot_id = "1"\n[mqtt]\nhost = "127.0.0.1"\nport = 1883\n' > /tmp/dev.toml
python3 -m services.body --config /tmp/dev.toml --fake
python3 -m services.sensor_ultrasonic --config /tmp/dev.toml --fake
python3 -m services.arbiter --config arbiter-dev.toml   # with [[rule]], [manual], [wander]
python3 -m services.sensor_gamepad --config /tmp/dev.toml --fake
python3 -m services.vision --config /tmp/dev.toml --fake
python3 -m services.web --config web-dev.toml --fake    # web.toml plus the [mqtt] section
```

`--fake` replaces hardware: servo stand-in, a distance swinging between 10
and 70 cm, a gamepad walking a square, a test card with a drifting
"person". With an `[mqtt]` section the services use the broker, otherwise
they run alone. Requires `paho-mqtt>=2`, plus `aiohttp` for the web UI.

## Safety behavior (summary)

- The body starts without moving and only accepts GOAL commands while a
  heartbeat (`cmd/heartbeat`, sent by the arbiter at 4 Hz) is present. If
  it is missing for more than 1 s, a running GOAL motion stops.
- `cmd/stop` is always executed, even with a wrong timestamp or broken
  content. Afterwards, lower priorities are locked out for 1 s.
- Connection to the broker lost → stop, sit down after 5 s.
- `SIGTERM`/`Ctrl+C` → sit down, `status` = `offline`.
- The obstacle stop only fires while walking forward (`when_motion`), so the
  crawler can turn or back away from a wall.
- The gamepad has precedence over wander, never over the safety stop.
