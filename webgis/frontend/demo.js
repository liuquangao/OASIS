const GLASGOW = [55.8642, -4.2518];
const HAZARD_BOUNDS = [
  [55.76898395467363, -4.412112769719773],
  [55.942438044680635, -4.051210467294865]
];
const HAZARD_WMS_URL = "http://127.0.0.1:8080/geoserver/glasgow_flood/wms";
const CARTO_BASEMAP_KEY = window.OASIS_CONFIG.cartoBasemapKey;

const map = L.map("demo-map", { center: GLASGOW, zoom: 13, zoomControl: false });

L.tileLayer(
  `https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png?key=${encodeURIComponent(CARTO_BASEMAP_KEY)}`,
  {
    maxZoom: 19,
    subdomains: "abcd",
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>'
  }
).addTo(map);

const hazardOverlay = L.tileLayer.wms(HAZARD_WMS_URL, {
  layers: "glasgow_flood:current_hazard_class_5m",
  styles: "hazard_class",
  format: "image/png",
  transparent: true,
  version: "1.1.1",
  opacity: 0.68,
  attribution: "OASIS latest calculated 5 m hazard raster"
});

const locationSelection = L.layerGroup().addTo(map);
const locationMarkers = new Map();
const routeSelection = L.layerGroup().addTo(map);
const routeLayers = new Map();
const analysisLayerObjects = new Map();
const agentPanel = document.getElementById("agent-panel");
const agentResizer = document.getElementById("agent-resizer");
const agentCollapseButton = document.getElementById("agent-collapse-btn");
const agentOpenButton = document.getElementById("agent-open-btn");
const AGENT_MIN_WIDTH = 320;
const AGENT_MAX_WIDTH = 720;
let agentPanelWidth = 420;
let sessionState = {
  locations: [],
  visible_location_ids: [],
  routes: [],
  visible_route_ids: [],
  active_location_id: null,
  hazard_layer_visible: false,
  analysis_layers: [],
  visible_analysis_layer_ids: [],
  risk_report: null,
  last_task: null
};

const conversationTab = document.getElementById("conversation-tab");
const riskReportTab = document.getElementById("risk-report-tab");
const conversationView = document.getElementById("conversation-view");
const riskReportView = document.getElementById("risk-report-view");

function textElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  element.textContent = text;
  return element;
}

function setPanelView(view) {
  const showReport = view === "report" && Boolean(sessionState.risk_report);
  conversationView.hidden = showReport;
  riskReportView.hidden = !showReport;
  conversationTab.classList.toggle("active", !showReport);
  riskReportTab.classList.toggle("active", showReport);
  conversationTab.setAttribute("aria-selected", String(!showReport));
  riskReportTab.setAttribute("aria-selected", String(showReport));
}

function renderRiskReport(report) {
  riskReportView.replaceChildren();
  riskReportTab.disabled = !report;
  riskReportTab.classList.toggle("ready", Boolean(report));
  if (!report) {
    setPanelView("conversation");
    return;
  }

  const article = document.createElement("article");
  article.className = `risk-report risk-${report.overall_risk}`;
  const header = document.createElement("header");
  header.className = "report-heading";
  const headingCopy = document.createElement("div");
  headingCopy.append(
    textElement("span", "report-kicker", `${report.area} · ${report.time_horizon}`),
    textElement("h2", "", report.title),
    textElement("p", "report-question", report.question)
  );
  header.append(headingCopy, textElement("span", "report-risk-badge", report.overall_risk));
  article.append(header, textElement("p", "report-summary", report.summary));

  if (report.key_findings?.length) {
    const section = document.createElement("section");
    section.appendChild(textElement("h3", "", "Key findings"));
    const list = document.createElement("ul");
    report.key_findings.forEach((finding) => list.appendChild(textElement("li", "", finding)));
    section.appendChild(list);
    article.appendChild(section);
  }

  if (report.evidence?.length) {
    const section = document.createElement("section");
    section.appendChild(textElement("h3", "", "Evidence"));
    const evidenceList = document.createElement("div");
    evidenceList.className = "report-evidence-list";
    report.evidence.forEach((item) => {
      const evidence = document.createElement("div");
      evidence.className = "report-evidence";
      evidence.append(
        textElement("span", "", item.label),
        textElement("strong", "", item.value),
        textElement("small", "", item.observed_at ? `${item.source} · ${new Date(item.observed_at).toLocaleString()}` : item.source)
      );
      evidenceList.appendChild(evidence);
    });
    section.appendChild(evidenceList);
    article.appendChild(section);
  }

  if (report.limitations?.length) {
    const section = document.createElement("section");
    section.className = "report-limitations";
    section.appendChild(textElement("h3", "", "Limitations"));
    const list = document.createElement("ul");
    report.limitations.forEach((limitation) => list.appendChild(textElement("li", "", limitation)));
    section.appendChild(list);
    article.appendChild(section);
  }

  article.appendChild(textElement("footer", "report-footer", `Generated ${new Date(report.generated_at).toLocaleString()}`));
  riskReportView.appendChild(article);
}

