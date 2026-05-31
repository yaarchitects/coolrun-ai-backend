import React, { useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { MapContainer, Marker, Polyline, TileLayer, useMapEvents } from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "./styles.css";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";
const DEFAULT_CENTER = {
  lat: 48.18461202879178,
  lon: 16.400399172025814,
};

const markerIcon = new L.Icon({
  iconUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
  iconRetinaUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png",
  shadowUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
  iconSize: [25, 41],
  iconAnchor: [12, 41],
});

function artifactUrl(path) {
  if (!path) return "";
  const normalized = path.replaceAll("\\", "/");
  if (normalized.startsWith("outputs/")) {
    return `${API_BASE}/${normalized}`;
  }
  return `${API_BASE}/${normalized}`;
}

function LocationPicker({ center, onChange }) {
  useMapEvents({
    click(event) {
      onChange({
        lat: event.latlng.lat,
        lon: event.latlng.lng,
      });
    },
  });

  return <Marker position={[center.lat, center.lon]} icon={markerIcon} />;
}

function Metric({ label, value, unit }) {
  const display = typeof value === "number" ? value.toFixed(2) : value ?? "Pending";
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>
        {display}
        {typeof value === "number" && unit ? <small>{unit}</small> : null}
      </strong>
    </div>
  );
}

function ImagePanel({ title, path }) {
  if (!path) {
    return (
      <section className="panel image-panel empty">
        <h2>{title}</h2>
        <div className="placeholder">Not available yet</div>
      </section>
    );
  }

  return (
    <section className="panel image-panel">
      <h2>{title}</h2>
      <img src={artifactUrl(path)} alt={title} />
    </section>
  );
}

