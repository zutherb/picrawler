from picrawler import Picrawler
from time import sleep

crawler = Picrawler()

def twist(speed):
    new_step = [[50, 50, -80], [50, 50, -80], [50, 50, -80], [50, 50, -80]]

    for i in range(4):
        for inc in range(30, 60, 5):
            rise = [50, 50, (-80 + inc * 0.5)]
            drop = [50, 50, (-80 - inc)]

            new_step[i] = rise
            new_step[(i + 2) % 4] = drop
            new_step[(i + 1) % 4] = rise
            new_step[(i - 1) % 4] = drop

            crawler.do_step(new_step, speed)
            sleep(0.06)

def main():
    try:
        crawler.do_step('stand', 40)
        sleep(1.0)

        while True:
            twist(speed=55)

    except KeyboardInterrupt:
        print("\nCtrl+C detected, exiting...")

    finally:
        # Sit down safely before exit
        try:
            crawler.do_step('sit', 40)
            sleep(1.0)
        except Exception:
            pass

if __name__ == "__main__":
    main()