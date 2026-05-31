"""FastAPI backend for the CoolRun AI analysis pipeline."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from PIL import Image

PROJECT_ROOT_FOR_IMPORTS = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT_FOR_IMPORTS) not in sys.path:
    sys.path.append(str(PROJECT_ROOT_FOR_IMPORTS))

from backend.app.detection.roboflow_detector import (
    draw_bounding_boxes,
    load_roboflow_settings,
    run_tree_detection,
)
from backend.app.imagery.vienna_orthofoto import (
    create_bbox_polygon,
    download_vienna_orthofoto,
    lonlat_to_webmercator,
)
from backend.app.imagery.mapbox_geo import create_image_bounds_polygon
from backend.app.imagery.mapbox_static import download_mapbox_static_image
from backend.app.routing.openroute_service import request_walking_routes
from backend.app.routing.route_scoring import score_route_features
from backend.app.simulation.infrared_runner import (
    DEFAULT_CENTER_LAT,
    DEFAULT_CENTER_LON,
    DEFAULT_IMAGE_HEIGHT,
    DEFAULT_IMAGE_WIDTH,
    DEFAULT_MAPBOX_ZOOM,
    get_infrared_debug_info,
    merge_detected_and_sdk_vegetation,
    run_utci_comparison,
    run_utci_for_polygon,
    save_utci_grid_image,
    save_utci_heatmap,
    save_utci_outputs,
    save_vegetation_geojson,
    tree_geojson_to_canopy_polygons,
    tree_geojson_to_infrared_vegetation,
)
from backend.app.simulation.vegetation_builder import detections_to_tree_geojson
from backend.app.visualization.input_maps import (
    save_cooling_effect_visualization,
    save_static_input_visualization,
)
from backend.app.visualization.route_maps import save_routes_map


PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
DATA_DIR = PROJECT_ROOT / "data"

app = FastAPI(title="CoolRun AI API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
        "https://yaarchitects.github.io",
        "https://yusuffakkoyun.github.io",
        "null",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/outputs", StaticFiles(directory=OUTPUTS_DIR), name="outputs")
FRONTEND_DIR = PROJECT_ROOT / "frontend"


class AnalyzeAreaRequest(BaseModel):
    """Request body for the full CoolRun AI analysis pipeline."""

    polygon: Optional[dict[str, Any]] = None
    date: str = "2026-07-15"
    hour: int = Field(15, ge=0, le=23)
    vegetation_mode: str = "merged"
    center_lat: float = Field(DEFAULT_CENTER_LAT, ge=-90, le=90)
    center_lon: float = Field(DEFAULT_CENTER_LON, ge=-180, le=180)
    zoom: int = Field(DEFAULT_MAPBOX_ZOOM, ge=0, le=22)
    width: int = Field(DEFAULT_IMAGE_WIDTH, gt=0)
    height: int = Field(DEFAULT_IMAGE_HEIGHT, gt=0)


class ScoreRoutesRequest(BaseModel):
    """Request body for route alternative scoring."""

    start_lat: float = Field(..., ge=-90, le=90)
    start_lon: float = Field(..., ge=-180, le=180)
    end_lat: float = Field(..., ge=-90, le=90)
    end_lon: float = Field(..., ge=-180, le=180)
    run_id: Optional[str] = None
    center_lat: float = Field(DEFAULT_CENTER_LAT, ge=-90, le=90)
    center_lon: float = Field(DEFAULT_CENTER_LON, ge=-180, le=180)
    zoom: int = Field(DEFAULT_MAPBOX_ZOOM, ge=0, le=22)


class AnalyzeRouteRequest(BaseModel):
    """Request body for route analysis using existing detected trees and UTCI outputs."""

    start_lat: float = Field(..., ge=-90, le=90)
    start_lon: float = Field(..., ge=-180, le=180)
    end_lat: float = Field(..., ge=-90, le=90)
    end_lon: float = Field(..., ge=-180, le=180)
    date: str = "2026-07-15"
    hour: int = Field(15, ge=0, le=23)
    vegetation_mode: str = "merged"


class AnalyzeSelectedAreaRequest(BaseModel):
    """Request body for analysis driven by user-selected route endpoints."""

    start_lat: float = Field(..., ge=-90, le=90)
    start_lon: float = Field(..., ge=-180, le=180)
    end_lat: float = Field(..., ge=-90, le=90)
    end_lon: float = Field(..., ge=-180, le=180)
    zoom: int = Field(18, ge=0, le=22)
    run_utci: bool = True
    date: str = "2026-07-15"
    hour: int = Field(15, ge=0, le=23)
    vegetation_mode: str = "merged"


def _relative_path(path: str | Path) -> str:
    """Return a project-relative path when possible."""
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(resolved)


def _write_json(path: Path, data: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return str(path)


def _public_output_url(path: str | Path) -> str:
    resolved = Path(path).resolve()
    try:
        relative = resolved.relative_to(OUTPUTS_DIR)
        return f"/outputs/{relative.as_posix()}"
    except ValueError:
        return _relative_path(resolved)


def _selected_area_center_and_size(request: AnalyzeSelectedAreaRequest) -> tuple[float, float, int, int]:
    """Return a Vienna orthophoto crop that covers both selected points."""
    center_lat = (request.start_lat + request.end_lat) / 2
    center_lon = (request.start_lon + request.end_lon) / 2

    start_x, start_y = lonlat_to_webmercator(request.start_lon, request.start_lat)
    end_x, end_y = lonlat_to_webmercator(request.end_lon, request.end_lat)
    meters_per_pixel = 156543.03392804097 / (2**request.zoom)
    required_width = int(abs(end_x - start_x) / meters_per_pixel) + 360
    required_height = int(abs(end_y - start_y) / meters_per_pixel) + 360

    width = min(max(required_width, 512), 1024)
    height = min(max(required_height, 512), 1024)
    if width % 2:
        width += 1
    if height % 2:
        height += 1
    return center_lat, center_lon, width, height


def _load_detected_trees_or_empty() -> dict[str, Any]:
    detected_path = OUTPUTS_DIR / "detected_trees.geojson"
    if not detected_path.exists():
        return {"type": "FeatureCollection", "features": []}
    return json.loads(detected_path.read_text(encoding="utf-8"))


def _route_bbox_polygon(route_geojson: dict[str, Any], padding_degrees: float = 0.00035) -> dict[str, Any]:
    points = []
    for feature in route_geojson.get("features", []):
        points.extend(feature.get("geometry", {}).get("coordinates", []))
    if not points:
        raise ValueError("No route coordinates available to create UTCI polygon.")
    lons = [float(point[0]) for point in points]
    lats = [float(point[1]) for point in points]
    west = min(lons) - padding_degrees
    east = max(lons) + padding_degrees
    south = min(lats) - padding_degrees
    north = max(lats) + padding_degrees
    return {
        "type": "Polygon",
        "coordinates": [[[west, south], [east, south], [east, north], [west, north], [west, south]]],
    }


def _polygon_bounds_lonlat(polygon: dict[str, Any]) -> dict[str, float]:
    """Return lon/lat bounds for a simple GeoJSON polygon."""
    coordinates = polygon.get("coordinates", [[]])[0]
    if not coordinates:
        raise ValueError("Polygon has no coordinates.")
    lons = [float(point[0]) for point in coordinates]
    lats = [float(point[1]) for point in coordinates]
    return {
        "west": min(lons),
        "south": min(lats),
        "east": max(lons),
        "north": max(lats),
    }


def _selected_area_utci_polygon(
    metadata: dict[str, Any],
    request: AnalyzeSelectedAreaRequest,
    padding_degrees: float = 0.00005,
) -> dict[str, Any]:
    """Create a UTCI polygon covering the full orthophoto and selected endpoints."""
    bbox = metadata.get("bbox_lonlat")
    if not bbox:
        raise ValueError("Vienna orthophoto metadata has no lon/lat bbox.")

    west = min(float(bbox["west"]), request.start_lon, request.end_lon) - padding_degrees
    east = max(float(bbox["east"]), request.start_lon, request.end_lon) + padding_degrees
    south = min(float(bbox["south"]), request.start_lat, request.end_lat) - padding_degrees
    north = max(float(bbox["north"]), request.start_lat, request.end_lat) + padding_degrees
    return create_bbox_polygon({"west": west, "south": south, "east": east, "north": north})


def _bbox_lonlat_to_webmercator_bbox(bounds: dict[str, float]) -> tuple[float, float, float, float]:
    """Convert lon/lat bounds to a Web Mercator bbox used for grid sampling."""
    min_x, min_y = lonlat_to_webmercator(float(bounds["west"]), float(bounds["south"]))
    max_x, max_y = lonlat_to_webmercator(float(bounds["east"]), float(bounds["north"]))
    return (min_x, min_y, max_x, max_y)


def _utci_bbox_from_summary(summary: dict[str, Any] | None) -> tuple[float, float, float, float] | None:
    if not summary:
        return None
    bounds = summary.get("utci_bbox_lonlat")
    if not isinstance(bounds, dict):
        return None
    return _bbox_lonlat_to_webmercator_bbox(bounds)


def _save_utci_npy_heatmap(npy_path: Path, output_path: Path, title: str) -> str:
    """Save a PNG heatmap from a real SDK UTCI grid stored as .npy."""
    import matplotlib.pyplot as plt
    import numpy as np

    if not npy_path.exists():
        raise FileNotFoundError(f"Missing UTCI grid: {npy_path}")

    grid = np.load(npy_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    plt.figure(figsize=(8, 7))
    heatmap = plt.imshow(grid, cmap="RdBu_r", interpolation="nearest")
    plt.colorbar(heatmap, label="UTCI C")
    plt.title(title)
    plt.axis("off")
    plt.tight_layout()
    plt.savefig(output_path, dpi=160, bbox_inches="tight")
    plt.close()
    return str(output_path)


@app.get("/debug/infrared")
def debug_infrared() -> dict[str, Any]:
    """Return safe Infrared SDK diagnostics without exposing the API key."""
    return get_infrared_debug_info()


def _latest_run_dir() -> Path:
    """Return the newest outputs/run_* directory."""
    run_dirs = [
        path
        for path in OUTPUTS_DIR.glob("run_*")
        if path.is_dir() and (path / "detected_trees.geojson").exists()
    ]
    if not run_dirs:
        raise FileNotFoundError("No analysis run found. Run /analyze-area first.")

    return max(run_dirs, key=lambda path: path.stat().st_mtime)


def _resolve_run_dir(run_id: str | None) -> Path:
    """Resolve a run directory from a run id or latest run."""
    if run_id:
        run_dir = OUTPUTS_DIR / run_id
        if not run_dir.exists():
            raise FileNotFoundError(f"Run id does not exist: {run_id}")
        return run_dir

    return _latest_run_dir()


def _resolve_analysis_artifacts(run_id: str | None = None) -> dict[str, Path]:
    """Resolve detected tree and UTCI files from notebook outputs or a FastAPI run."""
    if run_id:
        base_dir = _resolve_run_dir(run_id)
    elif (OUTPUTS_DIR / "detected_trees.geojson").exists():
        base_dir = OUTPUTS_DIR
    else:
        base_dir = _latest_run_dir()

    detected_trees_path = base_dir / "detected_trees.geojson"
    if not detected_trees_path.exists():
        raise FileNotFoundError(
            f"Missing detected_trees.geojson at {detected_trees_path}. "
            "Run notebooks 01-05 or POST /analyze-area first."
        )

    merged_utci_grid = base_dir / "utci_with_merged_trees.npy"
    legacy_utci_grid = base_dir / "utci_with_trees.npy"

    return {
        "base_dir": base_dir,
        "detected_trees": detected_trees_path,
        "utci_grid": merged_utci_grid if merged_utci_grid.exists() else legacy_utci_grid,
        "utci_summary": base_dir / "utci_summary.json",
    }


def _routes_to_feature_collection(scored: dict[str, Any]) -> dict[str, Any]:
    """Convert scored route dictionaries into a GeoJSON FeatureCollection."""
    output_features = []
    for route in scored["routes"]:
        output_features.append(
            {
                "type": "Feature",
                "geometry": route["geometry"],
                "properties": {
                    "id": route["id"],
                    "distance_m": route["distance_m"],
                    "sample_count": route["sample_count"],
                    "tree_count_near_route": route.get("tree_count_near_route"),
                    "tree_density": route["tree_density"],
                    "tree_density_per_km": route.get("tree_density_per_km"),
                    "shade_continuity": route.get("shade_continuity"),
                    "average_utci": route["average_utci"],
                    "coolrun_score": route["coolrun_score"],
                },
            }
        )

    return {
        "type": "FeatureCollection",
        "features": output_features,
        "selected": scored["selected"],
        "utci_available": scored["utci_available"],
    }


def _selected_routes(scored: dict[str, Any]) -> dict[str, Any]:
    """Return selected shortest, greenest, and coolest route objects."""
    routes_by_id = {route["id"]: route for route in scored["routes"]}
    return {
        label: routes_by_id[route_id]
        for label, route_id in scored["selected"].items()
    }


def _load_vienna_metadata() -> dict[str, Any] | None:
    """Load the current Vienna orthofoto metadata if notebook 01b created it."""
    metadata_path = DATA_DIR / "vienna_orthofoto_test_metadata.json"
    if not metadata_path.exists():
        return None

    return json.loads(metadata_path.read_text(encoding="utf-8"))


def _image_bbox_from_metadata(metadata: dict[str, Any] | None) -> tuple[float, float, float, float] | None:
    if not metadata:
        return None

    bbox = metadata.get("bbox")
    if not bbox:
        return None

    return (
        float(bbox["min_x"]),
        float(bbox["min_y"]),
        float(bbox["max_x"]),
        float(bbox["max_y"]),
    )


def _utci_grid_available(utci_grid_path: Path, utci_summary_path: Path) -> tuple[bool, dict[str, Any] | None, list[str]]:
    """Return whether a real UTCI grid should be used for route scoring."""
    warnings = []
    summary = None

    if utci_summary_path.exists():
        summary = json.loads(utci_summary_path.read_text(encoding="utf-8"))

    if not utci_grid_path.exists():
        reason = summary.get("reason") if summary else None
        warnings.append(reason or "UTCI simulation not available yet. Scoring uses distance and detected tree density.")
        return False, summary, warnings

    import numpy as np

    try:
        grid = np.load(utci_grid_path)
        finite_cell_count = int(np.isfinite(grid).sum())
    except Exception as exc:
        warnings.append(f"UTCI grid could not be loaded: {exc}. Scoring uses distance and detected tree density.")
        return False, summary, warnings

    if finite_cell_count <= 0:
        warnings.append("UTCI grid has no finite cells. Scoring uses distance and detected tree density.")
        return False, summary, warnings

    if summary and summary.get("is_placeholder"):
        summary = {**summary, "utci_available": True, "finite_cell_count": finite_cell_count}
        return True, summary, warnings

    return True, summary, warnings


def _public_route_name(route_id: str, selected: dict[str, str]) -> str:
    if route_id == selected.get("shortest_route"):
        return "Shortest route"
    if route_id == selected.get("greenest_route"):
        return "Greenest route"
    if route_id == selected.get("coolest_route"):
        return "Coolest route"
    if route_id == selected.get("balanced_route"):
        return "Balanced route"
    return route_id.replace("_", " ").title()


def _route_payload(route: dict[str, Any], name: str, explanation: str) -> dict[str, Any]:
    return {
        "id": route["id"],
        "name": name,
        "geometry": route["geometry"],
        "distance_m": route["distance_m"],
        "tree_count_near_route": route.get("tree_count_near_route", 0),
        "tree_density_per_km": route.get("tree_density_per_km", 0),
        "shade_continuity": route.get("shade_continuity", 0),
        "average_utci": route.get("average_utci"),
        "coolrun_score": route["coolrun_score"],
        "explanation": explanation,
    }


def _route_categories(scored: dict[str, Any]) -> dict[str, Any]:
    routes_by_id = {route["id"]: route for route in scored["routes"]}
    selected = scored["selected"]
    shortest = routes_by_id[selected["shortest_route"]]
    coolest = routes_by_id[selected["coolest_route"]]
    balanced = routes_by_id[selected.get("balanced_route", selected["coolest_route"])]
    return {
        "shortest": _route_payload(
            shortest,
            "Shortest Route",
            "Fastest route, but not necessarily the coolest.",
        ),
        "coolest": _route_payload(
            coolest,
            "Coolest Route",
            "Recommended because it has the best heat-comfort score.",
        ),
        "balanced": _route_payload(
            balanced,
            "Balanced Route",
            "A compromise between short distance and cooler conditions.",
        ),
    }


def _public_routes(scored: dict[str, Any]) -> list[dict[str, Any]]:
    selected = scored.get("selected", {})
    routes = []

    for route in scored.get("routes", []):
        routes.append(
            {
                "id": route["id"],
                "name": _public_route_name(route["id"], selected),
                "geometry": route["geometry"],
                "distance_m": route["distance_m"],
                "tree_count_near_route": route.get("tree_count_near_route", 0),
                "tree_density_per_km": route.get("tree_density_per_km", 0),
                "average_utci": route.get("average_utci"),
                "coolrun_score": route["coolrun_score"],
            }
        )

    return routes


@app.post("/analyze-area")
def analyze_area(request: AnalyzeAreaRequest) -> dict[str, Any]:
    """Run the end-to-end CoolRun AI analysis for one map area."""
    if request.polygon:
        detected_path = OUTPUTS_DIR / "detected_trees.geojson"
        result = run_utci_for_polygon(
            polygon=request.polygon,
            time_period={"date": request.date, "hour": request.hour},
            vegetation_mode=request.vegetation_mode,
            detected_tree_geojson_path=detected_path if detected_path.exists() else None,
            output_dir=OUTPUTS_DIR,
        )
        summary = result["summary"]
        try:
            summary["utci_bbox_lonlat"] = _polygon_bounds_lonlat(request.polygon)
            summary["utci_polygon"] = request.polygon
            _write_json(OUTPUTS_DIR / "utci_summary.json", summary)
        except Exception:
            pass
        visualization_paths = {}
        utci_with_heatmap_path = OUTPUTS_DIR / "utci_with_merged_trees_heatmap.png"
        if summary.get("utci_available") and (OUTPUTS_DIR / "utci_with_merged_trees.npy").exists():
            _save_utci_npy_heatmap(
                OUTPUTS_DIR / "utci_with_merged_trees.npy",
                utci_with_heatmap_path,
                "UTCI Heatmap With Merged Trees",
            )
            visualization_paths["utci_with_merged_trees_heatmap"] = _public_output_url(utci_with_heatmap_path)
        return {
            "infrared_debug": summary.get("infrared_debug", get_infrared_debug_info()),
            "utci_attempted": bool(summary.get("utci_attempted")),
            "utci_available": bool(summary.get("utci_available")),
            "failed_step": summary.get("failed_step"),
            "error_message": summary.get("error_message"),
            "sdk_tree_count": summary.get("sdk_tree_count", 0),
            "detected_tree_count": summary.get("detected_tree_count", 0),
            "merged_tree_count": summary.get("merged_tree_count", 0),
            "ground_material_count": summary.get("ground_material_count", 0),
            "has_merged_grid_without": bool(summary.get("has_merged_grid_without")),
            "has_merged_grid_with": bool(summary.get("has_merged_grid_with")),
            "time_period": summary.get("time_period"),
            "vegetation_summary": {
                "sdk_tree_count": summary.get("sdk_tree_count", 0),
                "detected_tree_count": summary.get("detected_tree_count", 0),
                "merged_tree_count": summary.get("merged_tree_count", 0),
                "duplicate_count": summary.get("duplicate_count", 0),
                "ground_material_count": summary.get("ground_material_count", 0),
            },
            "summary": summary,
            "visualization_paths": visualization_paths,
            "paths": {
                "utci_summary": _relative_path(result["summary_path"]),
                "merged_vegetation": _relative_path(OUTPUTS_DIR / "merged_vegetation.geojson"),
                "sdk_vegetation": _relative_path(OUTPUTS_DIR / "sdk_vegetation.geojson"),
            },
        }

    if request.width % 2 != 0 or request.height % 2 != 0:
        raise HTTPException(
            status_code=400,
            detail="width and height must be even because the Mapbox helper requests @2x images.",
        )

    run_id = datetime.utcnow().strftime("run_%Y%m%d_%H%M%S_%f")
    run_dir = OUTPUTS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    satellite_path = run_dir / "mapbox_satellite.png"
    predictions_path = run_dir / "roboflow_predictions.json"
    detection_image_path = run_dir / "tree_detection_result.png"
    detected_trees_path = run_dir / "detected_trees.geojson"
    infrared_tree_points_path = run_dir / "infrared_tree_points.geojson"
    canopy_geojson_path = run_dir / "infrared_tree_canopies.geojson"
    input_visualization_path = run_dir / "input_tree_visualization.png"
    cooling_effect_path = run_dir / "utci_cooling_effect.png"
    utci_without_png_path = run_dir / "utci_without_trees.png"
    utci_with_png_path = run_dir / "utci_with_detected_trees.png"
    utci_without_html_path = run_dir / "utci_without_trees.html"
    utci_with_html_path = run_dir / "utci_with_detected_trees.html"

    stage = "starting analysis"
    try:
        stage = "downloading Mapbox satellite image"
        # The Mapbox helper requests @2x retina images, so request half the
        # desired final pixel size to produce request.width x request.height.
        download_mapbox_static_image(
            lat=request.center_lat,
            lon=request.center_lon,
            zoom=request.zoom,
            width=request.width // 2,
            height=request.height // 2,
            output_path=str(satellite_path),
        )

        with Image.open(satellite_path) as image:
            image_width, image_height = image.size

        stage = "running Roboflow tree detection"
        roboflow_api_key, roboflow_model_id = load_roboflow_settings()
        detection_result = run_tree_detection(
            image_path=str(satellite_path),
            api_key=roboflow_api_key,
            model_id=roboflow_model_id,
            confidence=40,
            overlap=30,
        )
        predictions = detection_result.get("predictions", [])
        _write_json(predictions_path, predictions)

        stage = "drawing Roboflow tree detections"
        draw_bounding_boxes(
            image_path=str(satellite_path),
            predictions=predictions,
            output_path=str(detection_image_path),
        )

        stage = "converting detections to tree GeoJSON"
        tree_geojson = detections_to_tree_geojson(
            predictions=predictions,
            center_lon=request.center_lon,
            center_lat=request.center_lat,
            zoom=request.zoom,
            width=image_width,
            height=image_height,
            confidence_threshold=0.35,
        )
        _write_json(detected_trees_path, tree_geojson)

        stage = "preparing Infrared vegetation inputs"
        infrared_tree_points = tree_geojson_to_infrared_vegetation(tree_geojson)
        canopy_polygons = tree_geojson_to_canopy_polygons(tree_geojson)
        save_vegetation_geojson(infrared_tree_points, str(infrared_tree_points_path))
        save_vegetation_geojson(canopy_polygons, str(canopy_geojson_path))

        stage = "saving input visualization"
        save_static_input_visualization(
            image_path=str(satellite_path),
            tree_geojson=tree_geojson,
            polygon=create_image_bounds_polygon(
                center_lon=request.center_lon,
                center_lat=request.center_lat,
                zoom=request.zoom,
                width=image_width,
                height=image_height,
            ),
            output_path=str(input_visualization_path),
            center_lon=request.center_lon,
            center_lat=request.center_lat,
            zoom=request.zoom,
            canopy_geojson={"type": "FeatureCollection", "features": list(canopy_polygons.values())},
        )

        stage = "running Infrared UTCI simulations"
        utci_result = run_utci_comparison(
            tree_geojson_path=str(detected_trees_path),
            center_lon=request.center_lon,
            center_lat=request.center_lat,
            zoom=request.zoom,
            image_width=image_width,
            image_height=image_height,
            run_live=True,
        )
        stage = "saving UTCI outputs"
        saved_utci_outputs = save_utci_outputs(
            result=utci_result,
            output_dir=str(run_dir),
        )
        summary = saved_utci_outputs["summary"]

        visualization_paths = {
            "tree_detection_result": _relative_path(detection_image_path),
            "input_tree_visualization": _relative_path(input_visualization_path),
        }

        if not summary.get("is_placeholder"):
            stage = "saving UTCI cooling visualization"
            save_cooling_effect_visualization(
                without_trees_npy=saved_utci_outputs["utci_without_trees_npy"],
                with_trees_npy=saved_utci_outputs["utci_with_trees_npy"],
                output_path=str(cooling_effect_path),
            )
            visualization_paths["utci_cooling_effect"] = _relative_path(cooling_effect_path)

            stage = "saving UTCI PNG images"
            save_utci_grid_image(
                utci_result["result_without_trees"],
                str(utci_without_png_path),
                "UTCI without detected trees",
            )
            save_utci_grid_image(
                utci_result["result_with_trees"],
                str(utci_with_png_path),
                "UTCI with detected trees",
            )
            visualization_paths["utci_without_trees"] = _relative_path(utci_without_png_path)
            visualization_paths["utci_with_trees"] = _relative_path(utci_with_png_path)

            stage = "saving UTCI HTML heatmaps"
            save_utci_heatmap(
                utci_result["result_without_trees"],
                str(utci_without_html_path),
                "UTCI without detected trees",
            )
            save_utci_heatmap(
                utci_result["result_with_trees"],
                str(utci_with_html_path),
                "UTCI with detected trees",
            )
            visualization_paths["utci_without_trees_html"] = _relative_path(utci_without_html_path)
            visualization_paths["utci_with_trees_html"] = _relative_path(utci_with_html_path)

        return {
            "run_id": run_id,
            "tree_count": len(tree_geojson["features"]),
            "mean_utci_without": summary.get("mean_utci_without"),
            "mean_utci_with": summary.get("mean_utci_with"),
            "mean_cooling_effect": summary.get("mean_cooling_effect"),
            "max_cooling_effect": summary.get("max_cooling_effect"),
            "visualization_paths": visualization_paths,
            "detected_trees_geojson": _relative_path(detected_trees_path),
            "infrared_tree_points_geojson": _relative_path(infrared_tree_points_path),
            "canopy_visualization_geojson": _relative_path(canopy_geojson_path),
            "utci_summary_json": _relative_path(saved_utci_outputs["summary_path"]),
            "satellite_image": _relative_path(satellite_path),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{stage} failed: {exc}") from exc


@app.post("/analyze-selected-area")
def analyze_selected_area(request: AnalyzeSelectedAreaRequest) -> dict[str, Any]:
    """Run imagery, tree detection, and UTCI analysis for user-selected endpoints."""
    stage = "starting selected-area analysis"
    try:
        center_lat, center_lon, width, height = _selected_area_center_and_size(request)

        orthophoto_path = OUTPUTS_DIR / "vienna_orthofoto_test.png"
        metadata_path = OUTPUTS_DIR / "vienna_orthofoto_test_metadata.json"
        predictions_path = OUTPUTS_DIR / "roboflow_predictions.json"
        detection_image_path = OUTPUTS_DIR / "tree_detection_result.png"
        detected_trees_path = OUTPUTS_DIR / "detected_trees.geojson"
        infrared_tree_points_path = OUTPUTS_DIR / "infrared_tree_points.geojson"
        canopy_geojson_path = OUTPUTS_DIR / "infrared_tree_canopies.geojson"
        input_visualization_path = OUTPUTS_DIR / "input_tree_visualization.png"
        cooling_effect_path = OUTPUTS_DIR / "utci_cooling_effect.png"
        cooling_overlay_path = OUTPUTS_DIR / "utci_cooling_effect_tree_overlay.png"
        utci_without_heatmap_path = OUTPUTS_DIR / "utci_without_trees_heatmap.png"
        utci_with_heatmap_path = OUTPUTS_DIR / "utci_with_merged_trees_heatmap.png"

        stage = "extracting Vienna orthophoto from selected points"
        metadata = download_vienna_orthofoto(
            lat=center_lat,
            lon=center_lon,
            output_path=str(orthophoto_path),
            zoom=request.zoom,
            width=width,
            height=height,
        )
        _write_json(metadata_path, metadata)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _write_json(DATA_DIR / "vienna_orthofoto_test_metadata.json", metadata)
        Image.open(orthophoto_path).save(DATA_DIR / "vienna_orthofoto_test.png")

        image_bbox = _image_bbox_from_metadata(metadata)
        if image_bbox is None:
            raise RuntimeError("Vienna orthophoto metadata has no Web Mercator bbox.")

        stage = "running Roboflow tree detection on selected orthophoto"
        roboflow_api_key, roboflow_model_id = load_roboflow_settings()
        detection_result = run_tree_detection(
            image_path=str(orthophoto_path),
            api_key=roboflow_api_key,
            model_id=roboflow_model_id,
            confidence=40,
            overlap=30,
        )
        predictions = detection_result.get("predictions", [])
        _write_json(predictions_path, detection_result)

        stage = "drawing Roboflow tree detections"
        draw_bounding_boxes(
            image_path=str(orthophoto_path),
            predictions=predictions,
            output_path=str(detection_image_path),
        )

        stage = "converting selected-area detections to tree GeoJSON"
        tree_geojson = detections_to_tree_geojson(
            predictions=predictions,
            center_lon=center_lon,
            center_lat=center_lat,
            zoom=request.zoom,
            width=width,
            height=height,
            confidence_threshold=0.35,
            image_bbox=image_bbox,
            allowed_classes={"tree"},
        )
        _write_json(detected_trees_path, tree_geojson)

        stage = "preparing Infrared vegetation inputs"
        infrared_tree_points = tree_geojson_to_infrared_vegetation(tree_geojson)
        canopy_polygons = tree_geojson_to_canopy_polygons(tree_geojson)
        save_vegetation_geojson(infrared_tree_points, str(infrared_tree_points_path))
        save_vegetation_geojson(canopy_polygons, str(canopy_geojson_path))

        image_polygon = create_bbox_polygon(metadata["bbox_lonlat"])
        polygon = _selected_area_utci_polygon(metadata, request)
        utci_bounds = _polygon_bounds_lonlat(polygon)
        stage = "saving selected-area input visualization"
        save_static_input_visualization(
            image_path=str(orthophoto_path),
            tree_geojson=tree_geojson,
            polygon=image_polygon,
            output_path=str(input_visualization_path),
            center_lon=center_lon,
            center_lat=center_lat,
            zoom=request.zoom,
            canopy_geojson={"type": "FeatureCollection", "features": list(canopy_polygons.values())},
            image_bbox=image_bbox,
        )

        stage = "running Infrared UTCI simulations"
        if request.run_utci:
            utci_run = run_utci_for_polygon(
                polygon=polygon,
                time_period={"date": request.date, "hour": request.hour},
                vegetation_mode=request.vegetation_mode,
                detected_tree_geojson_path=detected_trees_path,
                output_dir=OUTPUTS_DIR,
            )
            summary = utci_run["summary"]
            summary["utci_bbox_lonlat"] = utci_bounds
            summary["utci_polygon"] = polygon
            _write_json(OUTPUTS_DIR / "utci_summary.json", summary)
        else:
            summary = {
                "utci_available": False,
                "status": "prepared_not_run",
                "is_placeholder": True,
                "reason": "run_utci=false",
                "time_period": {"date": request.date, "hour": request.hour},
                "sdk_tree_count": 0,
                "detected_tree_count": len(tree_geojson["features"]),
                "merged_tree_count": len(tree_geojson["features"]),
                "duplicate_count": 0,
                "ground_material_count": 0,
                "utci_bbox_lonlat": utci_bounds,
                "utci_polygon": polygon,
                "notes": ["UTCI was not requested."],
            }
            _write_json(OUTPUTS_DIR / "utci_summary.json", summary)

        visualization_paths = {
            "orthophoto": _public_output_url(orthophoto_path),
            "metadata": _public_output_url(metadata_path),
            "tree_detection_result": _public_output_url(detection_image_path),
            "input_tree_visualization": _public_output_url(input_visualization_path),
            "detected_trees_geojson": _public_output_url(detected_trees_path),
            "utci_summary": _public_output_url(OUTPUTS_DIR / "utci_summary.json"),
            "sdk_vegetation": _public_output_url(OUTPUTS_DIR / "sdk_vegetation.geojson"),
            "merged_vegetation": _public_output_url(OUTPUTS_DIR / "merged_vegetation.geojson"),
        }

        if summary.get("utci_available") and (OUTPUTS_DIR / "utci_with_merged_trees.npy").exists():
            stage = "saving SDK UTCI heatmaps"
            if (OUTPUTS_DIR / "utci_without_trees.npy").exists():
                stage = "saving UTCI cooling visualization"
                save_cooling_effect_visualization(
                    without_trees_npy=str(OUTPUTS_DIR / "utci_without_trees.npy"),
                    with_trees_npy=str(OUTPUTS_DIR / "utci_with_merged_trees.npy"),
                    output_path=str(cooling_effect_path),
                )
                # Keep the public demo image contract stable.
                Image.open(cooling_effect_path).save(cooling_overlay_path)
                visualization_paths["utci_cooling_effect"] = _public_output_url(cooling_effect_path)
                visualization_paths["utci_cooling_effect_tree_overlay"] = _public_output_url(cooling_overlay_path)
                stage = "saving SDK UTCI heatmaps"
                _save_utci_npy_heatmap(
                    OUTPUTS_DIR / "utci_without_trees.npy",
                    utci_without_heatmap_path,
                    "UTCI Heatmap Without Trees",
                )
                visualization_paths["utci_without_trees_heatmap"] = _public_output_url(utci_without_heatmap_path)
            _save_utci_npy_heatmap(
                OUTPUTS_DIR / "utci_with_merged_trees.npy",
                utci_with_heatmap_path,
                "UTCI Heatmap With Merged Trees",
            )
            visualization_paths["utci_with_merged_trees_heatmap"] = _public_output_url(utci_with_heatmap_path)

        return {
            "status": "completed",
            "center": {"lat": center_lat, "lon": center_lon},
            "image_width": width,
            "image_height": height,
            "zoom": request.zoom,
            "tree_count": len(tree_geojson["features"]),
            "infrared_debug": summary.get("infrared_debug", get_infrared_debug_info()),
            "utci_attempted": bool(summary.get("utci_attempted", request.run_utci)),
            "utci_available": bool(summary.get("utci_available")),
            "utci_warning": summary.get("reason"),
            "failed_step": summary.get("failed_step"),
            "error_message": summary.get("error_message"),
            "sdk_tree_count": summary.get("sdk_tree_count", 0),
            "detected_tree_count": summary.get("detected_tree_count", len(tree_geojson["features"])),
            "merged_tree_count": summary.get("merged_tree_count", 0),
            "ground_material_count": summary.get("ground_material_count", 0),
            "has_merged_grid_without": bool(summary.get("has_merged_grid_without")),
            "has_merged_grid_with": bool(summary.get("has_merged_grid_with")),
            "vegetation_summary": {
                "sdk_tree_count": summary.get("sdk_tree_count", 0),
                "detected_tree_count": summary.get("detected_tree_count", len(tree_geojson["features"])),
                "merged_tree_count": summary.get("merged_tree_count", 0),
                "duplicate_count": summary.get("duplicate_count", 0),
                "ground_material_count": summary.get("ground_material_count", 0),
            },
            "utci_summary": summary,
            "visualization_paths": visualization_paths,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{stage} failed: {exc}") from exc


@app.post("/analyze-route")
def analyze_route(request: AnalyzeRouteRequest) -> dict[str, Any]:
    """Generate, score, visualize, and return walking route alternatives."""
    stage = "starting route analysis"
    try:
        artifacts = _resolve_analysis_artifacts()
        detected_trees_path = artifacts["detected_trees"]
        utci_grid_path = artifacts["utci_grid"]
        utci_summary_path = artifacts["utci_summary"]
        routes_output_path = OUTPUTS_DIR / "scored_routes.geojson"
        route_demo_path = OUTPUTS_DIR / "route_demo.json"
        route_map_path = OUTPUTS_DIR / "coolrun_routes_map.html"

        metadata = _load_vienna_metadata()
        image_bbox = _image_bbox_from_metadata(metadata)
        warnings = []

        stage = "requesting OpenRouteService walking alternatives"
        route_geojson = request_walking_routes(
            start_lon=request.start_lon,
            start_lat=request.start_lat,
            end_lon=request.end_lon,
            end_lat=request.end_lat,
            alternatives=3,
        )

        stage = "checking existing UTCI outputs"
        utci_available, utci_summary, existing_warnings = _utci_grid_available(
            utci_grid_path,
            utci_summary_path,
        )
        utci_bbox = _utci_bbox_from_summary(utci_summary) or image_bbox
        warnings.extend(existing_warnings)
        if not utci_available:
            warnings.append(
                "Route optimization skipped a second Infrared run. Run the selected-area UTCI stage first, "
                "or continue with distance and detected tree density."
            )

        stage = "scoring route alternatives"
        scored = score_route_features(
            route_features=route_geojson.get("features", []),
            tree_geojson_path=str(detected_trees_path),
            utci_grid_path=str(utci_grid_path) if utci_available else None,
            center_lon=metadata["center"]["lon"] if metadata else DEFAULT_CENTER_LON,
            center_lat=metadata["center"]["lat"] if metadata else DEFAULT_CENTER_LAT,
            zoom=metadata["zoom"] if metadata else DEFAULT_MAPBOX_ZOOM,
            image_bbox=utci_bbox,
            tree_search_radius_m=20,
        )
        if utci_available and not scored["utci_available"]:
            warnings.append(
                "A finite UTCI grid exists, but none of the sampled route points overlap valid UTCI cells. "
                "Run the UTCI stage for the currently selected start/end area before route optimization."
            )

        scored_routes_geojson = _routes_to_feature_collection(scored)
        _write_json(routes_output_path, scored_routes_geojson)

        stage = "saving route visualization map"
        tree_geojson = json.loads(detected_trees_path.read_text(encoding="utf-8"))
        save_routes_map(
            scored_routes=scored_routes_geojson,
            tree_geojson=tree_geojson,
            output_path=str(route_map_path),
            utci_summary=utci_summary,
        )

        route_categories = _route_categories(scored)
        public_routes = [
            route_categories["shortest"],
            route_categories["coolest"],
            route_categories["balanced"],
        ]
        selected = scored["selected"]
        recommended_route_id = selected.get("coolest_route")
        recommended_route = route_categories["coolest"]
        distances = [route["distance_m"] for route in scored["routes"]]
        tree_densities = [route.get("tree_density_per_km", 0) for route in scored["routes"]]
        utci_values = [
            route["average_utci"]
            for route in scored["routes"]
            if route["average_utci"] is not None
        ]
        coolrun_scores = [route["coolrun_score"] for route in scored["routes"]]

        response = {
            "utci_available": scored["utci_available"],
            "time_period": {"date": request.date, "hour": request.hour},
            "vegetation_summary": {
                "sdk_tree_count": (utci_summary or {}).get("sdk_tree_count", 0),
                "detected_tree_count": (utci_summary or {}).get("detected_tree_count", scored["tree_count"]),
                "merged_tree_count": (utci_summary or {}).get("merged_tree_count", 0),
                "duplicate_count": (utci_summary or {}).get("duplicate_count", 0),
                "ground_material_count": (utci_summary or {}).get("ground_material_count", 0),
            },
            "route_categories": route_categories,
            "routes": public_routes,
            "recommended": "coolest",
            "recommended_route_name": recommended_route["name"] if recommended_route else None,
            "reasoning": (
                "The coolest route is recommended because it has the best combination of tree coverage, heat-stress reduction, and route comfort."
                if scored["utci_available"]
                else "UTCI simulation is not available for this run. Recommendation is based on tree density, shade continuity, and route distance."
            ),
            "summary": {
                "route_count": len(scored["routes"]),
                "tree_count": scored["tree_count"],
                "utci_available": scored["utci_available"],
                "min_distance_m": min(distances) if distances else None,
                "max_tree_density_per_km": max(tree_densities) if tree_densities else None,
                "min_average_utci": min(utci_values) if utci_values else None,
                "max_coolrun_score": max(coolrun_scores) if coolrun_scores else None,
                "selected": scored["selected"],
            },
            "route_geometries": scored_routes_geojson,
            "paths": {
                "detected_trees_geojson": _relative_path(detected_trees_path),
                "utci_grid": _relative_path(utci_grid_path) if utci_grid_path.exists() else None,
                "scored_routes_geojson": _relative_path(routes_output_path),
                "route_map_html": _relative_path(route_map_path),
            },
            "warnings": warnings,
        }

        _write_json(route_demo_path, response)
        return response
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"{stage} failed: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{stage} failed: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{stage} failed: {exc}") from exc


@app.post("/score-routes")
def score_routes(request: ScoreRoutesRequest) -> dict[str, Any]:
    """Generate and score walking route alternatives."""
    stage = "starting route scoring"
    try:
        run_dir = _resolve_run_dir(request.run_id)
        detected_trees_path = run_dir / "detected_trees.geojson"
        utci_grid_path = run_dir / "utci_with_trees.npy"
        routes_output_path = run_dir / "scored_routes.geojson"

        if not detected_trees_path.exists():
            raise FileNotFoundError(
                f"Missing detected trees for route scoring: {detected_trees_path}"
            )

        stage = "requesting OpenRouteService walking alternatives"
        route_geojson = request_walking_routes(
            start_lon=request.start_lon,
            start_lat=request.start_lat,
            end_lon=request.end_lon,
            end_lat=request.end_lat,
            alternatives=3,
        )

        stage = "scoring route alternatives"
        scored = score_route_features(
            route_features=route_geojson.get("features", []),
            tree_geojson_path=str(detected_trees_path),
            utci_grid_path=str(utci_grid_path) if utci_grid_path.exists() else None,
            center_lon=request.center_lon,
            center_lat=request.center_lat,
            zoom=request.zoom,
        )

        scored_routes_geojson = _routes_to_feature_collection(scored)
        _write_json(routes_output_path, scored_routes_geojson)
        selected_routes = _selected_routes(scored)

        return {
            "run_id": run_dir.name,
            "selected": scored["selected"],
            "selected_routes": selected_routes,
            "utci_available": scored["utci_available"],
            "tree_count": scored["tree_count"],
            "routes": scored["routes"],
            "scored_routes_geojson": _relative_path(routes_output_path),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{stage} failed: {exc}") from exc


@app.get("/")
def serve_frontend_index() -> FileResponse:
    """Serve the public frontend when FastAPI is deployed as a single app."""
    index_path = FRONTEND_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="frontend/index.html not found")
    return FileResponse(index_path)


@app.get("/styles.css")
def serve_frontend_styles() -> FileResponse:
    styles_path = FRONTEND_DIR / "styles.css"
    if not styles_path.exists():
        raise HTTPException(status_code=404, detail="frontend/styles.css not found")
    return FileResponse(styles_path)


@app.get("/app.js")
def serve_frontend_app() -> FileResponse:
    app_path = FRONTEND_DIR / "app.js"
    if not app_path.exists():
        raise HTTPException(status_code=404, detail="frontend/app.js not found")
    return FileResponse(app_path)


if FRONTEND_DIR.exists():
    app.mount("/frontend", StaticFiles(directory=FRONTEND_DIR), name="frontend")
