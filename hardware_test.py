"""Cardputer ADV hardware self-test for MicroPython.

The board variant is an M5Stack Cardputer ADV based on ESP32-S3FN8
with an ST7789V2 display, TCA8418 keyboard controller, ES8311 codec,
BMI270 IMU, 8 MB flash and no PSRAM.
"""

import gc
import math
import os
import sys
import time
import array

import framebuf
import machine


# Cardputer ADV pin map
I2C_SDA = 8
I2C_SCL = 9
KEY_INT = 11

LCD_BL = 38
LCD_RST = 33
LCD_DC = 34
LCD_MOSI = 35
LCD_SCK = 36
LCD_CS = 37

SD_CS = 12
SD_MOSI = 14
SD_CLK = 40
SD_MISO = 39

I2S_BCLK = 41
I2S_LRCK = 43
I2S_DOUT = 42

IR_TX = 44
BAT_ADC = 10
RGB_DATA = 21

I2C_ES8311 = 0x18
I2C_TCA8418 = 0x34
I2C_BMI270 = 0x68

WIDTH = 240
HEIGHT = 135
LCD_X_OFFSET = 40
LCD_Y_OFFSET = 53

BLACK = 0x0000
WHITE = 0xFFFF
RED = 0xF800
GREEN = 0x07E0
BLUE = 0x001F
YELLOW = 0xFFE0
CYAN = 0x07FF
GRAY = 0x8410

RESULTS = []
DISPLAY = None
I2C = None


def _rgb565(r, g, b):
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


class ST7789:
    def __init__(self):
        self.spi = machine.SPI(
            1,
            baudrate=40_000_000,
            polarity=0,
            phase=0,
            sck=machine.Pin(LCD_SCK),
            mosi=machine.Pin(LCD_MOSI),
        )
        self.cs = machine.Pin(LCD_CS, machine.Pin.OUT, value=1)
        self.dc = machine.Pin(LCD_DC, machine.Pin.OUT, value=1)
        self.rst = machine.Pin(LCD_RST, machine.Pin.OUT, value=1)
        self.bl = machine.Pin(LCD_BL, machine.Pin.OUT, value=0)
        self._reset()
        self._init()
        self.bl.value(1)

    def _reset(self):
        self.rst.value(1)
        time.sleep_ms(20)
        self.rst.value(0)
        time.sleep_ms(20)
        self.rst.value(1)
        time.sleep_ms(120)

    def _cmd(self, command, data=None):
        self.cs.value(0)
        self.dc.value(0)
        self.spi.write(bytes((command,)))
        if data:
            self.dc.value(1)
            self.spi.write(data)
        self.cs.value(1)

    def _init(self):
        self._cmd(0x01)
        time.sleep_ms(120)
        self._cmd(0x11)
        time.sleep_ms(120)
        self._cmd(0x3A, b"\x55")
        self._cmd(0x36, b"\x60")
        self._cmd(0xB2, b"\x0c\x0c\x00\x33\x33")
        self._cmd(0xB7, b"\x35")
        self._cmd(0xBB, b"\x19")
        self._cmd(0xC0, b"\x2c")
        self._cmd(0xC2, b"\x01")
        self._cmd(0xC3, b"\x12")
        self._cmd(0xC4, b"\x20")
        self._cmd(0xC6, b"\x0f")
        self._cmd(0xD0, b"\xa4\xa1")
        self._cmd(0x21)
        self._cmd(0x13)
        self._cmd(0x29)
        time.sleep_ms(20)

    def _window(self, x, y, w, h):
        x += LCD_X_OFFSET
        y += LCD_Y_OFFSET
        x1 = x + w - 1
        y1 = y + h - 1
        self._cmd(
            0x2A,
            bytes((x >> 8, x & 0xFF, x1 >> 8, x1 & 0xFF)),
        )
        self._cmd(
            0x2B,
            bytes((y >> 8, y & 0xFF, y1 >> 8, y1 & 0xFF)),
        )
        self._cmd(0x2C)

    def _fill_pixels(self, color, pixels):
        chunk_pixels = min(pixels, 1024)
        pixel = bytes((color >> 8, color & 0xFF))
        chunk = pixel * chunk_pixels
        self.cs.value(0)
        self.dc.value(1)
        while pixels >= chunk_pixels:
            self.spi.write(chunk)
            pixels -= chunk_pixels
        if pixels:
            self.spi.write(pixel * pixels)
        self.cs.value(1)

    def fill(self, color):
        self._window(0, 0, WIDTH, HEIGHT)
        self._fill_pixels(color, WIDTH * HEIGHT)

    def fill_rect(self, x, y, w, h, color):
        if w <= 0 or h <= 0:
            return
        self._window(x, y, w, h)
        self._fill_pixels(color, w * h)

    def text_line(self, text, y, color=WHITE, background=BLACK):
        buf = bytearray(WIDTH * 16 * 2)
        frame = framebuf.FrameBuffer(buf, WIDTH, 16, framebuf.RGB565)
        frame.fill(background)
        frame.text(text[:29], 3, 4, color)
        for index in range(0, len(buf), 2):
            buf[index], buf[index + 1] = buf[index + 1], buf[index]
        self._window(0, y, WIDTH, 16)
        self.cs.value(0)
        self.dc.value(1)
        self.spi.write(buf)
        self.cs.value(1)

    def status(self, title, lines, accent=CYAN):
        self.fill(BLACK)
        self.fill_rect(0, 0, WIDTH, 16, accent)
        self.text_line(title, 0, BLACK, accent)
        y = 18
        for line in lines[-6:]:
            self.text_line(line, y, WHITE, BLACK)
            y += 17


