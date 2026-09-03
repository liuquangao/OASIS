(() => {
  "use strict";

  const data = window.HYDROMIND_MONITORING_DATA;
  const mapMessage = document.getElementById("map-message");

  function showMessage(message) {
    mapMessage.textContent = message;
    mapMessage.classList.add("visible");
    window.clearTimeout(showMessage.timeout);
    showMessage.timeout = window.setTimeout(() => mapMessage.classList.remove("visible"), 4200);
  }

  if (!window.L) {
    showMessage("Leaflet could not be loaded. An internet connection is required for the map library and basemap.");
    return;
  }
  if (!data) {
    showMessage("Monitoring snapshot is missing. Run scripts/refresh-terrain-monitoring.ps1.");
    return;
  }

  const map = L.map("monitoring-map", { zoomControl: false, preferCanvas: true });
  L.control.zoom({ position: "topright" }).addTo(map);

  map.createPane("terrainPane");
  map.getPane("terrainPane").style.zIndex = 240;
  map.createPane("floodPane");
  map.getPane("floodPane").style.zIndex = 260;

  const osm = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
  }).addTo(map);
  const carto = L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
    maxZoom: 20,
    subdomains: "abcd",
    attribution: '&copy; OpenStreetMap contributors &copy; CARTO'
  });
  L.control.layers({ "OpenStreetMap": osm, "Light basemap": carto }, null, { position: "topright" }).addTo(map);

  const demBounds = L.latLngBounds([
    [55.811433005, -4.320745807],
    [55.904117763, -4.155955091]
  ]);
  const demLayer = L.tileLayer.wms("http://localhost:8080/geoserver/glasgow_flood/wms", {
    layers: "glasgow_flood:dem_10km",
    styles: "dem_elevation",
    format: "image/png",
    transparent: true,
    version: "1.1.1",
    opacity: 0.82,
    pane: "terrainPane",
    attribution: "GeoServer WMS"
  });
  demLayer.addTo(map);

  const sepaFloodWms = "https://map.sepa.org.uk/server/services/Open/Flood_Maps/MapServer/WMSServer";
  const floodLayers = {
    river: {
      label: "River",
      high: "River_Flooding_High_Likelihood5469",
      medium: "River_Flooding_Medium_Likelihood22646",
      low: "River_Flooding_Low_Likelihood52415"
    },
    surface: {
      label: "Surface water",
      high: "Surface_Water_and_Small_Watercourses_Flooding_High_Likelihood39344",
      medium: "Surface_Water_and_Small_Watercourses_Flooding_Medium_Likelihood29035",
      low: "Surface_Water_and_Small_Watercourses_Flooding_Low_Likelihood26090"
    },
    coastal: {
      label: "Coastal",
      high: "Coastal_Flooding_High_Likelihood21000",
      medium: "Coastal_Flooding_Medium_Likelihood21859",
      low: "Coastal_Flooding_Low_Likelihood29650"
    }
  };
  const likelihoods = {
    high: { label: "High", chance: "10%" },
    medium: { label: "Medium", chance: "0.5%" },
    low: { label: "Low", chance: "0.1%" }
  };
  let floodLayer;
  let floodErrorShown = false;

  function buildFloodLayer(source, likelihood) {
    const detail = document.getElementById("flood-layer-detail");
    const symbol = document.querySelector(".flood-symbol");
    const layer = L.tileLayer.wms(sepaFloodWms, {
      layers: floodLayers[source][likelihood],
      styles: "",
      format: "image/png",
      transparent: true,
      version: "1.1.1",
      opacity: Number(document.getElementById("flood-opacity").value),
      pane: "floodPane",
      attribution: "&copy; SEPA 2025 · OGL v3.0"
    });
    layer.on("loading", () => {
      symbol.classList.remove("error");
      symbol.classList.add("loading");
      detail.textContent = `Loading ${likelihoods[likelihood].chance} annual chance map…`;
    });
    layer.on("load", () => {
      symbol.classList.remove("loading");
      detail.textContent = `${likelihoods[likelihood].chance} annual chance · official WMS`;
    });
    layer.on("tileerror", () => {
      symbol.classList.remove("loading");
      symbol.classList.add("error");
      if (!floodErrorShown) {
        floodErrorShown = true;
        showMessage("The SEPA flood layer could not be loaded. The terrain and station layers remain available.");
      }
    });
    return layer;
  }

  function updateFloodLayer() {
    const source = document.getElementById("flood-source").value;
    const likelihood = document.getElementById("flood-likelihood").value;
    const visible = document.getElementById("toggle-flood").checked;
    if (floodLayer && map.hasLayer(floodLayer)) map.removeLayer(floodLayer);
    floodErrorShown = false;
    floodLayer = buildFloodLayer(source, likelihood);
    document.getElementById("flood-layer-title").textContent = `${floodLayers[source].label} · ${likelihoods[likelihood].label}`;
    document.getElementById("flood-layer-detail").textContent = `${likelihoods[likelihood].chance} annual chance · official WMS`;
    if (visible) floodLayer.addTo(map);
  }

  updateFloodLayer();

  const rainfallLayer = L.layerGroup().addTo(map);
  const levelLayer = L.layerGroup().addTo(map);

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function formatTime(value) {
    if (!value) return "Not available";
    return new Intl.DateTimeFormat("en-GB", {
      dateStyle: "medium", timeStyle: "short", timeZone: "Europe/London"
    }).format(new Date(value));
  }

  function markerIcon(kind, value, state = "") {
    const isLevelContext = kind === "level" && state;
    return L.divIcon({
      className: "station-marker",
      html: `<div class="station-marker-inner ${kind}${state ? ` state-${state}` : ""}">${escapeHtml(value)}</div>`,
      iconSize: [isLevelContext ? 112 : 76, 26],
      iconAnchor: [20, 13],
      popupAnchor: [0, -13]
    });
  }

  function inDem(lat, lon) {
    return demBounds.contains(L.latLng(lat, lon));
  }

  function qualityText(codes) {
    if (!codes || !codes.length) return "Not supplied";
    return [...new Set(codes.map(String))].join(", ");
  }

  function rainPopup(summary) {
    const station = summary.station;
    return `
      <div class="popup-kicker">SEPA rainfall gauge</div>
      <div class="popup-title">${escapeHtml(station.name)}</div>
      <div class="popup-grid">
        <div class="popup-stat"><span>Last 24 h</span><strong>${summary.last_24h_mm.toFixed(1)} mm</strong></div>
        <div class="popup-stat"><span>Last 1 h</span><strong>${summary.last_1h_mm.toFixed(1)} mm</strong></div>
        <div class="popup-stat"><span>Max 15 min</span><strong>${summary.maximum_15min_mm.toFixed(1)} mm</strong></div>
        <div class="popup-stat"><span>Distance</span><strong>${station.distance_km.toFixed(1)} km</strong></div>
      </div>
      <div class="popup-meta">Station ${escapeHtml(station.station_no)} · ${formatTime(summary.latest_timestamp)}<br>Quality codes: ${escapeHtml(qualityText(summary.quality_codes))}<br>${inDem(station.latitude, station.longitude) ? "Inside" : "Outside"} the current 10 km DTM coverage</div>
      <div class="popup-warning">Local gauge observation only; not a rainfall forecast or flood-warning threshold.</div>`;
  }

  function levelPopup(summary) {
    const station = summary.station;
    const latestQuality = summary.recent_readings.at(-1)?.quality_code;
    const hasContext = summary.level_state && Number.isFinite(summary.relative_level_percent);
    const state = hasContext
      ? summary.level_state.charAt(0).toUpperCase() + summary.level_state.slice(1)
      : "Unavailable";
    const relative = hasContext ? `${summary.relative_level_percent.toFixed(1)}%` : "—";
    const normalRange = Number.isFinite(summary.normal_range_low_m) && Number.isFinite(summary.normal_range_high_m)
      ? `${summary.normal_range_low_m.toFixed(3)}–${summary.normal_range_high_m.toFixed(3)} m`
      : "Not available";
    return `
      <div class="popup-kicker">SEPA river-level station</div>
      <div class="popup-title">${escapeHtml(station.name)}</div>
      <div class="popup-grid">
        <div class="popup-stat"><span>Latest level</span><strong>${summary.latest_value_m.toFixed(3)} m</strong></div>
        <div class="popup-stat"><span>Station state</span><strong>${escapeHtml(state)} · ${escapeHtml(relative)}</strong></div>
        <div class="popup-stat"><span>SEPA normal range</span><strong>${escapeHtml(normalRange)}</strong></div>
        <div class="popup-stat"><span>Trend / 24 h change</span><strong>${escapeHtml(summary.trend)} · ${summary.change_m >= 0 ? "+" : ""}${summary.change_m.toFixed(3)} m</strong></div>
      </div>
      <div class="popup-meta">Station ${escapeHtml(station.station_no)} · ${formatTime(summary.latest_timestamp)}<br>Latest quality code: ${escapeHtml(latestQuality ?? "Not supplied")}<br>${inDem(station.latitude, station.longitude) ? "Inside" : "Outside"} the current 10 km DTM coverage</div>
      <div class="popup-warning">Percentage is the position within this station's SEPA normal range (0% = lower bound, 100% = upper bound). Low / Normal / High is historical context, not a flood warning.</div>`;
  }

  (data.rainfall?.stations || []).forEach((summary) => {
    const station = summary.station;
    L.marker([station.latitude, station.longitude], {
      icon: markerIcon("rain", `${summary.last_24h_mm.toFixed(1)} mm`),
      title: `${station.name}: ${summary.last_24h_mm.toFixed(1)} mm / 24 h`
    }).bindPopup(rainPopup(summary)).addTo(rainfallLayer);
  });

  (data.water_levels?.stations || []).forEach((summary) => {
    const station = summary.station;
    const hasContext = summary.level_state && Number.isFinite(summary.relative_level_percent);
    const state = hasContext ? summary.level_state : "";
    const stateLabel = state ? state.charAt(0).toUpperCase() + state.slice(1) : "";
    const markerValue = hasContext
      ? `${summary.relative_level_percent.toFixed(0)}% ${stateLabel}`
      : `${summary.latest_value_m.toFixed(2)}m`;
    L.marker([station.latitude, station.longitude], {
      icon: markerIcon("level", markerValue, state),
      title: `${station.name}: ${summary.latest_value_m.toFixed(3)} m${hasContext ? ` · ${stateLabel} · ${summary.relative_level_percent.toFixed(1)}% of normal range` : ""}`
    }).bindPopup(levelPopup(summary)).addTo(levelLayer);
  });

  const allBounds = L.latLngBounds([demBounds.getSouthWest(), demBounds.getNorthEast()]);
  (data.rainfall?.stations || []).forEach((summary) => {
    allBounds.extend([summary.station.latitude, summary.station.longitude]);
  });
  (data.water_levels?.stations || []).forEach((summary) => {
    allBounds.extend([summary.station.latitude, summary.station.longitude]);
  });
  map.fitBounds(demBounds, { padding: [18, 18] });

  document.getElementById("rain-count").textContent = data.rainfall?.station_count ?? 0;
  document.getElementById("level-count").textContent = data.water_levels?.station_count ?? 0;
  const retrieved = data.rainfall?.provenance?.retrieved_at || data.generated_at;
  document.getElementById("retrieval-time").textContent = `SEPA snapshot retrieved ${formatTime(retrieved)} · quality codes preserved`;

  document.getElementById("toggle-dem").addEventListener("change", (event) => {
    event.target.checked ? demLayer.addTo(map) : map.removeLayer(demLayer);
  });
  document.getElementById("toggle-rain").addEventListener("change", (event) => {
    event.target.checked ? rainfallLayer.addTo(map) : map.removeLayer(rainfallLayer);
  });
  document.getElementById("toggle-level").addEventListener("change", (event) => {
    event.target.checked ? levelLayer.addTo(map) : map.removeLayer(levelLayer);
  });
  document.getElementById("toggle-flood").addEventListener("change", (event) => {
    event.target.checked ? floodLayer.addTo(map) : map.removeLayer(floodLayer);
  });
  document.getElementById("flood-source").addEventListener("change", updateFloodLayer);
  document.getElementById("flood-likelihood").addEventListener("change", updateFloodLayer);

  const opacity = document.getElementById("dem-opacity");
  opacity.addEventListener("input", () => {
    const value = Number(opacity.value);
    demLayer.setOpacity(value);
    document.getElementById("dem-opacity-value").textContent = `${Math.round(value * 100)}%`;
  });

  const floodOpacity = document.getElementById("flood-opacity");
  floodOpacity.addEventListener("input", () => {
    const value = Number(floodOpacity.value);
    floodLayer.setOpacity(value);
    document.getElementById("flood-opacity-value").textContent = `${Math.round(value * 100)}%`;
  });

  document.getElementById("fit-all").addEventListener("click", () => map.fitBounds(allBounds, { padding: [34, 34], maxZoom: 12 }));
  document.getElementById("fit-dem").addEventListener("click", () => map.fitBounds(demBounds, { padding: [24, 24] }));

  const warnings = [...(data.rainfall?.warnings || []), ...(data.water_levels?.warnings || [])];
  if (warnings.some((warning) => warning.includes("quality codes"))) {
    showMessage("SEPA quality codes are preserved in station popups; interpret them with provider metadata.");
  }
})();
