# Maker Place Air Quality Checker

A real-time indoor air quality monitor for the Maker Place, built on a
Raspberry Pi 3B+. All physical sensors - BME680 (I2C), CO2 (SEN0219), dust
(GP2Y1014AU0F), and MQ-2 gas/smoke - are wired to a Raspberry Pi Pico, which
sends one JSON line per reading cycle to the Pi over USB serial.

## Architecture

- **`main.py`** — reads all sensors on a fixed interval, evaluates their
  status via `thresholds.py`, and writes `data/history.csv` (append) and
  `data/latest.json` (overwrite).
- **`app.py`** — a Flask server that reads those two data files and serves
  the dashboard + JSON API. It never talks to the sensors directly.
- These are two separate processes on purpose: sensor logging must keep
  running even if the dashboard isn't open, and restarting/crashing the web
  server should never interrupt data collection (or vice versa).
- **`sensors.py`** — hardware abstraction with a mock-mode fake data
  generator, so the whole stack runs without any hardware attached.
- **`thresholds.py`** — converts raw readings into Normal/Caution/Warning/
  Danger statuses (English strings), used unchanged by both `main.py` and
  `app.py`.

## Install

```bash
python -m venv venv
source venv/bin/activate        # on Windows: venv\Scripts\activate
pip install -r requirements.txt
```

On a machine without serial hardware (e.g. your laptop), `pyserial` won't
actually connect to anything, but that's fine in mock mode - it's never
imported there.

## Run in mock mode (no hardware required)

Open two terminals from the project root:

```bash
# Terminal 1: sensor loop with fake data
python main.py --mock
# (or: set MOCK_MODE=1 as an env var instead of the flag)

# Terminal 2: dashboard
python app.py
```

Then open **http://localhost:5000** in a browser. Cards, colors, the
ventilation banner, and the CO2/dust charts should all update every 5
seconds using generated data.

## Run on the Raspberry Pi with real hardware

1. Wire the BME680, CO2 (SEN0219), dust (GP2Y1014AU0F), and MQ-2 sensors to
   the Pico (see `Maker_Space_Air_Checker_V2.py` for pin assignments), and
   upload it to the Pico via Thonny so it runs on boot.
2. Plug the Pico into the Pi over USB. It should show up as `/dev/ttyACM0`
   (check with `ls /dev/ttyACM*`). The Pico sends one JSON line per reading
   cycle with all 7 fields:
   ```json
   {"temperature_c": 24.1, "humidity_pct": 55.0, "pressure_hpa": 1012.3,
    "gas_resistance_ohm": 90000, "co2_ppm": 850, "dust_voltage_v": 0.75,
    "mq2_voltage_v": 0.55}
   ```
   `dust_voltage_v` and `mq2_voltage_v` are raw sensor voltages - all
   conversion to density/status happens on the Pi in `thresholds.py`, so the
   Pico must not pre-compute those.
3. Install dependencies (this time they'll actually be used):
   ```bash
   pip install -r requirements.txt
   ```
4. Run the two processes as before, **without** `--mock`:
   ```bash
   python main.py
   python app.py
   ```
   `main.py` will wait ~90s on startup for the MQ-2 to warm up before
   capturing its "clean air" baseline (configurable via `MQ2_WARMUP_S`).

### Useful environment variables

| Variable                 | Default          | Purpose                                   |
|---------------------------|------------------|--------------------------------------------|
| `MOCK_MODE`               | `0`              | Set to `1` to force mock mode              |
| `PICO_SERIAL_PORT`        | `/dev/ttyACM0`   | Serial port the Pico is connected to       |
| `READ_INTERVAL_S`         | `30`             | Seconds between sensor reads/log rows      |
| `BASELINE_SAMPLE_COUNT`   | `5`              | Samples averaged for the "clean air" baseline |
| `MQ2_WARMUP_S`            | `90`             | Warm-up wait before baseline capture (real hardware only) |
| `FLASK_PORT`              | `5000`           | Dashboard port                             |

## Data files

- `data/history.csv` — one row per reading cycle, all sensor values + statuses.
- `data/latest.json` — most recent reading + statuses, overwritten every cycle.

Both are created automatically on first run of `main.py`.
