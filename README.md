# CoolRun AI

CoolRun AI is a Vienna-focused hackathon project for finding cooler running routes with official Vienna orthophotos, AI tree detection, UTCI thermal-comfort analysis, and route scoring.

## Project Goal

The MVP helps runners compare walking/running route alternatives in Vienna by combining:

- City of Vienna Orthofoto / ViennaGIS WMTS imagery
- Roboflow tree detections
- Optional Infrared UTCI simulation outputs
- OpenRouteService walking alternatives
- A public Leaflet demo page for route selection and result presentation

Mapbox imagery is kept as a fallback in older backend code, but the primary imagery source for the notebook workflow is Vienna Orthofoto / ViennaGIS.

## Workflow

```text
ViennaGIS orthofoto
  -> Roboflow tree detection
  -> detected_trees.geojson
  -> optional Infrared UTCI grids
  -> OpenRouteService route alternatives
  -> CoolRun route score
  -> public HTML demo
```

## Project Structure

```text
coolrun-ai/
  notebooks/              Jupyter experiments and step-by-step workflow
  backend/                FastAPI backend and reusable Python modules
    app/
      imagery/            ViennaGIS orthofoto helpers; Mapbox fallback helpers
      detection/          Roboflow detection helpers
      simulation/         Infrared UTCI integration
      routing/            OpenRouteService route scoring
      visualization/      Map and plot helpers
  frontend/               Public HTML/CSS/JS Leaflet demo
  data/                   Local input imagery and metadata
  outputs/                Generated detections, UTCI files, route demos, maps
```

## Required Environment Variables

Copy `.env.example` to `.env` and fill in the keys you use:

```text
ROBOFLOW_API_KEY=...
ROBOFLOW_MODEL_ID=tree-uecm9/3
INFRARED_API_KEY=...
OPENROUTESERVICE_API_KEY=...
MAPBOX_TOKEN=...   # fallback only
```

Do not put API keys in frontend files. The public demo calls the backend for route optimization, and the backend reads keys from `.env`.

## Requirements For UTCI Simulation

- Python 3.11+
- `pip install infrared-sdk`
- `INFRARED_API_KEY` in `.env`

The app keeps working without Infrared SDK, but UTCI is marked unavailable and route scoring falls back to distance plus tree density.

## Run The Backend

Recommended for Infrared UTCI, from the project root on Windows:

```powershell
cd path\to\coolrun-ai
.venv311\Scripts\Activate.ps1
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload
```

The Infrared SDK requires Python 3.11+. If the backend is started with the older `.venv` Python 3.8 environment, UTCI will not run even if `.venv311` has the package installed.

Alternative, from the project root:

```bash
uvicorn backend.app.main:app --reload
```

Alternative, from the backend folder:

```bash
cd backend
uvicorn app.main:app --reload
```

The API exposes:

- `GET /debug/infrared`
- `POST /analyze-selected-area`
- `POST /analyze-route`
- `GET /outputs/detected_trees.geojson`
- `GET /outputs/utci_summary.json`
- `GET /outputs/route_demo.json`

## Infrared Troubleshooting

Check which backend environment is active:

```text
GET http://127.0.0.1:8001/debug/infrared
```

The debug endpoint reports whether Infrared imports, which import name worked, the active Python executable/version, and whether `INFRARED_API_KEY` is present. It never returns the API key value.

Verify the package in the activated environment:

```powershell
python -m pip show infrared-sdk
```

Verify possible import names:

```powershell
python -c "import infrared; print('works')"
python -c "import infrared_sdk; print('works')"
```

Common causes of UTCI fallback:

- SDK package missing from the active backend environment.
- Backend running with the wrong Python environment, especially `.venv` Python 3.8 instead of `.venv311` Python 3.11+.
- VS Code selected a different Python interpreter than the one used to start FastAPI.
- Import name mismatch between SDK versions; the backend tries both `infrared` and `infrared_sdk`.
- `INFRARED_API_KEY` missing from `.env` or environment variables.
- SDK/API call failed after import, for example account access, weather data, geometry, or remote API errors.

After installing `infrared-sdk`, restart FastAPI. Already-running backend processes do not pick up newly installed packages.

## Open The Frontend

Option 1, open the file directly:

```text
frontend/index.html
```

Some browsers restrict local `file://` requests. If static files do not load, use Option 2.

Option 2, serve the frontend:

```bash
cd frontend
python -m http.server 5500
```

Open:

```text
http://localhost:5500
```

## Notebook Workflow

Run these notebooks in order for the current Vienna test area:

