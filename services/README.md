# Services

Each service is its own process, started with
`python -m services.<name> --config <file>.toml`. On the Pi, systemd does
this (units from `deploy/ansible`). Background: `docs/target-architecture.md`.

| Service | Job | MQTT user |
|---|---|---|
| `services.body` | the only process with access to the servos: validates commands, `MotionExecutor`, watchdog, obstacle interlock | `body` |
| `services.arbiter` | sensor readings and requests → commands: rules, heartbeat, gamepad/remote, look-around scan (local map), agent goals, optional "wander" | `arbiter` |
| `services.sensor_ultrasonic` | measures and publishes the distance | `sensor-ultrasonic` |
| `services.sensor_gamepad` | reads any Bluetooth/USB gamepad via evdev, publishes stick and buttons | `sensor-gamepad` |
| `services.vision` | camera, MJPEG stream on 127.0.0.1:8081, object detection on the Coral (with bearing and floor distance), free floor ahead for the local map | `sensor-vision` |
| `services.web` | login, live video with boxes, object log, emergency stop, agent input, admin (Bluetooth, vision mode, services, metrics), MQTT debug page | `ui` |
| `services.sensor_speech` | USB microphone, voice activity detection, whisper.cpp → text | `sensor-speech` |
| `services.agent` | offline language model (Gemma 4 E2B via LiteRT-LM) with robot tools; asks the arbiter for movement; keyword parser as fallback | `agent` |
| `services.voice` | speaks the agent's replies with Piper | `voice` |
| `services.display` | 128x64 I2C OLED: Wi-Fi, IP/temperature, Bluetooth, bars for battery, CPU, RAM, SD, NVMe | `display` (battery only) |
| `services.common` | config, MQTT connection, message validation, broker ACL (`topics.py`) | – |

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
Away from home the Pi opens its own Wi-Fi `PiCrawler-1` (password from the
vault); connect your phone and open `https://10.42.0.1:8080`. The OLED shows
the address.
Users come from `picrawler_web_users` in Ansible; create a password hash with

```bash
python3 -m services.web.hashpw
```

Roles: `viewer` sees video and log and may press STOP, `admin` additionally
gets the admin page. `Esc` on any page also sends a stop.

Remote control: the "Remote" page (admin) has a joystick, WASD/arrow keys,
action buttons and a speed slider, and also takes a gamepad plugged into
the computer you browse from. It only moves while you hold a control.

Pairing a controller: Admin → "Search", put the pad into pairing mode,
"Pair". The "Gamepad input" panel shows the button names for
`picrawler_manual_buttons`.

## Voice and agent

Speak with the wake word first: "Crawler, geh zwei Schritte vorwärts",
"Crawler, such die Katze", "Crawler, was siehst du?". "Stopp" or "halt"
always stops at once, without waiting for the model. Admins can also type
requests on the live page.

The agent model is a `.litertlm` file of Gemma 4 E2B from the
`litert-community` organisation on Hugging Face; set its download URL as
`picrawler_agent_model_url`. Without it the agent still understands simple
commands through the keyword parser. A stronger model on a Mac can be added
with `picrawler_agent_remote_url` (OpenAI-compatible server, e.g.
`llama-server` or Ollama); it is skipped while unreachable.

Needs a USB microphone (`arecord -l` must list it). The speaker is the
Robot HAT's.

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
python3 -m services.sensor_speech --config /tmp/dev.toml --fake   # scripted phrases
python3 -m services.agent --config /tmp/dev.toml --fake           # keyword parser only
python3 -m services.voice --config /tmp/dev.toml --fake           # prints instead of speaking
python3 -m services.display --config /tmp/dev.toml --fake         # writes /tmp/picrawler-display.png (needs Pillow)
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
- Battery below 6.6 V for 20 s → sit down, motion refused until charged.
- The obstacle stop only fires while walking forward (`when_motion`), so the
  crawler can turn or back away from a wall. The body has its own interlock
  for the same case (`[interlock]` in body.toml), so this also holds when
  the arbiter is down.
- Precedence of goal sources in the arbiter: gamepad/remote > scan > agent >
  wander. None of them beats the safety stop.
- Broker rights per service are defined in `services/common/topics.py`.
  After changing it, regenerate the Ansible vars:
  `python -m services.common.topics > deploy/ansible/roles/mosquitto/vars/main.yml`.
