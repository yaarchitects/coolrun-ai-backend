const API_BASE =
  window.COOLRUN_API_BASE ||
  (window.location.protocol === "file:" || window.location.port === "5500"
    ? "http://127.0.0.1:8001"
    : "");
const VIENNA_CENTER = [48.22057422849521, 16.411494407045154];
const DEMO_START = { lat: 48.220116732630395, lon: 16.41067043243575 };
const DEMO_END = { lat: 48.22103170800606, lon: 16.4123183816545 };
const ROUTE_COLORS = ["#2563eb", "#f59e0b", "#7c3aed", "#64748b"];
const OPTIMIZED_COLOR = "#059669";
const TREE_RENDER_LIMIT = 1200;
const IMAGE_TREE_TIMEOUT_MS = 120000;
const ROUTE_TIMEOUT_MS = 90000;
const UTCI_TIMEOUT_MS = 15 * 60 * 1000;
const ROUTE_STYLES = {
  shortest: { color: "#2563eb", weight: 3, opacity: 0.75, dashArray: "8 8" },
  coolest: { color: OPTIMIZED_COLOR, weight: 8, opacity: 0.95 },
  balanced: { color: "#f59e0b", weight: 5, opacity: 0.85 },
};

const state = {
  start: null,
  end: null,
  startMarker: null,
  endMarker: null,
  imageLayer: null,
  treeLayer: null,
  sdkTreeLayer: null,
  mergedTreeLayer: null,
  routeLayer: null,
  routeLayersByCategory: {},
  coolestLayer: null,
  selectedRouteCategory: null,
  runnerMarker: null,
  animationFrame: null,
  latestPayload: null,
  areaAnalysis: null,
  areaAnalysisIncludesUtci: false,
  analysisToken: null,
};

const hasLeaflet = Boolean(window.L);
let map = null;
let overlays = {};
let layerControl = null;

if (hasLeaflet) {
  map = L.map("map", { zoomControl: true }).setView(VIENNA_CENTER, 16);
  const baseLayer = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 20,
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);

  setTimeout(() => map.invalidateSize(), 150);
  window.addEventListener("resize", () => map.invalidateSize());
  layerControl = L.control.layers({ OpenStreetMap: baseLayer }, overlays, { collapsed: false }).addTo(map);
}

const statusEl = document.getElementById("status");
const resultsEl = document.getElementById("results");
const treeCountEl = document.getElementById("treeCount");
const utciStateEl = document.getElementById("utciState");
const treeSummaryEl = document.getElementById("treeSummary");
const utciSummaryEl = document.getElementById("utciSummary");
const optimizedRouteEl = document.getElementById("optimizedRoute");
const optimizedAnimationEl = document.getElementById("optimizedAnimation");
const workflowStatusEl = document.getElementById("workflowStatus");
const utciDateEl = document.getElementById("utciDate");
const utciHourEl = document.getElementById("utciHour");
const vegetationModeEl = document.getElementById("vegetationMode");
const replayBtn = document.getElementById("replayBtn");
const sdkTreeCountEl = document.getElementById("sdkTreeCount");
const aiTreeCountEl = document.getElementById("aiTreeCount");
const mergedTreeCountEl = document.getElementById("mergedTreeCount");
const duplicateTreeCountEl = document.getElementById("duplicateTreeCount");
const groundMaterialCountEl = document.getElementById("groundMaterialCount");
const selectedTimeEl = document.getElementById("selectedTime");
const stepIds = ["stepSelect", "stepExtract", "stepDetect", "stepUtci", "stepRoute"];

function setStatus(message, type = "") {
  statusEl.textContent = message;
  statusEl.className = `status ${type}`.trim();
}

function setWorkflowStatus(message, type = "") {
  if (!workflowStatusEl) return;
  workflowStatusEl.textContent = message;
  workflowStatusEl.className = `status ${type}`.trim();
}

function setWorkflowStep(stepId, stateName) {
  stepIds.forEach((id) => {
    const element = document.getElementById(id);
    if (!element) return;
    element.classList.remove("active", "error");
    if (id === stepId && stateName) element.classList.add(stateName);
  });
}

function markWorkflowDone(stepId) {
  const element = document.getElementById(stepId);
  if (!element) return;
  element.classList.remove("active", "error");
  element.classList.add("done");
}

function resetWorkflowSteps() {
  stepIds.forEach((id) => {
    const element = document.getElementById(id);
    if (!element) return;
    element.classList.remove("active", "done", "error");
  });
  const selectStep = document.getElementById("stepSelect");
  if (selectStep) selectStep.classList.add("active");
}

function formatNumber(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "n/a";
  return Number(value).toFixed(digits);
}

function formatMeters(value) {
  if (value === null || value === undefined) return "n/a";
  return value >= 1000 ? `${formatNumber(value / 1000, 2)} km` : `${formatNumber(value, 0)} m`;
}

function routeColor(route, index, recommendedName) {
  if (route.category && ROUTE_STYLES[route.category]) return ROUTE_STYLES[route.category].color;
  return route.name === recommendedName ? OPTIMIZED_COLOR : ROUTE_COLORS[index % ROUTE_COLORS.length];
}

function routeSelectionBasis(payload) {
  return payload.utci_available
    ? "Selected by the best route UTCI result with detected tree shading."
    : "UTCI simulation is not available for this run. Recommendation is based on tree density and route distance.";
}

