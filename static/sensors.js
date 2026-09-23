const REFRESH_MS = 5000;

// Colors for the exact strings thresholds.py's evaluate_*() functions
// return (passed through the API unchanged - no translation layer).
const STATUS_STYLES = {
  "Normal": { color: "green" },
  "Reference only (not alertable)": { color: "gray" },
  "Calibration needed (no baseline)": { color: "gray" },
  "Caution": { color: "yellow" },
  "Caution (dry)": { color: "yellow" },
  "Caution (mold risk)": { color: "yellow" },
  "Warning": { color: "orange" },
  "Danger": { color: "red" },
  "N/A (sensor offline)": { color: "gray" },
};

function statusStyle(status) {
  return STATUS_STYLES[status] || { color: "gray" };
}

function formatNumber(value, decimals = 2) {
  if (value === null || value === undefined || Number.isNaN(value)) return "N/A";
  return Number(value).toFixed(decimals);
}

function renderBaselines(baselines) {
  const bar = document.getElementById("baseline-bar");
  const gas = baselines?.gas_resistance_ohm;
  const mq2 = baselines?.mq2_voltage_v;
  bar.innerHTML = `
    <div class="baseline-item">
      <span class="baseline-label">Baseline gas resistance (BME680 VOC)</span>
      <span class="baseline-value">${formatNumber(gas, 0)} Ω</span>
    </div>
    <div class="baseline-item">
      <span class="baseline-label">Baseline MQ-2 voltage</span>
      <span class="baseline-value">${formatNumber(mq2, 3)} V</span>
    </div>
  `;
}

function renderSensorCards(rows) {
  const container = document.getElementById("sensor-cards");
  container.innerHTML = "";

  for (const row of rows) {
    const statusLabel = row.status ?? "Unknown";
    const { color } = statusStyle(statusLabel);

    const rawText = row.raw_value === null || row.raw_value === undefined
      ? "N/A"
      : `${formatNumber(row.raw_value, 3)} ${row.raw_unit || ""}`.trim();

    const processedText = row.processed_value === null || row.processed_value === undefined
      ? "—"
      : `${formatNumber(row.processed_value, 3)} ${row.processed_unit || ""}`.trim();

    const card = document.createElement("div");
    card.className = `card status-${color} sensor-detail-card`;
    card.innerHTML = `
      <p class="card-label">${row.sensor}</p>
      <p class="sensor-connection">${row.connection}</p>
      <div class="sensor-row">
        <span class="sensor-row-label">Raw output</span>
        <span class="sensor-row-value">${rawText}</span>
      </div>
      <div class="sensor-row">
        <span class="sensor-row-label">${row.processed_label}</span>
        <span class="sensor-row-value">${processedText}</span>
      </div>
      <span class="card-status status-${color}">${statusLabel}</span>
    `;
    container.appendChild(card);
  }
}

async function refresh() {
  try {
    const res = await fetch("/api/sensors");
    if (!res.ok) return;
    const payload = await res.json();

    renderSensorCards(payload.rows);
    renderBaselines(payload.baselines);

    const lastUpdated = document.getElementById("last-updated");
    const ts = payload.timestamp ? new Date(payload.timestamp) : null;
    lastUpdated.textContent = ts ? `· last updated ${ts.toLocaleTimeString()}` : "";
  } catch (err) {
    console.error("Failed to refresh sensor details:", err);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  refresh();
  setInterval(refresh, REFRESH_MS);
});
