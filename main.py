"""
Maker Place Air Quality Checker - sensor reading & logging loop.

Run this as a background process/service. It is intentionally separate from
app.py (the Flask server) because they have different failure modes and
lifecycles: this loop must keep running and appending data even if nobody is
viewing the dashboard, and a Flask dev-server reload should never interrupt
sensor logging. Running them as two processes also means the dashboard can
be restarted (or crash) without losing readings, and vice versa.

Usage:
    python main.py             # uses real hardware (or config.MOCK_MODE env var)
    python main.py --mock      # force mock mode regardless of env var
"""

import argparse
import csv
import json
import logging
import os
import time
from collections import deque
from datetime import datetime, timezone

import config
from sensors import SensorHub
from thresholds import ALERT_LEVELS, evaluate

# Maps a raw reading field to the status key thresholds.evaluate() derives
# from it, so a missing sensor can be reported as offline instead of crashing
# evaluate() (which does not expect None) or silently reading as "normal".
_FIELD_TO_STATUS_KEY = {
    "co2_ppm": "co2",
    "temperature_c": "temperature",
    "humidity_pct": "humidity",
    "pressure_hpa": "pressure",
    "gas_resistance_ohm": "voc",
    "dust_voltage_v": "dust",
    "mq2_voltage_v": "gas_mq2",
}
# Neutral stand-ins used only so evaluate() has something to compare against
# for a missing field; the resulting status is overwritten with "N/A" below,
# so these values never reach the dashboard.
_DUMMY_VALUES = {
    "co2_ppm": 400,
    "temperature_c": 22.0,
    "humidity_pct": 45.0,
    "pressure_hpa": 1013.0,
    "gas_resistance_ohm": 100000.0,
    "dust_voltage_v": 0.4,
    "mq2_voltage_v": 0.5,
}
SENSOR_OFFLINE_STATUS = "N/A (sensor offline)"


def safe_evaluate(reading: dict, baseline_gas_resistance_ohm, baseline_mq2_voltage) -> dict:
    """
    Wraps thresholds.evaluate() so a disconnected/failed sensor (value None)
    degrades to an explicit "N/A (sensor offline)" status instead of crashing
    the read loop or misreporting as normal. ventilation_alert/alert_level are
    recomputed from only the sensors that actually reported a value.
    """
    missing_fields = [key for key, value in reading.items() if value is None]
    safe_reading = {
        key: (value if value is not None else _DUMMY_VALUES.get(key))
        for key, value in reading.items()
    }

    statuses = evaluate(
        safe_reading,
        baseline_gas_resistance_ohm=baseline_gas_resistance_ohm,
        baseline_mq2_voltage=baseline_mq2_voltage,
    )

    for field in missing_fields:
        status_key = _FIELD_TO_STATUS_KEY.get(field)
        if status_key:
            statuses[status_key] = SENSOR_OFFLINE_STATUS
    if "dust_voltage_v" in missing_fields:
        statuses["dust_density_ugm3"] = None

    valid_levels = [
        ALERT_LEVELS.get(value, 0)
        for key, value in statuses.items()
        if key not in ("ventilation_alert", "alert_level", "dust_density_ugm3")
        and value != SENSOR_OFFLINE_STATUS
    ]
    max_level = max(valid_levels) if valid_levels else 0
    statuses["alert_level"] = max_level
    statuses["ventilation_alert"] = max_level >= 1
    return statuses


class RecentReadingsBuffer:
    """
    Keeps a short in-memory history of (temperature, humidity) so the read
    loop can compare "now" against "~RAPID_CHANGE_WINDOW_S ago" without any
    new storage mechanism - this is process-local, bounded, and reset on
    restart, which is fine since it only needs to span a few minutes.
    """

    def __init__(self, window_s: float = config.RAPID_CHANGE_WINDOW_S):
        self.window_s = window_s
        self._entries = deque()  # (monotonic_timestamp, temperature_c, humidity_pct)

    def add_and_get_reference(self, temperature_c, humidity_pct):
        """
        Records the current reading and returns the (temperature_c,
        humidity_pct) from the most recent entry that is at least
        window_s old - i.e. the closest available approximation of
        "the reading from ~window_s ago" - or None if not enough history
        has accumulated yet (e.g. right after startup).
        """
        now = time.monotonic()

        reference = None
        for timestamp, temp, humidity in self._entries:
            if now - timestamp >= self.window_s:
                reference = (temp, humidity)
            else:
                break  # entries are in insertion (time) order, so we can stop early

        # Bound memory: once an entry is older than we could ever need again
        # (a bit past the window), it can never become a useful reference,
        # so drop it.
        while self._entries and (now - self._entries[0][0]) > self.window_s * 1.5:
            self._entries.popleft()

        if temperature_c is not None and humidity_pct is not None:
            self._entries.append((now, temperature_c, humidity_pct))

        return reference


