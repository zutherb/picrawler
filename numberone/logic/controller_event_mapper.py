import json
from enum import Enum
from threading import Thread

import paho.mqtt.client as mqtt


class NimbusButton(Enum):
  ABS_X = 0
  ABS_Y = 1
  ABS_Z = 2
  ABS_RZ = 5
  BTN_A = 304
  BTN_B = 305
  BTN_X = 306
  BTN_Y = 307
  BTN_MENU = 172
  BTN_L1 = 308
  BTN_L2 = 310
  BTN_R1 = 309
  BTN_R2 = 311


class NimbusEventType(Enum):
  EV_SYN = 0
  EV_KEY = 1
  EV_ABS = 3
  EV_MSC = 4


class ControllerEventMapper(Thread):

  def __init__(self, config):
    Thread.__init__(self)
    self.name = "ControllerEventMapper"
    self.config = config

    self.client = mqtt.Client(self.name)
    self.client.connect(config['MQTT']['Host'])

    self.client.subscribe(config['TOPICS']['controller'])

    self.x_buffer = [0]
    self.y_buffer = [0]

    def on_message(client, userdata, message):
      payload = json.loads(str(message.payload.decode("utf-8")))

      eventType = NimbusEventType(payload["type"])

      if eventType not in [NimbusEventType.EV_SYN, NimbusEventType.EV_MSC]:
        button = NimbusButton(payload["code"])

        if eventType == NimbusEventType.EV_KEY:
          payload = {
            "button": button.name,
            "value": payload["value"],
            "type": eventType.name
          }
          self.client.publish(self.config['TOPICS']['controllerEventMapper'], json.dumps(payload))

        if eventType == NimbusEventType.EV_ABS:
          if button == NimbusButton.ABS_X:
            if payload["value"] == 0:
              absoluteValueX = max(self.x_buffer) if max(self.x_buffer) > 0 else min(self.x_buffer)

              payload = {
                "button": button.name,
                "value": int(absoluteValueX / 127 * 100),
                "type": eventType.name
              }

              self.client.publish(self.config['TOPICS']['controllerEventMapper'], json.dumps(payload))

              self.x_buffer = [0]
            else:
              self.x_buffer.append(payload["value"])

          if button == NimbusButton.ABS_Y:
            if payload["value"] == 0:
              absoluteValueY = max(self.y_buffer) if max(self.y_buffer) > 0 else min(self.y_buffer)

              payload = {
                "button": button.name,
                "value": int(absoluteValueY / 127 * 100),
                "type": eventType.name
              }

              self.client.publish(self.config['TOPICS']['controllerEventMapper'], json.dumps(payload))

              self.y_buffer = [0]
            else:
              self.y_buffer.append(payload["value"])

    self.client.on_message = on_message

  def run(self):
    self.client.loop_forever()
    while True:
      pass
