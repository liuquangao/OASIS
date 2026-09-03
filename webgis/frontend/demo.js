const GLASGOW = [55.8642, -4.2518];
const HAZARD_BOUNDS = [
  [55.76898395467363, -4.412112769719773],
  [55.942438044680635, -4.051210467294865]
];
const HAZARD_WMS_URL = "http://127.0.0.1:8080/geoserver/glasgow_flood/wms";
const AGENT_API_URL = "http://127.0.0.1:8000";
const CARTO_BASEMAP_KEY = window.HYDROMIND_CONFIG?.cartoBasemapKey || "";

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
  attribution: "HydroMind latest calculated 5 m hazard raster"
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
let setupCanUseAgent = false;
let setupPollTimer = null;
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
  pending_assessment: null,
  recent_analysis_run_id: null,
  last_task: null
};

const assessmentDecision = document.getElementById("assessment-decision");
const decisionControls = document.getElementById("decision-controls");
const assessmentProgress = document.getElementById("assessment-progress");
const assessmentStatusBadge = document.getElementById("assessment-status-badge");
const decisionBody = document.getElementById("decision-body");
const decisionToggle = document.getElementById("decision-toggle");
const assessmentRationale = document.getElementById("assessment-rationale");
const confirmAssessmentButton = document.getElementById("assessment-confirm");
const weightInputs = {
  hazard: document.getElementById("hazard-weight"),
  exposure: document.getElementById("exposure-weight"),
  vulnerability: document.getElementById("vulnerability-weight")
};
let activeAssessmentPlan = null;
let assessmentMode = "plan";
let selectedPriorityPreset = "social_equity";

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

const PRIORITY_PRESETS = {
  life_safety: { hazard: 0.45, exposure: 0.40, vulnerability: 0.15 },
  social_equity: { hazard: 0.25, exposure: 0.25, vulnerability: 0.50 },
  economic_protection: { hazard: 0.40, exposure: 0.45, vulnerability: 0.15 }
};

function setDecisionWeights(weights, preset = "custom") {
  Object.entries(weightInputs).forEach(([key, input]) => {
    input.value = weights[key];
  });
  selectedPriorityPreset = preset;
  document.querySelectorAll("[data-preset]").forEach((button) => {
    button.classList.toggle("active", button.dataset.preset === preset);
  });
  updateWeightDisplay();
}

function updateWeightDisplay() {
  const values = Object.fromEntries(
    Object.entries(weightInputs).map(([key, input]) => [key, Number(input.value)])
  );
  Object.entries(values).forEach(([key, value]) => {
    document.getElementById(`${key}-weight-output`).value = `${Math.round(value * 100)}%`;
  });
  const total = values.hazard + values.exposure + values.vulnerability;
  const valid = Math.abs(total - 1) < 0.001;
  const totalElement = document.getElementById("weight-total");
  totalElement.textContent = `Total ${Math.round(total * 100)}%`;
  totalElement.classList.toggle("invalid", !valid);
  confirmAssessmentButton.disabled = !valid || assessmentMode === "running";
  return { values, valid };
}

function setDecisionCollapsed(collapsed) {
  decisionBody.hidden = collapsed;
  decisionToggle.textContent = collapsed ? "▸" : "▾";
  decisionToggle.setAttribute("aria-expanded", String(!collapsed));
  const label = `${collapsed ? "Expand" : "Collapse"} decision settings`;
  decisionToggle.setAttribute("aria-label", label);
  decisionToggle.title = label;
}

decisionToggle.addEventListener("click", () => setDecisionCollapsed(!decisionBody.hidden));