async function fetchWithTimeout(url, options = {}, timeoutMs = 45000) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } catch (error) {
    if (error.name === "AbortError") {
      const minutes = Math.round(timeoutMs / 60000);
      throw new Error(
        `Request timed out after ${minutes || 1} minute(s). The backend may still be processing Infrared or routing.`
      );
    }
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

function categorizedRoutes(payload) {
  if (payload.route_categories) {
    return ["shortest", "coolest", "balanced"]
      .filter((key) => payload.route_categories[key])
      .map((key) => ({ ...payload.route_categories[key], category: key }));
  }
  return (payload.routes || []).map((route, index) => ({
    ...route,
    category: index === 0 ? "shortest" : route.name?.toLowerCase().includes("balanced") ? "balanced" : "coolest",
  }));
}

function simplifyCoordinates(coords, maxPoints = 140) {
  if (!coords || coords.length <= maxPoints) return coords || [];
  const step = Math.ceil(coords.length / maxPoints);
  const simplified = coords.filter((_point, index) => index % step === 0);
  const last = coords[coords.length - 1];
  if (simplified[simplified.length - 1] !== last) simplified.push(last);
  return simplified;
}

function currentAnalysisOptions() {
  if (vegetationModeEl) vegetationModeEl.value = "merged";
  return {
    date: utciDateEl?.value || "2026-07-15",
    hour: Number(utciHourEl?.value || 15),
    vegetation_mode: "merged",
  };
}

function updateSelectedTimeLabel() {
  const options = currentAnalysisOptions();
  if (selectedTimeEl) selectedTimeEl.textContent = `${options.date} ${String(options.hour).padStart(2, "0")}:00`;
}

function renderVegetationSummary(summary = {}) {
  if (sdkTreeCountEl) sdkTreeCountEl.textContent = String(summary.sdk_tree_count ?? "--");
  if (aiTreeCountEl) aiTreeCountEl.textContent = String(summary.detected_tree_count ?? summary.ai_tree_count ?? "--");
  if (mergedTreeCountEl) mergedTreeCountEl.textContent = String(summary.merged_tree_count ?? "--");
  if (duplicateTreeCountEl) duplicateTreeCountEl.textContent = String(summary.duplicate_count ?? "--");
  if (groundMaterialCountEl) groundMaterialCountEl.textContent = String(summary.ground_material_count ?? "--");
}

function withCacheBuster(path) {
  if (!path || !state.analysisToken) return path;
  const separator = path.includes("?") ? "&" : "?";
  return `${path}${separator}v=${state.analysisToken}`;
}

function apiUrl(path) {
  if (!path) return null;
  if (path.startsWith("http://") || path.startsWith("https://")) return path;
  if (path.startsWith("/")) return `${API_BASE}${path}`;
  return path;
}

function setArtifactImage(elementId, paths, missingTextId = null, missingText = "Image not available yet.") {
  const image = document.getElementById(elementId);
  if (!image) return Promise.resolve(false);

  return new Promise((resolve) => {
    let index = 0;
    const tryNext = () => {
      if (index >= paths.length) {
        image.removeAttribute("src");
        image.classList.add("missing");
        if (missingTextId) {
          const note = document.getElementById(missingTextId);
          if (note) note.textContent = missingText;
        }
        resolve(false);
        return;
      }
      image.src = paths[index];
      index += 1;
    };

    image.onerror = tryNext;
    image.onload = () => {
      image.classList.remove("missing");
      resolve(true);
    };
    tryNext();
  });
}

function bboxToPolygon(bbox) {
  if (!bbox) return null;
  const west = Number(bbox.west);
  const south = Number(bbox.south);
  const east = Number(bbox.east);
  const north = Number(bbox.north);
  if (![west, south, east, north].every(Number.isFinite)) return null;
  return {
    type: "Polygon",
    coordinates: [[
      [west, south],
      [east, south],
      [east, north],
      [west, north],
      [west, south],
    ]],
  };
}

function selectedPointsPolygon(bufferDegrees = 0.00045) {
  if (!state.start || !state.end) return null;
  return bboxToPolygon({
    west: Math.min(state.start.lon, state.end.lon) - bufferDegrees,
    south: Math.min(state.start.lat, state.end.lat) - bufferDegrees,
    east: Math.max(state.start.lon, state.end.lon) + bufferDegrees,
    north: Math.max(state.start.lat, state.end.lat) + bufferDegrees,
  });
}

function buildUtciPolygon() {
  const existingPolygon = state.areaAnalysis?.utci_summary?.utci_polygon;
  if (existingPolygon?.type === "Polygon" && existingPolygon.coordinates?.length) {
    return existingPolygon;
  }

  const summaryBounds = state.areaAnalysis?.utci_summary?.utci_bbox_lonlat;
  const summaryPolygon = bboxToPolygon(summaryBounds);
  if (summaryPolygon) return summaryPolygon;

  const metadataPolygon = bboxToPolygon(state.areaAnalysis?.metadata?.bbox_lonlat);
  if (metadataPolygon) return metadataPolygon;

  return selectedPointsPolygon();
}

async function runSelectedAreaAnalysis({ force = false } = {}) {
  if (!force && state.areaAnalysis) {
    return state.areaAnalysis;
  }
  if (!state.start || !state.end) {
    throw new Error("Select start and end points first.");
  }

  setWorkflowStep("stepExtract", "active");
  setWorkflowStatus("Sending selected coordinates to the backend for orthophoto extraction and tree detection...");

  const url = `${API_BASE}/analyze-selected-area`;
  const body = {
    start_lat: state.start.lat,
    start_lon: state.start.lon,
    end_lat: state.end.lat,
    end_lon: state.end.lon,
    ...currentAnalysisOptions(),
    run_utci: false,
  };
  console.log("Calling selected-area endpoint:", url, body);
  const response = await fetchWithTimeout(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }, IMAGE_TREE_TIMEOUT_MS);

  if (!response.ok) {
    const errorPayload = await response.json().catch(() => ({}));
    throw new Error(errorPayload.detail || `${response.status} ${response.statusText}`);
  }

  const payload = await response.json();
  console.log("Selected-area response:", payload);
  state.areaAnalysis = payload;
  state.areaAnalysisIncludesUtci = false;
  state.analysisToken = Date.now();
  return state.areaAnalysis;
}

async function runUtciAreaAnalysis(polygon) {
  if (!polygon) {
    throw new Error("Could not build a UTCI polygon from the selected points.");
  }

  const url = `${API_BASE}/analyze-area`;
  const body = {
    polygon,
    ...currentAnalysisOptions(),
    vegetation_mode: "merged",
  };
  console.log("Calling UTCI endpoint:", url, body);
  const response = await fetchWithTimeout(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }, UTCI_TIMEOUT_MS);

  if (!response.ok) {
    const errorPayload = await response.json().catch(() => ({}));
    throw new Error(errorPayload.detail || `${response.status} ${response.statusText}`);
  }

  const payload = await response.json();
  console.log("UTCI response:", payload);
  state.areaAnalysis = {
    ...(state.areaAnalysis || {}),
    ...payload,
    utci_summary: payload.summary,
    utci_warning: payload.summary?.reason || payload.error_message,
    visualization_paths: {
      ...(state.areaAnalysis?.visualization_paths || {}),
      ...(payload.visualization_paths || {}),
    },
  };
  state.areaAnalysisIncludesUtci = true;
  state.analysisToken = Date.now();
  return state.areaAnalysis;
}

