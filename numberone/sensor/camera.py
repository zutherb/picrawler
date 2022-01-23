import time

from vilib import Vilib
import paho.mqtt.client as mqtt

from threading import Thread


class Camera(Thread):

  def __init__(self, config):
    Thread.__init__(self)
    self.name = "camera"
    self.config = config

    Vilib.camera_start(True)
    Vilib.human_detect_switch(True)

    self.client = mqtt.Client(self.name)
    self.client.connect(config['MQTT']['Host'])

  def run(self):
    while True:
      human_detect_object = Vilib.human_detect_object('number')
      self.client.publish(self.config['TOPICS']['human'], human_detect_object)
      time.sleep(0.1)

