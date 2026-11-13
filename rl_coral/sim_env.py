"""PyBullet environment for the residual gait policy (50 Hz, 32 -> 12).

The action is the same thing the robot loop sends: residual foot offsets
in mm on top of the CPG trot, turned into servo angles by the SunFounder
inverse kinematics (picrawler.kinematics.coord2polar via
rl_coral.common.servo_angles, including the robot's servo clamping).

Sim-to-real measures:
- domain randomization: floor/foot friction 0.5..1.2, link masses +-10 %
- vision features (roll, pitch, yaw rate, obstacle distance) computed
  like the camera does (roll/pitch high-passed), delayed by 2 steps and
  perturbed with Gaussian noise
- first-order low-pass on the action (0.7 new + 0.3 old) for servo inertia

The policy's mean goes through tanh in the env, so the exported network
(Dense ... Dense(12, tanh)) reproduces the deterministic action exactly.
"""
import collections
import math
import os

import gymnasium as gym
import numpy as np
import pybullet as p
import pybullet_data

from rl_coral import common

URDF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "picrawler.urdf")
SUBSTEPS = 4
EPISODE_STEPS = 1000                 # 20 s
COMMAND_STEPS = 200                  # new command every 4 s
TILT_LIMIT = 0.7                     # rad, episode ends beyond this
CAMERA_OFFSET = (0.0, 0.065, 0.02)   # m in the body frame, in front of the torso
LEG_NAMES = ("rf", "lf", "lr", "rr")


