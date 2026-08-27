const API_URL = "http://localhost:8000";
const GEOSERVER = "http://localhost:8080/geoserver";
const WORKSPACE = "glasgow_flood";
const WMS_URL = `${GEOSERVER}/${WORKSPACE}/wms`;
const WFS_URL = `${GEOSERVER}/${WORKSPACE}/ows`;
const GLASGOW_CENTER = [55.8642, -4.2518];

const terrainLayerNames = new Set(["dem", "dtm", "dsm", "slope", "flow_accumulation"]);

const map = L.map("map", {
  center: GLASGOW_CENTER,
  zoom: 11,
  zoomControl: false
});

const osm = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
});

const cartoVoyager = L.tileLayer(
  "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
  {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
    subdomains: "abcd"
  }
);

const esriSat = L.tileLayer(
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
  {
    maxZoom: 18,
    attribution: "Tiles &copy; Esri"
  }
);

osm.addTo(map);

const baseMaps = {
  "OpenStreetMap (Standard)": osm,
  "Carto Voyager (Color)": cartoVoyager,
  "Esri Satellite": esriSat
};

L.control.layers(baseMaps, null, {
  position: "topright",
  collapsed: true
}).addTo(map);

const layersByName = {};
const layerStatusByName = {};
let layerDefinitions = [];

function scheduleMapResize() {
  requestAnimationFrame(() => {
    map.invalidateSize({ pan: false });
  });
}

function forceMapResize() {
  map.invalidateSize({ pan: false });
}

forceMapResize();

function workspaceLayerName(name) {
  return `${WORKSPACE}:${name}`;
}

function displayGroup(definition) {
  if (terrainLayerNames.has(definition.name)) return "Terrain Data";
  if (definition.category === "Flood Hazard") return "Flood Hazard";
  if (definition.category === "Exposure") return "Exposure Assets";
  return definition.category;
}

function setLayerStatus(layerName, message) {
  layerStatusByName[layerName] = message;
  const status = document.querySelector(`[data-layer-status="${layerName}"]`);
  if (status) status.textContent = message || "";
}

function createWmsLayer(definition) {
  const layer = L.tileLayer.wms(WMS_URL, {
    layers: workspaceLayerName(definition.geoserver_name),
    format: "image/png",
    transparent: true,
    version: "1.1.1",
    attribution: "GeoServer WMS"
  });

  layer.on("tileerror", () => {
    setLayerStatus(definition.name, "WMS layer is not published or has no readable data.");
  });

  layer.on("load", () => {
    setLayerStatus(definition.name, "");
  });

  return layer;
}

function styleFeature(definition, opacity = 1) {
  return {
    color: definition.color || "#1677b8",
    opacity,
    weight: 2,
    fillColor: definition.color || "#1677b8",
    fillOpacity: Math.min(opacity, 0.35)
  };
}

function createPopup(properties) {
  const rows = Object.entries(properties || {})
    .filter(([key]) => !["boundedBy", "geom", "geometry"].includes(key))
    .slice(0, 12)
    .map(([key, value]) => `<tr><th>${key}</th><td>${value ?? ""}</td></tr>`)
    .join("");
  return rows ? `<table class="popup-table">${rows}</table>` : "No attributes available.";
}

function createWfsLayer(definition) {
  const layer = L.geoJSON(null, {
    style: () => styleFeature(definition),
    pointToLayer: (_feature, latlng) =>
      L.circleMarker(latlng, { radius: 6, ...styleFeature(definition) }),
    onEachFeature: (feature, featureLayer) => {
      featureLayer.bindPopup(createPopup(feature.properties));
    }
  });

  layer.on("add", () => {
    if (layer.getLayers().length > 0) return;

    setLayerStatus(definition.name, "Loading vector features...");

    const params = new URLSearchParams({
      service: "WFS",
      version: "1.0.0",
      request: "GetFeature",
      typeName: workspaceLayerName(definition.geoserver_name),
      outputFormat: "application/json",
      srsName: "EPSG:4326"
    });

    fetch(`${WFS_URL}?${params}`)
      .then((response) => {
        if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
        return response.json();
      })
      .then((geojson) => {
        layer.addData(geojson);
        const count = geojson.features ? geojson.features.length : 0;
        setLayerStatus(definition.name, count ? "" : "Layer loaded, but no features were returned.");
        if (layer.getBounds().isValid()) map.fitBounds(layer.getBounds(), { maxZoom: 13 });
        scheduleMapResize();
      })
      .catch((error) => {
        console.error(`Could not load ${definition.display_name}`, error);
        setLayerStatus(definition.name, "WFS layer is not published or has no readable data.");
      });
  });

  return layer;
}

