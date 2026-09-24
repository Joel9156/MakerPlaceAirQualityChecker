"""
Sensor reading module.

Reads:
  - BME680 over I2C: temperature, humidity, pressure, gas_resistance_ohm
  - Raspberry Pi Pico over serial: co2_ppm, dust_voltage_v, mq2_voltage_v
    (Pico sends one JSON line per reading cycle)

Both readers expose the same interface whether running against real
hardware or in mock mode, so main.py doesn't need to care which one it is
using. On any read failure/timeout, the last known good value is returned
(or None if there isn't one yet) rather than raising.
"""

import json
import logging
import random
import time

import config

logger = logging.getLogger(__name__)


class BME680Reader:
    """Reads temperature/humidity/pressure/gas resistance from a BME680 over I2C."""

    def __init__(self, i2c_address=config.BME680_I2C_ADDRESS):
        self.i2c_address = i2c_address
        self._sensor = None
        self._last_values = {
            "temperature_c": None,
            "humidity_pct": None,
            "pressure_hpa": None,
            "gas_resistance_ohm": None,
        }
        self._connect()

    def _connect(self):
        try:
            import bme680  # adafruit-circuitpython-bme680 alt / pimoroni bme680 lib

            self._sensor = bme680.BME680(i2c_addr=self.i2c_address)
            self._sensor.set_gas_status(bme680.ENABLE_GAS_MEAS)
            logger.info("BME680 connected at address 0x%x", self.i2c_address)
        except Exception as exc:  # pragma: no cover - hardware dependent
            logger.warning("Could not initialize BME680 (%s). Readings will be None.", exc)
            self._sensor = None

    def read(self) -> dict:
        """Returns a dict with the last known values, updated if a fresh read succeeds."""
        if self._sensor is None:
            self._connect()
            if self._sensor is None:
                return dict(self._last_values)

        try:
            if self._sensor.get_sensor_data():
                data = self._sensor.data
                self._last_values["temperature_c"] = round(data.temperature, 2)
                self._last_values["humidity_pct"] = round(data.humidity, 2)
                self._last_values["pressure_hpa"] = round(data.pressure, 2)
                if getattr(data, "heat_stable", False):
                    self._last_values["gas_resistance_ohm"] = round(data.gas_resistance, 1)
        except Exception as exc:  # pragma: no cover - hardware dependent
            logger.warning("BME680 read failed: %s", exc)

        return dict(self._last_values)


class PicoSerialReader:
    """Reads CO2 / dust / MQ-2 readings sent as JSON lines from the Pico over USB serial."""

    def __init__(self, port=config.PICO_SERIAL_PORT, baudrate=config.PICO_SERIAL_BAUDRATE,
                 timeout=config.PICO_SERIAL_TIMEOUT_S):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._serial = None
        self._last_values = {
            "co2_ppm": None,
            "dust_voltage_v": None,
            "mq2_voltage_v": None,
        }
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
        {"co2_ppm": 850, "dust_voltage_v": 0.55, "mq2_voltage_v": 0.62}
        Returns last known values on timeout/parse failure.
        """
        if self._serial is None:
            self._connect()
            if self._serial is None:
                return dict(self._last_values)

        try:
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
            # thresholds.voltage_to_dust_density_mgm3() computes
            # 0.17*V - 0.1 and clamps negative results to 0, so V must stay
            # above ~0.588 for the density to read as anything but zero.
            # This range keeps density roughly in 0.01-0.08 mg/m3.
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
    Combines the BME680 and Pico readers (or the mock generator) into a single
    reading, with the interface main.py actually uses.
    """

    def __init__(self, mock_mode: bool = config.MOCK_MODE):
        self.mock_mode = mock_mode
        if mock_mode:
            logger.info("Running in MOCK MODE - generating fake sensor data.")
            self._mock = MockReader()
            self._bme680 = None
            self._pico = None
        else:
            self._mock = None
            self._bme680 = BME680Reader()
            self._pico = PicoSerialReader()

    def read_all(self) -> dict:
        """Returns a single dict combining all sensor readings, ready for
        thresholds.evaluate()."""
        if self.mock_mode:
            reading = {}
            reading.update(self._mock.read_bme680())
            reading.update(self._mock.read_pico())
            return reading

        reading = {}
        reading.update(self._bme680.read())
        reading.update(self._pico.read())
        return reading