class PiCrawlerVisionEnv(gym.Env):
    metadata = {"render_modes": ["human"], "render_fps": int(1 / common.DT)}

    def __init__(self, render_mode=None, randomize=True, obstacles=True, seed=None):
        super().__init__()
        self.render_mode = render_mode
        self.randomize = randomize
        self.obstacles = obstacles
        # Wide bounds plus tanh inside: SB3 clips samples to the box, the
        # tanh keeps the offsets in [-1, 1] and matches the exported model.
        self.action_space = gym.spaces.Box(-3.0, 3.0, (common.ACT_DIM,), np.float32)
        low = np.full(common.OBS_DIM, -1.0, np.float32)
        high = np.full(common.OBS_DIM, 1.0, np.float32)
        low[4:8], high[4:8] = common.VISION_LIMITS[:, 0], common.VISION_LIMITS[:, 1]
        self.observation_space = gym.spaces.Box(low, high, dtype=np.float32)

        self.client = p.connect(p.GUI if render_mode == "human" else p.DIRECT)
        p.setAdditionalSearchPath(pybullet_data.getDataPath(), physicsClientId=self.client)
        p.setGravity(0, 0, -9.81, physicsClientId=self.client)
        p.setTimeStep(common.DT / SUBSTEPS, physicsClientId=self.client)
        self.plane = p.loadURDF("plane.urdf", physicsClientId=self.client)
        self.robot = p.loadURDF(URDF, [0, 0, 0.08], useFixedBase=False, physicsClientId=self.client)
        self._index_joints()
        self.obstacle = p.createMultiBody(
            baseMass=0,
            baseCollisionShapeIndex=p.createCollisionShape(p.GEOM_BOX, halfExtents=[0.15, 0.04, 0.08],
                                                           physicsClientId=self.client),
            baseVisualShapeIndex=p.createVisualShape(p.GEOM_BOX, halfExtents=[0.15, 0.04, 0.08],
                                                     rgbaColor=[0.8, 0.35, 0.25, 1], physicsClientId=self.client),
            basePosition=[0, 50, 0.08], physicsClientId=self.client)
        self.nominal_mass = {link: p.getDynamicsInfo(self.robot, link, physicsClientId=self.client)[0]
                             for link in [-1] + list(range(p.getNumJoints(self.robot, physicsClientId=self.client)))}
        self.leak = common.leak(common.DT)
        if seed is not None:
            self.reset(seed=seed)

    def _index_joints(self):
        names = {}
        for j in range(p.getNumJoints(self.robot, physicsClientId=self.client)):
            info = p.getJointInfo(self.robot, j, physicsClientId=self.client)
            names[info[1].decode()] = j
        self.joints = np.array([[names["%s_%s" % (leg, part)] for part in ("hip", "femur", "knee")]
                                for leg in LEG_NAMES])
        self.feet = [names["%s_foot_fixed" % leg] for leg in LEG_NAMES]
        limits = [p.getJointInfo(self.robot, j, physicsClientId=self.client) for j in self.joints.ravel()]
        self.max_force = [info[10] for info in limits]
        self.max_velocity = [info[11] for info in limits]

    # --- gym API ---

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        rng = self.np_random
        if self.randomize:
            friction = rng.uniform(0.5, 1.2)
            for link, mass in self.nominal_mass.items():
                p.changeDynamics(self.robot, link, mass=mass * rng.uniform(0.9, 1.1), physicsClientId=self.client)
        else:
            friction = 0.8
        p.changeDynamics(self.plane, -1, lateralFriction=friction, physicsClientId=self.client)
        for foot in self.feet:
            p.changeDynamics(self.robot, foot, lateralFriction=friction, physicsClientId=self.client)

        self.step_count = 0
        self.phase = 0.0
        self.last_action = np.zeros(common.ACT_DIM, np.float32)
        self.prev_action = np.zeros(common.ACT_DIM, np.float32)
        self.filtered = np.zeros(common.ACT_DIM, np.float32)
        yaw = rng.uniform(-math.pi, math.pi)
        stance = common.joint_angles(common.servo_angles(common.foot_targets(0.0, 0.0, 0.0, self.filtered)))
        p.resetBasePositionAndOrientation(self.robot, [0, 0, 0.058], p.getQuaternionFromEuler([0, 0, yaw]),
                                          physicsClientId=self.client)
        p.resetBaseVelocity(self.robot, [0, 0, 0], [0, 0, 0], physicsClientId=self.client)
        for joint, angle in zip(self.joints.ravel(), stance.ravel()):
            p.resetJointState(self.robot, int(joint), float(angle), 0.0, physicsClientId=self.client)
        self._place_obstacle(yaw)
        self._sample_command()
        for _ in range(SUBSTEPS * 10):              # settle on the feet
            self._drive(stance)
            p.stepSimulation(physicsClientId=self.client)

        state = self._state()
        self.roll_hp, self.pitch_hp = 0.0, 0.0
        self.last_roll, self.last_pitch = state["roll"], state["pitch"]
        self.last_yaw = state["yaw"]
        clean = np.array([0.0, 0.0, 0.0, self._obstacle_distance(state)], np.float32)
        self.vision_fifo = collections.deque([clean] * (common.VISION_DELAY_STEPS + 1),
                                             maxlen=common.VISION_DELAY_STEPS + 1)
        return self._observe(), {}

    def step(self, action):
        raw = np.tanh(np.asarray(action, np.float32))
        self.filtered = 0.7 * raw + 0.3 * self.filtered
        vx_used, wz = self.cmd_used
        targets = common.foot_targets(self.phase, vx_used, wz, self.filtered)
        joints = common.joint_angles(common.servo_angles(targets))
        for _ in range(SUBSTEPS):
            self._drive(joints)
            p.stepSimulation(physicsClientId=self.client)

        self.prev_action, self.last_action = self.last_action, raw
        self.step_count += 1
        self.phase = common.phase_at(self.step_count)
        if self.step_count % COMMAND_STEPS == 0:
            self._sample_command()

        state = self._state()
        reward, terminated = self._reward(state, raw, vx_used, wz)
        self._push_vision(state)
        obs = self._observe()
        truncated = self.step_count >= EPISODE_STEPS
        info = {"v_forward": state["v_forward"], "yaw_rate": state["yaw_rate"],
                "vx_target": vx_used * common.MAX_VX, "wz_target": wz * common.MAX_WZ}
        return obs, reward, terminated, truncated, info

    def close(self):
        if self.client is not None:
            p.disconnect(self.client)
            self.client = None

    # --- internals ---

    def _drive(self, joints):
        for joint, angle, force, velocity in zip(self.joints.ravel(), np.ravel(joints),
                                                 self.max_force, self.max_velocity):
            p.setJointMotorControl2(self.robot, int(joint), p.POSITION_CONTROL, targetPosition=float(angle),
                                    force=force, maxVelocity=velocity, physicsClientId=self.client)

    def _sample_command(self):
        rng = self.np_random
        if rng.random() < 0.15:
            self.cmd = (0.0, 0.0)
        else:
            vx = float(rng.uniform(-0.5, 1.0))
            wz = float(rng.uniform(-1.0, 1.0)) if rng.random() < 0.5 else 0.0
            self.cmd = (vx, wz)

    def _place_obstacle(self, yaw):
        if self.obstacles and self.np_random.random() < 0.5:
            dist = self.np_random.uniform(0.3, 1.2)
            offset = self.np_random.uniform(-0.1, 0.1)
            # body forward is +y, i.e. world direction yaw + 90 degrees
            fx, fy = -math.sin(yaw), math.cos(yaw)
            pos = [fx * dist + fy * offset, fy * dist - fx * offset, 0.08]
            orn = p.getQuaternionFromEuler([0, 0, yaw])
        else:
            pos, orn = [0, 50, 0.08], [0, 0, 0, 1]
        p.resetBasePositionAndOrientation(self.obstacle, pos, orn, physicsClientId=self.client)

    def _state(self):
        pos, orn = p.getBasePositionAndOrientation(self.robot, physicsClientId=self.client)
        lin, ang = p.getBaseVelocity(self.robot, physicsClientId=self.client)
        rot = np.array(p.getMatrixFromQuaternion(orn)).reshape(3, 3)
        right, forward = rot[:, 0], rot[:, 1]
        yaw = math.atan2(-forward[0], forward[1])
        heading = forward[:2] / max(1e-6, np.linalg.norm(forward[:2]))
        side = np.array([heading[1], -heading[0]])
        return {
            "pos": np.array(pos), "rot": rot,
            "roll": -math.asin(max(-1.0, min(1.0, right[2]))),         # right side down positive
            "pitch": math.asin(max(-1.0, min(1.0, forward[2]))),       # nose up positive
            "yaw": yaw,                                                # counter-clockwise positive
            "yaw_rate": ang[2],
            "v_forward": float(np.dot(lin[:2], heading)),
            "v_side": float(np.dot(lin[:2], side)),
        }

    def _obstacle_distance(self, state):
        start = state["pos"] + state["rot"] @ np.array(CAMERA_OFFSET)
        forward = state["rot"][:, 1].copy()
        forward[2] = 0.0
        forward /= max(1e-6, np.linalg.norm(forward))
        end = start + forward * common.OBSTACLE_RANGE_M
        hit = p.rayTest(start.tolist(), end.tolist(), physicsClientId=self.client)[0]
        return float(hit[2]) if hit[0] == self.obstacle else 1.0

    def _push_vision(self, state):
        """What the camera would report now; it reaches the policy 2 steps later."""
        self.roll_hp = self.leak * self.roll_hp + (state["roll"] - self.last_roll)
        self.pitch_hp = self.leak * self.pitch_hp + (state["pitch"] - self.last_pitch)
        self.last_roll, self.last_pitch = state["roll"], state["pitch"]
        # like the camera: heading change between two frames, not the instantaneous rate
        flow_yaw = ((state["yaw"] - self.last_yaw + math.pi) % (2 * math.pi) - math.pi) / common.DT
        self.last_yaw = state["yaw"]
        clean = np.array([self.roll_hp, self.pitch_hp, flow_yaw, self._obstacle_distance(state)], np.float32)
        noisy = clean + self.np_random.normal(0.0, common.VISION_NOISE_STD).astype(np.float32)
        noisy[3] = min(1.0, max(0.0, noisy[3]))
        self.vision_fifo.append(noisy)

    def _observe(self):
        vision = self.vision_fifo[0]                     # oldest entry = 2 steps late
        vx, wz = self.cmd
        self.cmd_used = (common.effective_vx(vx, float(vision[3])), wz)
        return common.build_observation(self.phase, self.cmd_used[0], self.cmd_used[1], vision,
                                        self.last_action, self.prev_action)

    def _reward(self, state, action, vx_used, wz):
        vx_err = (state["v_forward"] - vx_used * common.MAX_VX) / 0.03
        wz_err = (state["yaw_rate"] - wz * common.MAX_WZ) / 0.25
        reward = (1.0 * math.exp(-vx_err ** 2)
                  + 0.5 * math.exp(-wz_err ** 2)
                  - 2.0 * (state["roll"] ** 2 + state["pitch"] ** 2)
                  - 0.5 * (state["v_side"] / common.MAX_VX) ** 2 * 0.1
                  - 0.02 * float(np.sum((action - self.prev_action) ** 2))
                  - 0.005 * float(np.sum(action ** 2)))
        if p.getContactPoints(self.robot, self.obstacle, physicsClientId=self.client):
            reward -= 1.0
        torso_down = bool(p.getContactPoints(self.robot, self.plane, linkIndexA=-1, physicsClientId=self.client))
        fallen = abs(state["roll"]) > TILT_LIMIT or abs(state["pitch"]) > TILT_LIMIT or state["pos"][2] < 0.02
        if fallen or torso_down:
            return reward - 5.0, True
        return reward, False