function renderAssessmentPlan(plan) {
  if (!plan) return;
  activeAssessmentPlan = plan;
  assessmentMode = "plan";
  assessmentDecision.hidden = false;
  setDecisionCollapsed(false);
  decisionControls.hidden = false;
  assessmentProgress.hidden = true;
  assessmentStatusBadge.textContent = "Awaiting confirmation";
  assessmentStatusBadge.className = "decision-status";
  assessmentRationale.textContent = `${plan.intent.rationale} ${plan.missing_datasets.length} data dependencies are incomplete or unavailable.`;
  document.getElementById("assessment-scenario").value = plan.preferences.scenario;
  document.getElementById("assessment-horizon").value = String(plan.preferences.forecast_horizon_hours);
  document.getElementById("assessment-threshold").value = String(plan.preferences.hazard_threshold);
  ["assessment-scenario", "assessment-horizon", "assessment-threshold"].forEach((id) => {
    document.getElementById(id).disabled = false;
  });
  document.getElementById("assessment-simd").checked = plan.preferences.include_simd;
  setDecisionWeights(plan.preferences.weights, plan.preferences.priority_scenario);
  confirmAssessmentButton.textContent = "Confirm and run";
}

function assessmentPreferences() {
  const { values, valid } = updateWeightDisplay();
  if (!valid) throw new Error("Hazard, exposure and vulnerability weights must total 100%.");
  const scenario = document.getElementById("assessment-scenario").value;
  return {
    scenario,
    use_live_data: scenario !== "historical",
    forecast_horizon_hours: Number(document.getElementById("assessment-horizon").value),
    hazard_threshold: Number(document.getElementById("assessment-threshold").value),
    priority_scenario: selectedPriorityPreset,
    weights: values,
    include_simd: document.getElementById("assessment-simd").checked,
    historical_issue_time: scenario === "historical" ? "2023-10-06T06:00:00Z" : null
  };
}

function renderAssessmentProgress(job) {
  assessmentDecision.hidden = false;
  decisionControls.hidden = job.status === "running" || job.status === "queued" || job.status === "validating";
  assessmentProgress.hidden = false;
  assessmentProgress.replaceChildren();
  job.steps.forEach((step) => {
    const item = textElement("li", step.status, step.label);
    item.appendChild(textElement("small", "", step.status));
    item.title = step.detail || step.label;
    assessmentProgress.appendChild(item);
  });
  assessmentStatusBadge.textContent = job.status.replaceAll("_", " ");
  assessmentStatusBadge.className = `decision-status ${job.status}`;
}

async function executeAssessment() {
  if (!activeAssessmentPlan) return;
  if (assessmentMode === "rerank") {
    await rerankAssessment();
    return;
  }
  const preferences = assessmentPreferences();
  assessmentMode = "running";
  updateWeightDisplay();
  const response = await fetch(
    `${AGENT_API_URL}/assessments/${activeAssessmentPlan.plan_id}/execute`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ preferences, state: sessionState })
    }
  );
  if (!response.ok) throw new Error((await response.json()).detail || "Assessment could not be queued.");
  const job = await response.json();
  renderAssessmentProgress(job);
  await pollAssessmentJob(job.job_id);
}

async function pollAssessmentJob(jobId) {
  while (true) {
    const response = await fetch(`${AGENT_API_URL}/assessment-jobs/${jobId}`);
    if (!response.ok) throw new Error("Assessment progress is unavailable.");
    const job = await response.json();
    renderAssessmentProgress(job);
    if (["completed", "partial", "failed"].includes(job.status)) {
      if (job.final_response) {
        sessionState = job.final_response.state;
        keepPublicAnalysisLayers();
        applyMapEvents(job.final_response.events);
        renderRiskReport(sessionState.risk_report);
        addMessage("agent", job.final_response.message);
        assessmentMode = "rerank";
        decisionControls.hidden = false;
        ["assessment-scenario", "assessment-horizon", "assessment-threshold"].forEach((id) => {
          document.getElementById(id).disabled = true;
        });
        confirmAssessmentButton.textContent = "Apply re-ranking";
        updateWeightDisplay();
      } else {
        assessmentMode = "plan";
        decisionControls.hidden = false;
        assessmentStatusBadge.classList.add("failed");
        addMessage("agent", job.error || "The confirmed assessment failed. Review the failed step and retry.");
        updateWeightDisplay();
      }
      return;
    }
    await new Promise((resolve) => window.setTimeout(resolve, 1000));
  }
}

