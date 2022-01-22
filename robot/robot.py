import time
from threading import Thread
import configparser

from actor.lcd import LCD
from actor.led import Led

from sensor.user_button import UserButton
from sensor.remote_controller import RemoteController
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

t1 = Thread(target=consumer, args=(1,))
t1.start()

#Sensors
speak_recognition_thread = SpeechRecognizer(config)
speak_recognition_thread.start()
controller = RemoteController()
controller.start()
user_button_thread = UserButton()
user_button_thread.start()

#Actors
led_thread = Led(config)
led_thread.start()
lcd_thread = LCD()
lcd_thread.start()
