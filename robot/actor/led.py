import sys
sys.path.append(r'/opt/ezblock')
from threading import Thread
from ezblock import Pin
import paho.mqtt.client as mqtt

class Led(Thread):

  def __init__(self):
    Thread.__init__(self)
    self.name ="Led"
    self.led = Pin("LED")
    self.client = mqtt.Client(self.name)
    self.client.connect("127.0.0.1")
    self.client.subscribe("picrawler/led")

    def on_message(client, userdata, message):
      decoded_payload = str(message.payload.decode("utf-8"))
      if decoded_payload == "True":
        self.led.on()
      else:
        self.led.off()

    self.client.on_message = on_message  # Define callback function for receipt of a message

  def run(self):
    self.client.loop_forever()  # Start networking daemon
    while True:
      pass



