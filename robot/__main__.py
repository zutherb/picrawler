import json
import sys
from queue import Queue
from threading import Thread

import sounddevice as sd
import vosk

soundQueue = Queue()


def callback(indata, frames, time, status):
  """This is called (from a separate thread) for each audio block."""
  if status:
    print(status, file=sys.stderr)
  soundQueue.put(bytes(indata))


def speach_recognition(out_q):
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
    while True:
      data = soundQueue.get()
      if recognizer.AcceptWaveform(data):
        result = recognizer.Result()
        speek_recognition_result = json.loads(result)["text"]
        textCommandQueue.put(speek_recognition_result)
      else:
        print(recognizer.PartialResult())


def consumer(in_q):
  while True:
    data = in_q.get()
    if data is not None:
      print("receive result", data)

print('#' * 80)
print('Press Ctrl+C to stop the recording')
print('#' * 80)

textCommandQueue = Queue()
t1 = Thread(target=consumer, args=(textCommandQueue,))
speak_recognition_thread = Thread(target=speach_recognition, args=(textCommandQueue,))
t1.start()
speak_recognition_thread.start()

