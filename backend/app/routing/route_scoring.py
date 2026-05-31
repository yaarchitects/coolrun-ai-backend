"""Route sampling and CoolRun scoring utilities."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from backend.app.imagery.mapbox_geo import lonlat_to_world_pixel
from backend.app.imagery.vienna_orthofoto import lonlat_to_image_pixel as vienna_lonlat_to_image_pixel
from backend.app.simulation.vegetation_builder import haversine_distance_m


DEFAULT_CENTER_LAT = 48.18461202879178
DEFAULT_CENTER_LON = 16.400399172025814
DEFAULT_MAPBOX_ZOOM = 16


def load_tree_points(tree_geojson_path: str) -> list[tuple[float, float]]:
    """Load detected tree point lon/lat coordinates."""
    path = Path(tree_geojson_path)
    if not path.exists():
        raise FileNotFoundError(f"Missing detected tree GeoJSON: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    points = []
    for feature in data.get("features", []):
        geometry = feature.get("geometry", {})
        if geometry.get("type") != "Point":
            continue
        lon, lat = geometry.get("coordinates", [None, None])[:2]
        if lon is not None and lat is not None:
            points.append((float(lon), float(lat)))

    return points


def route_distance_m(coordinates: list[list[float]]) -> float:
    """Calculate route distance from lon/lat coordinate list."""
    total = 0.0
    for first, second in zip(coordinates, coordinates[1:]):
        total += haversine_distance_m(first[0], first[1], second[0], second[1])
    return total


def interpolate_lonlat(
    start: list[float],
    end: list[float],
    fraction: float,
) -> tuple[float, float]:
    """Interpolate between two nearby lon/lat points."""
    lon = start[0] + (end[0] - start[0]) * fraction
    lat = start[1] + (end[1] - start[1]) * fraction
    return lon, lat


def sample_route_every_meters(
    coordinates: list[list[float]],
    interval_m: float = 25,
) -> list[tuple[float, float]]:
    """Sample route points approximately every interval_m meters."""
    if len(coordinates) < 2:
        return []

    samples = [(coordinates[0][0], coordinates[0][1])]
    distance_since_sample = 0.0

    for start, end in zip(coordinates, coordinates[1:]):
        segment_distance = haversine_distance_m(start[0], start[1], end[0], end[1])
        if segment_distance == 0:
            continue

        travelled_on_segment = 0.0
        while distance_since_sample + (segment_distance - travelled_on_segment) >= interval_m:
            remaining = interval_m - distance_since_sample
            travelled_on_segment += remaining
            fraction = travelled_on_segment / segment_distance
            samples.append(interpolate_lonlat(start, end, fraction))
            distance_since_sample = 0.0

        distance_since_sample += segment_distance - travelled_on_segment

    last = coordinates[-1]
    if samples[-1] != (last[0], last[1]):
        samples.append((last[0], last[1]))

    return samples


def nearby_tree_density(
    samples: list[tuple[float, float]],
    tree_points: list[tuple[float, float]],
    search_radius_m: float = 30,
) -> float:
    """Calculate average nearby tree count per sampled route point."""
    if not samples:
        return 0.0

    counts = []
    for sample_lon, sample_lat in samples:
        count = 0
        for tree_lon, tree_lat in tree_points:
            distance = haversine_distance_m(sample_lon, sample_lat, tree_lon, tree_lat)
            if distance <= search_radius_m:
                count += 1
        counts.append(count)

    return sum(counts) / len(counts)


def nearby_tree_count(
    samples: list[tuple[float, float]],
    tree_points: list[tuple[float, float]],
    search_radius_m: float = 20,
) -> int:
    """Count unique detected trees within search_radius_m of a sampled route."""
    nearby_indexes = set()

    for sample_lon, sample_lat in samples:
        for index, (tree_lon, tree_lat) in enumerate(tree_points):
            distance = haversine_distance_m(sample_lon, sample_lat, tree_lon, tree_lat)
            if distance <= search_radius_m:
                nearby_indexes.add(index)

    return len(nearby_indexes)


def nearby_tree_metrics(
    samples: list[tuple[float, float]],
    tree_points: list[tuple[float, float]],
    search_radius_m: float = 20,
) -> tuple[int, float, float]:
    """Return unique nearby trees, average tree density, and shade continuity.

    shade_continuity is a lightweight proxy: the share of sampled route points
    that have at least one detected tree within search_radius_m.
    """
    if not samples:
        return 0, 0.0, 0.0

    nearby_indexes = set()
    sample_counts = []
    shaded_samples = 0

    for sample_lon, sample_lat in samples:
        count = 0
        for index, (tree_lon, tree_lat) in enumerate(tree_points):
            distance = haversine_distance_m(sample_lon, sample_lat, tree_lon, tree_lat)
            if distance <= search_radius_m:
                nearby_indexes.add(index)
                count += 1
        if count:
            shaded_samples += 1
        sample_counts.append(count)

    tree_density = sum(sample_counts) / len(sample_counts)
    shade_continuity = shaded_samples / len(samples)
    return len(nearby_indexes), tree_density, shade_continuity


def lonlat_to_grid_index(
    lon: float,
    lat: float,
    center_lon: float,
    center_lat: float,
    zoom: int,
    grid_width: int,
    grid_height: int,
    image_bbox: tuple[float, float, float, float] | None = None,
) -> tuple[int, int] | None:
    """Convert lon/lat to nearest UTCI grid row/column."""
    if image_bbox is None:
        center_world_x, center_world_y = lonlat_to_world_pixel(center_lon, center_lat, zoom)
        point_world_x, point_world_y = lonlat_to_world_pixel(lon, lat, zoom)

        col = round(point_world_x - (center_world_x - grid_width / 2))
        row = round(point_world_y - (center_world_y - grid_height / 2))
    else:
        col_float, row_float = vienna_lonlat_to_image_pixel(
            lon=lon,
            lat=lat,
            bbox=image_bbox,
            image_width=grid_width,
            image_height=grid_height,
        )
        col = round(col_float)
        row = round(row_float)

    if row < 0 or col < 0 or row >= grid_height or col >= grid_width:
        return None

    return row, col


def average_utci_along_route(
    samples: list[tuple[float, float]],
    utci_grid_path: str | None,
    center_lon: float,
    center_lat: float,
    zoom: int,
    image_bbox: tuple[float, float, float, float] | None = None,
) -> float | None:
    """Calculate average UTCI along sampled route points if a grid exists."""
    if not utci_grid_path:
        return None

    path = Path(utci_grid_path)
    if not path.exists():
        return None

    import numpy as np

    grid = np.load(path)
    grid_height, grid_width = grid.shape[:2]
    values = []

    for lon, lat in samples:
        index = lonlat_to_grid_index(
            lon=lon,
            lat=lat,
            center_lon=center_lon,
            center_lat=center_lat,
            zoom=zoom,
            grid_width=grid_width,
            grid_height=grid_height,
            image_bbox=image_bbox,
        )
        if index is None:
            continue
        row, col = index
        value = grid[row, col]
        if not np.isnan(value):
            values.append(float(value))

    if not values:
        return None

    return sum(values) / len(values)


def normalize_higher_better(value: float, values: list[float]) -> float:
    """Normalize where high values are better."""
    minimum = min(values)
    maximum = max(values)
    if maximum == minimum:
        return 1.0
    return (value - minimum) / (maximum - minimum)


def normalize_lower_better(value: float, values: list[float]) -> float:
    """Normalize where low values are better."""
    minimum = min(values)
    maximum = max(values)
    if maximum == minimum:
        return 1.0
    return (maximum - value) / (maximum - minimum)


def score_route_features(
    route_features: list[dict[str, Any]],
    tree_geojson_path: str,
    utci_grid_path: str | None = None,
    center_lon: float = DEFAULT_CENTER_LON,
    center_lat: float = DEFAULT_CENTER_LAT,
    zoom: int = DEFAULT_MAPBOX_ZOOM,
    image_bbox: tuple[float, float, float, float] | None = None,
    tree_search_radius_m: float = 20,
) -> dict[str, Any]:
    """Score route alternatives and select shortest, greenest, and coolest."""
    tree_points = load_tree_points(tree_geojson_path)
    scored_routes = []

    for index, feature in enumerate(route_features):
        coordinates = feature.get("geometry", {}).get("coordinates", [])
        samples = sample_route_every_meters(coordinates, interval_m=25)
        distance_m = route_distance_m(coordinates)
        tree_count, tree_density, shade_continuity = nearby_tree_metrics(
            samples,
            tree_points,
            search_radius_m=tree_search_radius_m,
        )
        distance_km = distance_m / 1000 if distance_m else 0
        tree_density_per_km = tree_count / distance_km if distance_km else 0
        avg_utci = average_utci_along_route(
            samples=samples,
            utci_grid_path=utci_grid_path,
            center_lon=center_lon,
            center_lat=center_lat,
            zoom=zoom,
            image_bbox=image_bbox,
        )

        scored_routes.append(
            {
                "id": f"route_{index + 1}",
                "distance_m": distance_m,
                "sample_count": len(samples),
                "tree_count_near_route": tree_count,
                "tree_density": tree_density,
                "tree_density_per_km": tree_density_per_km,
                "shade_continuity": shade_continuity,
                "average_utci": avg_utci,
                "coolrun_score": None,
                "geometry": feature.get("geometry"),
                "properties": feature.get("properties", {}),
            }
        )

    if not scored_routes:
        raise ValueError("No route alternatives were returned by OpenRouteService.")

    distances = [route["distance_m"] for route in scored_routes]
    densities = [route["tree_density"] for route in scored_routes]
    continuity_values = [route["shade_continuity"] for route in scored_routes]
    utci_values = [
        route["average_utci"]
        for route in scored_routes
        if route["average_utci"] is not None
    ]

    for route in scored_routes:
        distance_score = normalize_lower_better(route["distance_m"], distances)
        green_score = normalize_higher_better(route["tree_density"], densities)
        continuity_score = normalize_higher_better(route["shade_continuity"], continuity_values)

        if route["average_utci"] is not None and utci_values:
            cool_score = normalize_lower_better(route["average_utci"], utci_values)
            route["coolrun_score"] = round(
                65 * cool_score
                + 20 * distance_score
                + 10 * green_score
                + 5 * continuity_score,
                2,
            )
        else:
            cool_score = 0.65 * green_score + 0.35 * continuity_score
            route["coolrun_score"] = round(
                25 * distance_score
                + 45 * green_score
                + 20 * continuity_score
                + 10 * cool_score,
                2,
            )

    shortest = min(scored_routes, key=lambda route: route["distance_m"])
    greenest = max(scored_routes, key=lambda route: route["tree_density"])

    if utci_values:
        coolest = min(
            scored_routes,
            key=lambda route: (
                route["average_utci"]
                if route["average_utci"] is not None
                else float("inf")
            ),
        )
    else:
        coolest = max(scored_routes, key=lambda route: route["coolrun_score"])

    shortest_distance = shortest["distance_m"]
    reasonable_routes = [
        route for route in scored_routes if route["distance_m"] <= shortest_distance * 1.25
    ] or scored_routes
    balanced = max(
        reasonable_routes,
        key=lambda route: (
            (
                0.45 * normalize_lower_better(route["average_utci"], utci_values)
                if route["average_utci"] is not None and utci_values
                else 0
            )
            + 0.30 * normalize_lower_better(route["distance_m"], distances)
            + 0.15 * normalize_higher_better(route["tree_density"], densities)
            + 0.10 * normalize_higher_better(route["shade_continuity"], continuity_values)
        ),
    )

    return {
        "routes": scored_routes,
        "selected": {
            "shortest_route": shortest["id"],
            "greenest_route": greenest["id"],
            "coolest_route": coolest["id"],
            "balanced_route": balanced["id"],
        },
        "utci_available": bool(utci_values),
        "tree_count": len(tree_points),
    }