function analysisPath(key) {
  const path = state.areaAnalysis?.visualization_paths?.[key];
  return path ? withCacheBuster(apiUrl(path)) : null;
}

async function loadGeojsonLayer(paths, style, layerKey, layerName) {
  if (!hasLeaflet || !map) return false;
  try {
    const geojson = await fetchFirst(paths.filter(Boolean));
    const features = geojson.features || [];
    const visibleFeatures = features.length > TREE_RENDER_LIMIT ? features.slice(0, TREE_RENDER_LIMIT) : features;
    const displayGeojson = { ...geojson, features: visibleFeatures };
    if (state[layerKey]) {
      if (layerControl) layerControl.removeLayer(state[layerKey]);
      map.removeLayer(state[layerKey]);
    }
    state[layerKey] = L.geoJSON(displayGeojson, {
      renderer: L.canvas({ padding: 0.5 }),
      pointToLayer: (_feature, latlng) =>
        L.circleMarker(latlng, {
          radius: style.radius || 4,
          color: style.color,
          weight: 1,
          fillColor: style.fillColor,
          fillOpacity: style.fillOpacity ?? 0.75,
        }),
      style: () => ({
        color: style.color,
        weight: 1,
        fillColor: style.fillColor,
        fillOpacity: style.fillOpacity ?? 0.25,
      }),
    }).addTo(map);
    if (layerControl) layerControl.addOverlay(state[layerKey], layerName);
    return true;
  } catch (_error) {
    return false;
  }
}

async function extractImage() {
  setWorkflowStep("stepExtract", "active");
  setWorkflowStatus("Extracting Vienna orthophoto for the selected route corridor...");
  try {
    await runSelectedAreaAnalysis();
  } catch (error) {
    setWorkflowStep("stepExtract", "error");
    setWorkflowStatus(`Selected-area analysis failed: ${error.message}`, "error");
    const fallbackLoaded = await setArtifactImage("orthophotoImage", [
      "./outputs/vienna_orthofoto_test.png",
      "../data/vienna_orthofoto_test.png",
    ], "orthophotoNote", "Vienna orthophoto image not available yet.");
    return fallbackLoaded;
  }

  const orthophotoPath = analysisPath("orthophoto");
  const loaded = await setArtifactImage("orthophotoImage", [
    orthophotoPath,
    "./outputs/vienna_orthofoto_test.png",
    "../data/vienna_orthofoto_test.png",
  ].filter(Boolean), "orthophotoNote", "Vienna orthophoto image not available yet.");

  const note = document.getElementById("orthophotoNote");
  if (loaded) {
    const overlayAdded = await addOrthophotoOverlay(
      orthophotoPath || "./outputs/vienna_orthofoto_test.png",
      analysisPath("metadata") || "./outputs/vienna_orthofoto_test_metadata.json"
    );
    if (note) {
      note.textContent = overlayAdded
        ? "Selected coordinates were used to extract the Vienna orthophoto and place it on the map."
        : "Selected coordinates were used to extract the Vienna orthophoto.";
    }
    markWorkflowDone("stepExtract");
    setWorkflowStatus("Orthophoto extracted. Next: detect trees.", "ready");
  } else {
    setWorkflowStep("stepExtract", "error");
    setWorkflowStatus("Orthophoto image is not available. Run notebook 01b or copy the output into frontend/outputs.", "error");
  }
  return loaded;
}

async function detectTreesWorkflow() {
  setWorkflowStep("stepDetect", "active");
  setWorkflowStatus("Detecting trees from the selected orthophoto...");
  try {
    await runSelectedAreaAnalysis();
  } catch (error) {
    setWorkflowStep("stepDetect", "error");
    setWorkflowStatus(`Tree detection failed: ${error.message}`, "error");
    return false;
  }

  await loadTrees({ drawOnMap: true });
  const loaded = await setArtifactImage("detectedTreesImage", [
    analysisPath("input_tree_visualization"),
    analysisPath("tree_detection_result"),
    "../outputs/input_tree_visualization.png",
    "../outputs/tree_detection_result.png",
  ].filter(Boolean), "detectedTreesNote", "Tree detection visualization not available yet.");

  const count = state.areaAnalysis?.tree_count ?? (treeCountEl.textContent === "--" ? 0 : Number(treeCountEl.textContent));
  const note = document.getElementById("detectedTreesNote");
  if (loaded || count > 0) {
    if (note) note.textContent = `Roboflow tree detections were created from the selected orthophoto and filtered to tree class. Current count: ${Number.isFinite(count) ? count : "n/a"}.`;
    markWorkflowDone("stepDetect");
    renderVegetationSummary(state.areaAnalysis?.vegetation_summary || { detected_tree_count: count });
    await loadGeojsonLayer(
      [analysisPath("sdk_vegetation"), `${API_BASE}/outputs/sdk_vegetation.geojson`, "./outputs/sdk_vegetation.geojson"],
      { color: "#2563eb", fillColor: "#60a5fa", radius: 3 },
      "sdkTreeLayer",
      "SDK / OSM trees"
    );
    await loadGeojsonLayer(
      [analysisPath("merged_vegetation"), `${API_BASE}/outputs/merged_vegetation.geojson`, "./outputs/merged_vegetation.geojson"],
      { color: "#059669", fillColor: "#34d399", radius: 3 },
      "mergedTreeLayer",
      "Merged vegetation"
    );
    setWorkflowStatus("Tree detection ready. Next: run UTCI.", "ready");
  } else {
    setWorkflowStep("stepDetect", "error");
    setWorkflowStatus("Detected tree outputs are not available yet.", "error");
  }
  return loaded || count > 0;
}

