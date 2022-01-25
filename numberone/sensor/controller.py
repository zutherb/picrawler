import time
import json
from threading import Thread

from evdev import InputDevice
import paho.mqtt.client as mqtt
from evdev.ecodes import ecodes


class RemoteController(Thread):
  def __init__(self, config):
    Thread.__init__(self)
    self.name = "Controller"
    self.config = config

    self.client = mqtt.Client(self.name)
    self.client.connect(config['MQTT']['Host'])

  def run(self):
    while True:
      try:
        gamepad = InputDevice(self.config['CONTROLLER']['device'])
        print(gamepad)
        print(gamepad.capabilities(verbose=True))

        for event in gamepad.read_loop():
          payload = {
            "timestamp" : event.timestamp(),
            "code" : event.code,
            "value" : event.value,
            "type" : event.type
          }
          self.client.publish(self.config['TOPICS']['controller'], json.dumps(payload))

      except FileNotFoundError as error:
        print("No Controller found", error)
        time.sleep(5)
