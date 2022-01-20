#!/usr/bin/python3
import sys
sys.path.append(r'/opt/ezblock')
from ezblock import __reset_mcu__
import time
__reset_mcu__()
time.sleep(0.01)
from vilib import Vilib
from spider import Spider
__SPIDER__ = Spider([10,11,12,4,5,6,1,2,3,7,8,9])
from ezblock import TTS

speed = None

Vilib.camera_start(True)
Vilib.human_detect_switch(True)

speed = 100
__SPIDER__.do_action('stand', 1, speed)

__tts__ = TTS()


def forever():
  global speed
  if (Vilib.human_detect_object('number')) >= 1:
    __SPIDER__.do_action('wave', 1, speed)
    __tts__.say('Hello,nice to meet you!')

if __name__ == "__main__":
    while True:
        forever()