function App() {
  const [center, setCenter] = useState(DEFAULT_CENTER);
  const [zoom, setZoom] = useState(16);
  const [routeStart, setRouteStart] = useState({
    lat: 48.1828,
    lon: 16.3948,
  });
  const [routeEnd, setRouteEnd] = useState({
    lat: 48.1872,
    lon: 16.4056,
  });
  const [result, setResult] = useState(null);
  const [routeResult, setRouteResult] = useState(null);
  const [status, setStatus] = useState("Ready");
  const [error, setError] = useState("");

  const requestBody = useMemo(
    () => ({
      center_lat: center.lat,
      center_lon: center.lon,
      zoom,
      width: 1024,
      height: 1024,
    }),
    [center, zoom],
  );

  async function analyzeArea() {
    setError("");
    setStatus("Running full analysis. This may take a few minutes...");
    setResult(null);

    try {
      const response = await fetch(`${API_BASE}/analyze-area`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(requestBody),
      });

      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail ?? "Analysis failed");
      }

      setResult(data);
      setRouteResult(null);
      setStatus("Analysis complete");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setStatus("Analysis failed");
    }
  }

  async function scoreRoutes() {
    setError("");
    setStatus("Scoring walking route alternatives...");
    setRouteResult(null);

    try {
      const response = await fetch(`${API_BASE}/score-routes`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          start_lat: routeStart.lat,
          start_lon: routeStart.lon,
          end_lat: routeEnd.lat,
          end_lon: routeEnd.lon,
          run_id: result?.run_id,
          center_lat: center.lat,
          center_lon: center.lon,
          zoom,
        }),
      });

      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail ?? "Route scoring failed");
      }

      setRouteResult(data);
      setStatus("Routes scored");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setStatus("Route scoring failed");
    }
  }

  const paths = result?.visualization_paths ?? {};

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <h1>CoolRun AI</h1>
          <p>Satellite tree detection and UTCI cooling comparison</p>
        </div>
        <button className="primary-button" onClick={analyzeArea}>
          Analyze Area
        </button>
      </header>

      <section className="control-band">
        <div className="map-wrap">
          <MapContainer center={[center.lat, center.lon]} zoom={zoom} scrollWheelZoom>
            <TileLayer
              attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            />
            <LocationPicker center={center} onChange={setCenter} />
            {routeResult?.routes?.map((route) => {
              const selected = routeResult.selected;
              const isCoolest = selected.coolest_route === route.id;
              const isGreenest = selected.greenest_route === route.id;
              const color = isCoolest ? "#0969da" : isGreenest ? "#147d42" : "#5b6472";
              const positions = route.geometry.coordinates.map(([lon, lat]) => [lat, lon]);
              return (
                <Polyline
                  key={route.id}
                  positions={positions}
                  pathOptions={{ color, weight: 5, opacity: 0.85 }}
                />
              );
            })}
          </MapContainer>
        </div>
        <div className="settings">
          <label>
            Center latitude
            <input
              value={center.lat}
              onChange={(event) => setCenter({ ...center, lat: Number(event.target.value) })}
            />
          </label>
          <label>
            Center longitude
            <input
              value={center.lon}
              onChange={(event) => setCenter({ ...center, lon: Number(event.target.value) })}
            />
          </label>
          <label>
            Zoom
            <input value={zoom} onChange={(event) => setZoom(Number(event.target.value))} />
          </label>
          <div className="status">
            <span>{status}</span>
            {error ? <strong>{error}</strong> : null}
          </div>
        </div>
      </section>

      <section className="route-band">
        <div className="route-controls">
          <label>
            Start latitude
            <input
              value={routeStart.lat}
              onChange={(event) => setRouteStart({ ...routeStart, lat: Number(event.target.value) })}
            />
          </label>
          <label>
            Start longitude
            <input
              value={routeStart.lon}
              onChange={(event) => setRouteStart({ ...routeStart, lon: Number(event.target.value) })}
            />
          </label>
          <label>
            End latitude
            <input
              value={routeEnd.lat}
              onChange={(event) => setRouteEnd({ ...routeEnd, lat: Number(event.target.value) })}
            />
          </label>
          <label>
            End longitude
            <input
              value={routeEnd.lon}
              onChange={(event) => setRouteEnd({ ...routeEnd, lon: Number(event.target.value) })}
            />
          </label>
          <button className="secondary-button" onClick={scoreRoutes}>
            Score Routes
          </button>
        </div>
        <div className="route-results">
          {routeResult ? (
            <>
              <RouteCard title="Shortest" route={routeResult.selected_routes.shortest_route} />
              <RouteCard title="Greenest" route={routeResult.selected_routes.greenest_route} />
              <RouteCard title="Coolest" route={routeResult.selected_routes.coolest_route} />
            </>
          ) : (
            <div className="route-placeholder">Run analysis, then score walking routes.</div>
          )}
        </div>
      </section>

      <section className="summary-grid">
        <Metric label="Trees detected" value={result?.tree_count} />
        <Metric label="Mean UTCI without trees" value={result?.mean_utci_without} unit=" C" />
        <Metric label="Mean UTCI with trees" value={result?.mean_utci_with} unit=" C" />
        <Metric label="Mean cooling effect" value={result?.mean_cooling_effect} unit=" C" />
        <Metric label="Max cooling effect" value={result?.max_cooling_effect} unit=" C" />
      </section>

      <section className="image-grid">
        <ImagePanel title="Satellite Image" path={result?.satellite_image} />
        <ImagePanel title="Detected Trees" path={paths.input_tree_visualization} />
        <ImagePanel title="UTCI Without Trees" path={paths.utci_without_trees} />
        <ImagePanel title="UTCI With Trees" path={paths.utci_with_trees} />
        <ImagePanel title="UTCI Cooling Difference" path={paths.utci_cooling_effect} />
      </section>
    </main>
  );
}

function RouteCard({ title, route }) {
  return (
    <div className="route-card">
      <span>{title}</span>
      <strong>{route.id}</strong>
      <small>{Math.round(route.distance_m)} m</small>
      <small>Tree density {route.tree_density.toFixed(2)}</small>
      <small>UTCI {route.average_utci === null ? "n/a" : route.average_utci.toFixed(2)}</small>
      <small>Score {route.coolrun_score}</small>
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);
