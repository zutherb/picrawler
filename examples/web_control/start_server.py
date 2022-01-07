from os import system
from ezblock import getIP
import time
import os
from ezblock import __reset_mcu__
import time
__reset_mcu__()
time.sleep(0.01)

CODE_DIR = "/home/pi/picrawler/examples/web_control"


def start_http_server():
    system(f"cd {CODE_DIR}/web_client && sudo python3 -m http.server 80 2>&1 1> httpserver.log &")#开启服务器

def close_http_server():
    system("sudo kill $(ps aux | grep 'http.server' | awk '{ print $2 }') 2>&1 1>httpserver.log")

def start_websocket():
    system(f"cd {CODE_DIR}/web_server && sudo python3 web_server.py 2>&1 1> websocket.log &")

def close_websocket():
    # print("close_websocket")
    system("sudo kill $(ps aux | grep 'web_server.py' | awk '{ print $2 }') 2>&1 1> websocket.log")



if __name__ == '__main__':
    try:
        for _ in range(10):
            ip = getIP()
            if ip:
                break
            time.sleep(1)
        start_websocket()
        start_http_server()
        print("Web example starts at %s" % (ip))
        print("Open http://%s in your web browser to control the car!" % (ip))
        # print("Press Ctrl + C to exit")
        while 1:

            pass
    except KeyboardInterrupt:
        print('KeyboardInterrupt')
    finally:
        print("Finished")
        close_websocket()
        close_http_server()
