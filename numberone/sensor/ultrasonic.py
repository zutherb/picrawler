import sys
import time

import paho.mqtt.client as mqtt

sys.path.append(r'/opt/ezblock')
from ezblock import Pin, Ultrasonic

from threading import Thread


class UltraSonic(Thread):
  def __init__(self, config):
    Thread.__init__(self)
    self.name = "ultrasonic"

    trigger = Pin(config['PINS']['Trigger'])
    echo = Pin(config['PINS']['Echo'])

    self.ultrasonic = Ultrasonic(trigger, echo)

    self.config = config

    self.client = mqtt.Client(self.name)
    self.client.connect(config['MQTT']['Host'])

  def run(self):
    while True:
      distance = self.ultrasonic.read()
      self.client.publish(self.config['TOPICS']['ultrasonic'], distance)
      time.sleep(0.1)
