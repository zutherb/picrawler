# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Motion-only fork of the SunFounder PiCrawler library: inverse/forward kinematics, gait generation and servo control for the 4-legged robot on Raspberry Pi. Voice/LLM, audio, camera and sensor code was intentionally removed — do not re-add it here. Version 3.0.0.

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
  picrawler.py    # Picrawler(robot_hat.Robot): do_step, do_action, set_angle, calibration
tests/            # unittest; tests/fake_robot_hat.py stands in for the hardware
examples/         # motion demos, must run on the robot with sudo
```

- **Servo-order pitfall:** `coord2polar()` returns `[alpha, beta, gamma]` (femur, knee, hip) but each leg's servos are wired `[beta, alpha, gamma]`. `polar2coord()`, `limit_angle()` and `set_angle()` take servo order. Use `leg_servo_angles()`.
- `MoveList.__getitem__` resolves `"turn left"` → `turn_left` via `getattr` (never `eval`); names starting with `_` are rejected.
- `@check_stand` prepends `stand` frames when not standing; `@normal_action(mode)` swaps legs based on `stand_position`, which `Picrawler.do_action` toggles for walking/turning gaits.
- Gait properties have side effects on `MoveList` state (`z_current`, `ready_state`); don't evaluate them just to check existence.
- `Picrawler.step_list` holds `stand`/`sit` frames snapshotted once in `__init__`.
- Leg order everywhere: right front, left front, left rear, right rear. Units: mm and degrees.

## Calibration data

Servo offsets are stored at `~/.config/.picrawler.config`, read/written by `robot_hat.Robot` via the `db` parameter.
