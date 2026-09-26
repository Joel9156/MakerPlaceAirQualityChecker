const REFRESH_MS = 5000;

// Colors/severity for the exact strings thresholds.py's evaluate_*()
// functions return (passed through the API unchanged - no translation layer).
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

// key in the `reading`/`statuses` objects -> card display config
const SENSOR_CARDS = [
  { key: "co2", readingKey: "co2_ppm", label: "CO2", unit: "ppm", decimals: 0 },
  { key: "temperature", readingKey: "temperature_c", label: "Temperature", unit: "°C", decimals: 1 },
  { key: "humidity", readingKey: "humidity_pct", label: "Humidity", unit: "%", decimals: 0 },
  { key: "pressure", readingKey: "pressure_hpa", label: "Pressure", unit: "hPa", decimals: 1 },
  { key: "voc", readingKey: "gas_resistance_ohm", label: "VOC (gas resistance)", unit: "Ω", decimals: 0 },
  { key: "dust", readingKey: null, label: "Dust", unit: "µg/m³", decimals: 1, statusKey: "dust_density_ugm3" },
  { key: "gas_mq2", readingKey: "mq2_voltage_v", label: "Gas / Smoke (MQ-2)", unit: "V", decimals: 3 },
];

let co2Chart = null;
let dustChart = null;

function formatNumber(value, decimals) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return Number(value).toFixed(decimals);
}

function renderCards(payload) {
  const container = document.getElementById("cards");
  const { reading, statuses } = payload;
  container.innerHTML = "";

  for (const cfg of SENSOR_CARDS) {
    const statusLabel = statuses[cfg.key] ?? "Unknown";
    const { color } = statusStyle(statusLabel);
    const value = cfg.statusKey ? statuses[cfg.statusKey] : reading[cfg.readingKey];

    const card = document.createElement("div");
    card.className = `card status-${color}`;
    card.innerHTML = `
      <p class="card-label">${cfg.label}</p>
      <p class="card-value">${formatNumber(value, cfg.decimals)}<span class="card-unit">${cfg.unit}</span></p>
      <span class="card-status status-${color}">${statusLabel}</span>
    `;
    container.appendChild(card);
  }

  const banner = document.getElementById("alert-banner");
  banner.classList.toggle("hidden", !statuses.ventilation_alert);
  banner.classList.remove("level-1", "level-2", "level-3");
  if (statuses.ventilation_alert) {
    banner.classList.add(`level-${statuses.alert_level}`);
  }

  const mockBadge = document.getElementById("mock-badge");
  mockBadge.classList.toggle("hidden", payload.source !== "mock");

  const lastUpdated = document.getElementById("last-updated");
  const ts = payload.timestamp ? new Date(payload.timestamp) : null;
  lastUpdated.textContent = ts ? `· last updated ${ts.toLocaleTimeString()}` : "";
}

function makeLineChart(canvasId, label, borderColor) {
  const ctx = document.getElementById(canvasId).getContext("2d");
  return new Chart(ctx, {
    type: "line",
    data: {
      labels: [],
      datasets: [{
        label,
        data: [],
        borderColor,
        backgroundColor: borderColor,
        tension: 0.3,
        pointRadius: 0,
        borderWidth: 2,
      }],
    },
    options: {
      responsive: true,
      animation: false,
      scales: {
        x: { ticks: { maxTicksLimit: 6 } },
        y: { beginAtZero: false },
      },
      plugins: { legend: { display: false } },
    },
  });
}

function formatTimeLabels(timestamps) {
  return timestamps.map((t) => {
    const d = new Date(t);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  });
}

function updateCharts(history) {
  const labels = formatTimeLabels(history.timestamps);

  co2Chart.data.labels = labels;
  co2Chart.data.datasets[0].data = history.co2_ppm;
  co2Chart.update();

  dustChart.data.labels = labels;
  dustChart.data.datasets[0].data = history.dust_density_ugm3;
  dustChart.update();
}

async function refresh() {
  try {
    const [latestRes, historyRes] = await Promise.all([
      fetch("/api/latest"),
      fetch("/api/history"),
    ]);

    if (latestRes.ok) {
      renderCards(await latestRes.json());
    }
    if (historyRes.ok) {
      updateCharts(await historyRes.json());
    }
  } catch (err) {
    console.error("Failed to refresh dashboard data:", err);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  co2Chart = makeLineChart("co2-chart", "CO2 (ppm)", "#c62828");
  dustChart = makeLineChart("dust-chart", "Dust (µg/m3)", "#a68b00");
  refresh();
  setInterval(refresh, REFRESH_MS);
});
