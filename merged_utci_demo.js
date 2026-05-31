const LOCAL_API_BASE = "http://127.0.0.1:8001";
const API_BASE = window.COOLRUN_API_BASE || LOCAL_API_BASE;
const PUBLIC_BACKEND_REQUIRED =
  window.location.hostname.endsWith("github.io") && API_BASE === LOCAL_API_BASE;
const VIENNA_CENTER = [48.22057422849521, 16.411494407045154];
const UTCI_TIMEOUT_MS = 15 * 60 * 1000;
const state = {
  start: null,
  end: null,
  startMarker: null,
  endMarker: null,
  imageLayer: null,
  routeLayers: [],
  routeLayersByCategory: {},
  latestRoutePayload: null,
  runnerMarker: null,
  areaAnalysis: null,
  imageLayer: null,
  sdkLayer: null,
  aiLayer: null,
  mergedLayer: null,
};

const mapElement = document.getElementById("map");
const hasLeaflet = Boolean(window.L);
let map = null;

if (hasLeaflet && mapElement) {
  map = L.map("map", { zoomControl: true }).setView(VIENNA_CENTER, 16);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 20,
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);
  setTimeout(() => map.invalidateSize(), 150);
  window.addEventListener("resize", () => map.invalidateSize());
} else if (mapElement) {
  mapElement.className = "map-fallback";
  mapElement.innerHTML = "<p>Leaflet map could not be loaded. Check the internet connection for the Leaflet CDN and open this page through http://localhost:5500.</p>";
}

const statusText = document.getElementById("mergedStatusText");
const statusBadge = document.getElementById("mergedStatus");
const utciBadge = document.getElementById("mergedUtciState");

function setStatus(message, type = "") {
  statusText.textContent = message;
  statusText.className = `status ${type}`.trim();
  statusBadge.textContent = type === "error" ? "Error" : type === "ready" ? "Done" : "Running";
}

if (PUBLIC_BACKEND_REQUIRED) {
  setStatus(
    "Public backend is not configured. GitHub Pages is serving the frontend, but API_BASE still points to localhost.",
    "error"
  );
}

async function fetchWithTimeout(url, options = {}, timeoutMs = 45000) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timeout);
  }
}

function setImage(id, path, fallbackTextId, fallbackText) {
  const img = document.getElementById(id);
  if (!path) {
    img.removeAttribute("src");
    img.classList.add("missing");
    document.getElementById(fallbackTextId).textContent = fallbackText;
    return;
  }
  img.onload = () => img.classList.remove("missing");
  img.onerror = () => {
    img.classList.add("missing");
    document.getElementById(fallbackTextId).textContent = fallbackText;
  };
  img.src = `${API_BASE}${path}?v=${Date.now()}`;
}