def check_rapid_change(reference, temperature_c, humidity_pct):
    """
    Flags a "Rapid environmental change" alert when BOTH temperature and
    humidity have risen by more than their configured deltas since the
    reference reading (see config.RAPID_CHANGE_* for the reasoning behind
    the default thresholds). Returns (alert: bool, temp_delta, humidity_delta) -
    the deltas are returned even when alert is False so they can be logged
    for the report.
    """
    if reference is None or temperature_c is None or humidity_pct is None:
        return False, None, None

    ref_temp, ref_humidity = reference
    temp_delta = round(temperature_c - ref_temp, 2)
    humidity_delta = round(humidity_pct - ref_humidity, 2)

    alert = (
        temp_delta > config.RAPID_CHANGE_TEMP_DELTA_C
        and humidity_delta > config.RAPID_CHANGE_HUMIDITY_DELTA_PCT
    )
    return alert, temp_delta, humidity_delta


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Row order for history.csv
CSV_FIELDS = [
    "timestamp",
    "source",  # "mock" or "real" - lets old rows never be mistaken for live hardware data
    "co2_ppm",
    "temperature_c",
    "humidity_pct",
    "pressure_hpa",
    "gas_resistance_ohm",
    "dust_voltage_v",
    "dust_density_ugm3",
    "mq2_voltage_v",
    "status_co2",
    "status_temperature",
    "status_humidity",
    "status_pressure",
    "status_voc",
    "status_dust",
    "status_gas_mq2",
    "ventilation_alert",
    "alert_level",
    "rapid_change_alert",
    "temp_delta_5min_c",
    "humidity_delta_5min_pct",
]


def capture_baseline(hub: SensorHub) -> tuple:
    """
    Averages the first BASELINE_SAMPLE_COUNT readings to build the "clean air"
    reference thresholds.evaluate() needs for VOC (gas resistance) and MQ-2.

    Note: MQ-2 needs a warm-up period after power-on before its readings are
    valid. In mock mode this warm-up is skipped since there is no real sensor.
    """
    if not hub.mock_mode and config.MQ2_WARMUP_S > 0:
        logger.info("Waiting %.0fs for MQ-2 warm-up before capturing baseline...",
                    config.MQ2_WARMUP_S)
        time.sleep(config.MQ2_WARMUP_S)

    logger.info("Capturing baseline from %d samples...", config.BASELINE_SAMPLE_COUNT)
    gas_samples = []
    mq2_samples = []
    for i in range(config.BASELINE_SAMPLE_COUNT):
        reading = hub.read_all()
        if reading.get("gas_resistance_ohm") is not None:
            gas_samples.append(reading["gas_resistance_ohm"])
        if reading.get("mq2_voltage_v") is not None:
            mq2_samples.append(reading["mq2_voltage_v"])
        time.sleep(config.BASELINE_SAMPLE_INTERVAL_S)

    baseline_gas = sum(gas_samples) / len(gas_samples) if gas_samples else 0
    baseline_mq2 = sum(mq2_samples) / len(mq2_samples) if mq2_samples else 0
    logger.info("Baseline captured: gas_resistance_ohm=%.1f, mq2_voltage=%.3f",
                baseline_gas, baseline_mq2)
    return baseline_gas, baseline_mq2


def ensure_data_files():
    os.makedirs(config.DATA_DIR, exist_ok=True)
    if not os.path.exists(config.HISTORY_CSV_PATH):
        with open(config.HISTORY_CSV_PATH, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=CSV_FIELDS).writeheader()
        return

    with open(config.HISTORY_CSV_PATH, "r", newline="", encoding="utf-8") as f:
        existing_header = next(csv.reader(f), None)

    if existing_header != CSV_FIELDS:
        # Old file predates the "source" column (or some other schema
        # change). Rather than appending mismatched columns under the old
        # header - or silently overwriting history - move it aside and
        # start a fresh file with the current header.
        backup_path = config.HISTORY_CSV_PATH + ".pre-migration.bak"
        logger.warning(
            "%s has an outdated header %s (expected %s). "
            "Moving old file to %s and starting a new one so rows stay aligned with the header.",
            config.HISTORY_CSV_PATH, existing_header, CSV_FIELDS, backup_path,
        )
        os.replace(config.HISTORY_CSV_PATH, backup_path)
        with open(config.HISTORY_CSV_PATH, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=CSV_FIELDS).writeheader()


