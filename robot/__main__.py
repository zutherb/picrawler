import sys
import vosk

def run_robot(args):
  print("Starting robot:", sys.argv)
  model = vosk.Model("/opt/vosk/model")

if __name__ == '__main__':
  run_robot(sys.argv)
