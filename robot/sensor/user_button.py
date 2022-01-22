import sys
import time

import paho.mqtt.client as mqtt

sys.path.append(r'/opt/ezblock')
from ezblock import Pin

from threading import Thread


class UserButton(Thread):
  def __init__(self):
    Thread.__init__(self)
    self.name = "user-button"
    self.userButton = Pin("SW")
    self.client = mqtt.Client(self.name)
    self.client.connect("127.0.0.1")

  def run(self):
    lastState=None
    while True:
      b = not bool(self.userButton.value())
      if b is not lastState:
        self.client.publish("picrawler/button", b)
        lastState = b
      time.sleep(0.1)