conversationTab.addEventListener("click", () => setPanelView("conversation"));
riskReportTab.addEventListener("click", () => setPanelView("report"));

function analysisLayerById(id) {
  return sessionState.analysis_layers.find((layer) => layer.id === id);
}

function geoJsonStyle(feature) {
  const properties = feature.properties || {};
  const score = properties.relative_vulnerability ?? properties.priority_score ?? 0.5;
  const color = score >= 0.67 ? "#dc2626" : score >= 0.34 ? "#f59e0b" : "#16a34a";
  return { color: "#334155", weight: 1, fillColor: color, fillOpacity: 0.58 };
}

async function ensureAnalysisLayer(id) {
  if (analysisLayerObjects.has(id)) return analysisLayerObjects.get(id);
  const descriptor = analysisLayerById(id);
  if (!descriptor) return null;
  let layer;
  if (descriptor.kind === "wms") {
    layer = L.tileLayer.wms(descriptor.url, {
      layers: descriptor.layer_name,
      styles: descriptor.style,
      format: "image/png",
      transparent: true,
      opacity: descriptor.opacity
    });
  } else {
    const response = await fetch(descriptor.url);
    layer = L.geoJSON(await response.json(), {
      style: geoJsonStyle,
      onEachFeature: (feature, item) => item.bindPopup(
        Object.entries(feature.properties || {}).map(([key, value]) =>
          `<strong>${escapeHtml(key)}</strong>: ${escapeHtml(String(value))}`
        ).join("<br>")
      )
    });
  }
  analysisLayerObjects.set(id, layer);
  return layer;
}

async function setAnalysisLayer(id, visible) {
  const layer = await ensureAnalysisLayer(id);
  if (!layer) return;
  if (visible) {
    layer.addTo(map);
    if (!sessionState.visible_analysis_layer_ids.includes(id)) sessionState.visible_analysis_layer_ids.push(id);
  } else {
    map.removeLayer(layer);
    sessionState.visible_analysis_layer_ids = sessionState.visible_analysis_layer_ids.filter((item) => item !== id);
  }
  renderAnalysisLayerControls();
}

function renderAnalysisLayerControls() {
  const container = document.getElementById("analysis-layer-list");
  container.replaceChildren();
  sessionState.analysis_layers.forEach((descriptor) => {
    const button = document.createElement("button");
    const visible = sessionState.visible_analysis_layer_ids.includes(descriptor.id);
    button.type = "button";
    button.className = `action-button${visible ? " active" : ""}`;
    button.innerHTML = `<span>Analysis</span><strong>${escapeHtml(descriptor.label)}</strong>`;
    button.addEventListener("click", () => setAnalysisLayer(descriptor.id, !visible));
    container.appendChild(button);
  });
}

function resizeMap() {
  map.invalidateSize({ pan: false });
}

