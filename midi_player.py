"""Play a preprocessed three-voice MIDI frequency timeline."""

import array
import binascii
import struct
import time

import machine

import midi_song


SR = midi_song.SAMPLE_RATE
BLOCK_FRAMES = 256
STOP_BUTTON = machine.Pin(0, machine.Pin.IN, machine.Pin.PULL_UP)


class Keyboard:
    def __init__(self, i2c):
        self.i2c = i2c
        self.i2c.writeto_mem(0x34, 0x1D, b"\x7f")
        self.i2c.writeto_mem(0x34, 0x1E, b"\xff")
        self.i2c.writeto_mem(0x34, 0x1F, b"\x00")
        self.i2c.writeto_mem(0x34, 0x01, b"\x91")
        try:
            count = self.i2c.readfrom_mem(0x34, 0x03, 1)[0] & 0x0F
            for _ in range(count):
                self.i2c.readfrom_mem(0x34, 0x04, 1)
            status = self.i2c.readfrom_mem(0x34, 0x02, 1)[0]
            if status:
                self.i2c.writeto_mem(0x34, 0x02, bytes((status,)))
        except OSError:
            pass

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
        1,
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

    tone = array.array("h", [0] * (BLOCK_FRAMES * 2))
    view = memoryview(tone)
    offset = 0
    playing = autostart
    volume = 7
    current_voices = ()
    current_periods = ()
    current_halves = ()
    current_frames = 0
    phases = [0, 0, 0]
    active_ms = 0
    last_tick = time.ticks_ms()
    stopped_by_user = False
    print(midi_song.TITLE)
    print("P: start/pause, U/I: volume up/down, G0: exit")

    try:
        while offset + 8 <= len(data) or current_frames:
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

            if current_frames:
                frames = min(BLOCK_FRAMES, current_frames)
                if current_voices:
                    amplitude = volume * 1000 // len(current_voices)
                    for sample in range(frames):
                        mixed = 0
                        for voice in range(len(current_voices)):
                            if phases[voice] < current_halves[voice]:
                                mixed += amplitude
                            else:
                                mixed -= amplitude
                            phases[voice] += 1
                            if phases[voice] >= current_periods[voice]:
                                phases[voice] = 0
                        tone[2 * sample] = mixed
                        tone[2 * sample + 1] = mixed
                    audio.write(view[: frames * 2])
                else:
                    time.sleep_ms(max(1, frames * 1000 // SR))
                current_frames -= frames
                continue

            f1, f2, f3, duration_ms = struct.unpack_from(">HHHH", data, offset)
            offset += 8
            if duration_ms == 0:
                continue
            current_voices = tuple(
                frequency_hz for frequency_hz in (f1, f2, f3) if frequency_hz
            )
            current_periods = tuple(
                max(2, SR // frequency_hz) for frequency_hz in current_voices
            )
            current_halves = tuple(period // 2 for period in current_periods)
            phases = [0] * len(current_voices)
            current_frames = max(1, duration_ms * SR // 1000)
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
