# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Motion-only fork of the SunFounder PiCrawler library: inverse/forward kinematics, gait generation and servo control for the 4-legged robot on Raspberry Pi. Voice/LLM, audio, camera and sensor code was intentionally removed from the `picrawler` package — do not re-add it there. Sensors, MQTT and anything with networking live in `services/` (one process each). Target design: `docs/target-architecture.md` (German). Version 3.0.0.

## Build / install / test

```bash
sudo pip3 install . --break-system-packages            # on the Pi
python3 -m unittest discover -s tests -t .             # anywhere, no hardware
```

Runtime dependency `robot_hat` (2.5.x, installed from source) is only needed by `picrawler/picrawler.py`. Examples additionally need `readchar` (`pip install .[examples]`).

## Architecture

```
picrawler/
  __init__.py     # exports kinematics, MoveList, __version__; Picrawler lazily (PEP 562)
  kinematics.py   # pure functions: coord2polar, polar2coord, leg_servo_angles, limit_angle
  gaits.py        # MoveList(dict): gaits as @property, frames of 4 [x, y, z] leg tips
  picrawler.py    # Picrawler(robot_hat.Robot): do_step, do_action, iter_action, set_angle, calibration
  executor.py     # MotionExecutor: sole owner of the crawler, priorities GOAL<REFLEX<SAFETY
services/         # python -m services.<name> --config x.toml [--fake]; needs Python 3.11 (tomllib)
  common/         # config, paho wrapper (Bus), schema validation
  body/           # BodyAdapter: MQTT <-> executor, watchdog
  arbiter/        # Arbiter: rules, heartbeat, optional wander
  sensor_ultrasonic/
deploy/ansible/   # Pi provisioning; unit names/configs must match services/
tests/            # unittest; tests/fake_robot_hat.py stands in for the hardware
examples/         # motion demos, must run on the robot with sudo
```

- **Servo-order pitfall:** `coord2polar()` returns `[alpha, beta, gamma]` (femur, knee, hip) but each leg's servos are wired `[beta, alpha, gamma]`. `polar2coord()`, `limit_angle()` and `set_angle()` take servo order. Use `leg_servo_angles()`.
- `MoveList.__getitem__` resolves `"turn left"` → `turn_left` via `getattr` (never `eval`); names starting with `_` are rejected.
- `@check_stand` prepends `stand` frames when not standing; `@normal_action(mode)` swaps legs based on `stand_position`, which `Picrawler.do_action` toggles for walking/turning gaits.
- Gait properties have side effects on `MoveList` state (`z_current`, `ready_state`); don't evaluate them just to check existence.
- `Picrawler.step_list` holds `stand`/`sit` frames snapshotted once in `__init__`.
- Leg order everywhere: right front, left front, left rear, right rear. Units: mm and degrees.
- Only the `MotionExecutor` worker thread may call the `Picrawler`; it checks for abort between frames.
- Service logic (`BodyAdapter`, `Arbiter`) is transport-agnostic: `on_message(suffix, payload, retain)`, `tick()`, injected `publish(suffix, payload, qos, retain)` and clocks. Keep paho inside `services/common/mqtt.py` so tests need no broker.
- `cmd/stop` must never be rejected. GOAL commands need a recent `cmd/heartbeat`.
- MQTT topics/ACLs are mirrored in `deploy/ansible/roles/mosquitto/templates/picrawler.acl.j2`; update both together.

## Calibration data

Servo offsets are stored at `~/.config/.picrawler.config`, read/written by `robot_hat.Robot` via the `db` parameter.