function availableAgentWidth() {
  return Math.max(AGENT_MIN_WIDTH, Math.min(AGENT_MAX_WIDTH, window.innerWidth - 360));
}

function setAgentPanelWidth(width) {
  agentPanelWidth = Math.max(AGENT_MIN_WIDTH, Math.min(width, availableAgentWidth()));
  document.documentElement.style.setProperty("--agent-width", `${agentPanelWidth}px`);
  agentResizer.setAttribute("aria-valuemax", String(Math.round(availableAgentWidth())));
  agentResizer.setAttribute("aria-valuenow", String(Math.round(agentPanelWidth)));
  requestAnimationFrame(resizeMap);
}

function setAgentPanelCollapsed(collapsed) {
  document.body.classList.toggle("agent-collapsed", collapsed);
  agentPanel.inert = collapsed;
  agentPanel.setAttribute("aria-hidden", String(collapsed));
  agentOpenButton.setAttribute("aria-expanded", String(!collapsed));
  window.setTimeout(resizeMap, 230);
}

function beginAgentResize(event) {
  if (window.innerWidth <= 760 || document.body.classList.contains("agent-collapsed")) return;
  event.preventDefault();
  const startX = event.clientX;
  const startWidth = agentPanel.getBoundingClientRect().width;
  document.body.classList.add("agent-resizing");

  function resize(eventMove) {
    setAgentPanelWidth(startWidth + startX - eventMove.clientX);
  }

  function finishResize() {
    document.body.classList.remove("agent-resizing");
    window.removeEventListener("pointermove", resize);
    window.removeEventListener("pointerup", finishResize);
  }

  window.addEventListener("pointermove", resize);
  window.addEventListener("pointerup", finishResize);
}

setAgentPanelWidth(agentPanelWidth);
agentResizer.addEventListener("pointerdown", beginAgentResize);
agentResizer.addEventListener("keydown", (event) => {
  if (event.key === "ArrowLeft") setAgentPanelWidth(agentPanelWidth + 20);
  else if (event.key === "ArrowRight") setAgentPanelWidth(agentPanelWidth - 20);
  else if (event.key === "Home") setAgentPanelWidth(AGENT_MIN_WIDTH);
  else if (event.key === "End") setAgentPanelWidth(availableAgentWidth());
  else return;
  event.preventDefault();
});
agentCollapseButton.addEventListener("click", () => setAgentPanelCollapsed(true));
agentOpenButton.addEventListener("click", () => setAgentPanelCollapsed(false));

window.addEventListener("resize", () => {
  setAgentPanelWidth(agentPanelWidth);
});
window.addEventListener("load", resizeMap);

function setHazardOverlay(visible, fitLayer = false) {
  sessionState.hazard_layer_visible = visible;
  const button = document.getElementById("risk-overlay-btn");
  if (visible) {
    hazardOverlay.setParams({ snapshot: Date.now() });
    hazardOverlay.addTo(map);
    button.classList.add("active");
    if (fitLayer) map.flyToBounds(HAZARD_BOUNDS, { padding: [40, 40], duration: 0.8 });
  } else {
    map.removeLayer(hazardOverlay);
    button.classList.remove("active");
  }
  updateHazardLegend();
}

function updateHazardLegend() {
  const legend = document.getElementById("legend-card");
  document.getElementById("legend-title").textContent = "Latest calculated hazard · 5 m";
  document.getElementById("legend-note").textContent =
    "Latest SEPA-rainfall prototype snapshot. Not an operational flood warning.";
  legend.classList.toggle("visible", sessionState.hazard_layer_visible);
}

function addMessage(role, text, typing = false) {
  const history = document.getElementById("agent-history");
  const message = document.createElement("article");
  message.className = `message ${role}${typing ? " typing" : ""}`;
  const content = document.createElement("p");
  content.textContent = text;
  message.append(content);
  history.appendChild(message);
  history.scrollTop = history.scrollHeight;
  return message;
}

