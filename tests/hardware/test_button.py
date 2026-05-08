from hardware.button import Button
import time

button = Button(
    pin_num=1,
    pull_up=True,
    active_high=True
)

while True:
    if button.is_pressed:
        print("PRESSED")
    else:
        print("RELEASED")

    time.sleep_ms(100)