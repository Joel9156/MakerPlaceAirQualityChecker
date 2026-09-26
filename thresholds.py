"""
Maker Place Air Quality Checker - threshold evaluation module
Takes each sensor value and determines its status (Normal/Caution/Warning/Danger)
plus an overall ventilation alert flag.

Usage:
    from thresholds import evaluate

    reading = {
        "co2_ppm": 1450,
        "temperature_c": 24.1,
        "humidity_pct": 58,
        "pressure_hpa": 1012.3,
        "gas_resistance_ohm": 42000,   # BME680 raw gas resistance
        "dust_voltage_v": 0.62,        # GP2Y1014AU0F raw analog voltage (Vo)
        "mq2_voltage_v": 0.9,
    }
    result = evaluate(reading, baseline_gas_resistance_ohm=110000)
    print(result)
"""

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# 1) CO2 (ppm) - ASHRAE 62.1 ventilation standard / typical indoor CO2 guidelines
# ---------------------------------------------------------------------------
def evaluate_co2(ppm: float) -> str:
    if ppm < 1000:
        return "Normal"
    elif ppm < 2000:
        return "Caution"       # poor ventilation, may cause drowsiness
    elif ppm < 5000:
        return "Warning"       # headache, fatigue
    else:
        return "Danger"        # exceeds industrial safety regulation level


# ---------------------------------------------------------------------------
# 2) Temperature (°C) - approximate comfort range per ASHRAE Standard 55
# ---------------------------------------------------------------------------
def evaluate_temperature(celsius: float) -> str:
    if 20 <= celsius <= 26:
        return "Normal"
    elif 18 <= celsius < 20 or 26 < celsius <= 28:
        return "Caution"
    else:
        return "Warning"


# ---------------------------------------------------------------------------
# 3) Humidity (%RH) - recommended range per ASHRAE 62.1 / Standard 55
# ---------------------------------------------------------------------------
def evaluate_humidity(rh_pct: float) -> str:
    if 30 <= rh_pct <= 60:
        return "Normal"
    elif rh_pct < 30:
        return "Caution (dry)"
    else:
        return "Caution (mold risk)"


# ---------------------------------------------------------------------------
# 4) Pressure (hPa) - no absolute danger threshold. Reference/logging only.
# ---------------------------------------------------------------------------
def evaluate_pressure(hpa: float) -> str:
    return "Reference only (not alertable)"


# ---------------------------------------------------------------------------
# 5) VOC / gas resistance (BME680, ohm) - cannot be converted to an absolute ppm.
#    Judged relative to a "clean air" baseline resistance captured at startup.
# ---------------------------------------------------------------------------
def evaluate_gas_resistance(current_ohm: float, baseline_ohm: float) -> str:
    if baseline_ohm <= 0:
        return "Calibration needed (no baseline)"
    ratio = current_ohm / baseline_ohm
    if ratio >= 0.75:
        return "Normal"
    elif ratio >= 0.5:
        return "Caution"
    else:
        return "Warning"       # resistance dropped 50%+ from baseline = VOC spike


# ---------------------------------------------------------------------------
# 6) Dust (GP2Y1014AU0F) - uses Sharp's official conversion formula, in ug/m3
#    (the mg/m3 version - density(mg/m3) = 0.17*Vo - 0.1 - rounds real clean-air
#    readings like 0.006 mg/m3 down to 0.0 on the dashboard, so this is scaled
#    up by exactly 1000x to ug/m3, not a different formula):
#    dust_density(ug/m3) = 170 * Vo - 100  (Vo: sensor output voltage)
#    Thresholds below use general indoor PM guidance as a rough reference,
#    loosened from strict WHO residential limits since this is a makerspace
#    (laser cutters/3D printing add background particulates) and the sensor
#    itself isn't precision-grade.
# ---------------------------------------------------------------------------
def voltage_to_dust_density_ugm3(voltage: float) -> float:
    density = 170 * voltage - 100
    return max(density, 0.0)


def evaluate_dust(voltage: float) -> str:
    density = voltage_to_dust_density_ugm3(voltage)
    if density < 50:
        return "Normal"
    elif density < 150:
        return "Caution"
    else:
        return "Danger"