function utciFailureMessage(source) {
  const debug = source?.infrared_debug || source?.utci_summary?.infrared_debug;
  const summary = source?.utci_summary || source || {};
  if (debug?.infrared_available && debug?.api_key_present) {
    const step = summary.failed_step || source?.failed_step || "unknown_step";
    const message = summary.error_message || source?.error_message || summary.reason || source?.utci_warning;
    return message
      ? `Infrared SDK detected. UTCI run attempted. Failed at ${step}: ${message}`
      : `Infrared SDK detected. UTCI run attempted. Failed at ${step}.`;
  }
  return summary.reason || source?.utci_warning || "UTCI simulation not available for this run.";
}

function utciBackendStatus(payload) {
  if (payload?.utci_available || payload?.summary?.status === "completed") return "success";
  const status = payload?.summary?.status || payload?.status;
  if (status === "timeout") return "timeout";
  if (status === "partial") return "partial";
  return "failed";
}

function setUtciFailureUi(message) {
  const utciNote = document.getElementById("utciImageNote");
  if (utciNote) utciNote.textContent = `UTCI failed: ${message}`;
  if (utciStateEl) utciStateEl.textContent = "Unavailable";
  if (utciSummaryEl) {
    utciSummaryEl.textContent = "Route scoring will use tree density and distance.";
  }
}

async function recoverUtciSummaryAfterFetchFailure() {
  try {
    const summary = await fetchFirst([`${API_BASE}/outputs/utci_summary.json`]);
    state.areaAnalysis = {
      ...(state.areaAnalysis || {}),
      utci_available: Boolean(summary.utci_available),
      utci_summary: summary,
      utci_warning: summary.reason || summary.error_message,
    };
    return summary;
  } catch (_error) {
    return null;
  }
}

async function runUtciWorkflow() {
  setWorkflowStep("stepUtci", "active");
  setWorkflowStatus("Running Infrared UTCI stage for the selected area. Keep this tab open; larger areas can take several minutes...");
  const utciImage = document.getElementById("utciCoolingImage");
  if (utciImage) {
    utciImage.removeAttribute("src");
    utciImage.classList.add("missing");
  }
  const utciNote = document.getElementById("utciImageNote");
  if (utciNote) utciNote.textContent = "Running UTCI for the current selected area...";

  try {
    if (!state.start || !state.end) {
      throw new Error("Select start and end points first.");
    }
    if (!state.areaAnalysis) {
      await runSelectedAreaAnalysis();
    }
    const polygon = buildUtciPolygon();
    await runUtciAreaAnalysis(polygon);
  } catch (error) {
    const recoveredSummary =
      error.message === "Failed to fetch"
        ? await recoverUtciSummaryAfterFetchFailure()
        : null;
    const message = recoveredSummary
      ? utciFailureMessage(recoveredSummary)
      : error.message.includes("timed out")
      ? "UTCI request timed out. The backend may still be processing. Try a smaller area or use route optimization fallback."
      : error.message === "Failed to fetch"
      ? "Could not read the UTCI response from the backend. The backend may have restarted or the Infrared request may have failed during submission."
      : error.message;
    setWorkflowStep("stepUtci", "error");
    setWorkflowStatus(`UTCI stage failed: ${message}`, "error");
    setUtciFailureUi(message);
    return false;
  }

  const backendStatus = utciBackendStatus(state.areaAnalysis);
  await loadUtciSummary({ allowFallback: false });
  const loaded = await loadUtciHeatmapImage(
    "SDK UTCI heatmap loaded for the selected orthophoto area.",
    { allowFallback: false }
  );

  if (backendStatus === "success" && loaded) {
    markWorkflowDone("stepUtci");
    if (utciStateEl) utciStateEl.textContent = "Available";
    setWorkflowStatus("Infrared UTCI result loaded. Refreshing route suggestions with UTCI...", "ready");
    await findRoute();
  } else {
    const warning = utciFailureMessage(state.areaAnalysis);
    setUtciFailureUi(warning);
    if (utciNote && !loaded) {
      utciNote.textContent = `${warning}. No UTCI heatmap was produced for the current selected area.`;
    }
    markWorkflowDone("stepUtci");
    setWorkflowStatus(`${warning}. Route scoring will use tree density and distance.`, "ready");
    await findRoute();
  }
  return loaded;
}