def append_history_row(reading: dict, statuses: dict, source: str):
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "co2_ppm": reading.get("co2_ppm"),
        "temperature_c": reading.get("temperature_c"),
        "humidity_pct": reading.get("humidity_pct"),
        "pressure_hpa": reading.get("pressure_hpa"),
        "gas_resistance_ohm": reading.get("gas_resistance_ohm"),
        "dust_voltage_v": reading.get("dust_voltage_v"),
        "dust_density_ugm3": statuses.get("dust_density_ugm3"),
        "mq2_voltage_v": reading.get("mq2_voltage_v"),
        "status_co2": statuses.get("co2"),
        "status_temperature": statuses.get("temperature"),
        "status_humidity": statuses.get("humidity"),
        "status_pressure": statuses.get("pressure"),
        "status_voc": statuses.get("voc"),
        "status_dust": statuses.get("dust"),
        "status_gas_mq2": statuses.get("gas_mq2"),
        "ventilation_alert": statuses.get("ventilation_alert"),
        "alert_level": statuses.get("alert_level"),
        "rapid_change_alert": statuses.get("rapid_change_alert"),
        "temp_delta_5min_c": statuses.get("temp_delta_5min_c"),
        "humidity_delta_5min_pct": statuses.get("humidity_delta_5min_pct"),
    }
    with open(config.HISTORY_CSV_PATH, "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=CSV_FIELDS).writerow(row)


def write_latest_json(reading: dict, statuses: dict, baseline_gas: float, baseline_mq2: float,
                       source: str):
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": source,  # "mock" or "real" - shown as a badge on the dashboard
        "reading": reading,
        "statuses": statuses,
        "baselines": {
            "gas_resistance_ohm": baseline_gas,
            "mq2_voltage_v": baseline_mq2,
        },
    }
    tmp_path = config.LATEST_JSON_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp_path, config.LATEST_JSON_PATH)  # atomic on POSIX and Windows


def run(mock_mode: bool):
    ensure_data_files()
    hub = SensorHub(mock_mode=mock_mode)
    baseline_gas, baseline_mq2 = capture_baseline(hub)
    source = "mock" if mock_mode else "real"
    # Process-local only: reset on every restart. That's fine - see the
    # "no reference yet" branch in check_rapid_change(), which just means
    # this alert stays silent (not crashed or false-triggered) until the
    # buffer has accumulated RAPID_CHANGE_WINDOW_S worth of readings again.
    recent_readings = RecentReadingsBuffer()

    logger.info("Starting main read/log loop (interval=%.0fs, source=%s)...",
                config.READ_INTERVAL_S, source)
    while True:
        start = time.monotonic()
        reading = hub.read_all()
        statuses = safe_evaluate(
            reading,
            baseline_gas_resistance_ohm=baseline_gas,
            baseline_mq2_voltage=baseline_mq2,
        )

        reference = recent_readings.add_and_get_reference(
            reading.get("temperature_c"), reading.get("humidity_pct")
        )
        rapid_alert, temp_delta, humidity_delta = check_rapid_change(
            reference, reading.get("temperature_c"), reading.get("humidity_pct")
        )
        statuses["rapid_change_alert"] = rapid_alert
        statuses["temp_delta_5min_c"] = temp_delta
        statuses["humidity_delta_5min_pct"] = humidity_delta
        # Distinct from the per-sensor Caution/Warning/Danger statuses - this
        # is a cross-sensor, time-based signal, not a single reading crossing
        # a fixed threshold, so it gets its own message rather than folding
        # into ventilation_alert/alert_level.
        statuses["rapid_change_message"] = (
            "Rapid environmental change - check ventilation" if rapid_alert else None
        )

        append_history_row(reading, statuses, source)
        write_latest_json(reading, statuses, baseline_gas, baseline_mq2, source)

        if rapid_alert:
            logger.warning(
                "RAPID CHANGE ALERT - temp +%.2fC, humidity +%.2f%% over %.0fs",
                temp_delta, humidity_delta, config.RAPID_CHANGE_WINDOW_S,
            )

        if statuses.get("ventilation_alert"):
            logger.warning("VENTILATION ALERT - level=%s statuses=%s",
                            statuses.get("alert_level"), statuses)
        else:
            logger.info("Reading logged: co2=%s temp=%s humidity=%s dust=%s",
                        reading.get("co2_ppm"), reading.get("temperature_c"),
                        reading.get("humidity_pct"), statuses.get("dust_density_ugm3"))

        elapsed = time.monotonic() - start
        time.sleep(max(0.0, config.READ_INTERVAL_S - elapsed))


def main():
    parser = argparse.ArgumentParser(description="Air quality sensor reading & logging loop")
    parser.add_argument("--mock", action="store_true", help="Force mock mode (fake sensor data)")
    args = parser.parse_args()

    mock_mode = args.mock or config.MOCK_MODE
    run(mock_mode)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Stopped.")
