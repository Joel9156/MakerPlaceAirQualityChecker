"""
Maker Place Air Quality Checker - Flask dashboard server.

Kept separate from main.py (the sensor reading/logging loop) - see main.py's
module docstring for why. This process only ever reads data/latest.json and
data/history.csv; it never talks to the sensors directly.

Usage:
    python app.py
"""

import csv
import logging
import os

from flask import Flask, jsonify, render_template

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)


def read_latest():
    if not os.path.exists(config.LATEST_JSON_PATH):
        return None
    import json
    try:
        with open(config.LATEST_JSON_PATH, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read %s: %s", config.LATEST_JSON_PATH, exc)
        return None

    # payload["statuses"] values are already the English strings
    # thresholds.evaluate() (or main.py's safe_evaluate()) produced, e.g.
    # "Normal" / "Caution" / "Warning" / "Danger" - passed through unchanged.
    return payload


def read_history(limit: int = config.HISTORY_CHART_POINTS):
    if not os.path.exists(config.HISTORY_CSV_PATH):
        return []
    with open(config.HISTORY_CSV_PATH, "r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return rows[-limit:]


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/sensors")
def sensors_page():
    return render_template("sensors.html")


@app.route("/api/latest")
def api_latest():
    latest = read_latest()
    if latest is None:
        return jsonify({"error": "No data yet. Is main.py running?"}), 503
    return jsonify(latest)


# Describes each physical sensor: which raw reading field(s) it produces and
# which processed/status field(s) thresholds.evaluate() derives from them.
# Used to assemble /api/sensors from the same data /api/latest already has.
SENSOR_DEFINITIONS = [
    {
        "sensor": "BME680",
        "connection": "I2C (0x77)",
        "raw_key": "temperature_c",
        "raw_unit": "°C",
        "processed_label": "Temperature",
        "status_key": "temperature",
    },
    {
        "sensor": "BME680",
        "connection": "I2C (0x77)",
        "raw_key": "humidity_pct",
        "raw_unit": "%RH",
        "processed_label": "Humidity",
        "status_key": "humidity",
    },
    {
        "sensor": "BME680",
        "connection": "I2C (0x77)",
        "raw_key": "pressure_hpa",
        "raw_unit": "hPa",
        "processed_label": "Pressure",
        "status_key": "pressure",
    },
    {
        "sensor": "BME680",
        "connection": "I2C (0x77)",
        "raw_key": "gas_resistance_ohm",
        "raw_unit": "Ω",
        "processed_label": "VOC status (relative to baseline)",
        "status_key": "voc",
    },
    {
        "sensor": "GP2Y1014AU0F (dust)",
        "connection": "Pico ADC (via serial)",
        "raw_key": "dust_voltage_v",
        "raw_unit": "V",
        "processed_label": "Dust density",
        "processed_key": "dust_density_mgm3",
        "processed_unit": "mg/m³",
        "status_key": "dust",
    },
    {
        "sensor": "SEN0219 (CO2)",
        "connection": "Pico (via serial)",
        "raw_key": "co2_ppm",
        "raw_unit": "ppm",
        "processed_label": "CO2 status",
        "status_key": "co2",
    },
    {
        "sensor": "MQ-2 (gas/smoke)",
        "connection": "Pico ADC (via serial)",
        "raw_key": "mq2_voltage_v",
        "raw_unit": "V",
        "processed_label": "Gas/smoke status (relative to baseline)",
        "status_key": "gas_mq2",
    },
]


@app.route("/api/sensors")
def api_sensors():
    latest = read_latest()
    if latest is None:
        return jsonify({"error": "No data yet. Is main.py running?"}), 503

    reading = latest.get("reading", {})
    statuses = latest.get("statuses", {})
    baselines = latest.get("baselines", {})

    rows = []
    for definition in SENSOR_DEFINITIONS:
        processed_key = definition.get("processed_key")
        rows.append({
            "sensor": definition["sensor"],
            "connection": definition["connection"],
            "raw_value": reading.get(definition["raw_key"]),
            "raw_unit": definition["raw_unit"],
            "processed_label": definition["processed_label"],
            "processed_value": statuses.get(processed_key) if processed_key else None,
            "processed_unit": definition.get("processed_unit"),
            "status": statuses.get(definition["status_key"]),
        })

    return jsonify({
        "timestamp": latest.get("timestamp"),
        "rows": rows,
        "baselines": baselines,
    })


@app.route("/api/history")
def api_history():
    rows = read_history()
    return jsonify({
        "timestamps": [r["timestamp"] for r in rows],
        "co2_ppm": [_to_float(r["co2_ppm"]) for r in rows],
        "dust_density_mgm3": [_to_float(r["dust_density_mgm3"]) for r in rows],
    })


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    app.run(host=config.FLASK_HOST, port=config.FLASK_PORT, debug=True)
