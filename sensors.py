"""
Sensor reading module.

Hardware layout: the BME680, CO2 (SEN0219), dust (GP2Y1014AU0F), and MQ-2
sensors are all wired to the Pico, not the Pi. The Pico is the only thing
the Pi talks to for sensor data - one JSON line per reading cycle over USB
serial, containing all 7 fields:
  temperature_c, humidity_pct, pressure_hpa, gas_resistance_ohm,
  co2_ppm, dust_voltage_v, mq2_voltage_v
dust_voltage_v and mq2_voltage_v are raw sensor voltages, not
pre-computed density/status - thresholds.py does that conversion on the Pi
side, so the Pico must not duplicate it.

On any read failure/timeout, the last known good value is returned (or None
if there isn't one yet) rather than raising.
"""

import json
import logging
import random
import time

import config

logger = logging.getLogger(__name__)


class PicoSerialReader:
    """Reads all 7 sensor fields (BME680 + CO2 + dust + MQ-2) sent as one JSON
    line per cycle from the Pico over USB serial."""

    FIELDS = (
        "temperature_c", "humidity_pct", "pressure_hpa", "gas_resistance_ohm",
        "co2_ppm", "dust_voltage_v", "mq2_voltage_v",
    )

    def __init__(self, port=config.PICO_SERIAL_PORT, baudrate=config.PICO_SERIAL_BAUDRATE,
                 timeout=config.PICO_SERIAL_TIMEOUT_S):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._serial = None
        self._last_values = {key: None for key in self.FIELDS}
        self._connect()

    def _connect(self):
        try:
            import serial

            self._serial = serial.Serial(self.port, self.baudrate, timeout=self.timeout)
            logger.info("Connected to Pico on %s @ %d baud", self.port, self.baudrate)
        except Exception as exc:  # pragma: no cover - hardware dependent
            logger.warning("Could not open serial port %s (%s). Readings will be None.",
                            self.port, exc)
            self._serial = None

    def read(self) -> dict:
        """
        Reads one JSON line from the Pico, e.g.
        {"temperature_c": 24.1, "humidity_pct": 55.0, "pressure_hpa": 1012.3,
         "gas_resistance_ohm": 90000, "co2_ppm": 850, "dust_voltage_v": 0.75,
         "mq2_voltage_v": 0.55}
        Returns last known values on timeout/parse failure.
        """
        if self._serial is None:
            self._connect()
            if self._serial is None:
                return dict(self._last_values)

        try:
            # The Pico sends faster than the Pi necessarily reads (READ_INTERVAL_S
            # can be much longer than the Pico's send interval), so the input
            # buffer can accumulate several stale lines between reads. Drop
            # them and read whatever the Pico sends next, so every reading is
            # fresh instead of growing progressively more lagged over time.
            self._serial.reset_input_buffer()
            line = self._serial.readline().decode("utf-8", errors="ignore").strip()
            if line:
                parsed = json.loads(line)
                for key in self._last_values:
                    if key in parsed:
                        self._last_values[key] = parsed[key]
        except Exception as exc:  # pragma: no cover - hardware dependent
            logger.warning("Pico serial read failed: %s", exc)

        return dict(self._last_values)


class MockReader:
    """Generates plausible fake sensor data so the whole system can be demoed
    without any hardware attached."""

    def __init__(self):
        self._t = 0
        # Start near "clean air" values and let them drift/wander slightly
        # each reading so charts look alive but not random noise.
        self._state = {
            "co2_ppm": 600.0,
            "temperature_c": 23.0,
            "humidity_pct": 45.0,
            "pressure_hpa": 1013.0,
            "gas_resistance_ohm": 110000.0,
            # thresholds.voltage_to_dust_density_ugm3() computes
            # 170*V - 100 and clamps negative results to 0, so V must stay
            # above ~0.588 for the density to read as anything but zero.
            # This range keeps density roughly in 0-90 ug/m3 (mostly Normal,
            # occasionally Caution).
            "dust_voltage_v": 0.75,
            "mq2_voltage_v": 0.5,
        }

    def _drift(self, key, step, low, high):
        value = self._state[key] + random.uniform(-step, step)
        # Occasionally simulate an event (someone opens a door, smoke nearby, etc.)
        if random.random() < 0.03:
            value += random.uniform(0, step * 15)
        self._state[key] = max(low, min(high, value))
        return self._state[key]

    def read_bme680(self) -> dict:
        return {
            "temperature_c": round(self._drift("temperature_c", 0.15, 15, 32), 2),
            "humidity_pct": round(self._drift("humidity_pct", 0.5, 20, 80), 2),
            "pressure_hpa": round(self._drift("pressure_hpa", 0.2, 990, 1030), 2),
            "gas_resistance_ohm": round(self._drift("gas_resistance_ohm", 2000, 10000, 130000), 1),
        }

    def read_pico(self) -> dict:
        return {
            "co2_ppm": round(self._drift("co2_ppm", 15, 400, 5000)),
            "dust_voltage_v": round(self._drift("dust_voltage_v", 0.03, 0.6, 1.1), 3),
            "mq2_voltage_v": round(self._drift("mq2_voltage_v", 0.02, 0.4, 2.0), 3),
        }


class SensorHub:
    """
    Wraps the Pico serial reader (or the mock generator) with the interface
    main.py actually uses. All physical sensors (BME680 included) are wired
    to the Pico, so in real mode this is just PicoSerialReader - there is no
    separate Pi-side I2C reader.
    """

    def __init__(self, mock_mode: bool = config.MOCK_MODE):
        self.mock_mode = mock_mode
        if mock_mode:
            logger.info("Running in MOCK MODE - generating fake sensor data.")
            self._mock = MockReader()
            self._pico = None
        else:
            self._mock = None
            self._pico = PicoSerialReader()

    def read_all(self) -> dict:
        """Returns a single dict combining all sensor readings, ready for
        thresholds.evaluate()."""
        if self.mock_mode:
            reading = {}
            reading.update(self._mock.read_bme680())
            reading.update(self._mock.read_pico())
            return reading

        return self._pico.read()
