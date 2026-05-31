"""Infrared City UTCI test runner."""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any
import math
import calendar

from dotenv import load_dotenv

from backend.app.imagery.mapbox_geo import create_image_bounds_polygon

import matplotlib

matplotlib.use("Agg")


DEFAULT_CENTER_LAT = 48.18461202879178
DEFAULT_CENTER_LON = 16.400399172025814
DEFAULT_MAPBOX_ZOOM = 16
DEFAULT_IMAGE_WIDTH = 1024
DEFAULT_IMAGE_HEIGHT = 1024
INFRARED_IMPORT_CANDIDATES = ("infrared", "infrared_sdk")
EARTH_RADIUS_M = 6_371_000


def haversine_distance_m(
    lon_a: float,
    lat_a: float,
    lon_b: float,
    lat_b: float,
) -> float:
    """Calculate distance in meters between two lon/lat points."""
    lat_a_rad = math.radians(lat_a)
    lat_b_rad = math.radians(lat_b)
    delta_lat = math.radians(lat_b - lat_a)
    delta_lon = math.radians(lon_b - lon_a)
    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat_a_rad) * math.cos(lat_b_rad) * math.sin(delta_lon / 2) ** 2
    )
    return EARTH_RADIUS_M * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _load_infrared_modules() -> dict[str, Any]:
    """Try supported Infrared SDK import names and return loaded SDK objects."""
    failures = []

    for import_name in INFRARED_IMPORT_CANDIDATES:
        try:
            root_module = importlib.import_module(import_name)
        except ImportError as exc:
            failures.append(f"{import_name}: {exc}")
            continue

        try:
            analyses_types = importlib.import_module(f"{import_name}.analyses.types")
            models = importlib.import_module(f"{import_name}.models")
        except ImportError as exc:
            failures.append(
                f"{import_name}: root package imports, but expected SDK submodules are missing ({exc})"
            )
            continue

        required = {
            "InfraredClient": getattr(root_module, "InfraredClient", None),
            "AnalysesName": getattr(analyses_types, "AnalysesName", None),
            "UtciModelBaseRequest": getattr(analyses_types, "UtciModelBaseRequest", None),
            "UtciModelRequest": getattr(analyses_types, "UtciModelRequest", None),
            "Location": getattr(models, "Location", None),
            "TimePeriod": getattr(models, "TimePeriod", None),
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            failures.append(
                f"{import_name}: package imports, but required SDK objects are missing: {', '.join(missing)}"
            )
            continue

        return {
            "available": True,
            "import_name": import_name,
            "message": f"Infrared SDK imported successfully as '{import_name}'.",
            "failures": failures,
            **required,
        }

    return {
        "available": False,
        "import_name": None,
        "message": (
            "Infrared SDK package is not importable in the active backend Python environment. "
            "This usually means the backend is running with the wrong interpreter, or the package "
            "is missing from that environment. Tried import names: infrared, infrared_sdk."
        ),
        "failures": failures,
    }


def get_infrared_debug_info() -> dict[str, Any]:
    """Return safe Infrared SDK/environment diagnostics without exposing keys."""
    load_dotenv(override=True)
    sdk = _load_infrared_modules()
    api_key_present = bool(os.getenv("INFRARED_API_KEY"))

    if sdk["available"] and api_key_present:
        message = "Infrared SDK and INFRARED_API_KEY are available in the active backend environment."
    elif sdk["available"]:
        message = "Infrared SDK imports, but INFRARED_API_KEY is missing from the active backend environment."
    else:
        failure_detail = "; ".join(sdk.get("failures", []))
        message = sdk["message"]
        if failure_detail:
            message = f"{message} Import failures: {failure_detail}"

    return {
        "infrared_available": bool(sdk["available"]),
        "import_name": sdk["import_name"],
        "python_executable": sys.executable,
        "python_version": sys.version,
        "api_key_present": api_key_present,
        "message": message,
    }


def check_infrared_available() -> bool:
    """Return whether a supported Infrared SDK import is available."""
    return bool(_load_infrared_modules()["available"])


def create_infrared_client() -> Any:
    """Create an Infrared SDK client using INFRARED_API_KEY from .env."""
    sdk = _load_infrared_modules()
    if not sdk["available"]:
        raise RuntimeError(get_infrared_debug_info()["message"])

    api_key = load_infrared_api_key()
    return sdk["InfraredClient"](api_key=api_key)


def _features_to_dict(features: Any, source: str) -> dict[str, dict[str, Any]]:
    """Normalize SDK/list/dict vegetation features into a feature dictionary."""
    if not features:
        return {}

    if isinstance(features, dict):
        iterable = features.items()
    else:
        iterable = ((f"{source}_{index:06d}", feature) for index, feature in enumerate(features, start=1))

    normalized = {}
    for key, feature in iterable:
        if not isinstance(feature, dict):
            continue
        item = dict(feature)
        item.setdefault("type", "Feature")
        item.setdefault("properties", {})
        item["properties"] = dict(item["properties"])
        item["properties"].setdefault("source", source)
        normalized[str(key)] = item
    return normalized


def _feature_point_lonlat(feature: dict[str, Any]) -> tuple[float, float] | None:
    geometry = feature.get("geometry", {})
    coordinates = geometry.get("coordinates")
    if geometry.get("type") == "Point" and coordinates and len(coordinates) >= 2:
        return float(coordinates[0]), float(coordinates[1])
    if geometry.get("type") == "Polygon" and coordinates and coordinates[0]:
        ring = coordinates[0]
        lon = sum(float(point[0]) for point in ring) / len(ring)
        lat = sum(float(point[1]) for point in ring) / len(ring)
        return lon, lat
    return None


def fetch_sdk_context(client: Any, polygon: dict[str, Any]) -> dict[str, Any]:
    """Fetch reusable Infrared buildings, OSM vegetation, and ground materials."""
    buildings = None
    print("Fetching buildings...")
    try:
        area = client.buildings.get_area(polygon)
        buildings = getattr(area, "buildings", None)
    except Exception as exc:
        print(f"Fetching buildings failed, continuing without buildings: {exc}")
        buildings = None

    print("Fetching SDK vegetation...")
    area_veg = client.vegetation.get_area(polygon)
    print("Fetching ground materials...")
    area_gm = client.ground_materials.get_area(polygon)

    return {
        "buildings": buildings,
        "sdk_vegetation": getattr(area_veg, "features", {}),
        "sdk_tree_count": getattr(area_veg, "total_trees", None),
        "ground_materials": getattr(area_gm, "layers", {}),
        "ground_material_count": getattr(area_gm, "total_features", None),
    }


def merge_detected_and_sdk_vegetation(
    sdk_vegetation: Any,
    detected_tree_geojson: dict[str, Any] | None,
    deduplicate: bool = True,
    duplicate_distance_m: float = 5.0,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Merge Infrared/OSM vegetation and AI trees, dropping near duplicates.

    Deduplication compares detected tree centers to SDK/OSM tree centers. If a
    detected tree is within duplicate_distance_m of an SDK/OSM tree, the SDK/OSM
    tree is kept and the detected tree is counted as a duplicate. This avoids
    double-counting the same canopy when both sources identify one tree.
    """
    sdk_features = _features_to_dict(sdk_vegetation, "infrared_osm")
    detected_point_features = tree_geojson_to_infrared_vegetation(
        detected_tree_geojson or {"type": "FeatureCollection", "features": []}
    )
    detected_features = {}
    duplicate_count = 0

    sdk_points = [
        point
        for point in (_feature_point_lonlat(feature) for feature in sdk_features.values())
        if point is not None
    ]

    detected_tree_geojson = detected_tree_geojson or {"type": "FeatureCollection", "features": []}
    detected_items = detected_tree_geojson.get("features", [])

    for index, feature in enumerate(detected_items, start=1):
        if not isinstance(feature, dict):
            continue
        detected_point = _feature_point_lonlat(feature)
        if detected_point is None:
            continue

        is_duplicate = False
        if deduplicate:
            for sdk_lon, sdk_lat in sdk_points:
                if haversine_distance_m(detected_point[0], detected_point[1], sdk_lon, sdk_lat) <= duplicate_distance_m:
                    is_duplicate = True
                    break

        if is_duplicate:
            duplicate_count += 1
            continue

        item = dict(detected_point_features.get(f"detected_tree_{index:06d}", feature))
        item.setdefault("type", "Feature")
        item.setdefault("properties", {})
        item["properties"] = dict(item["properties"])
        item["properties"]["source"] = "ai_detected"
        item["properties"]["geometry_for_simulation"] = "ai_detected_point_tree"
        detected_features[f"ai_detected_tree_{index:06d}"] = item

    merged = {**sdk_features, **detected_features}
    summary = {
        "sdk_tree_count": len(sdk_features),
        "detected_tree_count": len(detected_items),
        "merged_tree_count": len(merged),
        "duplicate_count": duplicate_count,
        "ai_detected_point_tree_count": len(detected_features),
    }
    return merged, summary


def build_utci_payload(time_period: dict[str, Any]) -> dict[str, Any]:
    """Normalize frontend UTCI date/hour into an SDK-friendly time window."""
    date_value = str(time_period.get("date", "2026-07-15"))
    hour = int(time_period.get("hour", 15))
    if not 0 <= hour <= 23:
        raise ValueError("hour must be between 0 and 23.")

    month = int(date_value[5:7])
    selected_day = int(date_value[8:10])
    _, month_days = calendar.monthrange(2024, month)
    selected_day = min(max(selected_day, 1), month_days)
    # The SDK docs model TimePeriod as a recurring weather-data filter, not a
    # single timestamp. UTCI needs enough weather records, so use the selected
    # month and a narrow hour band around the selected hour.
    start_hour = max(hour - 1, 0)
    end_hour = min(hour + 1, 23)
    if end_hour <= start_hour:
        end_hour = min(start_hour + 1, 23)
    return {
        "date": date_value,
        "hour": hour,
        "selected_day": selected_day,
        "sdk_time_period": {
            "start_month": month,
            "start_day": 1,
            "start_hour": start_hour,
            "end_month": month,
            "end_day": month_days,
            "end_hour": end_hour,
        },
    }


def _limit_vegetation_for_live_utci(
    vegetation: Any,
    polygon: dict[str, Any],
    max_features: int = 350,
) -> dict[str, dict[str, Any]]:
    """Keep live UTCI vegetation payload bounded in dense areas.

    Dense parks can return thousands of SDK vegetation features. The public demo
    only needs a stable merged-vegetation UTCI run, so it keeps all vegetation
    for saved GeoJSON/display but sends a deterministic, spatially spread subset
    to the live thermal-comfort job when the layer is very large.
    """
    normalized = _features_to_dict(vegetation, "infrared_osm")
    if len(normalized) <= max_features:
        return normalized

    center_lon, center_lat = _polygon_center(polygon)
    items = []
    for key, feature in normalized.items():
        point = _feature_point_lonlat(feature)
        if point is None:
            continue
        distance = haversine_distance_m(point[0], point[1], center_lon, center_lat)
        source = feature.get("properties", {}).get("source")
        source_priority = 0 if source == "ai_detected" else 1
        items.append((source_priority, distance, key, feature))

    items.sort(key=lambda item: (item[0], item[1], item[2]))
    limited = {key: feature for _priority, _distance, key, feature in items[:max_features]}
    return limited


def _ground_materials_for_run(context: dict[str, Any]) -> dict[str, Any]:
    """Return ground materials in the SDK cookbook's conservative format."""
    known_materials = {"asphalt", "concrete", "soil", "vegetation", "water"}
    count = context.get("ground_material_count") or 0
    if count > 5000:
        return {}
    layers = context.get("ground_materials") or {}
    return {
        material: layer
        for material, layer in layers.items()
        if str(material).lower() in known_materials
    }


def _run_area_and_wait_with_retry(
    client: Any,
    payload: Any,
    polygon: dict[str, Any],
    buildings: Any,
    vegetation: Any,
    ground_materials: Any,
    label: str,
) -> Any:
    """Run an Infrared area analysis with lighter retries for gateway errors."""
    sdk_runtime = importlib.import_module("infrared_sdk.sdk")
    poll_until_complete = getattr(sdk_runtime, "_poll_until_complete")
    attempts = [
        {
            "name": "full context",
            "buildings": buildings,
            "vegetation": vegetation,
            "ground_materials": ground_materials,
        },
        {
            "name": "without ground materials",
            "buildings": buildings,
            "vegetation": vegetation,
            "ground_materials": {},
        },
        {
            "name": "without buildings or ground materials",
            "buildings": None,
            "vegetation": vegetation,
            "ground_materials": {},
        },
    ]
    last_error = None

    for attempt in attempts:
        try:
            if attempt["name"] != "full context":
                print(f"Retrying Infrared {label} run {attempt['name']} after gateway failure.")
            schedule = client.run_area(
                payload,
                polygon,
                buildings=attempt["buildings"],
                vegetation=attempt["vegetation"],
                ground_materials=attempt["ground_materials"],
                max_workers=1,
            )
            failed_submissions = tuple(getattr(schedule, "failed_submissions", ()) or ())
            if failed_submissions:
                raise RuntimeError(
                    "Infrared failed to submit thermal-comfort-index tile job(s): "
                    + ", ".join(str(tile_id) for tile_id in failed_submissions)
                )

            poll_until_complete(
                [schedule],
                api_key=client._api_key_value(),
                base_url=client.base_url,
                area_timeout=120,
                on_progress=None,
            )
            return client.merge_area_jobs(schedule)
        except Exception as exc:
            last_error = exc
            message = str(exc)
            retryable_gateway_error = (
                "502" in message
                or "failed to submit thermal-comfort-index tile job" in message.lower()
                or "Failed to submit thermal-comfort-index job" in message
                or "Bad Gateway" in message
            )
            if not retryable_gateway_error:
                raise

    raise last_error


def _load_detected_tree_geojson(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {"type": "FeatureCollection", "features": []}
    tree_path = Path(path)
    if not tree_path.exists():
        return {"type": "FeatureCollection", "features": []}
    return json.loads(tree_path.read_text(encoding="utf-8"))


def _save_feature_collection(path: Path, features: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = _features_to_dict(features, "infrared_osm")
    path.write_text(
        json.dumps({"type": "FeatureCollection", "features": list(normalized.values())}, indent=2),
        encoding="utf-8",
    )
    return str(path)


def run_utci_for_polygon(
    polygon: dict[str, Any],
    time_period: dict[str, Any],
    vegetation_mode: str = "merged",
    detected_tree_geojson_path: str | Path | None = None,
    output_dir: str | Path = "outputs",
    run_baseline: bool = False,
) -> dict[str, Any]:
    """Run Infrared UTCI for a polygon with selectable vegetation mode."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    summary_path = output_path / "utci_summary.json"
    for stale_name in (
        "utci_without_trees.npy",
        "utci_with_merged_trees.npy",
        "utci_with_trees.npy",
    ):
        stale_path = output_path / stale_name
        if stale_path.exists():
            stale_path.unlink()

    failed_step = "build_time_period"
    debug = get_infrared_debug_info()
    detected_count = 0
    merge_summary = {
        "sdk_tree_count": 0,
        "detected_tree_count": 0,
        "merged_tree_count": 0,
        "duplicate_count": 0,
        "ai_detected_point_tree_count": 0,
    }
    context = {"ground_material_count": 0}
    result_without = None
    result_with = None
    normalized_time = None
    preview_summary = None
    live_vegetation_count_before_limit = 0
    live_vegetation_count = 0
    weather_file_id = None
    weather_data_count = 0

    def failure_summary(
        reason: str,
        *,
        attempted: bool,
        step: str,
        error_message: str | None = None,
        notes: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "infrared_debug": debug,
            "utci_attempted": attempted,
            "utci_available": False,
            "status": "utci_failed" if attempted else "utci_not_attempted",
            "is_placeholder": True,
            "failed_step": step,
            "error_message": error_message or reason,
            "reason": reason,
            "time_period": normalized_time or time_period,
            "vegetation_mode": vegetation_mode,
            "run_baseline": run_baseline,
            "sdk_tree_count": merge_summary.get("sdk_tree_count", 0),
            "detected_tree_count": detected_count,
            "merged_tree_count": merge_summary.get("merged_tree_count", 0),
            "duplicate_count": merge_summary.get("duplicate_count", 0),
            "ai_detected_point_tree_count": merge_summary.get("ai_detected_point_tree_count", 0),
            "live_vegetation_count_before_limit": live_vegetation_count_before_limit,
            "live_vegetation_count": live_vegetation_count,
            "ground_material_count": context.get("ground_material_count") or 0,
            "weather_file_id": weather_file_id,
            "weather_data_count": weather_data_count,
            "utci_preview": preview_summary,
            "has_merged_grid_without": getattr(result_without, "merged_grid", None) is not None,
            "has_merged_grid_with": getattr(result_with, "merged_grid", None) is not None,
            "mean_utci_without": None,
            "mean_utci_with_merged": None,
            "mean_cooling_effect": None,
            "max_cooling_effect": None,
            "notes": notes or ["UTCI did not complete. Route scoring can continue without UTCI."],
        }

    try:
        print("Building UTCI payload...")
        normalized_time = build_utci_payload(time_period)
    except Exception as exc:
        summary = failure_summary(
            "Failed to build Infrared UTCI time period from date/hour.",
            attempted=False,
            step="build_time_period",
            error_message=str(exc),
        )
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return {"summary": summary, "summary_path": str(summary_path)}

    print("Loading detected trees...")
    debug = get_infrared_debug_info()
    detected_geojson = _load_detected_tree_geojson(detected_tree_geojson_path)
    detected_vegetation = tree_geojson_to_infrared_vegetation(detected_geojson)
    detected_count = len(detected_geojson.get("features", []))
    merge_summary["detected_tree_count"] = detected_count

    if not debug["infrared_available"]:
        summary = failure_summary(
            debug["message"],
            attempted=False,
            step="check_infrared_available",
            notes=[debug["message"]],
        )
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return {"summary": summary, "summary_path": str(summary_path)}

    try:
        failed_step = "create_infrared_client"
        print("Creating Infrared client...")
        client = create_infrared_client()
    except Exception as exc:
        summary = failure_summary(
            str(exc),
            attempted=False,
            step="create_infrared_client",
            error_message=str(exc),
            notes=["Infrared client could not be created."],
        )
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return {"summary": summary, "summary_path": str(summary_path)}

    sdk = _load_infrared_modules()
    TimePeriod = sdk["TimePeriod"]
    Location = sdk["Location"]
    AnalysesName = sdk["AnalysesName"]
    UtciModelBaseRequest = sdk["UtciModelBaseRequest"]
    UtciModelRequest = sdk["UtciModelRequest"]
    tp = normalized_time["sdk_time_period"]

    try:
        with client:
            failed_step = "preview_area"
            print("Previewing UTCI area cost...")
            try:
                preview = client.preview_area(polygon, analysis_type="thermal-comfort-index")
                preview_summary = {
                    "tile_count": getattr(preview, "tile_count", None),
                    "estimated_time_s": getattr(preview, "estimated_time_s", None),
                    "estimated_cost_tokens": getattr(preview, "estimated_cost_tokens", None),
                }
            except Exception as exc:
                print(f"Previewing UTCI area cost failed, continuing without preview: {exc}")
                preview_summary = {"error": str(exc)}

            failed_step = "fetch_sdk_context"
            context = fetch_sdk_context(client, polygon)
            sdk_vegetation = context["sdk_vegetation"]
            failed_step = "merge_vegetation"
            print("Merging vegetation...")
            merged_vegetation, merge_summary = merge_detected_and_sdk_vegetation(
                sdk_vegetation=sdk_vegetation,
                detected_tree_geojson=detected_geojson,
            )
            _save_feature_collection(output_path / "sdk_vegetation.geojson", sdk_vegetation)
            _save_feature_collection(output_path / "merged_vegetation.geojson", merged_vegetation)
            (output_path / "vegetation_merge_summary.json").write_text(
                json.dumps({**merge_summary, "ground_material_count": context["ground_material_count"]}, indent=2),
                encoding="utf-8",
            )

            if vegetation_mode == "none":
                selected_vegetation = None
            elif vegetation_mode == "sdk_only":
                selected_vegetation = sdk_vegetation
            elif vegetation_mode == "detected_only":
                selected_vegetation = detected_vegetation
            elif vegetation_mode == "merged":
                selected_vegetation = merged_vegetation
            else:
                raise ValueError("vegetation_mode must be one of: none, sdk_only, detected_only, merged.")

            live_vegetation_count_before_limit = (
                len(_features_to_dict(selected_vegetation, "live_utci"))
                if selected_vegetation
                else 0
            )
            if selected_vegetation:
                selected_vegetation = _limit_vegetation_for_live_utci(
                    selected_vegetation,
                    polygon,
                )
            live_vegetation_count = len(selected_vegetation or {})

            failed_step = "build_time_period"
            sdk_time_period = TimePeriod(**tp)
            center_lon, center_lat = _polygon_center(polygon)
            failed_step = "fetch_weather"
            stations = client.weather.get_weather_file_from_location(
                lat=center_lat,
                lon=center_lon,
                radius=50,
            )
            if not stations:
                raise RuntimeError("No Infrared weather stations found for the selected polygon.")
            weather_file_id = stations[0].get("uuid") or stations[0].get("identifier")
            if not weather_file_id:
                raise RuntimeError(f"Weather station response has no identifier: {stations[0]}")
            weather_data = client.weather.filter_weather_data(
                identifier=weather_file_id,
                time_period=sdk_time_period,
            )
            if not weather_data:
                raise RuntimeError(
                    "Infrared weather filtering returned no data for the selected UTCI time window."
                )
            weather_data_count = len(weather_data)
            failed_step = "build_utci_payload"
            print("Building UTCI payload...")
            payload = UtciModelRequest.from_weatherfile_payload(
                payload=UtciModelBaseRequest(
                    analysis_type=AnalysesName.thermal_comfort_index,
                ),
                location=Location(latitude=center_lat, longitude=center_lon),
                time_period=sdk_time_period,
                weather_data=weather_data,
            )
            ground_materials_for_run = _ground_materials_for_run(context)
            if run_baseline:
                failed_step = "run_utci_without_vegetation"
                print("Running UTCI without vegetation...")
                result_without = _run_area_and_wait_with_retry(
                    client=client,
                    payload=payload,
                    polygon=polygon,
                    buildings=context["buildings"],
                    vegetation=None,
                    ground_materials=ground_materials_for_run,
                    label="without vegetation",
                )
            failed_step = "run_utci_with_merged_vegetation"
            print("Running UTCI with merged vegetation...")
            result_with = _run_area_and_wait_with_retry(
                client=client,
                payload=payload,
                polygon=polygon,
                buildings=context["buildings"],
                vegetation=selected_vegetation,
                ground_materials=ground_materials_for_run,
                label="with vegetation",
            )

        failed_step = "check_merged_grid"
        print("Checking result.merged_grid...")
        with_grid = _result_grid_as_array(result_with)
        without_grid = _result_grid_as_array(result_without) if result_without is not None else None
        import numpy as np

        if not np.isfinite(with_grid).any():
            raise ValueError("Infrared SDK returned a with-vegetation UTCI grid, but it contains no finite values.")
        if without_grid is not None and not np.isfinite(without_grid).any():
            raise ValueError("Infrared SDK returned a baseline UTCI grid, but it contains no finite values.")

        np.save(output_path / "utci_with_merged_trees.npy", with_grid)
        # Keep compatibility with existing route scorer.
        np.save(output_path / "utci_with_trees.npy", with_grid)
        if without_grid is not None:
            np.save(output_path / "utci_without_trees.npy", without_grid)
            cooling_effect = without_grid - with_grid
            mean_utci_without = float(np.nanmean(without_grid))
            mean_cooling_effect = float(np.nanmean(cooling_effect))
            max_cooling_effect = float(np.nanmax(cooling_effect))
            max_abs_utci_difference = float(np.nanmax(np.abs(cooling_effect)))
        else:
            mean_utci_without = None
            mean_cooling_effect = None
            max_cooling_effect = None
            max_abs_utci_difference = None
        summary = {
            "infrared_debug": debug,
            "utci_attempted": True,
            "utci_available": True,
            "status": "completed",
            "is_placeholder": False,
            "failed_step": None,
            "error_message": None,
            "time_period": normalized_time,
            "vegetation_mode": vegetation_mode,
            "run_baseline": run_baseline,
            "utci_preview": preview_summary,
            "sdk_tree_count": merge_summary["sdk_tree_count"],
            "detected_tree_count": merge_summary["detected_tree_count"],
            "merged_tree_count": merge_summary["merged_tree_count"],
            "duplicate_count": merge_summary["duplicate_count"],
            "ai_detected_point_tree_count": merge_summary.get("ai_detected_point_tree_count", 0),
            "live_vegetation_count_before_limit": live_vegetation_count_before_limit,
            "live_vegetation_count": live_vegetation_count,
            "ground_material_count": context["ground_material_count"],
            "weather_file_id": weather_file_id,
            "weather_data_count": weather_data_count,
            "has_merged_grid_without": getattr(result_without, "merged_grid", None) is not None,
            "has_merged_grid_with": getattr(result_with, "merged_grid", None) is not None,
            "mean_utci_without": mean_utci_without,
            "mean_utci_with_merged": float(np.nanmean(with_grid)),
            "mean_utci_with": float(np.nanmean(with_grid)),
            "mean_cooling_effect": mean_cooling_effect,
            "max_cooling_effect": max_cooling_effect,
            "max_abs_utci_difference": max_abs_utci_difference,
            "notes": [
                "UTCI completed using Infrared SDK.",
                "Interactive demo mode runs the merged-vegetation UTCI scenario only by default for speed."
                if not run_baseline
                else "Baseline and merged-vegetation UTCI scenarios completed.",
            ],
        }
    except Exception as exc:
        summary = failure_summary(
            f"SDK call failed at {failed_step}: {exc}",
            attempted=True,
            step=failed_step,
            error_message=str(exc),
            notes=["UTCI failed after SDK import. Route scoring can continue without UTCI."],
        )

    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return {"summary": summary, "summary_path": str(summary_path)}


def _polygon_center(polygon: dict[str, Any]) -> tuple[float, float]:
    ring = polygon.get("coordinates", [[]])[0]
    if not ring:
        return DEFAULT_CENTER_LON, DEFAULT_CENTER_LAT
    lon = sum(float(point[0]) for point in ring) / len(ring)
    lat = sum(float(point[1]) for point in ring) / len(ring)
    return lon, lat


def load_infrared_api_key() -> str:
    """Load the Infrared API key from .env or environment variables."""
    load_dotenv(override=True)
    api_key = os.getenv("INFRARED_API_KEY")

    if not api_key:
        raise ValueError(
            "INFRARED_API_KEY is missing from the active backend environment. "
            "Add it to .env or environment variables. The key value is never returned by debug endpoints."
        )

    return api_key


def create_test_polygon(
    center_lon: float = DEFAULT_CENTER_LON,
    center_lat: float = DEFAULT_CENTER_LAT,
    half_size_degrees: float = 0.0015,
) -> dict[str, Any]:
    """Create a small square GeoJSON polygon around the Mapbox image center."""
    west = center_lon - half_size_degrees
    east = center_lon + half_size_degrees
    south = center_lat - half_size_degrees
    north = center_lat + half_size_degrees

    return {
        "type": "Polygon",
        "coordinates": [
            [
                [west, south],
                [east, south],
                [east, north],
                [west, north],
                [west, south],
            ]
        ],
    }


def load_tree_geojson(path: str) -> dict[str, Any]:
    """Load detected tree GeoJSON from disk."""
    import json

    geojson_path = Path(path)
    if not geojson_path.exists():
        raise FileNotFoundError(
            f"Missing tree GeoJSON: {geojson_path}. Run notebook 03 first."
        )

    return json.loads(geojson_path.read_text(encoding="utf-8"))


def create_canopy_polygon(
    lon: float,
    lat: float,
    radius_m: float,
    segments: int = 24,
) -> dict[str, Any]:
    """Create an approximate circular canopy polygon around a tree point."""
    lat_m = 111_320
    lon_m = 111_320 * math.cos(math.radians(lat))

    if lon_m == 0:
        raise ValueError("Cannot create canopy polygon at this latitude.")

    coordinates = []
    for index in range(segments):
        angle = 2 * math.pi * index / segments
        dx_m = math.cos(angle) * radius_m
        dy_m = math.sin(angle) * radius_m

        coordinates.append(
            [
                lon + dx_m / lon_m,
                lat + dy_m / lat_m,
            ]
        )

    coordinates.append(coordinates[0])

    return {
        "type": "Polygon",
        "coordinates": [coordinates],
    }


def tree_geojson_to_infrared_vegetation(
    tree_geojson: dict[str, Any],
    min_canopy_radius_m: float = 3.0,
) -> dict[str, dict[str, Any]]:
    """Convert detected trees into SDK-compatible point vegetation."""
    features = tree_geojson.get("features", [])
    vegetation = {}

    for index, feature in enumerate(features, start=1):
        geometry = feature.get("geometry", {})
        if geometry.get("type") != "Point":
            continue

        lon, lat = geometry.get("coordinates", [None, None])[:2]
        if lon is None or lat is None:
            continue

        source_properties = dict(feature.get("properties", {}))
        properties = {
            "source": source_properties.get("source", "roboflow"),
            "natural": "tree",
            "class": source_properties.get("class", "tree"),
            "confidence": source_properties.get("confidence"),
            "geometry_for_simulation": "infrared_point_tree",
        }
        properties = {key: value for key, value in properties.items() if value is not None}

        vegetation[f"detected_tree_{index:06d}"] = {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [float(lon), float(lat)],
            },
            "properties": properties,
        }

    return vegetation


def tree_geojson_to_canopy_polygons(
    tree_geojson: dict[str, Any],
    min_canopy_radius_m: float = 3.0,
) -> dict[str, dict[str, Any]]:
    """Convert detected tree points into canopy polygons for visualization only."""
    point_vegetation = tree_geojson_to_infrared_vegetation(
        tree_geojson,
        min_canopy_radius_m=min_canopy_radius_m,
    )
    polygon_vegetation = {}

    for tree_id, feature in point_vegetation.items():
        lon, lat = feature["geometry"]["coordinates"][:2]
        properties = dict(feature.get("properties", {}))
        radius_m = float(properties.get("canopy_radius_m", min_canopy_radius_m))
        properties["geometry_for_visualization"] = "canopy_polygon"

        polygon_vegetation[tree_id] = {
            "type": "Feature",
            "geometry": create_canopy_polygon(
                lon=float(lon),
                lat=float(lat),
                radius_m=radius_m,
            ),
            "properties": properties,
        }

    return polygon_vegetation


def save_vegetation_geojson(
    vegetation: dict[str, dict[str, Any]],
    output_path: str,
) -> str:
    """Save Infrared vegetation features as a GeoJSON FeatureCollection."""
    import json

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    feature_collection = {
        "type": "FeatureCollection",
        "features": list(vegetation.values()),
    }
    path.write_text(json.dumps(feature_collection, indent=2), encoding="utf-8")

    return str(path)


def average_grid_value(result: Any) -> float | None:
    """Return the average value from an Infrared result grid, if available."""
    grid = getattr(result, "merged_grid", None)
    if grid is None:
        return None

    try:
        import numpy as np

        return float(np.nanmean(grid))
    except Exception:
        values = []
        for row in grid:
            for value in row:
                if value is not None:
                    values.append(float(value))
        return sum(values) / len(values) if values else None


def _result_grid_as_array(result: Any) -> Any:
    """Convert an Infrared result merged grid into a numpy array."""
    import numpy as np

    grid = getattr(result, "merged_grid", None)
    if grid is None:
        raise ValueError("Infrared result has no merged_grid.")

    return np.asarray(grid, dtype=float)


def prepare_utci_payload_summary(
    polygon: dict[str, Any],
    vegetation: dict[str, dict[str, Any]],
    vegetation_geometry: str = "points",
) -> dict[str, Any]:
    """Return a lightweight summary of the UTCI test inputs."""
    return {
        "analysis": "thermal_comfort_index",
        "polygon": polygon,
        "without_trees": {
            "vegetation_feature_count": 0,
        },
        "with_trees": {
            "vegetation_feature_count": len(vegetation),
            "vegetation_geometry": vegetation_geometry,
            "geometry_types": sorted(
                {
                    feature.get("geometry", {}).get("type", "unknown")
                    for feature in vegetation.values()
                }
            ),
        },
        "todos": [
            "Confirm the Infrared account has API access.",
            "Confirm the desired UTCI time period for the hackathon demo.",
            "Confirm whether detected tree point features need canopy/radius properties.",
        ],
    }


def save_utci_heatmap(result: Any, output_path: str, title: str) -> str:
    """Save an Infrared UTCI result grid as an interactive Plotly HTML heatmap."""
    import plotly.graph_objects as go

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    fig = go.Figure(
        go.Heatmap(
            z=result.merged_grid,
            colorscale="RdBu_r",
            zmin=getattr(result, "min_legend", None),
            zmax=getattr(result, "max_legend", None),
            colorbar={"title": "UTCI C"},
        )
    )
    fig.update_layout(
        title=title,
        width=900,
        height=900,
        template="plotly_white",
        yaxis={"scaleanchor": "x", "scaleratio": 1},
    )
    fig.write_html(output_file, include_plotlyjs=True)
    return str(output_file)


def save_utci_grid_image(result: Any, output_path: str, title: str) -> str:
    """Save an Infrared UTCI result grid as a PNG image."""
    import matplotlib.pyplot as plt

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    plt.figure(figsize=(8, 7))
    image = plt.imshow(
        result.merged_grid,
        cmap="RdBu_r",
        vmin=getattr(result, "min_legend", None),
        vmax=getattr(result, "max_legend", None),
    )
    plt.colorbar(image, label="UTCI C")
    plt.title(title)
    plt.axis("off")
    plt.tight_layout()
    plt.savefig(output_file, dpi=160, bbox_inches="tight")
    plt.close()

    return str(output_file)


def save_utci_outputs(
    result: dict[str, Any],
    output_dir: str,
) -> dict[str, Any]:
    """Save UTCI grids and a summary JSON after an Infrared comparison run."""
    import json
    import numpy as np

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    without_grid_path = output_path / "utci_without_trees.npy"
    with_grid_path = output_path / "utci_with_trees.npy"
    summary_path = output_path / "utci_summary.json"

    vegetation_without = 0
    vegetation_with = result.get("detected_tree_count")
    if vegetation_with is None:
        vegetation_with = result.get("payload_summary", {}).get("with_trees", {}).get(
            "vegetation_feature_count"
        )

    summary = {
        "status": result.get("status"),
        "is_placeholder": result.get("status") != "completed",
        "vegetation_feature_count_without": vegetation_without,
        "vegetation_feature_count_with": vegetation_with,
        "vegetation_geometry": result.get("vegetation_geometry"),
        "input_metadata": result.get("input_metadata"),
        "simulation_polygon": result.get("simulation_polygon"),
        "mean_utci_without": None,
        "mean_utci_with": None,
        "mean_cooling_effect": None,
        "max_cooling_effect": None,
        "max_abs_utci_difference": None,
        "utci_without_trees_npy": None,
        "utci_with_trees_npy": None,
    }

    if result.get("status") == "completed":
        without_grid = _result_grid_as_array(result["result_without_trees"])
        with_grid = _result_grid_as_array(result["result_with_trees"])

        np.save(without_grid_path, without_grid)
        np.save(with_grid_path, with_grid)

        cooling_effect = without_grid - with_grid

        summary.update(
            {
                "is_placeholder": False,
                "mean_utci_without": float(np.nanmean(without_grid)),
                "mean_utci_with": float(np.nanmean(with_grid)),
                "mean_cooling_effect": float(np.nanmean(cooling_effect)),
                "max_cooling_effect": float(np.nanmax(cooling_effect)),
                "max_abs_utci_difference": float(np.nanmax(np.abs(cooling_effect))),
                "utci_without_trees_npy": str(without_grid_path),
                "utci_with_trees_npy": str(with_grid_path),
            }
        )

    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    return {
        "summary": summary,
        "summary_path": str(summary_path),
        "utci_without_trees_npy": summary["utci_without_trees_npy"],
        "utci_with_trees_npy": summary["utci_with_trees_npy"],
    }


def run_utci_comparison(
    tree_geojson_path: str,
    center_lon: float = DEFAULT_CENTER_LON,
    center_lat: float = DEFAULT_CENTER_LAT,
    zoom: int = DEFAULT_MAPBOX_ZOOM,
    image_width: int = DEFAULT_IMAGE_WIDTH,
    image_height: int = DEFAULT_IMAGE_HEIGHT,
    run_live: bool = False,
    polygon: dict[str, Any] | None = None,
    vegetation_geometry: str = "points",
    input_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Prepare or run a simple Infrared UTCI comparison with detected trees."""
    tree_geojson = load_tree_geojson(tree_geojson_path)
    if vegetation_geometry == "points":
        vegetation = tree_geojson_to_infrared_vegetation(tree_geojson)
    elif vegetation_geometry == "canopy_polygons":
        vegetation = tree_geojson_to_canopy_polygons(tree_geojson)
        for feature in vegetation.values():
            feature.setdefault("properties", {})[
                "geometry_for_simulation"
            ] = "infrared_canopy_polygon"
    else:
        raise ValueError("vegetation_geometry must be 'points' or 'canopy_polygons'.")

    if polygon is None:
        polygon = create_image_bounds_polygon(
            center_lon=center_lon,
            center_lat=center_lat,
            zoom=zoom,
            width=image_width,
            height=image_height,
        )
    summary = prepare_utci_payload_summary(
        polygon,
        vegetation,
        vegetation_geometry=vegetation_geometry,
    )

    if not run_live:
        return {
            "status": "prepared_not_run",
            "message": "Payloads are prepared. Set run_live=True to run the Infrared SDK test.",
            "average_utci_without_trees": None,
            "average_utci_with_trees": None,
            "utci_difference": None,
            "payload_summary": summary,
            "vegetation_geometry": vegetation_geometry,
            "input_metadata": input_metadata,
            "simulation_polygon": polygon,
        }

    sdk = _load_infrared_modules()
    if not sdk["available"]:
        failure_detail = "; ".join(sdk.get("failures", []))
        message = sdk["message"]
        if failure_detail:
            message = f"{message} Import failures: {failure_detail}"
        raise RuntimeError(message)

    api_key = load_infrared_api_key()
    InfraredClient = sdk["InfraredClient"]
    AnalysesName = sdk["AnalysesName"]
    UtciModelBaseRequest = sdk["UtciModelBaseRequest"]
    UtciModelRequest = sdk["UtciModelRequest"]
    Location = sdk["Location"]
    TimePeriod = sdk["TimePeriod"]

    # TODO: Adjust this time window to match the final hackathon demo scenario.
    time_period = TimePeriod(
        start_month=7,
        start_day=1,
        start_hour=12,
        end_month=7,
        end_day=31,
        end_hour=16,
    )

    try:
        with InfraredClient(api_key=api_key) as client:
            area = client.buildings.get_area(polygon)
            area_ground_materials = client.ground_materials.get_area(polygon)

            # Pre-filter ground materials to avoid oversized requests on larger polygons.
            ground_materials = (
                area_ground_materials.layers
                if area_ground_materials.total_features <= 5000
                else {}
            )

            stations = client.weather.get_weather_file_from_location(
                lat=center_lat,
                lon=center_lon,
                radius=50,
            )
            if not stations:
                raise RuntimeError("No Infrared weather stations found for the test location.")

            weather_file_id = stations[0].get("identifier") or stations[0].get("uuid")
            if not weather_file_id:
                raise RuntimeError(f"Weather station response has no identifier: {stations[0]}")

            weather_data = client.weather.filter_weather_data(
                identifier=weather_file_id,
                time_period=time_period,
            )

            payload = UtciModelRequest.from_weatherfile_payload(
                payload=UtciModelBaseRequest(
                    analysis_type=AnalysesName.thermal_comfort_index,
                ),
                location=Location(latitude=center_lat, longitude=center_lon),
                time_period=time_period,
                weather_data=weather_data,
            )

            result_without_trees = client.run_area_and_wait(
                payload,
                polygon,
                buildings=area.buildings,
                vegetation=None,
                ground_materials=ground_materials,
            )
            result_with_trees = client.run_area_and_wait(
                payload,
                polygon,
                buildings=area.buildings,
                vegetation=vegetation,
                ground_materials=ground_materials,
            )
    except Exception as exc:
        raise RuntimeError(f"Infrared SDK call failed after successful import: {exc}") from exc

    avg_without = average_grid_value(result_without_trees)
    avg_with = average_grid_value(result_with_trees)
    difference = None
    if avg_without is not None and avg_with is not None:
        difference = avg_with - avg_without

    return {
        "status": "completed",
        "average_utci_without_trees": avg_without,
        "average_utci_with_trees": avg_with,
        "utci_difference": difference,
        "weather_file_id": weather_file_id,
        "building_count": getattr(area, "total_buildings", None),
        "ground_material_feature_count": getattr(area_ground_materials, "total_features", None),
        "detected_tree_count": len(vegetation),
        "result_without_trees": result_without_trees,
        "result_with_trees": result_with_trees,
        "payload_summary": summary,
        "vegetation_geometry": vegetation_geometry,
        "input_metadata": input_metadata,
        "simulation_polygon": polygon,
    }
