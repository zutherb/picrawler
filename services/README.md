# Services

Each service is its own process, started with
`python -m services.<name> --config <file>.toml`. On the Pi, systemd does
this (units from `deploy/ansible`). Background: `docs/target-architecture.md`.

| Service | Job | MQTT user |
|---|---|---|
| `services.body` | the only process with access to the servos: validates commands, `MotionExecutor`, watchdog | `body` |
| `services.arbiter` | sensor readings → commands by rules, heartbeat, optional "wander" | `arbiter` |
| `services.sensor_ultrasonic` | measures and publishes the distance | `sensor-ultrasonic` |
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

## On a Mac without a Pi

With a local broker (e.g. `brew install mosquitto`, then
`mosquitto -p 1883`) and configs without a user:

```bash
printf 'robot_id = "1"\n[mqtt]\nhost = "127.0.0.1"\nport = 1883\n' > /tmp/dev.toml
python3 -m services.body --config /tmp/dev.toml --fake
python3 -m services.sensor_ultrasonic --config /tmp/dev.toml --fake
python3 -m services.arbiter --config arbiter-dev.toml   # with [[rule]] and [wander]
```

`--fake` replaces the servos with a stand-in, or produces an artificial
distance that swings between 10 and 70 cm. Requires `paho-mqtt>=2`.

## Safety behavior (summary)

- The body starts without moving and only accepts GOAL commands while a
  heartbeat (`cmd/heartbeat`, sent by the arbiter at 4 Hz) is present. If
  it is missing for more than 1 s, a running GOAL motion stops.
- `cmd/stop` is always executed, even with a wrong timestamp or broken
  content. Afterwards, lower priorities are locked out for 1 s.
- Connection to the broker lost → stop, sit down after 5 s.
- `SIGTERM`/`Ctrl+C` → sit down, `status` = `offline`.