async function runAgentTurn(prompt) {
  const response = await fetch("http://127.0.0.1:8000/agent/turn", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt, state: sessionState })
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || "The tool-using Agent is unavailable.");
  }
  return response.json();
}

function escapeHtml(value) {
  const element = document.createElement("div");
  element.textContent = value;
  return element.innerHTML;
}

function locationById(id) {
  return sessionState.locations.find((location) => location.id === id);
}

function routeById(id) {
  return sessionState.routes.find((route) => route.id === id);
}

function markerPopup(location) {
  let hazard = "Not queried";
  if (location.risk_level === "no_data") hazard = "No classified value";
  else if (location.risk_level) hazard = `${location.risk_label} · class ${location.class_value}`;
  const distance = location.distance_km == null ? "" : `<br>${location.distance_km} km from search centre`;
  const snapshot = location.hazard_snapshot_time
    ? `<br><small>Calculated ${escapeHtml(new Date(location.hazard_snapshot_time).toLocaleString())}</small>`
    : "";
  const source = "Latest calculated 5 m raster";
  return (
    `<strong>${escapeHtml(location.label)}</strong><br>` +
    `${escapeHtml(location.place_type)}${distance}<br>` +
    `Representative-point hazard: <strong>${escapeHtml(hazard)}</strong>${snapshot}<br>` +
    `<small>${source} · not an operational warning.</small>`
  );
}

function displayLocation(id) {
  const location = locationById(id);
  if (!location) return;
  const existing = locationMarkers.get(id);
  if (existing) {
    existing.setPopupContent(markerPopup(location));
    return;
  }
  const number = sessionState.locations.indexOf(location) + 1;
  const icon = L.divIcon({
    className: "agent-location-icon",
    html: `<span aria-label="Location ${number}">${number}</span>`,
    iconSize: [30, 30],
    iconAnchor: [15, 15]
  });
  const marker = L.marker([location.latitude, location.longitude], { icon })
    .bindPopup(markerPopup(location))
    .addTo(locationSelection);
  locationMarkers.set(id, marker);
}

function removeLocations(ids) {
  ids.forEach((id) => {
    const marker = locationMarkers.get(id);
    if (marker) locationSelection.removeLayer(marker);
    locationMarkers.delete(id);
  });
}

function routePopup(route) {
  const hazard = route.hazard;
  const rank = route.rank ? ` · rank ${route.rank}` : "";
  const analysis = hazard
    ? `<br>High: ${Math.round(hazard.high_distance_m)} m · ` +
      `Medium: ${Math.round(hazard.medium_distance_m)} m · ` +
      `Low: ${Math.round(hazard.low_distance_m)} m` +
      `<br>Raster coverage: ${hazard.coverage_percent}% · ` +
      `index: ${hazard.hazard_index ?? "unknown"}`
    : "<br>Calculated hazard not analysed";
  return (
    `<strong>${escapeHtml(route.label)}${rank}</strong><br>` +
    `${(route.distance_m / 1000).toFixed(1)} km · ` +
    `${Math.round(route.duration_seconds / 60)} min · driving${analysis}` +
    `<br><small>Centreline sampling of the latest calculated raster; not a guaranteed safe route.</small>`
  );
}

function routeStyle(route) {
  if (route.rank === 1) return { color: "#16a34a", weight: 7, opacity: 0.92 };
  if (route.rank === 2) return { color: "#2563eb", weight: 5, opacity: 0.78, dashArray: "10 7" };
  return { color: "#64748b", weight: 4, opacity: 0.7, dashArray: "6 7" };
}

function displayRoute(id) {
  const route = routeById(id);
  if (!route) return;
  const existing = routeLayers.get(id);
  if (existing) {
    existing.setStyle(routeStyle(route));
    existing.setPopupContent(routePopup(route));
    return;
  }
  const latLngs = route.coordinates.map(([longitude, latitude]) => [latitude, longitude]);
  const layer = L.polyline(latLngs, routeStyle(route))
    .bindPopup(routePopup(route))
    .addTo(routeSelection);
  routeLayers.set(id, layer);
}

