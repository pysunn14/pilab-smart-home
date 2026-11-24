"""Logical LCD output. No I2C bus is used in the integration profile."""

import json


class CharLCD:
    def __init__(self, **settings):
        self.settings = settings

    def write_string(self, text):
        print(json.dumps({"event": "lcd", "text": text}), flush=True)

    def clear(self):
        print(json.dumps({"event": "lcd_clear"}), flush=True)
