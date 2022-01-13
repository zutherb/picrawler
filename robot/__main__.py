import logging
from multiprocessing import Queue
from threading import Thread

from sensor.speech_recognition import SpeechRecognizer

textCommandQueue = Queue()

def consumer(in_q):
  while True:
    data = in_q.get()
    if data is not None:
      logging.debug("receive result", data)
      print("receive result", data)

t1 = Thread(target=consumer, args=(textCommandQueue,))
speak_recognition_thread = SpeechRecognizer(textCommandQueue)
t1.start()
speak_recognition_thread.start()