async function fetchFirst(paths) {
  let lastError;
  for (const path of paths) {
    try {
      const response = await fetch(path, { cache: "no-store" });
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      return await response.json();
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError || new Error("No paths to fetch");
}

async function addOrthophotoOverlay(
  imageUrl = "./outputs/vienna_orthofoto_test.png",
  metadataUrl = "./outputs/vienna_orthofoto_test_metadata.json"
) {
  if (!hasLeaflet || !map) return false;

  try {
    const metadata = await fetchFirst([
      metadataUrl,
      "./outputs/vienna_orthofoto_test_metadata.json",
      "../data/vienna_orthofoto_test_metadata.json",
    ]);
    const bbox = metadata.bbox_lonlat;
    if (!bbox) return false;

    if (state.imageLayer) {
      if (layerControl) layerControl.removeLayer(state.imageLayer);
      map.removeLayer(state.imageLayer);
      state.imageLayer = null;
    }

    const bounds = [
      [bbox.south, bbox.west],
      [bbox.north, bbox.east],
    ];
    state.imageLayer = L.imageOverlay(imageUrl, bounds, {
      opacity: 0.9,
      interactive: false,
    }).addTo(map);
    if (layerControl) layerControl.addOverlay(state.imageLayer, "Vienna orthophoto");
    map.fitBounds(bounds, { padding: [30, 30] });
    setTimeout(() => map.invalidateSize(), 50);
    return true;
  } catch (_error) {
    return false;
  }
}

async function loadTrees({ drawOnMap = false } = {}) {
  try {
    const treeGeojson = await fetchFirst([
      `${API_BASE}/outputs/detected_trees.geojson`,
      "./outputs/detected_trees.geojson",
      "../outputs/detected_trees.geojson",
    ]);

    const features = treeGeojson.features || [];
    treeCountEl.textContent = String(features.length);
    treeSummaryEl.textContent = `${features.length} detected tree points loaded from outputs/detected_trees.geojson.`;

    if (drawOnMap && hasLeaflet && map) {
      if (state.treeLayer) {
        if (layerControl) layerControl.removeLayer(state.treeLayer);
        map.removeLayer(state.treeLayer);
        state.treeLayer = null;
      }
      const visibleFeatures = features.length > TREE_RENDER_LIMIT ? features.slice(0, TREE_RENDER_LIMIT) : features;
      state.treeLayer = L.geoJSON({ ...treeGeojson, features: visibleFeatures }, {
        renderer: L.canvas({ padding: 0.5 }),
        pointToLayer: (_feature, latlng) =>
          L.circleMarker(latlng, {
            radius: 4,
            color: "#0b6b2c",
            weight: 1,
            fillColor: "#16b84e",
            fillOpacity: 0.8,
          }),
        onEachFeature: (feature, layer) => {
          const props = feature.properties || {};
          layer.bindPopup(
            `<strong>Detected tree</strong><br>Confidence: ${formatNumber(props.confidence, 2)}<br>Canopy radius: ${formatNumber(props.canopy_radius_m, 1)} m`
          );
        },
      }).addTo(map);
      overlays["Detected trees"] = state.treeLayer;
      if (layerControl) layerControl.addOverlay(state.treeLayer, "Detected trees");

      if (features.length) {
        const treeBounds = state.treeLayer.getBounds();
        if (treeBounds.isValid()) {
          map.fitBounds(treeBounds.pad(0.25));
        }
      }
    }
  } catch (_error) {
    treeCountEl.textContent = "--";
    treeSummaryEl.textContent = "Detected tree GeoJSON not available yet. Run notebooks 01b-03 or start the backend.";
  }
}

function currentAnalysisUtciImagePaths() {
  return [
    analysisPath("utci_with_merged_trees_heatmap"),
    analysisPath("utci_cooling_effect_tree_overlay"),
    analysisPath("utci_cooling_effect"),
  ].filter(Boolean);
}

function fallbackUtciImagePaths() {
  return [
    `${API_BASE}/outputs/utci_with_merged_trees_heatmap.png`,
    `${API_BASE}/outputs/utci_cooling_effect_tree_overlay.png`,
    `${API_BASE}/outputs/utci_cooling_effect.png`,
    "./outputs/utci_with_merged_trees_heatmap.png",
    "../outputs/utci_with_merged_trees_heatmap.png",
    "../outputs/utci_cooling_effect_tree_overlay.png",
    "../outputs/utci_cooling_effect.png",
  ];
}

async function loadUtciHeatmapImage(
  noteText = "SDK UTCI heatmap loaded from outputs.",
  { allowFallback = true } = {}
) {
  const paths = currentAnalysisUtciImagePaths();
  if (allowFallback) paths.push(...fallbackUtciImagePaths());

  const loaded = await setArtifactImage(
    "utciCoolingImage",
    paths.filter(Boolean),
    "utciImageNote",
    allowFallback
      ? "UTCI simulation image not available yet."
      : "No UTCI heatmap was produced for the current selected area."
  );
  if (loaded) {
    const note = document.getElementById("utciImageNote");
    if (note) note.textContent = noteText;
  }
  return loaded;
}

async function loadUtciSummary({ allowFallback = true } = {}) {
  try {
    const summary = await fetchFirst([
      `${API_BASE}/outputs/utci_summary.json`,
      "./outputs/utci_summary.json",
      "../outputs/utci_summary.json",
    ]);

    if (summary.is_placeholder || summary.status !== "completed") {
      const heatmapLoaded = await loadUtciHeatmapImage(
        "Existing SDK UTCI heatmap loaded from outputs.",
        { allowFallback }
      );
      utciStateEl.textContent = heatmapLoaded ? "Grid found" : "Unavailable";
      utciSummaryEl.textContent = heatmapLoaded
        ? "A finite SDK UTCI grid image is available. Route scoring will use it if the grid overlaps the route."
        : `${utciFailureMessage(summary)} Route scoring will use tree density and distance.`;
      return;
    }

    const maxDiff = summary.max_abs_utci_difference ?? summary.max_cooling_effect;
    if (summary.vegetation_feature_count_with > 0 && maxDiff === 0) {
      utciStateEl.textContent = "No effect";
      utciSummaryEl.textContent =
        "UTCI grids exist, but with-tree and without-tree results are identical. Route scoring will not invent cooling values.";
      return;
    }

    utciStateEl.textContent = "Available";
    utciSummaryEl.textContent = `Mean UTCI with trees: ${formatNumber(summary.mean_utci_with, 2)} C. Mean cooling effect: ${formatNumber(summary.mean_cooling_effect, 2)} C.`;
    await loadUtciHeatmapImage("SDK UTCI heatmap loaded from outputs.");
  } catch (_error) {
    const heatmapLoaded = await loadUtciHeatmapImage(
      "Existing SDK UTCI heatmap loaded from outputs.",
      { allowFallback }
    );
    utciStateEl.textContent = heatmapLoaded ? "Grid found" : "Unavailable";
    utciSummaryEl.textContent = heatmapLoaded
      ? "A SDK UTCI heatmap is available. Route scoring will use UTCI only when the backend finds finite grid values along the route."
      : "UTCI summary is unavailable. Route scoring will use tree density and distance.";
  }
}

function clearRoutes() {
  if (state.animationFrame) {
    cancelAnimationFrame(state.animationFrame);
    state.animationFrame = null;
  }
  if (state.runnerMarker && map) {
    map.removeLayer(state.runnerMarker);
    state.runnerMarker = null;
  }
  if (state.routeLayer && map) {
    map.removeLayer(state.routeLayer);
    state.routeLayer = null;
  }
  state.routeLayersByCategory = {};
  state.coolestLayer = null;
  state.selectedRouteCategory = null;
}

function clearPoints() {
  state.start = null;
  state.end = null;
  state.areaAnalysis = null;
  state.areaAnalysisIncludesUtci = false;
  state.analysisToken = null;
  if (state.startMarker && map) map.removeLayer(state.startMarker);
  if (state.endMarker && map) map.removeLayer(state.endMarker);
  state.startMarker = null;
  state.endMarker = null;
  clearRoutes();
  if (state.imageLayer && map) {
    if (layerControl) layerControl.removeLayer(state.imageLayer);
    map.removeLayer(state.imageLayer);
    state.imageLayer = null;
  }
  if (state.treeLayer && map) {
    if (layerControl) layerControl.removeLayer(state.treeLayer);
    map.removeLayer(state.treeLayer);
    state.treeLayer = null;
  }
  if (state.sdkTreeLayer && map) {
    if (layerControl) layerControl.removeLayer(state.sdkTreeLayer);
    map.removeLayer(state.sdkTreeLayer);
    state.sdkTreeLayer = null;
  }
  if (state.mergedTreeLayer && map) {
    if (layerControl) layerControl.removeLayer(state.mergedTreeLayer);
    map.removeLayer(state.mergedTreeLayer);
    state.mergedTreeLayer = null;
  }
  renderVegetationSummary();
  resultsEl.className = "results-empty";
  resultsEl.textContent = "Select two points and run the optimizer.";
  optimizedRouteEl.className = "results-empty";
  optimizedRouteEl.textContent = "Run the optimizer to select the final CoolRun route.";
  optimizedAnimationEl.className = "route-animation-empty";
  optimizedAnimationEl.textContent = "Optimized route animation appears after scoring.";
  resetWorkflowSteps();
  setWorkflowStatus("Start by selecting a start and end point on the map.");
  setStatus("Select start point");
}

function setPoint(latlng) {
  if (!hasLeaflet || !map) {
    setStatus("Interactive map is unavailable. Use Run Vienna Demo for the static route result.", "error");
    return;
  }

  if (!state.start) {
    state.start = { lat: latlng.lat, lon: latlng.lng };
    state.startMarker = L.marker(latlng, { title: "Start" }).addTo(map).bindPopup("Start point").openPopup();
    setWorkflowStep("stepSelect", "active");
    setWorkflowStatus("Start point selected. Select the end point.");
    setStatus("Select end point");
    return;
  }

  if (!state.end) {
    state.end = { lat: latlng.lat, lon: latlng.lng };
    state.endMarker = L.marker(latlng, { title: "End" }).addTo(map).bindPopup("End point").openPopup();
    markWorkflowDone("stepSelect");
    setWorkflowStatus("Start and end selected. Extract the Vienna orthophoto or run the full demo.", "ready");
    setStatus("Ready to find coolest route", "ready");
    return;
  }

  clearPoints();
  setPoint(latlng);
}

function setDemoPoints() {
  if (!hasLeaflet || !map) {
    state.start = { ...DEMO_START };
    state.end = { ...DEMO_END };
    setStatus("Demo points selected. Running static route optimization...");
    return;
  }

  clearPoints();
  setPoint(L.latLng(DEMO_START.lat, DEMO_START.lon));
  setPoint(L.latLng(DEMO_END.lat, DEMO_END.lon));
  map.fitBounds([
    [DEMO_START.lat, DEMO_START.lon],
    [DEMO_END.lat, DEMO_END.lon],
  ], { padding: [80, 80] });
}

function routePopup(route, recommendedName) {
  const isRecommended = route.name === recommendedName;
  return `
    <strong>${route.name}${isRecommended ? " (recommended)" : ""}</strong><br>
    Distance: ${formatMeters(route.distance_m)}<br>
    Nearby trees: ${route.tree_count_near_route ?? 0}<br>
    Tree density: ${formatNumber(route.tree_density_per_km, 1)} / km<br>
    Average UTCI: ${route.average_utci === null || route.average_utci === undefined ? "n/a" : `${formatNumber(route.average_utci, 2)} C`}<br>
    CoolRun score: ${formatNumber(route.coolrun_score, 1)}
  `;
}

function routeStyle(category) {
  return ROUTE_STYLES[category] || { color: "#64748b", weight: 4, opacity: 0.75 };
}

function highlightRouteCategory(category, { openPopup = false, animate = false } = {}) {
  if (!hasLeaflet || !map || !state.routeLayersByCategory) return;

  Object.entries(state.routeLayersByCategory).forEach(([key, layer]) => {
    const baseStyle = routeStyle(key);
    const isSelected = key === category;
    layer.setStyle({
      ...baseStyle,
      weight: isSelected ? Math.max(baseStyle.weight + 2, 7) : baseStyle.weight,
      opacity: isSelected ? 1 : Math.min(baseStyle.opacity, 0.45),
    });
    if (isSelected) {
      layer.bringToFront();
      if (openPopup) layer.openPopup();
    }
  });

  state.selectedRouteCategory = category;
  document.querySelectorAll(".route-card").forEach((card) => {
    card.classList.toggle("selected", card.dataset.routeCategory === category);
  });

  if (animate && category === "coolest") {
    animateCoolestRoute();
  }
}

function drawRoutes(payload) {
  clearRoutes();
  if (!hasLeaflet || !map) {
    return;
  }

  const routes = categorizedRoutes(payload);

  state.routeLayer = L.featureGroup().addTo(map);
  state.routeLayersByCategory = {};

  routes.forEach((route) => {
    const coords = simplifyCoordinates(route.geometry?.coordinates || []);
    const latLngs = coords.map(([lon, lat]) => [lat, lon]);
    const style = routeStyle(route.category);
    const layer = L.polyline(latLngs, style)
      .bindPopup(routePopup(route, payload.recommended_route_name))
      .addTo(state.routeLayer);
    layer.on("click", () => highlightRouteCategory(route.category, { openPopup: false }));
    state.routeLayersByCategory[route.category] = layer;
    if (route.category === "coolest") {
      state.coolestLayer = layer;
    }
  });

  if (routes.length && state.routeLayer.getLayers().length) {
    const bounds = state.routeLayer.getBounds();
    if (bounds.isValid()) {
      map.fitBounds(bounds.pad(0.2));
      setTimeout(() => map.invalidateSize(), 50);
    }
  }
  highlightRouteCategory(payload.recommended || "coolest", { animate: true });
}

function renderResults(payload, sourceLabel = "backend") {
  const warnings = payload.warnings || [];
  const routes = categorizedRoutes(payload);

  if (!routes.length) {
    resultsEl.className = "results-empty";
    resultsEl.textContent = "No routes returned.";
    return;
  }

  const warningHtml = warnings.length
    ? `<p class="warning">${warnings.map((warning) => String(warning)).join("<br>")}</p>`
    : "";

  resultsEl.className = "";
  resultsEl.innerHTML = `
    <p><strong>Recommended:</strong> ${payload.recommended_route_name || "n/a"} <span class="source">(${sourceLabel})</span></p>
    <p class="selection-basis">${routeSelectionBasis(payload)}</p>
    ${payload.reasoning ? `<p class="selection-basis">${payload.reasoning}</p>` : ""}
    ${warningHtml}
    <div class="route-list">
      ${routes
        .map((route, index) => {
          const color = routeColor(route, index, payload.recommended_route_name);
          const isRecommended = route.category === (payload.recommended || "coolest");
          return `
            <article class="route-card ${isRecommended ? "recommended selected" : ""}" data-route-category="${route.category}" style="--route-color: ${color}">
              <h3><span class="route-swatch"></span>${route.name}</h3>
              <div class="metric"><span>Distance</span><strong>${formatMeters(route.distance_m)}</strong></div>
              <div class="metric"><span>Nearby trees</span><strong>${route.tree_count_near_route ?? 0}</strong></div>
              <div class="metric"><span>Tree density</span><strong>${formatNumber(route.tree_density_per_km, 1)} / km</strong></div>
              <div class="metric"><span>Average UTCI</span><strong>${route.average_utci == null ? "n/a" : `${formatNumber(route.average_utci, 2)} C`}</strong></div>
              <div class="metric"><span>CoolRun score</span><strong>${formatNumber(route.coolrun_score, 1)}</strong></div>
              <p>${route.explanation || ""}</p>
              <button type="button" class="route-focus-btn" data-route-category="${route.category}">Show on map</button>
            </article>
          `;
        })
        .join("")}
    </div>
  `;
  resultsEl.querySelectorAll(".route-focus-btn").forEach((button) => {
    button.addEventListener("click", () => {
      highlightRouteCategory(button.dataset.routeCategory, {
        openPopup: true,
        animate: button.dataset.routeCategory === "coolest",
      });
    });
  });
  renderOptimizedRoute(payload, sourceLabel);
}

function renderRouteAnimation(route, color) {
  const coords = route.geometry?.coordinates || [];
  if (!coords.length || !optimizedAnimationEl) {
    return;
  }

  const width = 560;
  const height = 250;
  const padding = 28;
  const lons = coords.map(([lon]) => lon);
  const lats = coords.map(([, lat]) => lat);
  const minLon = Math.min(...lons);
  const maxLon = Math.max(...lons);
  const minLat = Math.min(...lats);
  const maxLat = Math.max(...lats);
  const lonSpan = maxLon - minLon || 1;
  const latSpan = maxLat - minLat || 1;

  const points = coords.map(([lon, lat]) => {
    const x = padding + ((lon - minLon) / lonSpan) * (width - padding * 2);
    const y = height - padding - ((lat - minLat) / latSpan) * (height - padding * 2);
    return [x, y];
  });
  const pathD = points.map(([x, y], index) => `${index === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");

  optimizedAnimationEl.className = "route-animation";
  optimizedAnimationEl.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Animated optimized route">
      <path class="route-shadow" d="${pathD}" />
      <path id="optimizedPath" class="route-path" d="${pathD}" style="stroke: ${color}" />
      <circle class="route-start" cx="${points[0][0]}" cy="${points[0][1]}" r="5" />
      <circle class="route-end" cx="${points[points.length - 1][0]}" cy="${points[points.length - 1][1]}" r="5" />
      <circle class="route-runner" r="6" fill="${color}">
        <animateMotion dur="4s" repeatCount="indefinite" rotate="auto">
          <mpath href="#optimizedPath"></mpath>
        </animateMotion>
      </circle>
    </svg>
  `;
}

function renderOptimizedRoute(payload, sourceLabel = "backend") {
  const routes = categorizedRoutes(payload);
  const recommended = payload.route_categories?.coolest || routes.find((route) => route.category === "coolest");
  if (!recommended) {
    optimizedRouteEl.className = "results-empty";
    optimizedRouteEl.textContent = "No optimized route available yet.";
    optimizedAnimationEl.className = "route-animation-empty";
    optimizedAnimationEl.textContent = "Optimized route animation appears after scoring.";
    return;
  }

  const recommendedIndex = routes.findIndex((route) => route.category === "coolest");
  const color = routeColor(recommended, recommendedIndex, payload.recommended_route_name);
  optimizedRouteEl.className = "";
  optimizedRouteEl.innerHTML = `
    <div class="optimized-card" style="--route-color: ${color}">
      <div>
        <div class="score-badge">${formatNumber(recommended.coolrun_score, 0)}</div>
        <p><strong><span class="route-swatch"></span>${recommended.name}</strong><br><span class="source">${sourceLabel}</span></p>
      </div>
      <div>
        <div class="metric"><span>Distance</span><strong>${formatMeters(recommended.distance_m)}</strong></div>
        <div class="metric"><span>Nearby trees</span><strong>${recommended.tree_count_near_route ?? 0}</strong></div>
        <div class="metric"><span>Tree density</span><strong>${formatNumber(recommended.tree_density_per_km, 1)} / km</strong></div>
        <div class="metric"><span>Average UTCI</span><strong>${recommended.average_utci == null ? "n/a" : `${formatNumber(recommended.average_utci, 2)} C`}</strong></div>
        <p>This route is recommended because it has the best combination of tree coverage, heat-stress reduction, and route comfort.</p>
        ${payload.utci_available ? "" : "<p>UTCI simulation is not available for this run. Recommendation is based on tree density and route distance.</p>"}
      </div>
    </div>
  `;
  renderRouteAnimation(recommended, color);
}

async function loadRouteFallback() {
  const payload = await fetchFirst([
    `${API_BASE}/outputs/route_demo.json`,
    "./outputs/route_demo.json",
    "../outputs/route_demo.json",
  ]);
  drawRoutes(payload);
  renderResults(payload, "static fallback");
  setStatus("Result ready from static fallback", "ready");
  return payload;
}

async function findRoute() {
  if (!state.start || !state.end) {
    setStatus("Select start and end points first", "error");
    setWorkflowStatus("Select start and end points before route optimization.", "error");
    return;
  }

  setWorkflowStep("stepRoute", "active");
  setWorkflowStatus("Finding routes...");
  setStatus("Finding routes...");
  setTimeout(() => {
    if (statusEl.textContent === "Finding routes...") {
      setStatus("This may take a moment because routing is being calculated.");
      setWorkflowStatus("Calculating CoolRun score...");
    }
  }, 3500);

  try {
    const response = await fetchWithTimeout(`${API_BASE}/analyze-route`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        start_lat: state.start.lat,
        start_lon: state.start.lon,
        end_lat: state.end.lat,
        end_lon: state.end.lon,
        ...currentAnalysisOptions(),
      }),
    }, ROUTE_TIMEOUT_MS);

    if (!response.ok) {
      const errorPayload = await response.json().catch(() => ({}));
      throw new Error(errorPayload.detail || `${response.status} ${response.statusText}`);
    }

    const payload = await response.json();
    state.latestPayload = payload;
    renderVegetationSummary(payload.vegetation_summary || payload.summary || {});
    drawRoutes(payload);
    renderResults(payload, "backend");
    markWorkflowDone("stepRoute");
    setWorkflowStatus("Recommendation ready", "ready");
    setStatus("Recommendation ready", "ready");
  } catch (error) {
    try {
      const fallbackPayload = await loadRouteFallback();
      markWorkflowDone("stepRoute");
      state.latestPayload = fallbackPayload;
      setWorkflowStatus("Backend unavailable. Static demo route result loaded.", "ready");
    } catch (_fallbackError) {
      setWorkflowStep("stepRoute", "error");
      setWorkflowStatus("Route optimization failed and no static route fallback was found.", "error");
      setStatus(
        `Backend not running or route optimization failed. Start FastAPI with: python -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload. ${error.message}`,
        "error"
      );
    }
  }
}