async function rerankAssessment() {
  const preferences = assessmentPreferences();
  assessmentMode = "running";
  updateWeightDisplay();
  assessmentStatusBadge.textContent = "Re-ranking";
  assessmentStatusBadge.className = "decision-status running";
  const response = await fetch(
    `${AGENT_API_URL}/analysis/runs/${sessionState.recent_analysis_run_id}/rerank`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        weights: preferences.weights,
        include_simd: preferences.include_simd,
        scenario_name: preferences.priority_scenario
      })
    }
  );
  if (!response.ok) throw new Error((await response.json()).detail || "Re-ranking failed.");
  const payload = await response.json();
  const oldPriorityIds = sessionState.analysis_layers
    .filter((layer) => layer.style === "priority")
    .map((layer) => layer.id);
  oldPriorityIds.forEach((id) => {
    const rendered = analysisLayerObjects.get(id);
    if (rendered) map.removeLayer(rendered);
    analysisLayerObjects.delete(id);
  });
  sessionState.analysis_layers = sessionState.analysis_layers.filter((layer) => layer.style !== "priority");
  sessionState.visible_analysis_layer_ids = sessionState.visible_analysis_layer_ids.filter((id) => !oldPriorityIds.includes(id));
  sessionState.analysis_layers.push(...payload.run.map_layers);
  sessionState.visible_analysis_layer_ids = payload.run.map_layers.filter((layer) => layer.visible).map((layer) => layer.id);
  sessionState.recent_analysis_run_id = payload.run.run_id;
  if (sessionState.risk_report) {
    sessionState.risk_report.summary =
      `Priority was re-ranked from persisted components with quality status ${payload.quality.status}; no weather API or Hazard recomputation was used.`;
    sessionState.risk_report.key_findings = (payload.run.summary.top_areas || []).slice(0, 5).map((item) =>
      `Rank ${item.rank}: ${item.name || item.id} — priority ${item.priority_score?.toFixed(3) ?? "unavailable"}, ` +
      `hazard ${item.hazard_score?.toFixed(3) ?? "unavailable"}, exposure ${item.exposure_score?.toFixed(3) ?? "unavailable"}, ` +
      `vulnerability ${item.vulnerability_score?.toFixed(3) ?? "unavailable"}; rank change ${item.rank_change > 0 ? "+" : ""}${item.rank_change ?? "unavailable"}.`
    );
    const previousFindings = new Map(
      (sessionState.risk_report.findings || []).map((item) => [item.area_id, item])
    );
    sessionState.risk_report.findings = (payload.run.summary.top_areas || []).slice(0, 5).map((item) =>
      reportFindingFromRank(item, preferences.weights, previousFindings.get(item.id))
    );
    sessionState.risk_report.calculation = {
      lens: preferences.priority_scenario.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()),
      formula: "priority = hazard × weight + exposure × weight + vulnerability × weight",
      weights: preferences.weights
    };
    sessionState.risk_report.evidence = [
      ...(sessionState.risk_report.evidence || []).filter((item) => item.label !== "Re-ranking run"),
      { label: "Re-ranking run", value: payload.run.run_id, source: "HydroMind deterministic priority rerank", observed_at: null, source_url: null }
    ];
    renderRiskReport(sessionState.risk_report);
  }
  applyMapEvents([{ type: "sync_analysis_layers", layer_ids: payload.run.map_layers.map((layer) => layer.id) }]);
  const top = payload.run.summary.top_areas || [];
  addMessage(
    "agent",
    top.length
      ? `Re-ranked without weather API calls. ${top[0].name || top[0].id} is now rank 1 (${top[0].rank_change >= 0 ? "+" : ""}${top[0].rank_change || 0} places). Quality: ${payload.quality.status}.`
      : `Re-ranking completed with quality status ${payload.quality.status}.`
  );
  assessmentMode = "rerank";
  assessmentStatusBadge.textContent = `Re-ranked · ${payload.quality.status}`;
  assessmentStatusBadge.className = `decision-status ${payload.quality.status === "fail" ? "failed" : ""}`;
  renderAnalysisLayerControls();
  updateWeightDisplay();
}

