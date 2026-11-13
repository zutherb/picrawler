"""Data contract shared by the simulator, the exporter and the robot loop.

Everything that must be identical in simulation and on the robot lives
here: the 32-dim observation layout, the CPG trot, the conversion from
policy output to SunFounder leg coordinates and the int8 (de)quantization.

Frames:
- Body frame: x right, y forward, z up, mm, origin at each leg's hip.
  The base stance and the policy's residual offsets are given in it.
- Leg frame (SunFounder, what Picrawler.do_step() takes): x outward,
  y along the body away from the center, z up. Identical numbers for all
  four legs mean a symmetric pose; body -> leg is a sign flip per leg.

Leg order everywhere: right front, left front, left rear, right rear.
"""
import math

import numpy as np

from picrawler import kinematics

DT = 0.02                   # control period, 50 Hz
GAIT_HZ = 2.0
OBS_DIM = 32
ACT_DIM = 12
OFFSET_MM = 15.0            # policy output 1.0 -> 15 mm

STANCE_BODY = np.array([[60, 60, -50], [-60, 60, -50], [-60, -60, -50], [60, -60, -50]], np.float32)
LEG_SIGNS = np.array([[1, 1], [-1, 1], [-1, -1], [1, -1]], np.float32)     # (sx, sy) per leg
HIP_SIDE_MM = 77.0          # MoveList.LENGTH_SIDE: the hips form a square
HIP_POS_MM = LEG_SIGNS * HIP_SIDE_MM / 2.0

# CPG trot: diagonal pairs (RF+LR, LF+RR) half a cycle apart.
PAIR_PHASE = np.array([0.0, math.pi, 0.0, math.pi])
STRIDE_MM = 20.0            # fore-aft stroke at |vx_cmd| = 1
TURN_MM = 20.0              # tangential stroke at |wz_cmd| = 1
LIFT_MM = 18.0              # swing height when walking

# Command scale: one stroke per stance half-cycle (0.25 s at 2 Hz).
MAX_VX = STRIDE_MM / 1000.0 * 2 * GAIT_HZ                 # 0.08 m/s
MAX_WZ = 0.6                                               # rad/s, roughly TURN_MM at the feet

# Vision features: [vis_roll, vis_pitch, flow_yaw, obstacle_dist]
OBSTACLE_RANGE_M = 1.0      # obstacle_dist 1.0 = free for at least this far
STOP_DIST = 0.15            # no forward command closer than 15 cm (like the body interlock)
SLOW_DIST = 0.40            # full forward command from here on
VISION_TAU_S = 0.5          # roll/pitch are high-passed with this time constant
# All inputs stay within [-1, 1]: one int8 scale per tensor covers the whole vector.
VISION_LIMITS = np.array([[-1.0, 1.0], [-1.0, 1.0], [-1.0, 1.0], [0.0, 1.0]], np.float32)
VISION_DELAY_STEPS = 2      # camera latency, 40 ms at 50 Hz
VISION_NOISE_STD = np.array([0.02, 0.02, 0.05, 0.03], np.float32)


def leak(dt, tau=VISION_TAU_S):
    """Decay factor of the high-pass used for vis_roll / vis_pitch."""
    return math.exp(-dt / tau)


def phase_at(step):
    return (2.0 * math.pi * GAIT_HZ * step * DT) % (2.0 * math.pi)


def effective_vx(vx_cmd, obstacle_dist):
    """Scale a forward command down near an obstacle, zero below STOP_DIST.

    Applied the same way in simulation and on the robot, before the
    command enters the observation and the CPG. Backing up stays allowed.
    """
    if vx_cmd <= 0.0:
        return float(vx_cmd)
    free = (obstacle_dist - STOP_DIST) / (SLOW_DIST - STOP_DIST)
    return float(vx_cmd * min(1.0, max(0.0, free)))