async function fetchJson(path) {
  const response = await fetch(`${API_BASE}${path}?v=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

async function addOrthophotoOverlay() {
  if (!map) return;
  try {
    const metadata = await fetchJson("/outputs/vienna_orthofoto_test_metadata.json");
    const bbox = metadata.bbox_lonlat;
    if (!bbox) return;
    const bounds = [[bbox.south, bbox.west], [bbox.north, bbox.east]];
    if (state.imageLayer) map.removeLayer(state.imageLayer);
    state.imageLayer = L.imageOverlay(`${API_BASE}/outputs/vienna_orthofoto_test.png?v=${Date.now()}`, bounds, {
      opacity: 0.9,
      interactive: false,
    }).addTo(map);
    map.fitBounds(bounds, { padding: [30, 30] });
  } catch (error) {
    console.warn("Orthophoto overlay failed", error);
  }
}

async function addGeojsonLayer(path, stateKey, color, radius) {
  if (!map) return;
  try {
    const geojson = await fetchJson(path);
    if (state[stateKey]) map.removeLayer(state[stateKey]);
    state[stateKey] = L.geoJSON(geojson, {
      renderer: L.canvas({ padding: 0.5 }),
      pointToLayer: (_feature, latlng) =>
        L.circleMarker(latlng, {
          radius,
          color,
          weight: 1,
          fillColor: color,
          fillOpacity: 0.75,
        }),
      style: () => ({
        color,
        weight: 1,
        fillColor: color,
        fillOpacity: 0.25,
      }),
    }).addTo(map);
  } catch (error) {
    console.warn(`GeoJSON layer failed for ${path}`, error);
  }
}

function bboxToPolygon(bbox) {
  return {
    type: "Polygon",
    coordinates: [[
      [bbox.west, bbox.south],
      [bbox.east, bbox.south],
      [bbox.east, bbox.north],
      [bbox.west, bbox.north],
      [bbox.west, bbox.south],
    ]],
  };
}

function selectedFallbackPolygon(buffer = 0.00045) {
  return bboxToPolygon({
    west: Math.min(state.start.lon, state.end.lon) - buffer,
    south: Math.min(state.start.lat, state.end.lat) - buffer,
    east: Math.max(state.start.lon, state.end.lon) + buffer,
    north: Math.max(state.start.lat, state.end.lat) + buffer,
  });
}

function buildUtciPolygon() {
  return (
    state.areaAnalysis?.utci_summary?.utci_polygon ||
    (state.areaAnalysis?.utci_summary?.utci_bbox_lonlat && bboxToPolygon(state.areaAnalysis.utci_summary.utci_bbox_lonlat)) ||
    selectedFallbackPolygon()
  );
}

async function postJson(path, body, timeoutMs = 120000) {
  const url = `${API_BASE}${path}`;
  console.log("Calling merged demo endpoint:", url, body);
  const response = await fetchWithTimeout(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }, timeoutMs);
  if (!response.ok) {
    const errorPayload = await response.json().catch(() => ({}));
    throw new Error(errorPayload.detail || `${response.status} ${response.statusText}`);
  }
  const payload = await response.json();
  console.log("Merged demo response:", payload);
  return payload;
}

async function runSelectedArea() {
  setStatus("Extracting Vienna orthophoto and detecting AI trees...");
  const payload = await postJson("/analyze-selected-area", {
    start_lat: state.start.lat,
    start_lon: state.start.lon,
    end_lat: state.end.lat,
    end_lon: state.end.lon,
    date: "2025-07-15",
    hour: 15,
    vegetation_mode: "merged",
    run_utci: false,
  });
  state.areaAnalysis = payload;
  setImage("mergedOrthophoto", payload.visualization_paths?.orthophoto, "mergedOrthophotoNote", "Orthophoto unavailable.");
  setImage("mergedTreesImage", payload.visualization_paths?.input_tree_visualization, "mergedTreesNote", "Tree visualization unavailable.");
  await addOrthophotoOverlay();
  await addGeojsonLayer("/outputs/detected_trees.geojson", "aiLayer", "#16a34a", 4);
  document.getElementById("mergedOrthophotoNote").textContent = "Selected points were used to extract the Vienna orthophoto.";
  document.getElementById("mergedTreesNote").textContent = `AI detected trees: ${payload.tree_count ?? "n/a"}. Merged vegetation will be fetched from Infrared SDK.`;
}

async function runMergedUtci() {
  setStatus("Running Infrared UTCI with merged SDK/OSM + AI detected trees...");
  const payload = await postJson("/analyze-area", {
    polygon: buildUtciPolygon(),
    date: "2025-07-15",
    hour: 15,
    vegetation_mode: "merged",
  }, UTCI_TIMEOUT_MS);
  state.areaAnalysis = {
    ...state.areaAnalysis,
    ...payload,
    utci_summary: payload.summary,
    visualization_paths: {
      ...(state.areaAnalysis?.visualization_paths || {}),
      ...(payload.visualization_paths || {}),
    },
  };
  const summary = payload.summary || {};
  document.getElementById("mergedSdkTrees").textContent = summary.sdk_tree_count ?? "--";
  document.getElementById("mergedAiTrees").textContent = summary.detected_tree_count ?? "--";
  document.getElementById("mergedTrees").textContent = summary.merged_tree_count ?? "--";
  document.getElementById("mergedDuplicates").textContent = summary.duplicate_count ?? "--";
  document.getElementById("mergedGround").textContent = summary.ground_material_count ?? "--";
  document.getElementById("mergedMeanUtci").textContent =
    summary.mean_utci_with_merged == null ? "--" : `${Number(summary.mean_utci_with_merged).toFixed(2)} C`;
  utciBadge.textContent = payload.utci_available ? "Available" : "Unavailable";
  setImage("mergedUtciImage", payload.visualization_paths?.utci_with_merged_trees_heatmap, "mergedUtciNote", "No merged UTCI heatmap returned.");
  document.getElementById("mergedUtciNote").textContent = payload.utci_available
    ? "UTCI heatmap generated from merged SDK/OSM + AI vegetation."
    : `UTCI failed: ${summary.failed_step || "unknown"} ${summary.error_message || summary.reason || ""}`;
  await addGeojsonLayer("/outputs/sdk_vegetation.geojson", "sdkLayer", "#2563eb", 3);
  await addGeojsonLayer("/outputs/merged_vegetation.geojson", "mergedLayer", "#059669", 3);
}

function clearRoutes() {
  if (!map) return;
  state.routeLayers.forEach((layer) => map.removeLayer(layer));
  state.routeLayers = [];
  state.routeLayersByCategory = {};
  if (state.runnerMarker) map.removeLayer(state.runnerMarker);
  state.runnerMarker = null;
}

function focusRoute(category) {
  if (!map) return;
  const layer = state.routeLayersByCategory[category];
  if (!layer) return;
  const bounds = layer.getBounds();
  if (bounds.isValid()) {
    map.fitBounds(bounds, { padding: [40, 40] });
  }
  layer.openPopup();
  if (category === "coolest") {
    const route = state.latestRoutePayload?.route_categories?.coolest;
    if (route) {
      const latlngs = route.geometry.coordinates.map(([lon, lat]) => [lat, lon]);
      animateRunner(latlngs);
      renderOptimizedAnimation(route);
    }
  }
}

window.focusMergedRoute = focusRoute;

function drawRoutes(payload) {
  if (!map) return;
  clearRoutes();
  const colors = { shortest: "#2563eb", coolest: "#059669", balanced: "#f59e0b" };
  const routes = payload.route_categories || {};
  Object.entries(routes).forEach(([key, route]) => {
    const latlngs = route.geometry.coordinates.map(([lon, lat]) => [lat, lon]);
    const layer = L.polyline(latlngs, {
      color: colors[key] || "#64748b",
      weight: key === "coolest" ? 8 : key === "balanced" ? 5 : 3,
      opacity: 0.9,
      dashArray: key === "shortest" ? "8 8" : null,
    }).addTo(map);
    layer.bindPopup(`${route.name}<br>${Math.round(route.distance_m)} m<br>UTCI: ${route.average_utci ?? "n/a"}`);
    state.routeLayers.push(layer);
    state.routeLayersByCategory[key] = layer;
  });
  const coolest = routes.coolest;
  if (coolest) {
    const latlngs = coolest.geometry.coordinates.map(([lon, lat]) => [lat, lon]);
    map.fitBounds(L.latLngBounds(latlngs), { padding: [30, 30] });
    animateRunner(latlngs);
    renderOptimizedRoute(coolest);
    renderOptimizedAnimation(coolest);
  }
}

function animateRunner(latlngs) {
  if (!map) return;
  let start = null;
  const icon = L.divIcon({ className: "runner-marker", html: "<span></span>", iconSize: [18, 18] });
  state.runnerMarker = L.marker(latlngs[0], { icon }).addTo(map);
  function step(timestamp) {
    if (!start) start = timestamp;
    const progress = Math.min((timestamp - start) / 5000, 1);
    const index = Math.floor(progress * (latlngs.length - 1));
    state.runnerMarker.setLatLng(latlngs[index]);
    if (progress < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

function renderOptimizedRoute(route) {
  const container = document.getElementById("mergedOptimizedRoute");
  if (!container || !route) return;
  container.className = "optimized-card";
  container.innerHTML = `
    <div>
      <div class="score-badge">${Number(route.coolrun_score).toFixed(0)}</div>
      <p class="selection-basis"><span class="route-swatch" style="--route-color:#059669"></span>${route.name}</p>
    </div>
    <div>
      <div class="metric"><span>Distance</span><strong>${Math.round(route.distance_m)} m</strong></div>
      <div class="metric"><span>Nearby trees</span><strong>${route.tree_count_near_route}</strong></div>
      <div class="metric"><span>Tree density</span><strong>${Number(route.tree_density_per_km).toFixed(1)} / km</strong></div>
      <div class="metric"><span>Average UTCI</span><strong>${route.average_utci == null ? "n/a" : `${Number(route.average_utci).toFixed(2)} C`}</strong></div>
      <p>${route.explanation}</p>
      <button class="route-focus-btn" type="button" onclick="focusMergedRoute('coolest')">Show on map</button>
    </div>
  `;
}

function renderOptimizedAnimation(route) {
  const container = document.getElementById("mergedOptimizedAnimation");
  if (!container || !route?.geometry?.coordinates?.length) return;
  const coords = route.geometry.coordinates;
  const lons = coords.map((point) => point[0]);
  const lats = coords.map((point) => point[1]);
  const minLon = Math.min(...lons);
  const maxLon = Math.max(...lons);
  const minLat = Math.min(...lats);
  const maxLat = Math.max(...lats);
  const width = 640;
  const height = 260;
  const pad = 34;
  const lonRange = maxLon - minLon || 0.0001;
  const latRange = maxLat - minLat || 0.0001;
  const points = coords.map(([lon, lat]) => {
    const x = pad + ((lon - minLon) / lonRange) * (width - pad * 2);
    const y = height - pad - ((lat - minLat) / latRange) * (height - pad * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  container.className = "route-animation";
  container.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Coolest route animation">
      <polyline class="route-shadow" points="${points.join(" ")}"></polyline>
      <polyline class="route-path merged-animated-path" points="${points.join(" ")}" style="stroke:#059669"></polyline>
      <circle class="route-start" cx="${points[0].split(",")[0]}" cy="${points[0].split(",")[1]}" r="7"></circle>
      <circle class="route-end" cx="${points[points.length - 1].split(",")[0]}" cy="${points[points.length - 1].split(",")[1]}" r="7"></circle>
      <circle r="8" fill="#059669">
        <animateMotion dur="5s" repeatCount="indefinite" path="M ${points.join(" L ")}"></animateMotion>
      </circle>
    </svg>
  `;
}

function renderRouteCards(payload) {
  const container = document.getElementById("mergedResults");
  const routes = payload.route_categories || {};
  container.className = "route-list";
  container.innerHTML = Object.entries(routes).map(([key, route]) => `
    <article class="route-card ${key === "coolest" ? "recommended" : ""}" style="--route-color:${key === "coolest" ? "#059669" : key === "balanced" ? "#f59e0b" : "#2563eb"}">
      <h3><span class="route-swatch"></span>${route.name}</h3>
      <div class="metric"><span>Distance</span><strong>${Math.round(route.distance_m)} m</strong></div>
      <div class="metric"><span>Nearby trees</span><strong>${route.tree_count_near_route}</strong></div>
      <div class="metric"><span>Tree density</span><strong>${Number(route.tree_density_per_km).toFixed(1)} / km</strong></div>
      <div class="metric"><span>Average UTCI</span><strong>${route.average_utci == null ? "n/a" : `${Number(route.average_utci).toFixed(2)} C`}</strong></div>
      <div class="metric"><span>CoolRun score</span><strong>${Number(route.coolrun_score).toFixed(1)}</strong></div>
      <p>${route.explanation}</p>
      <button class="route-focus-btn" type="button" onclick="focusMergedRoute('${key}')">Show on map</button>
    </article>
  `).join("");
}

async function runMergedWorkflow() {
  if (PUBLIC_BACKEND_REQUIRED) {
    setStatus(
      "This public demo needs a deployed FastAPI backend URL. Set window.COOLRUN_API_BASE or edit API_BASE in merged_utci_demo.js.",
      "error"
    );
    return;
  }
  if (!state.start || !state.end) {
    setStatus("Select start and end points first.", "error");
    return;
  }
  try {
    await runSelectedArea();
    await runMergedUtci();
    setStatus("Scoring route options from merged UTCI output...");
    const routePayload = await postJson("/analyze-route", {
      start_lat: state.start.lat,
      start_lon: state.start.lon,
      end_lat: state.end.lat,
      end_lon: state.end.lon,
      date: "2025-07-15",
      hour: 15,
      vegetation_mode: "merged",
    }, 90000);
    state.latestRoutePayload = routePayload;
    drawRoutes(routePayload);
    renderRouteCards(routePayload);
    setStatus(routePayload.utci_available ? "Merged UTCI route recommendation ready." : "Route ready using fallback score.", "ready");
  } catch (error) {
    setStatus(`Merged workflow failed: ${error.message}`, "error");
  }
}

if (map) {
  map.on("click", (event) => {
    const point = { lat: event.latlng.lat, lon: event.latlng.lng };
    if (!state.start || (state.start && state.end)) {
      state.start = point;
      state.end = null;
      if (state.startMarker) map.removeLayer(state.startMarker);
      if (state.endMarker) map.removeLayer(state.endMarker);
      clearRoutes();
      state.startMarker = L.marker(event.latlng).addTo(map).bindPopup("Start").openPopup();
      setStatus("Select end point.");
    } else {
      state.end = point;
      state.endMarker = L.marker(event.latlng).addTo(map).bindPopup("End").openPopup();
      setStatus("Ready to run merged UTCI + route.");
    }
  });
}

document.getElementById("mergedRunBtn").addEventListener("click", runMergedWorkflow);
document.getElementById("mergedClearBtn").addEventListener("click", () => {
  if (state.startMarker && map) map.removeLayer(state.startMarker);
  if (state.endMarker && map) map.removeLayer(state.endMarker);
  clearRoutes();
  state.start = null;
  state.end = null;
  state.areaAnalysis = null;
  setStatus("Select start point.");
});
