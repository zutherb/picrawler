import json
import sys
from queue import Queue

import sounddevice as sd
import vosk
from Music import *
from ezblock import Pin

soundQueue = Queue()


def callback(indata, frames, time, status):
  """This is called (from a separate thread) for each audio block."""
  if status:
    print(status, file=sys.stderr)
  soundQueue.put(bytes(indata))


def speech_recognition(out_q):
  device = 0
  modelPath = '/opt/vosk/model/vosk-model-small-en-us-0.15/'

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
                                      '["number one stand up", "number one sit", "number one dance"]')

    led = Pin("LED")
    startProcessing = True
    while True:
      data = soundQueue.get()
      if recognizer.AcceptWaveform(data):
        result = recognizer.Result()
        speech_recognition_result = json.loads(result)["text"]
        if speech_recognition_result:
          textCommandQueue.put(speech_recognition_result)
        led.off()
        startProcessing = True
      else:
        partial_result = recognizer.PartialResult()
        speech_recognition_result = json.loads(partial_result)["partial"]
        if speech_recognition_result.startswith('number one') and startProcessing:
          startProcessing = False
          led.on()
          sound_effect_play('bell.wav', 100)
