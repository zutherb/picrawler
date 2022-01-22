import time
from threading import Thread
import configparser

from actor.lcd import LCD
from actor.led import Led

from sensor.remote_controller import RemoteController
from sensor.ultrasonic import UltraSonic
from sensor.user_button import UserButton
from sensor.speech_recognition import SpeechRecognizer

import paho.mqtt.client as mqtt


# https://techtutorialsx.com/2017/04/23/python-subscribing-to-mqtt-topic/

config = configparser.ConfigParser()
config.read('robot.ini')

def consumer(in_q):
  client = mqtt.Client("SpeechConsumer")
  client.connect(config['MQTT']['Host'])
  client.subscribe(config['TOPICS']['speechrecognition'])

  def on_message(client, userdata, message):
      time.sleep(1)
      print("received message =",str(message.payload.decode("utf-8")))

  client.on_message = on_message  # Define callback function for receipt of a message

  while True:
    client.loop_forever()  # Start networking daemon

try:
  t1 = Thread(target=consumer, args=(1,))
  t1.start()

  #Sensors
  speak_recognition_thread = SpeechRecognizer(config)
  speak_recognition_thread.start()
  #controller = RemoteController()
  #controller.start()
  user_button_thread = UserButton(config)
  user_button_thread.start()
  ultrasonic_thread = UltraSonic(config)
  ultrasonic_thread.start()

  #Actors
  led_thread = Led(config)
  led_thread.start()
  lcd_thread = LCD()
  lcd_thread.start()
except KeyboardInterrupt:
  t1.__stop()
  speak_recognition_thread.__stop()
  #controller.__stop()
  user_button_thread.__stop()
  ultrasonic_thread.__stop()
  led_thread.__stop()
  lcd_thread.__stop()


