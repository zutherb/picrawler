from robot_hat import Robot, utils

import copy
import os
import time

from . import kinematics
from .gaits import MoveList


# Gaits that alternate the supporting legs on every repetition.
ALTERNATING_GAITS = ("forward", "backward", "turn left", "turn right", "turn left angle", "turn right angle")


class Picrawler(Robot):
    A = kinematics.A
    B = kinematics.B
    C = kinematics.C
    OFFSET_FILE = os.path.expanduser('~/.config/.picrawler.config')
    PIN_LIST = [9, 10, 11, 3, 4, 5, 0, 1, 2, 6, 7, 8]

    # Kept as an attribute for backward compatibility (Picrawler.MoveList).
    MoveList = MoveList

    def __init__(self, pin_list=PIN_LIST, init_angles=None):

        utils.reset_mcu()
        time.sleep(0.2)

        super().__init__(pin_list, db=self.OFFSET_FILE, name='picrawler', init_angles=init_angles)

        self.move_list = self.MoveList()
        self.move_list_add = {}

        self.step_list = {
            "stand": self.move_list['stand'],
            "sit": self.move_list['sit'],
        }

        self.stand_position = 0
        self.direction = [
            1,1,-1,
            1,1,1,
            1,1,-1,
            1,1,1,
        ]

        self.current_coord = [[60, 0, -30], [60, 0, -30], [60, 0, -30], [60, 0, -30]]
        self.coord_temp = [[60, 0, -30], [60, 0, -30], [60, 0, -30], [60, 0, -30]]

    # --- kinematics (thin wrappers kept for backward compatibility) ---

    def coord2polar(self, coord):
        return kinematics.coord2polar(coord, self.A, self.B, self.C)

    def polar2coord(self, angles):
        return kinematics.polar2coord(angles, self.A, self.B, self.C)

    def limit(self, min, max, x):
        return kinematics.limit(min, max, x)

    def limit_angle(self, angles):
        return kinematics.limit_angle(angles)

    # --- actions ---

    def _is_builtin_action(self, motion_name):
        # Check without evaluating the property: gait properties have side
        # effects on the MoveList state (z_current, ready_state).
        name = motion_name.replace(" ", "_")
        return not name.startswith("_") and isinstance(getattr(type(self.move_list), name, None), property)

    def iter_action(self, motion_name, step=1):
        """Yield the frames of an action one by one.

        Raises KeyError right away for unknown names. The stand_position
        alternation advances as each repetition starts, exactly like
        do_action, so a caller may stop between frames.
        """
        if self._is_builtin_action(motion_name):
            return self._iter_builtin(motion_name, step)
        if self.move_list_add.get(motion_name) is not None:
            return self._iter_custom(motion_name, step)
        raise KeyError(motion_name)

    def _iter_builtin(self, motion_name, step):
        for _ in range(step): # times
            self.move_list.stand_position = self.stand_position
            if motion_name in ALTERNATING_GAITS:
                self.stand_position = self.stand_position + 1 & 1
            yield from self.move_list[motion_name]

    def _iter_custom(self, motion_name, step):
        for _ in range(step):
            yield from self.move_list_add[motion_name]

    def do_action(self, motion_name, step=1, speed=50):
        try:
            frames = self.iter_action(motion_name, step)
        except KeyError:
            print("No such action")
            return
        for _step in frames: # spyder motion
            self.do_step(_step, speed=speed)

    def add_action(self, action_name, action_list):
        self.move_list_add[action_name] = action_list

    # --- low level ---

    def set_angle(self, angles_list, speed=50, israise=False):
        translate_list = []
        results = []
        for angles in angles_list:
            result, angles = self.limit_angle(angles)
            translate_list += angles
            results.append(result)

        if True in results:
            if israise == True:
                raise ValueError('\033[1;35mCoordinates out of controllable range.\033[0m')
            else:
                try:
                    # Recalculate the reachable coordinates from the clamped angles
                    coords = []
                    for i in range(4):
                        coords.append(self.polar2coord(translate_list[i*3:i*3+3]))
                    self.current_coord = coords
                except Exception as e:
                    print('re : %s'%e)
        else:
            self.current_coord = copy.deepcopy(self.coord_temp)

        self.servo_move(translate_list, speed)
        return list.copy(translate_list)

    def _do_frame(self, frame, speed, israise):
        angles_temp = [kinematics.leg_servo_angles(coord, self.A, self.B, self.C) for coord in frame]
        self.coord_temp = copy.deepcopy(frame)
        return self.set_angle(angles_temp, speed, israise)

    def do_step(self, _step, speed=50, israise=False):
        """Move all four legs to one frame, or play a named step.

        Returns the servo angles of the last frame sent, or None if the
        step was invalid.
        """
        if isinstance(_step, str):
            if _step in self.step_list.keys():
                angles = None
                for one_step in self.step_list[_step]:
                    angles = self._do_frame(one_step, speed, israise)
                return angles
            else:
                print("The name of gait is not in the default gait dictionary")
        elif isinstance(_step, list):
            return self._do_frame(_step, speed, israise)
        else:
            print("The \"_step\" parameter is wrong.")

    def do_single_leg(self, leg, coodinate=[50,50,-33], speed=50):
        target_coord = self.current_step_all_leg_value()
        target_coord[leg] = list(coodinate)
        self.do_step(target_coord, speed)

    # --- state ---

    def current_step_all_leg_angle(self):
        return list.copy(self.servo_positions)

    def current_step_leg_value(self, leg):
        return list.copy(self.current_coord[leg])

    def current_step_all_leg_value(self):
        return copy.deepcopy(self.current_coord)

    def mix_step(self, basic_step, leg, coodinate=[50,50,-33]):
        new_step = copy.deepcopy(basic_step)
        new_step[leg] = list(coodinate)
        return new_step

    # --- calibration ---

    def cali_helper_web(self, leg, pos, enter):
        step=0.2
        cali_position = []
        cali_coord = [[60, 0, -30], [60, 0, -30], [60, 0, -30], [60, 0, -30]]

        for coord in cali_coord: # each servo motion
            cali_position += kinematics.leg_servo_angles(coord, self.A, self.B, self.C)

        cali_position = [cali_position[i] + self.offset[i] for i in range(12)]

        positive_list = [
            [1, -1, -1, 1, 1, -1],
            [1, -1, 1, -1, 1, -1],
            [-1, 1, 1, -1, 1, -1],
            [-1, 1, -1, 1, 1, -1],
        ]

        offset = list.copy(self.offset)
        leg = leg - 1
        if pos == 'up':
            self.current_coord[leg][1] += step * positive_list[leg][0]
        elif pos == 'down':
            self.current_coord[leg][1] += step * positive_list[leg][1]
        elif pos == 'left':
            self.current_coord[leg][0] += step * positive_list[leg][2]
        elif pos == 'right':
            self.current_coord[leg][0] += step * positive_list[leg][3]
        elif pos == 'high':
            self.current_coord[leg][2] += step * positive_list[leg][4]
        elif pos == 'low':
            self.current_coord[leg][2] += step * positive_list[leg][5]

        for coord in self.current_coord:
            coord[0] = max(40, min(80, coord[0]))
            coord[1] = max(-20, min(20, coord[1]))
            coord[2] = max(-50, min(-10, coord[2]))
        current_position = self.do_step(self.current_coord, speed=100)
        if enter == 1:
            tmp = [current_position[i] - cali_position[i] + offset[i] for i in range(len(current_position))]
            offset[leg*3:(leg + 1)*3] = tmp[leg*3:(leg + 1)*3]
            self.current_coord[leg] = [60, 0, -30]
            self.set_offset(offset)
            self.do_step(self.current_coord, speed=100)
