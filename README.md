<p align="center"><img src="docs/logo.svg" width="160" alt="PiCrawler logo"></p>

# Picrawler motion framework

A stripped-down fork of [sunfounder/picrawler](https://github.com/sunfounder/picrawler)
that only contains the robot's motion stack: leg kinematics, gait
definitions and servo control. Voice/LLM, audio, camera and sensor code
has been removed from the library. On top of it, `services/` adds an
MQTT-based service stack (body, arbiter, sensors), see
[docs/target-architecture.md](docs/target-architecture.md).

## Layout

```
picrawler/
  kinematics.py   # pure math: coord2polar / polar2coord / limits (no hardware)
  gaits.py        # MoveList: all gaits as frames of 4 [x, y, z] leg tips (no hardware)
  picrawler.py    # Picrawler(robot_hat.Robot): drives the 12 servos
  executor.py     # MotionExecutor: one worker thread, priorities, stop between frames
services/         # MQTT microservices: body, arbiter, sensors, vision, web UI (see services/README.md)
deploy/ansible/   # provisions the Pi: Mosquitto, venv, systemd units
tests/            # run off-hardware with a fake robot_hat
examples/         # motion-only demos (need the real robot)
```

`kinematics` and `MoveList` can be imported without `robot_hat`, so you
can plan or test motions on any machine. `Picrawler` is imported lazily and
needs `robot_hat` on the Raspberry Pi.

## Installation

```bash
cd ~/
git clone -b 2.5.x --depth=1 https://github.com/sunfounder/robot-hat.git
cd robot-hat && sudo python3 install.py
sudo pip3 install ~/picrawler --break-system-packages
# for the keyboard examples:
sudo pip3 install readchar --break-system-packages
```

## Usage

```python
from picrawler import Picrawler

crawler = Picrawler()
crawler.do_step('stand', 40)
crawler.do_action('forward', 2, 70)          # named gait, repeated
crawler.do_step([[45, 45, -50]] * 4, 60)     # one frame of leg-tip coords (mm)
crawler.do_single_leg(0, [60, 10, -40], 70)  # move one leg
crawler.add_action('hop', [[[50, 50, -50]] * 4, [[50, 50, -40]] * 4])
crawler.do_action('hop')
crawler.do_step('sit', 40)
```

Leg order in every frame: right front, left front, left rear, right rear.

Built-in gaits: `stand`, `sit`, `forward`, `backward`, `turn left`,
`turn right`, `turn left angle`, `turn right angle`, `wave`, `push up`,
`dance`, `look left`, `look right`, `look up`, `look down`.

Interruptible execution from other threads:

```python
from picrawler.executor import Command, MotionExecutor, Priority

ex = MotionExecutor(crawler)
ex.start()
ex.submit(Command("action", "forward", steps=5, speed=70))
ex.stop()                 # aborts between two frames, locks out lower priorities for 1 s
ex.shutdown(sit=True)
```

Off-hardware kinematics:

```python
from picrawler import kinematics, MoveList

kinematics.leg_servo_angles([45, 45, -50])  # servo-order angles of one leg
MoveList()['forward']                        # frames of the forward gait
```

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

## Examples

Run with `sudo` on the robot:

| Example | Description |
|---------|-------------|
| `0_calibration.py` | Servo calibration helper |
| `1_move.py` | Walk and turn |
| `2_keyboard_control.py` | W/A/S/D keyboard control |
| `9_do_step.py` | Custom step coordinates |
| `10_do_single_leg.py` | Adjust a single leg |
| `11_record_new_step.py` | Record and replay steps |
| `12_twist.py` | Twist body motion |
| `14_preset_actions.py` | Poses (wave, look, push up, …) |
| `servo_zeroing.py` | Set all servos to 0° for assembly |

## License

GPLv3, like the upstream project.
