import json
from threading import Thread

import paho.mqtt.client as mqtt

from logic.controller_event_mapper import NimbusEventType, NimbusButton


class RemoteControl(Thread):

  def __init__(self, config):
    Thread.__init__(self)
    self.name = "RemoteControll"
    self.config = config

    self.client = mqtt.Client(self.name)
    self.client.connect(config['MQTT']['Host'])

    self.client.subscribe(config['TOPICS']['controllerEventMapper'])

    def on_message(client, userdata, message):
      payload = json.loads(str(message.payload.decode("utf-8")))

      eventType = NimbusEventType[payload["type"]]
      button = NimbusButton[payload["button"]]
      value = payload["value"]

      payload = None

      if eventType == NimbusEventType.EV_ABS:
        if button == NimbusButton.ABS_X and abs(value) > 70:
          payload = {
            "motion_name": "turn left" if value < 0 else "turn right",
            "step": 1,
            "speed": value
          }

        if button == NimbusButton.ABS_Y and abs(value) > 70:
          payload = {
            "motion_name": "backward" if value < 0 else "forward",
            "step": 1,
            "speed": value
          }

      if eventType == NimbusEventType.EV_KEY and value > 0:
        if button == NimbusButton.BTN_A:
          payload = {
            "motion_name": "sit",
            "step": 1,
            "speed": 100
          }
        if button == NimbusButton.BTN_B:
          payload = {
            "motion_name": "stand",
            "step": 1,
            "speed": 100
          }
        if button == NimbusButton.BTN_X:
           payload = {
             "motion_name": "dance",
             "step": 1,
             "speed": 100
          }
        if button == NimbusButton.BTN_Y:
          payload = {
            "motion_name": "push up",
            "step": 1,
            "speed": 100
          }

      if payload is not None:
        self.client.publish(self.config['TOPICS']['corpus'], json.dumps(payload))

    self.client.on_message = on_message

  def run(self):
    self.client.loop_forever()
    while True:
      pass