function reportFindingFromRank(item, weights, previous) {
  const contributions = ["hazard", "exposure", "vulnerability"].map((component) => ({
    component,
    score: item[`${component}_score`],
    weight: weights[component],
    contribution: item[`${component}_score`] * weights[component]
  }));
  const dominant = contributions.reduce((best, item) => item.contribution > best.contribution ? item : best);
  const reasons = {
    hazard: "the mapped flood hazard",
    exposure: "the number of people, buildings and critical facilities exposed",
    vulnerability: "the relative social vulnerability indicators"
  };
  return {
    area_id: item.id,
    name: item.name || item.id,
    rank: item.rank,
    priority_score: item.priority_score,
    explanation: `Ranks #${item.rank} mainly because of ${reasons[dominant.component]}.`,
    facts: previous?.facts || [],
    contributions
  };
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

  if (report.drivers?.length) {
    const section = document.createElement("section");
    section.className = "report-driver-section";
    section.appendChild(textElement("h3", "", "Why the hazard looks like this"));
    const chain = document.createElement("div");
    chain.className = "report-driver-chain";
    report.drivers.forEach((driver, index) => {
      const card = document.createElement("div");
      card.className = `report-driver driver-${driver.role || "used"}`;
      card.append(
        textElement("span", "driver-step", String(index + 1).padStart(2, "0")),
        textElement("strong", "", driver.label),
        textElement("b", "", driver.value),
        textElement("p", "", driver.explanation),
        textElement("small", "", driver.observed_at
          ? `${driver.source} · ${new Date(driver.observed_at).toLocaleString()}`
          : driver.source)
      );
      chain.appendChild(card);
    });
    section.appendChild(chain);
    article.appendChild(section);
  }

  if (report.findings?.length) {
    const section = document.createElement("section");
    section.appendChild(textElement("h3", "", "Why these areas rank highest"));
    const cards = document.createElement("div");
    cards.className = "report-finding-list";
    report.findings.forEach((finding) => {
      const details = document.createElement("details");
      details.className = "report-finding";
      const summary = document.createElement("summary");
      summary.append(
        textElement("span", "finding-rank", `#${finding.rank}`),
        textElement("strong", "", finding.name),
        textElement("span", "finding-score", finding.priority_score.toFixed(3)),
        textElement("p", "", finding.explanation)
      );
      details.appendChild(summary);
      const body = document.createElement("div");
      body.className = "finding-detail";
      if (finding.facts?.length) {
        const facts = document.createElement("ul");
        finding.facts.forEach((fact) => facts.appendChild(textElement("li", "", fact)));
        body.appendChild(facts);
      }
      if (finding.contributions?.length) {
        const contributions = document.createElement("div");
        contributions.className = "finding-contributions";
        finding.contributions.forEach((item) => {
          const row = document.createElement("div");
          row.append(
            textElement("span", "", item.component),
            textElement("span", "", `${item.score.toFixed(3)} × ${(item.weight * 100).toFixed(0)}%`),
            textElement("strong", "", item.contribution.toFixed(3))
          );
          contributions.appendChild(row);
        });
        body.appendChild(contributions);
      }
      details.appendChild(body);
      cards.appendChild(details);
    });
    section.appendChild(cards);
    article.appendChild(section);
  } else if (report.key_findings?.length) {
    const section = document.createElement("section");
    section.appendChild(textElement("h3", "", "Key findings"));
    const list = document.createElement("ul");
    report.key_findings.forEach((finding) => list.appendChild(textElement("li", "", finding)));
    section.appendChild(list);
    article.appendChild(section);
  }

  if (report.calculation) {
    const details = document.createElement("details");
    details.className = "report-calculation";
    const summary = document.createElement("summary");
    summary.textContent = `How the ${report.calculation.lens} score is calculated`;
    details.appendChild(summary);
    const weights = report.calculation.weights;
    details.append(
      textElement("p", "", report.calculation.formula),
      textElement("p", "calculation-weights",
        `Hazard ${(weights.hazard * 100).toFixed(0)}% · Exposure ${(weights.exposure * 100).toFixed(0)}% · Vulnerability ${(weights.vulnerability * 100).toFixed(0)}%`)
    );
    article.appendChild(details);
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
    const details = document.createElement("details");
    details.className = "report-limitations";
    const summary = document.createElement("summary");
    summary.textContent = "What to keep in mind";
    details.appendChild(summary);
    const list = document.createElement("ul");
    report.limitations.forEach((item) => list.appendChild(textElement("li", "", item)));
    details.appendChild(list);
    article.appendChild(details);
  }

  article.appendChild(textElement("footer", "report-footer", `Generated ${new Date(report.generated_at).toLocaleString()}`));
  riskReportView.appendChild(article);
}