```text
01b_vienna_orthofoto_test.ipynb
02_roboflow_tree_detection.ipynb
03_tree_geojson_conversion.ipynb
04_infrared_utci_test.ipynb
05_visualize_inputs.ipynb
06_visualize_routes.ipynb
07_visualize_routes.ipynb
```

The route demo can still work if UTCI outputs are missing. In that case it shows `UTCI simulation not available yet` and scores routes using distance plus detected tree density.

## Expected Outputs

```text
data/vienna_orthofoto_test.png
data/vienna_orthofoto_test_metadata.json
outputs/roboflow_predictions.json
outputs/detected_trees.geojson
outputs/infrared_tree_points.geojson
outputs/infrared_tree_canopies.geojson
outputs/utci_summary.json              # optional, if Infrared run is available
outputs/utci_without_trees.npy          # optional
outputs/utci_with_trees.npy             # optional
outputs/scored_routes.geojson           # generated by route notebooks or backend
outputs/route_demo.json                 # static frontend fallback
frontend/outputs/route_demo.json        # copy used when serving frontend/ without backend
frontend/outputs/detected_trees.geojson # copy used when serving frontend/ without backend
outputs/coolrun_routes_map.html
```

## Public Demo Behavior

The frontend page lets a user:

- Read the project overview
- View detected tree points on a Leaflet map
- See UTCI status without fake values
- Click start and end points
- Run `Find Coolest Route`
- Run a fixed `Run Vienna Demo`
- View alternative routes, nearby tree counts, tree density, UTCI if available, and CoolRun score

If FastAPI is not running, the page tries to load `outputs/route_demo.json`. If that file is missing too, it shows instructions for starting the backend.

## Route Suggestions

`POST /analyze-route` returns three public route categories:

- Shortest Route: the smallest route distance.
- Coolest Route: the recommended route, using UTCI when available and otherwise tree density plus shade continuity.
- Balanced Route: a compromise that avoids being much longer than the shortest route while improving tree coverage when possible.

The frontend highlights the Coolest Route and animates a runner marker along it. If UTCI is unavailable, `average_utci` stays `null`; no thermal values are invented.

## Deployment Plan

Option 1, split deployment:

- Backend on Render, Railway, Fly.io, or another Python web host.
- Frontend on Netlify, Vercel, or GitHub Pages.
- Set backend environment variables on the backend host only:
  `INFRARED_API_KEY`, `OPENROUTESERVICE_API_KEY`, `ROBOFLOW_API_KEY`, and `ROBOFLOW_MODEL_ID`.
- Set `window.COOLRUN_API_BASE` to the public backend URL before loading `frontend/app.js`, or update `API_BASE` in `frontend/app.js`.

Option 2, one FastAPI server:

- Deploy the backend as the public app.
- `backend/app/main.py` serves `frontend/index.html` at `/` and static frontend assets under `/frontend`.
- Keep API keys in backend environment variables only.

CORS currently allows localhost for development. For production, restrict `allow_origins` in `backend/app/main.py` to the deployed frontend URL.

## Publish To GitHub

This folder is prepared to publish as a new repository. Generated local files are ignored by default, so GitHub receives the source code and notebooks without private keys, virtual environments, cached SDK files, downloaded imagery, or generated UTCI outputs.

From the project root:

```powershell
git init
git add .
git status
git commit -m "Initial CoolRun AI demo"
git branch -M main
git remote add origin https://github.com/<your-username>/coolrun-ai.git
git push -u origin main
```

Before committing, confirm `git status` does not include `.env`, `.venv/`, `.venv311/`, `.uv-cache/`, `.uv-python/`, generated `data/` files, or generated `outputs/` files. See `GITHUB_EXPORT_CHECKLIST.md` for the shorter release checklist.

## Local Testing Checklist

1. Activate environment:
   `.venv311\Scripts\Activate.ps1`
2. Start backend:
   `cd backend`
   `python -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload`
3. Start frontend:
   `cd frontend`
   `python -m http.server 5500`
4. Open:
   `http://localhost:5500`

Test:

- Map loads quickly.
- Tree points do not freeze the browser.
- User can select start/end points.
- Route analysis returns Shortest, Coolest, and Balanced categories.
- Coolest route is highlighted.
- Replay animation works.
- Fallback message appears if UTCI is unavailable.
- API keys are not visible in frontend source.

## Known Limitations

- UTCI results depend on Infrared SDK availability and account/API access.
- Tree detection quality depends on the Roboflow model and orthophoto conditions.
- Current MVP is focused on Vienna and uses ViennaGIS orthophotos as the primary imagery source.
- If UTCI grids are unavailable or invalid, route scoring does not invent thermal values.
- OpenRouteService routing requires `OPENROUTESERVICE_API_KEY` in `.env`.