def log(message=""):
    print(message)


def add_result(name, status, detail=""):
    RESULTS.append((name, status, detail))
    log("[{}] {} {}".format(status, name, detail))
    if DISPLAY:
        lines = [
            "{} {:<7} {}".format(
                {"PASS": "+", "WARN": "!", "FAIL": "x"}.get(item[1], "?"),
                item[0],
                item[2],
            )
            for item in RESULTS
        ]
        DISPLAY.status("Cardputer ADV hardware test", lines)


def run_test(name, callback):
    started = time.ticks_ms()
    try:
        detail, status = callback()
        elapsed = time.ticks_diff(time.ticks_ms(), started)
        add_result(name, status, "{} ({} ms)".format(detail, elapsed))
        gc.collect()
        return True
    except Exception as error:
        elapsed = time.ticks_diff(time.ticks_ms(), started)
        add_result(name, "FAIL", "{} ({} ms)".format(error, elapsed))
        return False


def init_i2c():
    global I2C
    I2C = machine.I2C(
        0,
        sda=machine.Pin(I2C_SDA, machine.Pin.PULL_UP),
        scl=machine.Pin(I2C_SCL, machine.Pin.PULL_UP),
        freq=400_000,
    )
    return I2C


def test_system():
    import esp

    flash_mb = esp.flash_size() // (1024 * 1024)
    freq_mhz = machine.freq() // 1_000_000
    detail = "S3 {}MHz, {}MB flash, {}KB heap".format(
        freq_mhz, flash_mb, gc.mem_free() // 1024
    )
    if flash_mb < 8:
        return detail, "WARN"
    return detail, "PASS"


def test_display():
    global DISPLAY
    DISPLAY = ST7789()
    driver = DISPLAY
    for color in (RED, GREEN, BLUE, WHITE):
        driver.fill(color)
        time.sleep_ms(180)
    driver.fill(BLACK)
    driver.fill_rect(0, 0, WIDTH, 16, CYAN)
    driver.text_line("Cardputer ADV", 0, BLACK, CYAN)
    driver.text_line("ST7789 240x135", 19, WHITE)
    driver.text_line("RED  GREEN  BLUE", 38, YELLOW)
    driver.text_line("MicroPython hardware test", 57, GREEN)
    return "240x135 ST7789", "PASS"


def test_i2c():
    if I2C is None:
        init_i2c()
    devices = I2C.scan()
    wanted = {
        I2C_TCA8418: "TCA8418",
        I2C_ES8311: "ES8311",
        I2C_BMI270: "BMI270",
        I2C_BMI270 + 1: "BMI270",
    }
    found = [name for address, name in wanted.items() if address in devices]
    detail = " ".join(found) if found else "devices=" + str(devices)
    return detail, "PASS" if len(found) >= 2 else "WARN"


