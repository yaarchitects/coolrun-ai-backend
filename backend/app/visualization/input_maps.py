"""Visualization helpers for CoolRun AI input data."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from PIL import Image

import matplotlib

matplotlib.use("Agg")

from backend.app.imagery.mapbox_geo import image_pixel_to_lonlat, lonlat_to_world_pixel
from backend.app.imagery.vienna_orthofoto import lonlat_to_image_pixel as vienna_lonlat_to_image_pixel


def load_geojson(path: str) -> dict[str, Any]:
    """Load a GeoJSON file from disk."""
    geojson_path = Path(path)
    if not geojson_path.exists():
        raise FileNotFoundError(f"GeoJSON file does not exist: {geojson_path}")

    return json.loads(geojson_path.read_text(encoding="utf-8"))


def load_json_if_exists(path: str) -> dict[str, Any] | None:
    """Load a JSON file if it exists, otherwise return None."""
    json_path = Path(path)
    if not json_path.exists():
        return None

    return json.loads(json_path.read_text(encoding="utf-8"))


def count_point_features(geojson: dict[str, Any]) -> int:
    """Count Point features in a GeoJSON FeatureCollection."""
    return sum(
        1
        for feature in geojson.get("features", [])
        if feature.get("geometry", {}).get("type") == "Point"
    )


def lonlat_to_image_pixel(
    lon: float,
    lat: float,
    center_lon: float,
    center_lat: float,
    zoom: int,
    width: int,
    height: int,
) -> tuple[float, float]:
    """Convert longitude/latitude to image pixel x/y for the Mapbox image."""
    center_world_x, center_world_y = lonlat_to_world_pixel(center_lon, center_lat, zoom)
    point_world_x, point_world_y = lonlat_to_world_pixel(lon, lat, zoom)

    image_x = point_world_x - (center_world_x - width / 2)
    image_y = point_world_y - (center_world_y - height / 2)

    return image_x, image_y


def tree_points_lonlat(geojson: dict[str, Any]) -> list[tuple[float, float]]:
    """Extract detected tree point coordinates as lon/lat tuples."""
    points = []

    for feature in geojson.get("features", []):
        geometry = feature.get("geometry", {})
        if geometry.get("type") != "Point":
            continue

        lon, lat = geometry.get("coordinates", [None, None])[:2]
        if lon is None or lat is None:
            continue

        points.append((float(lon), float(lat)))

    return points


def tree_features_lonlat_radius(
    geojson: dict[str, Any],
) -> list[tuple[float, float, float]]:
    """Extract detected tree lon/lat points with canopy radius in meters."""
    trees = []

    for feature in geojson.get("features", []):
        geometry = feature.get("geometry", {})
        if geometry.get("type") != "Point":
            continue

        lon, lat = geometry.get("coordinates", [None, None])[:2]
        if lon is None or lat is None:
            continue

        properties = feature.get("properties", {})
        radius_m = properties.get("canopy_radius_m") or properties.get("radius_m") or 2.0
        trees.append((float(lon), float(lat), float(radius_m)))

    return trees


def polygon_lonlat_points(polygon: dict[str, Any]) -> list[tuple[float, float]]:
    """Extract the exterior polygon ring as lon/lat tuples."""
    coordinates = polygon.get("coordinates", [])
    if not coordinates:
        return []

    return [(float(lon), float(lat)) for lon, lat in coordinates[0]]


def polygon_features_lonlat(
    geojson: dict[str, Any],
) -> list[list[tuple[float, float]]]:
    """Extract Polygon feature exterior rings as lon/lat tuples."""
    polygons = []

    for feature in geojson.get("features", []):
        geometry = feature.get("geometry", {})
        if geometry.get("type") != "Polygon":
            continue

        coordinates = geometry.get("coordinates", [])
        if not coordinates:
            continue

        polygons.append([(float(lon), float(lat)) for lon, lat in coordinates[0]])

    return polygons


def image_extent_lonlat(
    center_lon: float,
    center_lat: float,
    zoom: int,
    width: int,
    height: int,
) -> tuple[float, float, float, float]:
    """Return the lon/lat bounds of the Mapbox image."""
    west, north = image_pixel_to_lonlat(
        0,
        0,
        center_lon,
        center_lat,
        zoom,
        width,
        height,
    )
    east, south = image_pixel_to_lonlat(
        width,
        height,
        center_lon,
        center_lat,
        zoom,
        width,
        height,
    )

    return west, south, east, north


def save_static_input_visualization(
    image_path: str,
    tree_geojson: dict[str, Any],
    polygon: dict[str, Any],
    output_path: str,
    center_lon: float,
    center_lat: float,
    zoom: int,
    canopy_geojson: dict[str, Any] | None = None,
    image_bbox: tuple[float, float, float, float] | None = None,
) -> str:
    """Save a PNG showing the source image, tree points, and polygon."""
    import matplotlib.pyplot as plt

    image = Image.open(image_path)
    image_width, image_height = image.size

    tree_pixels = [
        (
            *(
                vienna_lonlat_to_image_pixel(
                    lon,
                    lat,
                    image_bbox,
                    image_width,
                    image_height,
                )
                if image_bbox is not None
                else lonlat_to_image_pixel(
                    lon,
                    lat,
                    center_lon,
                    center_lat,
                    zoom,
                    image_width,
                    image_height,
                )
            ),
            radius_m,
        )
        for lon, lat, radius_m in tree_features_lonlat_radius(tree_geojson)
    ]
    polygon_pixels = [
        (
            vienna_lonlat_to_image_pixel(lon, lat, image_bbox, image_width, image_height)
            if image_bbox is not None
            else lonlat_to_image_pixel(lon, lat, center_lon, center_lat, zoom, image_width, image_height)
        )
        for lon, lat in polygon_lonlat_points(polygon)
    ]
    canopy_polygons_px = []
    if canopy_geojson:
        for canopy_polygon in polygon_features_lonlat(canopy_geojson):
            canopy_polygons_px.append(
                [
                    (
                        vienna_lonlat_to_image_pixel(
                            lon,
                            lat,
                            image_bbox,
                            image_width,
                            image_height,
                        )
                        if image_bbox is not None
                        else lonlat_to_image_pixel(
                            lon,
                            lat,
                            center_lon,
                            center_lat,
                            zoom,
                            image_width,
                            image_height,
                        )
                    )
                    for lon, lat in canopy_polygon
                ]
            )

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    plt.figure(figsize=(9, 9))
    plt.imshow(image)

    if tree_pixels:
        tree_x, tree_y, tree_radius_m = zip(*tree_pixels)
        # Matplotlib scatter size is area in points^2, so scale radius gently.
        tree_sizes = [max(18, min(radius_m * 12, 180)) for radius_m in tree_radius_m]
        plt.scatter(
            tree_x,
            tree_y,
            s=tree_sizes,
            c="lime",
            edgecolors="black",
            linewidths=0.4,
            alpha=0.75,
            label="Detected tree canopies",
        )

    for canopy_polygon_px in canopy_polygons_px:
        canopy_x, canopy_y = zip(*canopy_polygon_px)
        plt.fill(
            canopy_x,
            canopy_y,
            color="lime",
            alpha=0.18,
            edgecolor="green",
            linewidth=0.8,
        )

    if polygon_pixels:
        polygon_x, polygon_y = zip(*polygon_pixels)
        plt.plot(
            polygon_x,
            polygon_y,
            color="yellow",
            linewidth=2,
            label="UTCI test polygon",
        )

    plt.axis("off")
    plt.legend(loc="lower right")
    plt.title("CoolRun AI Inputs")
    plt.tight_layout()
    plt.savefig(output_file, dpi=160, bbox_inches="tight", pad_inches=0.05)
    plt.close()

    return str(output_file)


def save_interactive_input_map(
    tree_geojson: dict[str, Any],
    polygon: dict[str, Any],
    output_path: str,
    center_lon: float,
    center_lat: float,
    canopy_geojson: dict[str, Any] | None = None,
) -> str:
    """Save an interactive Folium map with detected trees and polygon."""
    import folium

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    fmap = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=16,
        tiles="OpenStreetMap",
    )

    folium.GeoJson(
        polygon,
        name="UTCI test polygon",
        style_function=lambda _: {
            "color": "yellow",
            "weight": 3,
            "fillOpacity": 0.05,
        },
    ).add_to(fmap)

    for lon, lat, radius_m in tree_features_lonlat_radius(tree_geojson):
        folium.Circle(
            location=[lat, lon],
            radius=radius_m,
            color="green",
            fill=True,
            fill_color="lime",
            fill_opacity=0.35,
            weight=1,
            popup=f"Estimated canopy radius: {radius_m:.1f} m",
        ).add_to(fmap)

    if canopy_geojson:
        folium.GeoJson(
            canopy_geojson,
            name="Infrared canopy polygons",
            style_function=lambda _: {
                "color": "green",
                "weight": 1,
                "fillColor": "lime",
                "fillOpacity": 0.25,
            },
        ).add_to(fmap)

    folium.LayerControl().add_to(fmap)
    fmap.save(output_file)

    return str(output_file)


def save_cooling_effect_visualization(
    without_trees_npy: str,
    with_trees_npy: str,
    output_path: str,
) -> str:
    """Save a PNG showing UTCI cooling effect from two real simulation grids."""
    import matplotlib.pyplot as plt
    import numpy as np

    without_path = Path(without_trees_npy)
    with_path = Path(with_trees_npy)

    if not without_path.exists():
        raise FileNotFoundError(f"Missing UTCI without-trees grid: {without_path}")

    if not with_path.exists():
        raise FileNotFoundError(f"Missing UTCI with-trees grid: {with_path}")

    without_grid = np.load(without_path)
    with_grid = np.load(with_path)
    cooling_effect = without_grid - with_grid

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    plt.figure(figsize=(8, 7))
    heatmap = plt.imshow(cooling_effect, cmap="YlGn", interpolation="nearest")
    plt.colorbar(heatmap, label="Cooling effect, UTCI C")
    plt.title("UTCI Cooling Effect: Without Trees - With Trees")
    plt.axis("off")
    plt.tight_layout()
    plt.savefig(output_file, dpi=160, bbox_inches="tight")
    plt.close()

    return str(output_file)


def summarize_tree_utci_overlap(
    tree_geojson: dict[str, Any],
    without_trees_npy: str,
    with_trees_npy: str,
    image_bbox: tuple[float, float, float, float],
) -> dict[str, Any]:
    """Summarize which detected trees intersect valid UTCI and cooling cells."""
    import numpy as np

    without_grid = np.load(without_trees_npy)
    with_grid = np.load(with_trees_npy)
    cooling_effect = without_grid - with_grid
    grid_height, grid_width = cooling_effect.shape[:2]

    tree_summaries = []
    valid_center_count = 0
    cooled_center_count = 0
    cooled_canopy_count = 0

    for index, feature in enumerate(tree_geojson.get("features", []), start=1):
        geometry = feature.get("geometry", {})
        if geometry.get("type") != "Point":
            continue

        lon, lat = geometry.get("coordinates", [None, None])[:2]
        if lon is None or lat is None:
            continue

        properties = feature.get("properties", {})
        radius_m = float(properties.get("canopy_radius_m") or properties.get("radius_m") or 3.0)
        pixel_x, pixel_y = vienna_lonlat_to_image_pixel(
            lon=float(lon),
            lat=float(lat),
            bbox=image_bbox,
            image_width=grid_width,
            image_height=grid_height,
        )
        row = round(pixel_y)
        col = round(pixel_x)

        center_valid = False
        center_cooling = None
        if 0 <= row < grid_height and 0 <= col < grid_width:
            value = cooling_effect[row, col]
            center_valid = not np.isnan(value)
            if center_valid:
                valid_center_count += 1
                center_cooling = float(value)
                if value > 0.01:
                    cooled_center_count += 1

        # Approximate 1 m in pixels from this tree's latitude.
        meter_lon = 1.0 / (111_320 * max(0.01, abs(math.cos(math.radians(float(lat))))))
        edge_x, _ = vienna_lonlat_to_image_pixel(
            lon=float(lon) + meter_lon,
            lat=float(lat),
            bbox=image_bbox,
            image_width=grid_width,
            image_height=grid_height,
        )
        pixels_per_meter = max(abs(edge_x - pixel_x), 0.1)
        radius_px = max(1, round(radius_m * pixels_per_meter))

        row_min = max(0, row - radius_px)
        row_max = min(grid_height, row + radius_px + 1)
        col_min = max(0, col - radius_px)
        col_max = min(grid_width, col + radius_px + 1)
        canopy_window = cooling_effect[row_min:row_max, col_min:col_max]
        canopy_has_cooling = bool(np.any(canopy_window > 0.01))
        if canopy_has_cooling:
            cooled_canopy_count += 1

        tree_summaries.append(
            {
                "index": index,
                "class": properties.get("class"),
                "confidence": properties.get("confidence"),
                "lon": float(lon),
                "lat": float(lat),
                "pixel_x": float(pixel_x),
                "pixel_y": float(pixel_y),
                "canopy_radius_m": radius_m,
                "center_on_valid_utci_cell": center_valid,
                "center_cooling_effect": center_cooling,
                "canopy_window_has_cooling": canopy_has_cooling,
            }
        )

    return {
        "tree_count": len(tree_summaries),
        "tree_centers_on_valid_utci_cells": valid_center_count,
        "tree_centers_with_positive_cooling": cooled_center_count,
        "tree_canopy_windows_with_positive_cooling": cooled_canopy_count,
        "positive_cooling_cell_count": int(np.sum(cooling_effect > 0.01)),
        "max_cooling_effect": float(np.nanmax(cooling_effect)),
        "trees": tree_summaries,
    }


def save_cooling_effect_tree_overlay(
    tree_geojson: dict[str, Any],
    without_trees_npy: str,
    with_trees_npy: str,
    output_path: str,
    image_bbox: tuple[float, float, float, float],
) -> str:
    """Save a cooling-effect PNG with detected tree centers overlaid."""
    import matplotlib.pyplot as plt
    import numpy as np

    without_grid = np.load(without_trees_npy)
    with_grid = np.load(with_trees_npy)
    cooling_effect = without_grid - with_grid
    grid_height, grid_width = cooling_effect.shape[:2]

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    tree_pixels = []
    for lon, lat, radius_m in tree_features_lonlat_radius(tree_geojson):
        pixel_x, pixel_y = vienna_lonlat_to_image_pixel(
            lon=lon,
            lat=lat,
            bbox=image_bbox,
            image_width=grid_width,
            image_height=grid_height,
        )
        tree_pixels.append((pixel_x, pixel_y, radius_m))

    plt.figure(figsize=(8, 7))
    heatmap = plt.imshow(cooling_effect, cmap="YlGn", interpolation="nearest")
    plt.colorbar(heatmap, label="Cooling effect, UTCI C")

    if tree_pixels:
        tree_x, tree_y, tree_radius_m = zip(*tree_pixels)
        tree_sizes = [max(24, min(radius_m * 14, 220)) for radius_m in tree_radius_m]
        plt.scatter(
            tree_x,
            tree_y,
            s=tree_sizes,
            facecolors="none",
            edgecolors="black",
            linewidths=0.8,
            label="Detected tree canopy",
        )
        plt.scatter(
            tree_x,
            tree_y,
            s=18,
            c="lime",
            edgecolors="black",
            linewidths=0.4,
            label="Tree center",
        )

    plt.title("UTCI Cooling Effect with Detected Trees")
    plt.axis("off")
    plt.legend(loc="lower right")
    plt.tight_layout()
    plt.savefig(output_file, dpi=160, bbox_inches="tight")
    plt.close()

    return str(output_file)


def plot_utci_grids(
    without_trees_npy: str,
    with_trees_npy: str,
) -> None:
    """Plot UTCI without trees, with trees, and cooling effect inline."""
    import matplotlib.pyplot as plt
    import numpy as np

    without_grid = np.load(without_trees_npy)
    with_grid = np.load(with_trees_npy)
    cooling_effect = without_grid - with_grid

    fig, axes = plt.subplots(1, 3, figsize=(17, 5))

    utci_min = float(np.nanmin([np.nanmin(without_grid), np.nanmin(with_grid)]))
    utci_max = float(np.nanmax([np.nanmax(without_grid), np.nanmax(with_grid)]))

    image_without = axes[0].imshow(without_grid, cmap="RdBu_r", vmin=utci_min, vmax=utci_max)
    axes[0].set_title("UTCI without detected trees")
    axes[0].axis("off")
    fig.colorbar(image_without, ax=axes[0], fraction=0.046, pad=0.04)

    image_with = axes[1].imshow(with_grid, cmap="RdBu_r", vmin=utci_min, vmax=utci_max)
    axes[1].set_title("UTCI with detected trees")
    axes[1].axis("off")
    fig.colorbar(image_with, ax=axes[1], fraction=0.046, pad=0.04)

    image_cooling = axes[2].imshow(cooling_effect, cmap="YlGn")
    axes[2].set_title("Cooling effect")
    axes[2].axis("off")
    fig.colorbar(image_cooling, ax=axes[2], fraction=0.046, pad=0.04)

    plt.tight_layout()
    plt.show()
