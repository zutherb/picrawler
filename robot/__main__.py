from queue import Queue
from threading import Thread

from sensor import speech_recognition

textCommandQueue = Queue()

def consumer(in_q):
  while True:
    data = in_q.get()
    if data is not None:
      print("receive result", data)


print('#' * 80)
print('Press Ctrl+C to stop the recording')
print('#' * 80)

t1 = Thread(target=consumer, args=(textCommandQueue,))
speak_recognition_thread = Thread(target=speech_recognition, args=(textCommandQueue,))
t1.start()
speak_recognition_thread.start()
