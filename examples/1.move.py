#!/usr/bin/python3
import sys
sys.path.append(r'/opt/ezblock')
from ezblock import __reset_mcu__
import time
__reset_mcu__()
time.sleep(0.01)
from spider import Spider
from ezblock import delay

__SPIDER__ = Spider([10,11,12,4,5,6,1,2,3,7,8,9])


def forever():
  __SPIDER__.do_action('backward', 1, 100)


if __name__ == "__main__":
    while True:
        forever()