async function runViennaDemoWorkflow() {
  setDemoPoints();
  markWorkflowDone("stepSelect");
  await runSelectedPointsWorkflow();
}

async function runSelectedPointsWorkflow() {
  if (!state.start || !state.end) {
    setStatus("Select start and end points first", "error");
    setWorkflowStatus("Select start and end points before running the CoolRun workflow.", "error");
    return;
  }

  setWorkflowStatus("Running CoolRun workflow in order: image, trees, then route optimization. Use Run UTCI when you want a fresh Infrared simulation.");
  const imageReady = await extractImage();
  if (!imageReady) return;

  const treesReady = await detectTreesWorkflow();
  if (!treesReady) return;

  setWorkflowStep("stepUtci", "active");
  await loadUtciSummary();
  markWorkflowDone("stepUtci");
  await findRoute();
}

function animateCoolestRoute() {
  if (!hasLeaflet || !map || !state.coolestLayer) return;
  const latLngs = state.coolestLayer.getLatLngs();
  if (!latLngs.length) return;

  if (state.runnerMarker) map.removeLayer(state.runnerMarker);
  if (state.animationFrame) cancelAnimationFrame(state.animationFrame);

  const icon = L.divIcon({
    className: "runner-marker",
    html: '<span></span>',
    iconSize: [18, 18],
    iconAnchor: [9, 9],
  });
  state.runnerMarker = L.marker(latLngs[0], { icon }).addTo(map);
  state.coolestLayer.setStyle({ weight: 10, opacity: 1 });

  const started = performance.now();
  const duration = 5000;
  const tick = (now) => {
    const progress = Math.min((now - started) / duration, 1);
    const index = Math.min(Math.floor(progress * (latLngs.length - 1)), latLngs.length - 1);
    state.runnerMarker.setLatLng(latLngs[index]);
    const pulse = 8 + Math.sin(progress * Math.PI * 8) * 2;
    state.coolestLayer.setStyle({ weight: pulse, opacity: 0.95 });
    if (progress < 1) {
      state.animationFrame = requestAnimationFrame(tick);
    } else {
      state.coolestLayer.setStyle(routeStyle("coolest"));
      state.animationFrame = null;
    }
  };
  state.animationFrame = requestAnimationFrame(tick);
}