def cpg_body(phase, vx_cmd, wz_cmd):
    """Trot offsets (4, 3) in mm, body frame, added to STANCE_BODY.

    A leg swings while sin(phase_i) > 0: it lifts and moves along the
    commanded direction; on the ground it strokes back and pushes the body.
    """
    vx = float(np.clip(vx_cmd, -1.0, 1.0))
    wz = float(np.clip(wz_cmd, -1.0, 1.0))
    phases = phase + PAIR_PHASE
    stroke = -np.cos(phases)[:, None]                               # -1 .. 1, forward in swing
    out = np.zeros((4, 3), np.float32)
    out[:, 1] = stroke[:, 0] * STRIDE_MM / 2.0 * vx
    tangent = np.stack([-HIP_POS_MM[:, 1], HIP_POS_MM[:, 0]], axis=1)  # counter-clockwise
    tangent /= np.linalg.norm(tangent, axis=1, keepdims=True)
    out[:, :2] += stroke * tangent * TURN_MM / 2.0 * wz
    activity = min(1.0, 4.0 * max(abs(vx), abs(wz)))
    out[:, 2] = np.maximum(np.sin(phases), 0.0) * LIFT_MM * activity
    return out


def body_to_leg(coords_body):
    """(4, 3) body-frame coordinates -> SunFounder leg-frame list for do_step()."""
    c = np.asarray(coords_body, np.float32)
    leg = np.empty_like(c)
    leg[:, 0] = c[:, 0] * LEG_SIGNS[:, 0]
    leg[:, 1] = c[:, 1] * LEG_SIGNS[:, 1]
    leg[:, 2] = c[:, 2]
    return [[round(float(v), 2) for v in row] for row in leg]


def body_coords(phase, vx_cmd, wz_cmd, action):
    """(4, 3) foot positions in the body frame relative to each hip, mm.

    action: 12 values in [-1, 1] from the policy, (dx, dy, dz) per leg in
    the body frame, scaled by OFFSET_MM.
    """
    offsets = np.clip(np.asarray(action, np.float32).reshape(4, 3), -1.0, 1.0) * OFFSET_MM
    return STANCE_BODY + cpg_body(phase, vx_cmd, wz_cmd) + offsets


def foot_targets(phase, vx_cmd, wz_cmd, action):
    """Leg-frame foot targets for one control step (see body_coords)."""
    return body_to_leg(body_coords(phase, vx_cmd, wz_cmd, action))


def stance_mask(phase, vx_cmd, wz_cmd):
    """True for the legs the CPG keeps on the ground at this phase."""
    return cpg_body(phase, vx_cmd, wz_cmd)[:, 2] < 1.0


def build_observation(phase, vx_cmd, wz_cmd, vision, last_action, prev_action):
    """The 32-dim float32 policy input (see docs in rl_coral/README.md)."""
    obs = np.empty(OBS_DIM, np.float32)
    obs[0] = math.sin(phase)
    obs[1] = math.cos(phase)
    obs[2] = np.clip(vx_cmd, -1.0, 1.0)
    obs[3] = np.clip(wz_cmd, -1.0, 1.0)
    obs[4:8] = np.clip(np.asarray(vision, np.float32), VISION_LIMITS[:, 0], VISION_LIMITS[:, 1])
    obs[8:20] = np.clip(last_action, -1.0, 1.0)
    obs[20:32] = np.clip(prev_action, -1.0, 1.0)
    return obs


def servo_angles(leg_coords):
    """Leg-frame coords -> 12 servo angles after the robot's own clamping.

    Same path as Picrawler.do_step(): leg_servo_angles() then limit_angle().
    Returns a (4, 3) array in servo order [knee, femur, hip] per leg.
    """
    return np.array([kinematics.limit_angle(kinematics.leg_servo_angles(c))[1] for c in leg_coords],
                    np.float64)


def joint_angles(servo):
    """Servo angles (4, 3) -> URDF joint angles (4, 3) [hip, femur, knee] in rad.

    URDF convention (rl_coral/picrawler.urdf): hip yaw about +z from the
    leg's 45 degree diagonal, femur elevation above horizontal, knee bend
    from a straight leg (0 = tibia in line with the femur).
    """
    servo = np.asarray(servo, np.float64)
    knee_s, femur_s, hip_s = servo[:, 0], servo[:, 1], servo[:, 2]
    mirror = LEG_SIGNS[:, 0] * LEG_SIGNS[:, 1]                   # left/right mirror of the yaw
    hip = -mirror * np.radians(hip_s)
    femur = np.radians(90.0 - femur_s)
    knee = np.radians(90.0 - knee_s)
    return np.stack([hip, femur, knee], axis=1)


def quantize(x, scale, zero_point):
    q = np.round(np.asarray(x, np.float32) / scale + zero_point)
    return np.clip(q, -128, 127).astype(np.int8)


def dequantize(q, scale, zero_point):
    return (np.asarray(q, np.float32) - zero_point) * scale
