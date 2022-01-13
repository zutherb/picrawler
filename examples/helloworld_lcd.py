#!/usr/bin/python3
import sys
sys.path.append(r'/opt/ezblock')
from ezblock import LCD

lcd = LCD(0X3C)

def forever():
  pass

if __name__ == "__main__":
    lcd.clear()
    lcd.message("Hello World")
    while True:
        forever()
