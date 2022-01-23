import json
import sys

sys.path.append(r'/opt/ezblock')
from ezblock import __reset_mcu__
from spider import Spider
import paho.mqtt.client as mqtt
from threading import Thread


class Corpus(Thread):
  def __init__(self, config):
    Thread.__init__(self)
    self.name = "Crawler"

    __reset_mcu__()
    self.spider = Spider([10, 11, 12, 4, 5, 6, 1, 2, 3, 7, 8, 9])

    self.config = config

    self.client = mqtt.Client(self.name)
    self.client.connect(config['MQTT']['Host'])
    self.client.subscribe(config['TOPICS']['crawler'])

    def on_message(client, userdata, message):
      payload = json.loads(str(message.payload.decode("utf-8")))
      print(payload)
      motion_name = payload["motion_name"]
      step = payload["step"]
      speed = payload["speed"]

      if motion_name == 'forward':
        self.spider.do_action('forward', step, speed)
      elif motion_name == 'backward':
        self.spider.do_action('backward', step, speed)
      elif motion_name == 'look_left':
        self.spider.do_action('look_left', step, speed)
      elif motion_name == 'look_right':
        self.spider.do_action('look_right', step, speed)
      elif motion_name == 'sit':
        self.spider.do_action('sit', step, speed)
      elif motion_name == 'stand':
        self.spider.do_action('stand', step, speed)
      elif motion_name == 'push up':
        self.spider.do_action('push up', step, speed)
      elif motion_name == 'dance':
        self.spider.do_action('dance', step, speed)
      elif motion_name == 'wave':
        self.spider.do_action('wave', step, speed)

    self.client.on_message = on_message

  def run(self):
    self.client.loop_forever()
    while True:
      pass