def test_imu():
    addresses = (I2C_BMI270, I2C_BMI270 + 1)
    for address in addresses:
        try:
            chip_id = I2C.readfrom_mem(address, 0, 1)[0]
            return "id=0x{:02x} @0x{:02x}".format(chip_id, address), (
                "PASS" if chip_id == 0x24 else "WARN"
            )
        except OSError:
            pass
    raise OSError("BMI270 not found")


KEYMAP = (
    ("`", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "-", "=", "DEL"),
    ("TAB", "q", "w", "e", "r", "t", "y", "u", "i", "o", "p", "[", "]", "\\"),
    ("SHIFT", "CAPS", "a", "s", "d", "f", "g", "h", "j", "k", "l", ";", "'", "ENTER"),
    ("CTRL", "OPT", "ALT", "z", "x", "c", "v", "b", "n", "m", ",", ".", "/", "SPACE"),
)


def _decode_key(event):
    code = event & 0x7F
    pressed = bool(event & 0x80)
    if code == 0:
        return "?"
    value = code - 1
    raw_row = value // 10
    raw_col = value % 10
    row = (raw_col + 4) % 4
    col = raw_row * 2 + (1 if raw_col > 3 else 0)
    if row < 4 and col < 14:
        key = KEYMAP[row][col]
    else:
        key = "code {}".format(code)
    return "{} {}".format(key, "down" if pressed else "up")


def test_keyboard():
    I2C.writeto_mem(I2C_TCA8418, 0x1D, b"\x7f")
    I2C.writeto_mem(I2C_TCA8418, 0x1E, b"\xff")
    I2C.writeto_mem(I2C_TCA8418, 0x1F, b"\x00")
    I2C.writeto_mem(I2C_TCA8418, 0x01, b"\x91")
    try:
        fault = I2C.readfrom_mem(I2C_TCA8418, 0x02, 1)[0]
        if fault:
            I2C.writeto_mem(I2C_TCA8418, 0x02, bytes((fault,)))
    except OSError:
        pass

    log("Keyboard ready: press keys for 5 seconds")
    if DISPLAY:
        DISPLAY.status("Keyboard test", ("TCA8418 ready", "Press any key..."))
    events = 0
    deadline = time.ticks_add(time.ticks_ms(), 5000)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        try:
            count = I2C.readfrom_mem(I2C_TCA8418, 0x03, 1)[0] & 0x0F
        except OSError:
            count = 0
        for _ in range(count):
            try:
                event = I2C.readfrom_mem(I2C_TCA8418, 0x04, 1)[0]
            except OSError:
                break
            if event:
                events += 1
                log("KEY {}".format(_decode_key(event)))
        if count:
            try:
                status = I2C.readfrom_mem(I2C_TCA8418, 0x02, 1)[0]
                if status:
                    I2C.writeto_mem(I2C_TCA8418, 0x02, bytes((status,)))
            except OSError:
                pass
        time.sleep_ms(20)
    if events:
        return "{} event(s)".format(events), "PASS"
    return "ready, no key pressed", "WARN"


def test_sd():
    card = machine.SDCard(
        slot=2,
        sck=machine.Pin(SD_CLK),
        mosi=machine.Pin(SD_MOSI),
        miso=machine.Pin(SD_MISO),
        cs=machine.Pin(SD_CS),
        freq=1_000_000,
    )
    mounted = False
    try:
        try:
            os.mount(card, "/sd")
        except OSError as error:
            if error.args and error.args[0] in (5, 16, 19):
                return "no SD card detected", "WARN"
            raise
        mounted = True
        try:
            names = os.listdir("/sd")
            detail = "{} item(s)".format(len(names))
            if names:
                detail += ": " + ", ".join(names[:3])
            return detail, "PASS"
        finally:
            os.umount("/sd")
            mounted = False
    finally:
        if mounted:
            os.umount("/sd")
        card.deinit()


def test_battery():
    adc = machine.ADC(machine.Pin(BAT_ADC))
    try:
        adc.atten(machine.ADC.ATTN_11DB)
    except AttributeError:
        pass
    samples = [adc.read_u16() for _ in range(16)]
    raw = sum(samples) // len(samples)
    millivolts = raw * 3300 * 2 // 65535
    return "ADC={} est={}mV".format(raw, millivolts), "PASS"


