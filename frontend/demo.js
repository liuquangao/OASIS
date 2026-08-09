const GLASGOW = [55.8642, -4.2518];
const RISK_ZONE = [55.8586, -4.2588];

const map = L.map("demo-map", {
  center: GLASGOW,
  zoom: 13,
  zoomControl: false
});

const voyager = L.tileLayer(
  "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
  {
    maxZoom: 19,
    subdomains: "abcd",
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>'
  }
).addTo(map);

const riskOverlay = L.layerGroup();
let overlayVisible = true;

const highRisk = L.polygon(
  [
    [55.864, -4.286],
    [55.869, -4.264],
    [55.859, -4.232],
    [55.849, -4.24],
    [55.852, -4.275]
  ],
  {
    color: "#dc2626",
    fillColor: "#ef4444",
    fillOpacity: 0.34,
    weight: 2
  }
).bindPopup("High risk inundation zone: River Clyde corridor");

const mediumRisk = L.polygon(
  [
    [55.871, -4.292],
    [55.879, -4.263],
    [55.863, -4.215],
    [55.843, -4.229],
    [55.845, -4.286]
  ],
  {
    color: "#d97706",
    fillColor: "#f59e0b",
    fillOpacity: 0.2,
    weight: 2
  }
).bindPopup("Moderate exposure zone: urban flood sensitivity");

const monitoringBuffer = L.circle(RISK_ZONE, {
  radius: 1550,
  color: "#0284c7",
  fillColor: "#38bdf8",
  fillOpacity: 0.12,
  weight: 2,
  dashArray: "6 6"
}).bindPopup("Monitoring buffer for response planning");

const affectedBuildings = [
  [55.8597, -4.2632],
  [55.8588, -4.2535],
  [55.8611, -4.2469],
  [55.8564, -4.2672],
  [55.8539, -4.2502],
  [55.8628, -4.2761]
].map((latlng, index) =>
  L.circleMarker(latlng, {
    radius: 6,
    color: "#7f1d1d",
    fillColor: "#fca5a5",
    fillOpacity: 0.9,
    weight: 2
  }).bindPopup(`Affected building cluster ${index + 1}`)
);

riskOverlay.addLayer(mediumRisk);
riskOverlay.addLayer(highRisk);
riskOverlay.addLayer(monitoringBuffer);
affectedBuildings.forEach((marker) => riskOverlay.addLayer(marker));
riskOverlay.addTo(map);

function resizeMap() {
  map.invalidateSize({ pan: false });
}

resizeMap();
window.addEventListener("resize", resizeMap);
window.addEventListener("load", resizeMap);

function toggleRiskOverlay() {
  overlayVisible = !overlayVisible;
  const button = document.getElementById("risk-overlay-btn");
  const legend = document.getElementById("legend-card");
  if (overlayVisible) {
    riskOverlay.addTo(map);
    button.classList.add("active");
    legend.classList.add("visible");
    map.flyTo(RISK_ZONE, 14, { duration: 0.8 });
  } else {
    map.removeLayer(riskOverlay);
    button.classList.remove("active");
    legend.classList.remove("visible");
  }
}

function openReport() {
  document.getElementById("report-drawer").classList.add("open");
}

function closeReport() {
  document.getElementById("report-drawer").classList.remove("open");
}

function addMessage(role, text, typing = false) {
  const history = document.getElementById("agent-history");
  const message = document.createElement("article");
  message.className = `message ${role}${typing ? " typing" : ""}`;
  message.innerHTML = `<span>${role === "user" ? "Decision Lead" : "Agent"}</span><p>${text}</p>`;
  history.appendChild(message);
  history.scrollTop = history.scrollHeight;
  return message;
}

function agentReply(prompt) {
  addMessage("user", prompt);
  const typing = addMessage("agent", "Hydromind is analysing spatial exposure, hazard overlap, and operational impact...", true);

  setTimeout(() => {
    typing.remove();
    const lower = prompt.toLowerCase();
    const mentionsReport = lower.includes("report");
    const mentionsMitigation = lower.includes("mitigation") || lower.includes("plan");

    const response = mentionsMitigation
      ? "Recommended actions: validate drainage assets in the highlighted corridor, stage temporary diversion routes, and prioritize vulnerable census zones for preparedness messaging."
      : "Analysis complete. The highlighted central corridor contains concentrated exposure: 142 buildings, 3.2 km of roads, and an estimated economic risk of £4.2M under the current demonstration scenario.";

    addMessage("agent", response);
    if (!overlayVisible) toggleRiskOverlay();
    map.flyTo(RISK_ZONE, 14, { duration: 1.0 });
    if (mentionsReport) openReport();
  }, 1000);
}

document.getElementById("risk-overlay-btn").addEventListener("click", toggleRiskOverlay);
document.getElementById("risk-report-btn").addEventListener("click", openReport);
document.getElementById("exposure-btn").addEventListener("click", () => {
  agentReply("Show exposure summary for the current flood extent.");
});
document.getElementById("mitigation-btn").addEventListener("click", () => {
  agentReply("Recommend Mitigation Plan");
});
document.getElementById("close-report").addEventListener("click", closeReport);
document.getElementById("drawer-backdrop").addEventListener("click", closeReport);

document.getElementById("agent-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const input = document.getElementById("agent-input");
  const prompt = input.value.trim();
  if (!prompt) return;
  input.value = "";
  agentReply(prompt);
});

document.querySelectorAll(".prompt-chip").forEach((button) => {
  button.addEventListener("click", () => {
    agentReply(button.dataset.prompt);
  });
});

document.getElementById("fullscreen-btn").addEventListener("click", () => {
  if (!document.fullscreenElement) {
    document.documentElement.requestFullscreen?.();
  } else {
    document.exitFullscreen?.();
  }
});

setTimeout(() => {
  map.flyTo(RISK_ZONE, 14, { duration: 1.2 });
}, 700);