function createLayer(definition) {
  if (definition.service === "WMS") return createWmsLayer(definition);
  if (definition.service === "WFS") return createWfsLayer(definition);
  console.warn(`Unsupported service for ${definition.name}: ${definition.service}`);
  return null;
}

function registerLayer(definition) {
  const layer = createLayer(definition);
  if (!layer) return;
  layersByName[definition.name] = layer;
}

function activeLayers(service) {
  return layerDefinitions.filter((definition) => {
    const layer = layersByName[definition.name];
    return definition.service === service && layer && map.hasLayer(layer);
  });
}

function updateLegend() {
  const activeWmsLayers = activeLayers("WMS");
  const activeVectorLayers = activeLayers("WFS");

  const wmsLegends = activeWmsLayers
    .map((definition) => {
      const params = new URLSearchParams({
        request: "GetLegendGraphic",
        version: "1.0.0",
        format: "image/png",
        layer: workspaceLayerName(definition.geoserver_name)
      });
      return `<section><h3>${definition.display_name}</h3><img alt="${definition.display_name} legend" src="${WMS_URL}?${params}"></section>`;
    })
    .join("");

  const vectorLegends = activeVectorLayers
    .map(
      (definition) =>
        `<section><h3>${definition.display_name}</h3><span class="swatch" style="background:${definition.color}"></span>${displayGroup(definition)}</section>`
    )
    .join("");

  document.getElementById("legend-content").innerHTML =
    wmsLegends || vectorLegends
      ? `${wmsLegends}${vectorLegends}`
      : "Turn layers on to view legends.";
}

function groupedLayers() {
  return layerDefinitions.reduce((groups, definition) => {
    const groupName = displayGroup(definition);
    groups[groupName] = groups[groupName] || [];
    groups[groupName].push(definition);
    return groups;
  }, {});
}

function renderLayerPanel() {
  const layerList = document.getElementById("layer-list");
  const groups = groupedLayers();

  layerList.innerHTML = Object.entries(groups)
    .map(
      ([groupName, definitions], groupIndex) => `
        <section class="layer-group ${groupIndex > 1 ? "closed" : ""}">
          <button class="accordion-trigger" type="button" data-accordion-trigger>
            <span>${groupName}</span>
            <span class="accordion-count">${definitions.length} layers</span>
          </button>
          <div class="accordion-body">
            ${definitions
              .map(
                (definition) => `
                  <article class="layer-item">
                    <div class="layer-row">
                      <label class="switch" title="Toggle ${definition.display_name}">
                        <input type="checkbox" data-layer-toggle="${definition.name}">
                        <span></span>
                      </label>
                      <span class="layer-title" title="${definition.display_name}">${definition.display_name}</span>
                      <span class="service-pill">${definition.service}</span>
                    </div>
                    <div class="layer-meta">
                      <span>${definition.type}</span>
                      <span>${definition.category}</span>
                    </div>
                    <label class="opacity-row">
                      <span>Opacity</span>
                      <input type="range" min="0" max="1" step="0.05" value="1" data-layer-opacity="${definition.name}">
                      <span data-opacity-value="${definition.name}">100%</span>
                    </label>
                    <div class="layer-status" data-layer-status="${definition.name}">${layerStatusByName[definition.name] || ""}</div>
                  </article>
                `
              )
              .join("")}
          </div>
        </section>
      `
    )
    .join("");

  layerList.querySelectorAll("[data-accordion-trigger]").forEach((trigger) => {
    trigger.addEventListener("click", () => {
      trigger.closest(".layer-group").classList.toggle("closed");
      scheduleMapResize();
    });
  });

  layerList.querySelectorAll("[data-layer-toggle]").forEach((checkbox) => {
    checkbox.addEventListener("change", (event) => {
      const layerName = event.target.dataset.layerToggle;
      const layer = layersByName[layerName];
      if (!layer) return;
      if (event.target.checked) {
        layer.addTo(map);
      } else {
        map.removeLayer(layer);
      }
      updateLegend();
      scheduleMapResize();
    });
  });

  layerList.querySelectorAll("[data-layer-opacity]").forEach((slider) => {
    slider.addEventListener("input", (event) => {
      const layerName = event.target.dataset.layerOpacity;
      const value = Number(event.target.value);
      const layer = layersByName[layerName];
      const label = document.querySelector(`[data-opacity-value="${layerName}"]`);
      if (label) label.textContent = `${Math.round(value * 100)}%`;
      if (layer && layer.setOpacity) layer.setOpacity(value);
      if (layer && !layer.setOpacity && layer.setStyle) {
        layer.setStyle(styleFeature(layerDefinitions.find((item) => item.name === layerName), value));
      }
    });
  });
}