def test_ir():
    pwm = machine.PWM(machine.Pin(IR_TX), freq=38_000, duty_u16=32768)
    time.sleep_ms(120)
    pwm.deinit()
    machine.Pin(IR_TX, machine.Pin.OUT, value=0)
    return "38kHz pulse sent", "PASS"


def test_rgb():
    import neopixel

    machine.Pin(LCD_BL, machine.Pin.OUT, value=1)
    led = neopixel.NeoPixel(machine.Pin(RGB_DATA), 1)
    for color in ((32, 0, 0), (0, 32, 0), (0, 0, 32)):
        led[0] = color
        led.write()
        time.sleep_ms(180)
    led[0] = (0, 0, 0)
    led.write()
    return "R-G-B pulse", "PASS"


def _es8311_write(register, value):
    I2C.writeto_mem(I2C_ES8311, register, bytes((value,)))
    time.sleep_ms(2)


def test_audio():
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
        _es8311_write(register, value)

    i2s = machine.I2S(
        0,
        sck=machine.Pin(I2S_BCLK),
        ws=machine.Pin(I2S_LRCK),
        sd=machine.Pin(I2S_DOUT),
        mode=machine.I2S.TX,
        bits=16,
        format=machine.I2S.STEREO,
        rate=44_100,
        ibuf=4096,
    )
    tones = 10
    total = 1024
    data = array.array("h", [0] * (total * 2))
    for n in range(total):
        value = int(7000 * math.sin(2 * math.pi * tones * n / total))
        data[2 * n] = value
        data[2 * n + 1] = value
    try:
        for _ in range(20):
            i2s.write(data)
        time.sleep_ms(100)
    finally:
        i2s.deinit()
    return "ES8311 + I2S ~431Hz", "PASS"


def test_wifi():
    import network

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    try:
        networks = wlan.scan()
        if networks:
            best = max(networks, key=lambda item: item[3])
            ssid = best[0].decode("utf-8", "replace")
            detail = "{} AP, strongest {}".format(len(networks), ssid[:16])
            status = "PASS"
        else:
            detail = "radio works, no AP found"
            status = "WARN"
    finally:
        wlan.active(False)
    return detail, status


def show_summary():
    passed = sum(1 for item in RESULTS if item[1] == "PASS")
    warned = sum(1 for item in RESULTS if item[1] == "WARN")
    failed = sum(1 for item in RESULTS if item[1] == "FAIL")
    summary = "PASS {}  WARN {}  FAIL {}".format(passed, warned, failed)
    log()
    log("===== SUMMARY: {} =====".format(summary))
    for name, status, detail in RESULTS:
        log("{:<10} {:<4} {}".format(name, status, detail))
    try:
        with open("/last_result.txt", "w") as report:
            report.write("SUMMARY: {}\n".format(summary))
            for name, status, detail in RESULTS:
                report.write("{:<10} {:<4} {}\n".format(name, status, detail))
    except OSError as error:
        log("report save failed: {}".format(error))
    if DISPLAY:
        lines = [
            "{} {:<7} {}".format(
                {"PASS": "+", "WARN": "!", "FAIL": "x"}.get(item[1], "?"),
                item[0],
                item[2][:20],
            )
            for item in RESULTS
        ]
        DISPLAY.status(summary, lines, GREEN if not failed else RED)
    log("MicroPython hardware test complete")


def run():
    global DISPLAY
    RESULTS.clear()
    log()
    log("Cardputer ADV MicroPython hardware test")
    log("platform={} version={}".format(sys.platform, sys.version))

    try:
        machine.freq(240_000_000)
    except Exception:
        pass
    run_test("system", test_system)
    display_ok = run_test("display", test_display)
    if not display_ok:
        DISPLAY = None
    run_test("i2c", test_i2c)
    run_test("imu", test_imu)
    run_test("keyboard", test_keyboard)
    run_test("sd", test_sd)
    run_test("battery", test_battery)
    run_test("rgb", test_rgb)
    run_test("ir", test_ir)
    run_test("audio", test_audio)
    gc.collect()
    run_test("wifi", test_wifi)
    show_summary()


if __name__ == "__main__":
    run()
