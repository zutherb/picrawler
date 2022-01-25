import time
from threading import Thread
import configparser

from actor.corpus import Corpus
from actor.lcd import LCD
from actor.led import Led

from logic.controller_event_mapper import ControllerEventMapper
from logic.remotecontrol import RemoteControl

from sensor.camera import Camera
from sensor.controller import RemoteController
from sensor.ultrasonic import UltraSonic
from sensor.button import UserButton
from sensor.speech_recognition import SpeechRecognizer

import paho.mqtt.client as mqtt

# https://techtutorialsx.com/2017/04/23/python-subscribing-to-mqtt-topic/

config = configparser.ConfigParser()
config.read('numberone.ini')


def consumer(in_q):
  client = mqtt.Client("SpeechConsumer")
  client.connect(config['MQTT']['Host'])
  client.subscribe(config['TOPICS']['speechrecognition'])

  def on_message(client, userdata, message):
    time.sleep(1)
    print("received message =", str(message.payload.decode("utf-8")))

  client.on_message = on_message  # Define callback function for receipt of a message

  while True:
    client.loop_forever()  # Start networking daemon


try:
  t1 = Thread(target=consumer, args=(1,))
  t1.start()

  # Actors
  corpus_thread = Corpus(config)
  corpus_thread.start()
  lcd_thread = LCD()
  lcd_thread.start()
  led_thread = Led(config)
  led_thread.start()

  # Logic
  event_mapper_thread = ControllerEventMapper(config)
  event_mapper_thread.start()
  remote_control = RemoteControl(config)
  remote_control.start()

  # Sensors
  camera_thread = Camera(config)
  camera_thread.start()
  controller = RemoteController(config)
  controller.start()
  speak_recognition_thread = SpeechRecognizer(config)
  speak_recognition_thread.start()
  user_button_thread = UserButton(config)
  user_button_thread.start()
  ultrasonic_thread = UltraSonic(config)
  ultrasonic_thread.start()

except KeyboardInterrupt:
  t1.__stop()
  corpus_thread.__stop()
  speak_recognition_thread.__stop()
  camera_thread.__stop()
  controller.__stop()

  event_mapper_thread.__stop()
  remote_control.__stop()

  user_button_thread.__stop()
  ultrasonic_thread.__stop()
  led_thread.__stop()
  lcd_thread.__stop()