function clearLayers() {
  Object.values(layersByName).forEach((layer) => {
    if (map.hasLayer(layer)) map.removeLayer(layer);
  });
  document.querySelectorAll("[data-layer-toggle]").forEach((checkbox) => {
    checkbox.checked = false;
  });
  updateLegend();
  scheduleMapResize();
}

async function loadLayerDefinitions() {
  const response = await fetch(`${API_URL}/layers`);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

async function refreshLayers() {
  const layerList = document.getElementById("layer-list");
  layerList.textContent = "Loading layers...";
  clearLayers();
  layerDefinitions = await loadLayerDefinitions();
  Object.keys(layersByName).forEach((key) => delete layersByName[key]);
  layerDefinitions.forEach(registerLayer);
  renderLayerPanel();
  updateLegend();
  scheduleMapResize();
}

function bindStaticControls() {
  document.getElementById("zoom-in").addEventListener("click", () => map.zoomIn());
  document.getElementById("zoom-out").addEventListener("click", () => map.zoomOut());
  document.getElementById("zoom-glasgow").addEventListener("click", () => {
    map.setView(GLASGOW_CENTER, 11);
    scheduleMapResize();
  });
  document.getElementById("clear-layers").addEventListener("click", clearLayers);
  document.getElementById("refresh-layers").addEventListener("click", () => {
    refreshLayers().catch((error) => {
      console.error("Could not refresh layers", error);
      document.getElementById("layer-list").textContent = "Could not refresh layers.";
    });
  });

  document.getElementById("toggle-layer-panel").addEventListener("click", () => {
    document.getElementById("layer-panel").classList.add("collapsed");
    document.getElementById("open-layer-panel").classList.add("visible");
    scheduleMapResize();
  });
  document.getElementById("open-layer-panel").addEventListener("click", () => {
    document.getElementById("layer-panel").classList.remove("collapsed");
    document.getElementById("open-layer-panel").classList.remove("visible");
    scheduleMapResize();
  });

  document.getElementById("toggle-legend").addEventListener("click", () => {
    const panel = document.getElementById("legend-panel");
    panel.classList.toggle("collapsed");
    document.getElementById("legend-toggle-icon").textContent = panel.classList.contains("collapsed")
      ? "+"
      : "−";
  });

  document.getElementById("toggle-agent").addEventListener("click", () => {
    const body = document.getElementById("agent-body");
    body.classList.toggle("collapsed");
    document.getElementById("agent-toggle-icon").textContent = body.classList.contains("collapsed")
      ? "+"
      : "−";
    scheduleMapResize();
  });

  window.addEventListener("resize", scheduleMapResize);
  window.addEventListener("load", forceMapResize);
}

async function init() {
  bindStaticControls();
  forceMapResize();

  try {
    const health = await fetch(`${API_URL}/health`);
    const status = document.getElementById("api-status");
    status.textContent = health.ok ? "API online" : "API unavailable";
    status.classList.toggle("ok", health.ok);
    status.classList.toggle("error", !health.ok);

    layerDefinitions = await loadLayerDefinitions();
    layerDefinitions.forEach(registerLayer);
    renderLayerPanel();
    updateLegend();
  } catch (error) {
    console.error("Could not load layer metadata", error);
    const status = document.getElementById("api-status");
    status.textContent = "API offline";
    status.classList.add("error");
    document.getElementById("layer-list").textContent =
      "Layer metadata API is unavailable. Start FastAPI on port 8000.";
    document.getElementById("legend-content").textContent =
      "Layer metadata API is unavailable.";
  }

  forceMapResize();
}

init();
