# Cardputer ADV MicroPython

MicroPython hardware diagnostics and a compact MIDI frequency player for
M5Stack Cardputer ADV (ESP32-S3FN8, 8 MB flash, no PSRAM).

## Hardware support

- ST7789V2 240x135 display
- TCA8418 keyboard
- ES8311 audio codec and I2S speaker
- BMI270 IMU
- microSD card
- Battery ADC
- RGB LED and IR transmitter
- Wi-Fi scan

The display driver uses the Cardputer ST7789 offsets `x=40, y=53`.

## Firmware

The included firmware is the official MicroPython v1.29.0 build for
ESP32-S3:

```sh
esptool --port /dev/ttyACM0 erase-flash
esptool --port /dev/ttyACM0 --baud 460800 \
  write-flash 0x0 micropython-esp32s3-v1.29.0.bin
```

Install the local tools:

```sh
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Upload the programs:

```sh
.venv/bin/mpremote connect /dev/ttyACM0 fs cp hardware_test.py :hardware_test.py
.venv/bin/mpremote connect /dev/ttyACM0 fs cp midi_song.py :midi_song.py
.venv/bin/mpremote connect /dev/ttyACM0 fs cp midi_player.py :midi_player.py
.venv/bin/mpremote connect /dev/ttyACM0 fs cp main.py :main.py
```

At boot `main.py` starts the MIDI player in a paused state.

Controls:

- `P`: start, pause or resume
- `U`: volume up
- `I`: volume down
- `G0`: exit

## MIDI preprocessing

`midi_preprocess.py` reads a Standard MIDI file, chooses the track with the
most notes, retains up to three simultaneous pitches, and emits a compact
base64-encoded `(voice1_hz, voice2_hz, voice3_hz, duration_ms)` timeline.

```sh
.venv/bin/python midi_preprocess.py input.mid --output midi_song.py
```

The included song has 204 records and lasts about 88 seconds. The device
does not parse MIDI at runtime.

## Hardware test

Run the hardware test manually:

```sh
.venv/bin/mpremote connect /dev/ttyACM0 exec \
  'import hardware_test; hardware_test.run()'
```

The latest report is written to `/last_result.txt` on the device.