function fitRoutes(ids) {
  const layers = ids.map((id) => routeLayers.get(id)).filter(Boolean);
  if (!layers.length) return;
  const bounds = L.featureGroup(layers).getBounds();
  map.flyToBounds(bounds, { padding: [70, 70], maxZoom: 15, duration: 1.1 });
}

function fitLocations(ids) {
  const locations = ids.map(locationById).filter(Boolean);
  if (!locations.length) return;
  if (locations.length === 1) {
    map.flyTo([locations[0].latitude, locations[0].longitude], 16, { duration: 1.1 });
    locationMarkers.get(locations[0].id)?.openPopup();
    return;
  }
  const bounds = L.latLngBounds(locations.map((location) => [location.latitude, location.longitude]));
  map.flyToBounds(bounds, { padding: [90, 90], maxZoom: 15, duration: 1.1 });
}

function applyMapEvents(events) {
  events.forEach((event) => {
    if (event.type === "display_locations") {
      event.location_ids.forEach(displayLocation);
    } else if (event.type === "refresh_locations") {
      event.location_ids.forEach((id) => {
        if (locationMarkers.has(id)) displayLocation(id);
      });
    } else if (event.type === "remove_locations") {
      removeLocations(event.location_ids);
    } else if (event.type === "clear_locations") {
      locationSelection.clearLayers();
      locationMarkers.clear();
      map.flyTo(GLASGOW, 13, { duration: 1 });
    } else if (event.type === "fit_locations") {
      fitLocations(event.location_ids);
    } else if (event.type === "set_hazard_layer") {
      setHazardOverlay(event.visible);
    } else if (event.type === "sync_analysis_layers") {
      event.layer_ids.forEach((id) => setAnalysisLayer(id, true));
    } else if (event.type === "display_routes") {
      event.route_ids.forEach(displayRoute);
      event.route_ids.forEach((id) => {
        if (routeById(id)?.rank === 1) routeLayers.get(id)?.bringToFront();
      });
    } else if (event.type === "refresh_routes") {
      event.route_ids.forEach((id) => {
        if (routeLayers.has(id)) displayRoute(id);
      });
    } else if (event.type === "clear_routes") {
      routeSelection.clearLayers();
      routeLayers.clear();
    } else if (event.type === "fit_routes") {
      fitRoutes(event.route_ids);
    }
  });
}

async function askAgent(prompt) {
  setPanelView("conversation");
  addMessage("user", prompt);
  const typing = addMessage("agent", "Choosing and running map tools…", true);
  const input = document.getElementById("agent-input");
  const submit = document.querySelector("#agent-form button");
  input.disabled = true;
  submit.disabled = true;
  try {
    const response = await runAgentTurn(prompt);
    sessionState = response.state;
    applyMapEvents(response.events);
    renderRiskReport(sessionState.risk_report);
    typing.remove();
    addMessage("agent", response.message);
  } catch (error) {
    typing.remove();
    let message = "The tool-using Agent is unavailable.";
    if (error instanceof TypeError) message = "Cannot connect to the Agent API at 127.0.0.1:8000.";
    else if (error instanceof Error) message = error.message;
    addMessage("agent", message);
  } finally {
    input.disabled = false;
    submit.disabled = false;
    input.focus();
  }
}

document.getElementById("risk-overlay-btn").addEventListener("click", () => {
  setHazardOverlay(!sessionState.hazard_layer_visible, !sessionState.hazard_layer_visible);
});

document.getElementById("agent-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const input = document.getElementById("agent-input");
  const prompt = input.value.trim();
  if (!prompt) return;
  input.value = "";
  askAgent(prompt);
});

document.querySelectorAll(".prompt-chip").forEach((button) => {
  button.addEventListener("click", () => askAgent(button.dataset.prompt));
});
