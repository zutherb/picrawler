import sys
from threading import Thread

sys.path.append(r'/opt/ezblock')

import json
from multiprocessing import Queue
import configparser

import sounddevice as sd
import vosk
import logging
from Music import *
from ezblock import Pin

import paho.mqtt.client as mqtt


soundQueue = Queue()

def callback(indata, frames, time, status):
  """This is called (from a separate thread) for each audio block."""
  if status:
    logging.error(status)
  soundQueue.put(bytes(indata))

class SpeechRecognizer(Thread):


  def __init__(self, config):
    Thread.__init__(self)
    self.name ="SpeechRecognizer"
    self.config = config

    self.client = mqtt.Client(self.name)
    self.client.connect(config['MQTT']['Host'])

  def run(self):
    device = 0
    modelPath = self.config['SPEECHRECOGNITION']['VoskModelPath']

    device_info = sd.query_devices(device, 'input')
    samplerate = int(device_info['default_samplerate'])

    with sd.RawInputStream(
      samplerate=samplerate,
      blocksize=1024,
      device=device,
      dtype='int16',
      channels=1,
      callback=callback):

      model = vosk.Model(modelPath)

      recognizer = vosk.KaldiRecognizer(model,
                                        samplerate,
                                        '["number one stand up", "number one sit down", "number one dance"]')

      startProcessing = True
      while True:
        data = soundQueue.get()
        if recognizer.AcceptWaveform(data):
          result = recognizer.Result()
          speech_recognition_result = json.loads(result)["text"]
          if speech_recognition_result:
            self.client.publish(self.config['TOPICS']['speechrecognition'], speech_recognition_result)
            self.client.publish(self.config['TOPICS']['led'], False)
          startProcessing = True
        else:
          partial_result = recognizer.PartialResult()
          speech_recognition_result = json.loads(partial_result)["partial"]
          if speech_recognition_result.startswith(self.config['SPEECHRECOGNITION']['Stopword']) and startProcessing:
            startProcessing = False
            self.client.publish(self.config['TOPICS']['led'], True)
            sound_effect_play('bell.wav', 100)
