"""
Central configuration for the Maker Place Air Quality Checker.
Values can be overridden with environment variables so the same code runs
unchanged on a laptop (mock mode) or on the Raspberry Pi (real hardware).
"""

import os


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    val = os.environ.get(name)
    return float(val) if val else default


def _env_int(name: str, default: int) -> int:
    val = os.environ.get(name)
    return int(val) if val else default


# Set MOCK_MODE=1 (or pass --mock to main.py) to run without any hardware.
MOCK_MODE = _env_bool("MOCK_MODE", False)

# BME680 I2C address. The BME680 is wired to the Pico (not the Pi), so this
# value is informational/documentation only - it's not read by any Python
# code on the Pi side. The Pico's own script hardcodes the address it uses.
BME680_I2C_ADDRESS = int(os.environ.get("BME680_I2C_ADDRESS", "0x77"), 0)

# Serial port the Pico shows up on. On Linux/Raspberry Pi this is typically
# /dev/ttyACM0; on Windows during development it would be a COM port.
PICO_SERIAL_PORT = os.environ.get("PICO_SERIAL_PORT", "/dev/ttyACM0")
PICO_SERIAL_BAUDRATE = _env_int("PICO_SERIAL_BAUDRATE", 115200)
PICO_SERIAL_TIMEOUT_S = _env_float("PICO_SERIAL_TIMEOUT_S", 2.0)

# How often main.py reads sensors and appends a row to history.
READ_INTERVAL_S = _env_float("READ_INTERVAL_S", 30.0)

# Number of initial readings averaged at startup to build the "clean air"
# baseline used by thresholds.evaluate() for VOC (gas resistance) and MQ-2.
BASELINE_SAMPLE_COUNT = _env_int("BASELINE_SAMPLE_COUNT", 5)
BASELINE_SAMPLE_INTERVAL_S = _env_float("BASELINE_SAMPLE_INTERVAL_S", 2.0)

# MQ-2 needs a warm-up period after power-on before readings are meaningful.
MQ2_WARMUP_S = _env_float("MQ2_WARMUP_S", 90.0)

# Rapid environmental change detection: flags temperature and humidity both
# rising quickly together over a short window (e.g. a door propped open to
# hot/humid outside air, a steam/water leak, a heat-emitting machine running,
# or a crowd suddenly filling the space) - something thresholds.py's
# single-reading model can't catch, since it needs history.
#
# Defaults reasoning: under stable indoor conditions, temperature and
# humidity drift only a fraction of a degree/percent over 5 minutes, so a
# genuine environmental disturbance stands out clearly at these levels. They
# are intentionally much looser than fire-alarm rate-of-rise heat detectors
# (commonly ~4-8 C/min) - this isn't a fire alarm, it's a "check ventilation"
# nudge, so being too sensitive would just cause alert fatigue. Both deltas
# are required together (not either alone) because a correlated rise in both
# readings is a much more specific signal of an actual environmental event
# than either sensor drifting/noising on its own.
RAPID_CHANGE_WINDOW_S = _env_float("RAPID_CHANGE_WINDOW_S", 300.0)  # 5 minutes
RAPID_CHANGE_TEMP_DELTA_C = _env_float("RAPID_CHANGE_TEMP_DELTA_C", 2.0)
RAPID_CHANGE_HUMIDITY_DELTA_PCT = _env_float("RAPID_CHANGE_HUMIDITY_DELTA_PCT", 10.0)

# Data files.
DATA_DIR = os.environ.get("DATA_DIR", "data")
HISTORY_CSV_PATH = os.path.join(DATA_DIR, "history.csv")
LATEST_JSON_PATH = os.path.join(DATA_DIR, "latest.json")

# How many recent rows the dashboard chart requests by default.
HISTORY_CHART_POINTS = _env_int("HISTORY_CHART_POINTS", 60)

# Flask server.
FLASK_HOST = os.environ.get("FLASK_HOST", "0.0.0.0")
FLASK_PORT = _env_int("FLASK_PORT", 5000)
FLASK_DEBUG = _env_bool("FLASK_DEBUG", False)