conversationTab.addEventListener("click", () => setPanelView("conversation"));
riskReportTab.addEventListener("click", () => setPanelView("report"));

function analysisLayerById(id) {
  return sessionState.analysis_layers.find((layer) => layer.id === id && isPublicAnalysisLayer(layer));
}

function isPublicAnalysisLayer(descriptor) {
  return descriptor.kind !== "wms" || !/\bhazard index\b/i.test(descriptor.label);
}

function keepPublicAnalysisLayers() {
  const diagnosticIds = sessionState.analysis_layers
    .filter((layer) => !isPublicAnalysisLayer(layer))
    .map((layer) => layer.id);
  diagnosticIds.forEach((id) => {
    const rendered = analysisLayerObjects.get(id);
    if (rendered) map.removeLayer(rendered);
    analysisLayerObjects.delete(id);
  });
  const publicIds = new Set(
    sessionState.analysis_layers.filter(isPublicAnalysisLayer).map((layer) => layer.id)
  );
  sessionState.analysis_layers = sessionState.analysis_layers.filter(isPublicAnalysisLayer);
  sessionState.visible_analysis_layer_ids = sessionState.visible_analysis_layer_ids
    .filter((id) => publicIds.has(id));
}

function geoJsonStyle(feature, descriptor) {
  const properties = feature.properties || {};
  const scoreFields = {
    hazard: "hazard_score",
    exposure: "exposure_score",
    vulnerability: "vulnerability_score",
    priority: "priority_score"
  };
  const palettes = {
    hazard: ["#fde68a", "#f97316", "#b91c1c"],
    exposure: ["#dbeafe", "#3b82f6", "#1e3a8a"],
    vulnerability: ["#dcfce7", "#22c55e", "#166534"],
    priority: ["#f3e8ff", "#a855f7", "#581c87"]
  };
  const style = descriptor?.style || "priority";
  const score = properties[scoreFields[style]] ?? properties.relative_vulnerability;
  const palette = palettes[style] || palettes.priority;
  const color = score == null ? "#94a3b8" : score >= 0.67 ? palette[2] : score >= 0.34 ? palette[1] : palette[0];
  return { color: "#334155", weight: 1, fillColor: color, fillOpacity: 0.58 };
}