# ---------------------------------------------------------------------------
# 7) MQ-2 flammable gas/smoke sensor - raw ADC/voltage value, cannot be
#    converted to an absolute ppm. A real ppm conversion needs the sensor's
#    load resistor value and a measured Rs/Ro clean-air ratio from the
#    datasheet curves - we don't have that calibration data, so this is
#    judged relative to a "clean air" baseline instead, the same way as
#    BME680 VOC. Needs a warm-up period, so readings from the first 1-2
#    minutes after power-on must be ignored.
#
#    Thresholds are intentionally sensitive (1.2x/1.5x rather than a looser
#    ratio) because this is a safety-relevant sensor (flammable gas/smoke) -
#    the team decided false positives are preferable to false negatives here.
#    Uses a 3-tier Normal/Caution/Danger scale (like dust) rather than CO2's
#    4-tier Normal/Caution/Warning/Danger scale, since "Danger" communicates
#    urgency better than "Warning" for a gas/smoke alert.
# ---------------------------------------------------------------------------
def evaluate_mq2(current_voltage: float, baseline_voltage: float) -> str:
    if baseline_voltage <= 0:
        return "Calibration needed (no baseline)"
    ratio = current_voltage / baseline_voltage
    if ratio < 1.2:
        return "Normal"
    elif ratio < 1.5:
        return "Caution"
    else:
        return "Danger"        # voltage well above baseline = gas/smoke detected


# ---------------------------------------------------------------------------
# Overall verdict
# ---------------------------------------------------------------------------
ALERT_LEVELS = {"Normal": 0, "Reference only (not alertable)": 0, "Calibration needed (no baseline)": 0,
                "Caution": 1, "Caution (dry)": 1, "Caution (mold risk)": 1,
                "Warning": 2, "Danger": 3}


def evaluate(reading: dict, baseline_gas_resistance_ohm: float = None,
             baseline_mq2_voltage: float = None) -> dict:
    """
    Takes a reading dict and returns each item's status plus whether an
    overall ventilation alert is needed.
    Required keys: co2_ppm, temperature_c, humidity_pct, pressure_hpa,
              gas_resistance_ohm, dust_voltage_v, mq2_voltage_v
    baseline_gas_resistance_ohm: BME680 baseline resistance measured in
        "clean air" at program startup.
    baseline_mq2_voltage: MQ-2 baseline voltage measured in "clean air"
        (after warm-up) at program startup.
    """
    statuses = {
        "co2": evaluate_co2(reading["co2_ppm"]),
        "temperature": evaluate_temperature(reading["temperature_c"]),
        "humidity": evaluate_humidity(reading["humidity_pct"]),
        "pressure": evaluate_pressure(reading["pressure_hpa"]),
        "voc": evaluate_gas_resistance(
            reading["gas_resistance_ohm"], baseline_gas_resistance_ohm or 0
        ),
        "dust": evaluate_dust(reading["dust_voltage_v"]),
        "dust_density_ugm3": round(voltage_to_dust_density_ugm3(reading["dust_voltage_v"]), 1),
        "gas_mq2": evaluate_mq2(reading["mq2_voltage_v"], baseline_mq2_voltage or 0),
    }

    max_level = max(ALERT_LEVELS.get(v, 0) for k, v in statuses.items() if k != "dust_density_ugm3")
    statuses["ventilation_alert"] = max_level >= 1   # alert fires if anything is Caution or worse
    statuses["alert_level"] = max_level              # 0=Normal 1=Caution 2=Warning 3=Danger
    return statuses


if __name__ == "__main__":
    # Quick sanity check (uses example data instead of real sensor readings)
    example = {
        "co2_ppm": 1450,
        "temperature_c": 24.1,
        "humidity_pct": 58,
        "pressure_hpa": 1012.3,
        "gas_resistance_ohm": 42000,
        "dust_voltage_v": 0.62,
        "mq2_voltage_v": 0.9,
    }
    print(evaluate(example, baseline_gas_resistance_ohm=110000, baseline_mq2_voltage=0.6))