if (hasLeaflet && map) {
  map.on("click", (event) => setPoint(event.latlng));
} else {
  document.getElementById("map").innerHTML =
    '<div class="map-fallback">Interactive map could not load. Use Run Vienna Demo to load the imagery, tree detection, UTCI stage, and static route result.</div>';
}
document.getElementById("clearBtn").addEventListener("click", clearPoints);
document.getElementById("routeBtn").addEventListener("click", runSelectedPointsWorkflow);
document.getElementById("demoBtn").addEventListener("click", runViennaDemoWorkflow);
replayBtn?.addEventListener("click", animateCoolestRoute);
document.getElementById("extractBtn").addEventListener("click", extractImage);
document.getElementById("detectBtn").addEventListener("click", detectTreesWorkflow);
document.getElementById("utciBtn").addEventListener("click", runUtciWorkflow);
utciDateEl?.addEventListener("change", () => {
  state.areaAnalysis = null;
  updateSelectedTimeLabel();
});
utciHourEl?.addEventListener("change", () => {
  state.areaAnalysis = null;
  updateSelectedTimeLabel();
});
vegetationModeEl?.addEventListener("change", () => {
  state.areaAnalysis = null;
});

setTimeout(() => {
  if (treeSummaryEl) treeSummaryEl.textContent = "Loading vegetation...";
  loadTrees();
}, 250);
loadUtciSummary();
updateSelectedTimeLabel();
setStatus(hasLeaflet ? "Select start point" : "Interactive map unavailable. Use Run Vienna Demo.");
setWorkflowStatus("Start by selecting a start and end point on the map.");