function analysisFeaturePopup(feature, descriptor) {
  const properties = feature.properties || {};
  const name = properties.name || properties.id || "Mapped area";
  if (descriptor?.style !== "priority") {
    const scoreKey = `${descriptor?.style || "priority"}_score`;
    const score = properties[scoreKey];
    return `<div class="analysis-popup"><strong>${escapeHtml(name)}</strong>` +
      (score == null ? "" : `<p>Relative ${escapeHtml(descriptor.style)} score: <b>${Number(score).toFixed(3)}</b></p>`) +
      "</div>";
  }
  const areaFraction = properties.hazardous_area_fraction;
  const population = properties.estimated_exposed_population;
  const buildings = properties.exposed_building_count;
  const buildingTotal = properties.building_count;
  const facts = [];
  if (areaFraction != null) facts.push(`${(Number(areaFraction) * 100).toFixed(0)}% of classified area is in class 2 or 3`);
  if (population != null) facts.push(`about ${Number(population).toLocaleString(undefined, { maximumFractionDigits: 0 })} residents exposed`);
  if (buildings != null && buildingTotal != null) facts.push(`${Number(buildings).toFixed(0)} of ${Number(buildingTotal).toFixed(0)} buildings exposed`);
  const scoreRows = ["hazard", "exposure", "vulnerability"].map((component) => {
    const value = properties[`${component}_score`];
    return value == null ? "" : `<span>${component}</span><b>${Number(value).toFixed(3)}</b>`;
  }).join("");
  return `<div class="analysis-popup priority-popup">` +
    `<small>Priority #${escapeHtml(String(properties.priority_rank ?? "—"))}</small>` +
    `<strong>${escapeHtml(name)}</strong>` +
    `<p>${escapeHtml(facts.join("; ") || "Open the report for the calculation explanation.")}</p>` +
    `<div class="popup-score-grid">${scoreRows}</div>` +
    `<em>Priority ${properties.priority_score == null ? "—" : Number(properties.priority_score).toFixed(3)}</em>` +
    `</div>`;
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
      style: (feature) => geoJsonStyle(feature, descriptor),
      onEachFeature: (feature, item) => item.bindPopup(analysisFeaturePopup(feature, descriptor))
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
  updateAnalysisLegend();
  renderAnalysisLayerControls();
}

function updateAnalysisLegend() {
  const visible = [...sessionState.visible_analysis_layer_ids].reverse()
    .map(analysisLayerById).find((descriptor) => descriptor?.kind === "geojson");
  if (!visible) {
    updateHazardLegend();
    return;
  }
  const legend = document.getElementById("legend-card");
  const palettes = {
    hazard: ["#fde68a", "#f97316", "#b91c1c"],
    exposure: ["#dbeafe", "#3b82f6", "#1e3a8a"],
    vulnerability: ["#dcfce7", "#22c55e", "#166534"],
    priority: ["#f3e8ff", "#a855f7", "#581c87"]
  };
  const palette = palettes[visible.style] || palettes.priority;
  document.getElementById("legend-title").textContent = visible.label;
  setLegendScale([
    ["high", "Higher relative score · ≥ 0.67", palette[2]],
    ["medium", "Medium relative score · 0.34–0.66", palette[1]],
    ["low", "Lower relative score · < 0.34", palette[0]]
  ]);
  document.getElementById("legend-note").textContent =
    `${visible.style || "relative"} score: pale = lower, dark = higher. Relative research indicator, not an official warning.`;
  legend.classList.add("visible");
}

