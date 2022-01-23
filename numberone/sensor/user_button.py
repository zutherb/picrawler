import sys
import time

import paho.mqtt.client as mqtt

sys.path.append(r'/opt/ezblock')
from ezblock import Pin

from threading import Thread


class UserButton(Thread):


  def __init__(self, config):
    Thread.__init__(self)
    self.name = "user-button"
    self.config = config

    self.userButton = Pin(config['PINS']['UserButton'])

    self.client = mqtt.Client(self.name)
    self.client.connect(config['MQTT']['Host'])

  def run(self):
    lastState = None
    while True:
      b = not bool(self.userButton.value())
      if b is not lastState:
        self.client.publish(self.config['TOPICS']['button'], b)
        lastState = b
      time.sleep(0.1)
