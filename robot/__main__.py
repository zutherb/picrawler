import time
from threading import Thread

from actor.lcd import LCD
from actor.led import Led

from sensor.remote_controller import RemoteController
from sensor.speech_recognition import SpeechRecognizer

import paho.mqtt.client as mqtt

#https://techtutorialsx.com/2017/04/23/python-subscribing-to-mqtt-topic/

def consumer(in_q):
  client = mqtt.Client("SpeechConsumer")
  client.connect("127.0.0.1")

  def on_connect(client, userdata, flags, rc):  # The callback for when the client connects to the broker
    print("Connected with result code {0}".format(str(rc)))  # Print result of connection attempt
    client.subscribe("picrawler/speechrecognition")

  def on_message(client, userdata, message):
      time.sleep(1)
      print("received message =",str(message.payload.decode("utf-8")))

  client.on_connect = on_connect  # Define callback function for successful connection
  client.on_message = on_message  # Define callback function for receipt of a message

  while True:
    client.loop_forever()  # Start networking daemon

t1 = Thread(target=consumer, args=(1,))
t1.start()

speak_recognition_thread = SpeechRecognizer()
speak_recognition_thread.start()

led_thread = Led()
led_thread.start()

lcd_thread = LCD()
lcd_thread.start()

controller = RemoteController()
controller.start()