function renderAnalysisLayerControls() {
  const container = document.getElementById("analysis-layer-list");
  container.replaceChildren();
  sessionState.analysis_layers.filter(isPublicAnalysisLayer).forEach((descriptor) => {
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
  setLegendScale([
    ["high", "High · class 3", "#ef4444"],
    ["medium", "Medium · class 2", "#f59e0b"],
    ["low", "Low · class 1", "#38bdf8"]
  ]);
  document.getElementById("legend-note").textContent =
    "Latest SEPA-rainfall prototype snapshot. Not an operational flood warning.";
  legend.classList.toggle("visible", sessionState.hazard_layer_visible);
}

function setLegendScale(rows) {
  rows.forEach(([level, label, color]) => {
    const row = document.getElementById(`legend-${level}-row`);
    const swatch = document.getElementById(`legend-${level}-swatch`);
    row.lastChild.textContent = label;
    swatch.style.background = color;
  });
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

function setAgentControlsDisabled(disabled) {
  document.getElementById("agent-input").disabled = disabled;
  document.querySelector("#agent-form button").disabled = disabled;
  document.querySelectorAll(".prompt-chip").forEach((button) => {
    button.disabled = disabled;
  });
}

function setupItem(label, state, detail) {
  const item = document.createElement("div");
  item.className = `setup-item ${state}`;
  const markerText = state === "ok" ? "✓" : state === "missing" ? "!" : "i";
  const marker = textElement("span", "setup-item-marker", markerText);
  const copy = document.createElement("div");
  copy.append(textElement("strong", "", label), textElement("p", "", detail));
  item.append(marker, copy);
  return item;
}

function renderSetupStatus(status) {
  const root = document.getElementById("setup-status");
  const dot = document.getElementById("setup-status-dot");
  const title = document.getElementById("setup-status-title");
  const summary = document.getElementById("setup-status-summary");
  const body = document.getElementById("setup-status-body");
  body.replaceChildren();

  const configuration = status.configuration.map((item) => ({ ...item }));
  const carto = configuration.find((item) => item.id === "carto_basemap");
  if (carto) {
    carto.configured = Boolean(CARTO_BASEMAP_KEY && !CARTO_BASEMAP_KEY.startsWith("your-"));
  }
  const missing = configuration.filter((item) => item.configured === false);
  const requiredMissing = missing.filter((item) => item.importance === "required");
  const importantMissing = missing.filter((item) => item.importance !== "optional");
  const isRunning = status.job.state === "running";

  setupCanUseAgent = Boolean(status.can_use_agent && !requiredMissing.length);
  setAgentControlsDisabled(!setupCanUseAgent);
  root.className = `setup-status ${isRunning ? "initializing" : setupCanUseAgent ? "ready" : "attention"}`;
  dot.className = `setup-status-dot ${isRunning ? "checking" : setupCanUseAgent ? "ready" : "attention"}`;

  if (isRunning) {
    title.textContent = "Preparing analysis data…";
    summary.textContent = status.job.action === "rebuild_all"
      ? "Downloading and building the complete Glasgow 5 m dataset"
      : "Downloading and processing missing social-risk inputs";
    body.appendChild(setupItem(
      "Automatic data preparation",
      "info",
      "This runs in the API background. Cached downloads are reused after interruptions."
    ));
  } else if (setupCanUseAgent) {
    title.textContent = missing.length ? "System ready · optional setup available" : "System ready";
    summary.textContent = `${status.data.checked_files} analysis rasters verified · language model configured`;
    body.appendChild(setupItem(
      "Analysis data",
      "ok",
      `${status.data.profile} passed reproducibility verification.`
    ));
  } else {
    title.textContent = "Setup required before analysis";
    summary.textContent = status.data.status === "complete"
      ? "Configure the language model"
      : "Analysis data is incomplete";
    if (status.data.automatic_action === "configure_lcm") {
      body.appendChild(setupItem(
        "Complete Glasgow data",
        "missing",
        "Set HYDROMIND_LCM2019_PATH to your licensed gb2019lcm25m.tif and HYDROMIND_ACCEPT_DATA_LICENCES=true, then restart the API. Anonymous official sources will be downloaded and processed automatically."
      ));
    }
  }

  configuration.forEach((item) => {
    if (item.configured) return;
    const variables = item.environment_variables.join(", ");
    const detail = `${item.message} Configure: ${variables}.`;
    const state = item.importance === "required" ? "missing" : "info";
    body.appendChild(setupItem(item.label, state, detail));
  });

  if (status.job.state === "failed") {
    body.appendChild(setupItem("Automatic preparation failed", "missing", status.job.error));
  }
  const button = textElement("button", "setup-recheck", isRunning ? "Refresh progress" : "Check again");
  button.type = "button";
  button.addEventListener("click", () => refreshSetup(!isRunning));
  body.appendChild(button);
  if (setupCanUseAgent && !importantMissing.length) root.open = false;
}

function renderSetupConnectionError() {
  setupCanUseAgent = false;
  setAgentControlsDisabled(true);
  document.getElementById("setup-status").className = "setup-status attention";
  document.getElementById("setup-status-dot").className = "setup-status-dot attention";
  document.getElementById("setup-status-title").textContent = "Agent API is offline";
  document.getElementById("setup-status-summary").textContent = "Start the API at 127.0.0.1:8000";
  const body = document.getElementById("setup-status-body");
  body.replaceChildren(setupItem(
    "HydroMind Agent API",
    "missing",
    "Run the HydroMind API, then select Check again."
  ));
  const button = textElement("button", "setup-recheck", "Check again");
  button.type = "button";
  button.addEventListener("click", () => refreshSetup(true));
  body.appendChild(button);
}

async function refreshSetup(initialize = false) {
  if (setupPollTimer) window.clearTimeout(setupPollTimer);
  try {
    const response = await fetch(`${AGENT_API_URL}/setup/${initialize ? "initialize" : "status"}`, {
      method: initialize ? "POST" : "GET"
    });
    if (!response.ok) throw new Error(`Setup check failed with HTTP ${response.status}`);
    const status = await response.json();
    renderSetupStatus(status);
    if (status.job.state === "running") {
      setupPollTimer = window.setTimeout(() => refreshSetup(false), 2500);
    }
  } catch {
    renderSetupConnectionError();
  }
}

async function runAgentTurn(prompt) {
  const response = await fetch(`${AGENT_API_URL}/agent/turn`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt, state: sessionState })
  });
  if (!response.ok) {
    const body = await response.text();
    let detail = body;
    try {
      detail = JSON.parse(body).detail || body;
    } catch {
      // Keep the plain HTTP response body.
    }
    throw new Error(detail || `Agent API request failed with HTTP ${response.status}.`);
  }
  return response.json();
}

