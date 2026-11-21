"""Play a preprocessed monophonic MIDI frequency timeline."""

import array
import binascii
import struct
import time

import machine

import midi_song


SR = midi_song.SAMPLE_RATE
STOP_BUTTON = machine.Pin(0, machine.Pin.IN, machine.Pin.PULL_UP)


class Keyboard:
    def __init__(self, i2c):
        self.i2c = i2c
        self.i2c.writeto_mem(0x34, 0x1D, b"\x7f")
        self.i2c.writeto_mem(0x34, 0x1E, b"\xff")
        self.i2c.writeto_mem(0x34, 0x1F, b"\x00")
        self.i2c.writeto_mem(0x34, 0x01, b"\x91")

    def poll(self):
        toggle_play = False
        volume_delta = 0
        count = self.i2c.readfrom_mem(0x34, 0x03, 1)[0] & 0x0F
        for _ in range(count):
            event = self.i2c.readfrom_mem(0x34, 0x04, 1)[0]
            code = event & 0x7F
            pressed = bool(event & 0x80)
            if not code or not pressed:
                continue
            value = code - 1
            raw_row = value // 10
            raw_col = value % 10
            row = (raw_col + 4) % 4
            col = raw_row * 2 + (1 if raw_col > 3 else 0)
            if row == 1 and col == 10:
                toggle_play = True
            elif row == 1 and col == 7:
                volume_delta += 1
            elif row == 1 and col == 8:
                volume_delta -= 1
        if count:
            status = self.i2c.readfrom_mem(0x34, 0x02, 1)[0]
            if status:
                self.i2c.writeto_mem(0x34, 0x02, bytes((status,)))
        return toggle_play, volume_delta


def _init_codec(i2c):
    for register, value in (
        (0x00, 0x80),
        (0x01, 0xB5),
        (0x02, 0x18),
        (0x0D, 0x01),
        (0x12, 0x00),
        (0x13, 0x10),
        (0x32, 0xBF),
        (0x37, 0x08),
    ):
        i2c.writeto_mem(0x18, register, bytes((value,)))
        time.sleep_ms(2)


def _init_i2c():
    return machine.I2C(
        0,
        sda=machine.Pin(8),
        scl=machine.Pin(9),
        freq=400_000,
    )


def play(autostart=False, max_seconds=None):
    data = binascii.a2b_base64("".join(midi_song.DATA_B64))
    i2c = _init_i2c()
    keyboard = Keyboard(i2c)
    _init_codec(i2c)
    audio = machine.I2S(
        0,
        sck=machine.Pin(41),
        ws=machine.Pin(43),
        sd=machine.Pin(42),
        mode=machine.I2S.TX,
        bits=16,
        format=machine.I2S.STEREO,
        rate=SR,
        ibuf=4096,
    )

    screen = None
    try:
        from hardware_test import ST7789

        screen = ST7789()
        screen.status(
            "MIDI ready",
            (
                midi_song.TITLE[:28],
                "Volume 7 / 10",
                "P start/pause  U/I volume",
                "G0 exits",
            ),
        )
    except Exception:
        pass

    tone = array.array("h", [0] * 2048)
    view = memoryview(tone)
    offset = 0
    playing = autostart
    volume = 7
    current_cycles = 0
    current_rest = 0
    active_ms = 0
    last_tick = time.ticks_ms()
    stopped_by_user = False
    print(midi_song.TITLE)
    print("P: start/pause, U/I: volume up/down, G0: exit")

    try:
        while offset + 4 <= len(data) or current_cycles or current_rest:
            now = time.ticks_ms()
            if playing:
                active_ms += time.ticks_diff(now, last_tick)
            last_tick = now

            if max_seconds is not None and active_ms >= max_seconds * 1000:
                break
            if STOP_BUTTON.value() == 0:
                print("Stopped by G0")
                stopped_by_user = True
                break

            toggle_play, volume_delta = keyboard.poll()
            if volume_delta:
                old_volume = volume
                volume = min(10, max(0, volume + volume_delta))
                if volume != old_volume:
                    print("Volume", volume, "/ 10")
                    if current_cycles:
                        half = current_period // 2
                        for sample in range(current_period):
                            value = volume * 1000 if sample < half else -volume * 1000
                            tone[2 * sample] = value
                            tone[2 * sample + 1] = value
                    if screen:
                        screen.status(
                            "MIDI playing" if playing else "MIDI paused",
                            (
                                midi_song.TITLE[:28],
                                "Volume {}".format(volume),
                                "P play/pause  U/I volume",
                            ),
                        )

            if toggle_play:
                playing = not playing
                print("Playing" if playing else "Paused")
                if screen:
                    screen.status(
                        "MIDI playing" if playing else "MIDI paused",
                        (
                            midi_song.TITLE[:28],
                            "Volume {}".format(volume),
                            "P play/pause  U/I volume",
                            "G0 exits",
                        ),
                    )
                continue

            if not playing:
                time.sleep_ms(20)
                continue

            if current_rest:
                step = min(current_rest, 20)
                time.sleep_ms(step)
                current_rest -= step
                continue

            if current_cycles:
                count = min(current_cycles, 4)
                cycle_buffer = view[:current_period * 2]
                for _ in range(count):
                    audio.write(cycle_buffer)
                current_cycles -= count
                continue

            frequency_hz, duration_ms = struct.unpack_from(">HH", data, offset)
            offset += 4
            if duration_ms == 0:
                continue

            if frequency_hz == 0:
                current_rest = duration_ms
                continue

            current_period = max(2, SR // frequency_hz)
            half = current_period // 2
            for sample in range(current_period):
                value = volume * 1000 if sample < half else -volume * 1000
                tone[2 * sample] = value
                tone[2 * sample + 1] = value
            current_cycles = max(
                1, duration_ms * SR // (current_period * 1000)
            )
    finally:
        time.sleep_ms(80)
        audio.deinit()

    elapsed = active_ms // 1000
    print("Finished after", elapsed, "active seconds")
    if screen:
        screen.status(
            "MIDI stopped" if stopped_by_user else "MIDI finished",
            (midi_song.TITLE[:28], "{} seconds".format(elapsed)),
        )


if __name__ == "__main__":
    play()
