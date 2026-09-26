import machine
import time
import ujson
from bme680 import BME680_I2C

# Before running this file import a bme680.py and copy and paste from github robert-hh BME680-Micropython

# --- Config

# --- Dust Sensor ---

dust_led = machine.Pin(15, machine.Pin.OUT, value=1)
dust_adc = machine.ADC(26)  # ADC0

ADC_MAX_VOLTAGE = 3.3
ADC_MAX_VALUE = 65535

# --- Co2 Sensor ---

co2_adc = machine.ADC(27)  # ADC1

# Calibration parameters based on sensor specifications
# 0.4V = 400ppm, 2.0V = 5000ppm
MIN_VOLTAGE = 0.4
MAX_VOLTAGE = 2.0
MIN_PPM = 400
MAX_PPM = 5000

# --- Gravity Environment Sensor ---

# Initialize I2C0 on GP4 (SDA) and GP5 (SCL)
i2c = machine.I2C(0, sda=machine.Pin(4), scl=machine.Pin(5), freq=100000)

# Initialize the BME680 sensor
# Note: Use 0x77 or 0x76 depending on your specific DFRobot board configuration
sensor = BME680_I2C(i2c, address=0x77)

# --- Gas Sensor (MQ-2) ---

# dust_adc is ADC0 (GP26) and co2_adc is ADC1 (GP27), so MQ-2 must use the
# one remaining ADC pin, ADC2 (GP28). It was previously also set to
# machine.ADC(26), which silently made it read the exact same pin as the
# dust sensor instead of its own.
gas_sensor = machine.ADC(28)  # ADC2

# --- Wait an additional 1-2 minutes after power-on for the MQ-2 to warm up
# before its readings are meaningful. The Pi side (main.py) already ignores
# early MQ-2 readings via MQ2_WARMUP_S before it captures its baseline, so
# this script does not need to withhold data - just keep sending readings
# from power-on.

# --  Main loop ---
while True:
    # --- Dust Sensor: average 30 quick samples over ~300ms to smooth spikes,
    # and send the raw averaged voltage. Converting voltage to a density
    # (mg/m3) happens on the Pi in thresholds.py, so this script must not
    # duplicate that calculation.
    running_voltage_sum = 0
    samples = 30

    for _ in range(samples):
        # Pulse LED
        dust_led.value(0)
        time.sleep_us(280)
        raw_value = dust_adc.read_u16()
        time.sleep_us(40)
        dust_led.value(1)
        time.sleep_us(9680)

        voltage = (raw_value / ADC_MAX_VALUE) * ADC_MAX_VOLTAGE
        running_voltage_sum += voltage

    dust_voltage_v = running_voltage_sum / samples

    # --- Co2 Sensor Calculations
    # Read raw 16-bit ADC value (0-65535)
    raw_val = co2_adc.read_u16()

    # Convert raw value to voltage (Pico ADC reference is 3.3V)
    voltage = (raw_val / 65535.0) * 3.3

    # Calculate PPM using linear interpolation
    if voltage < MIN_VOLTAGE:
        co2_ppm = MIN_PPM  # Below the baseline lower threshold
    else:
        co2_ppm = ((voltage - MIN_VOLTAGE) / (MAX_VOLTAGE - MIN_VOLTAGE)) * (MAX_PPM - MIN_PPM) + MIN_PPM

    # Cap maximum range value
    if co2_ppm > MAX_PPM:
        co2_ppm = MAX_PPM

    # --- Gas Sensor (MQ-2): raw voltage only, no threshold logic here -
    # the Pi compares this against a baseline captured at startup.
    raw_value = gas_sensor.read_u16()
    mq2_voltage_v = (raw_value / 65535) * 3.3

    # --- Send one JSON line per cycle with all 7 fields the Pi expects.
    reading = {
        "temperature_c": round(sensor.temperature, 2),
        "humidity_pct": round(sensor.humidity, 2),
        "pressure_hpa": round(sensor.pressure, 2),
        "gas_resistance_ohm": sensor.gas,
        "co2_ppm": round(co2_ppm),
        "dust_voltage_v": round(dust_voltage_v, 3),
        "mq2_voltage_v": round(mq2_voltage_v, 3),
    }
    print(ujson.dumps(reading))

    # --- Wait for next batch of data
    time.sleep(2)