async function agentApiReachable() {
  try {
    const response = await fetch(`${AGENT_API_URL}/health`);
    return response.ok;
  } catch {
    return false;
  }
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
      event.layer_ids.forEach((id) =>
        setAnalysisLayer(id, sessionState.visible_analysis_layer_ids.includes(id))
      );
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
  if (!setupCanUseAgent) {
    document.getElementById("setup-status").open = true;
    return;
  }
  setPanelView("conversation");
  addMessage("user", prompt);
  const typing = addMessage("agent", "Interpreting the request and preparing the smallest relevant toolset…", true);
  const input = document.getElementById("agent-input");
  const submit = document.querySelector("#agent-form button");
  input.disabled = true;
  submit.disabled = true;
  try {
    const response = await runAgentTurn(prompt);
    sessionState = response.state;
    keepPublicAnalysisLayers();
    applyMapEvents(response.events);
    renderRiskReport(sessionState.risk_report);
    if (response.pending_assessment || sessionState.pending_assessment) {
      renderAssessmentPlan(response.pending_assessment || sessionState.pending_assessment);
    }
    typing.remove();
    addMessage("agent", response.message);
  } catch (error) {
    typing.remove();
    let message = "The tool-using Agent is unavailable.";
    if (error instanceof TypeError) {
      message = await agentApiReachable()
        ? "The Agent API is online, but this request ended without a response. Check the Agent API log for the cause."
        : "Cannot connect to the Agent API at 127.0.0.1:8000.";
    }
    else if (error instanceof Error) message = error.message;
    addMessage("agent", message);
  } finally {
    setAgentControlsDisabled(!setupCanUseAgent);
    if (setupCanUseAgent) input.focus();
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

document.querySelectorAll("[data-preset]").forEach((button) => {
  button.addEventListener("click", () => {
    const preset = button.dataset.preset;
    setDecisionWeights(PRIORITY_PRESETS[preset], preset);
  });
});

Object.values(weightInputs).forEach((input) => {
  input.addEventListener("input", () => {
    selectedPriorityPreset = "custom";
    document.querySelectorAll("[data-preset]").forEach((button) => button.classList.remove("active"));
    updateWeightDisplay();
  });
});

confirmAssessmentButton.addEventListener("click", async () => {
  try {
    await executeAssessment();
  } catch (error) {
    assessmentMode = sessionState.recent_analysis_run_id ? "rerank" : "plan";
    decisionControls.hidden = false;
    assessmentStatusBadge.textContent = "Action failed";
    assessmentStatusBadge.className = "decision-status failed";
    addMessage("agent", error instanceof Error ? error.message : "Assessment action failed.");
    updateWeightDisplay();
  }
});

refreshSetup(true);
