"""Build vegetation GeoJSON from tree detection predictions."""

from __future__ import annotations

import math
from typing import Any

from backend.app.imagery.mapbox_geo import image_pixel_to_lonlat
from backend.app.imagery.vienna_orthofoto import image_pixel_to_lonlat as vienna_image_pixel_to_lonlat


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
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return EARTH_RADIUS_M * c


def estimate_canopy_radius_m(
    pixel_x: float,
    pixel_y: float,
    bbox_width: float,
    bbox_height: float,
    center_lon: float,
    center_lat: float,
    zoom: int,
    image_width: int,
    image_height: int,
    image_bbox: tuple[float, float, float, float] | None = None,
) -> float:
    """Estimate tree canopy radius in meters from a Roboflow bbox.

    Roboflow gives bbox size in image pixels. We treat half of the average bbox
    side length as an approximate crown radius, then convert that pixel distance
    into meters at the image location.
    """
    radius_px = max((bbox_width + bbox_height) / 4, 1)

    if image_bbox is None:
        lon_center, lat_center = image_pixel_to_lonlat(
            px=pixel_x,
            py=pixel_y,
            center_lon=center_lon,
            center_lat=center_lat,
            zoom=zoom,
            width=image_width,
            height=image_height,
        )
        lon_edge, lat_edge = image_pixel_to_lonlat(
            px=pixel_x + radius_px,
            py=pixel_y,
            center_lon=center_lon,
            center_lat=center_lat,
            zoom=zoom,
            width=image_width,
            height=image_height,
        )
    else:
        lon_center, lat_center = vienna_image_pixel_to_lonlat(
            px=pixel_x,
            py=pixel_y,
            bbox=image_bbox,
            image_width=image_width,
            image_height=image_height,
        )
        lon_edge, lat_edge = vienna_image_pixel_to_lonlat(
            px=pixel_x + radius_px,
            py=pixel_y,
            bbox=image_bbox,
            image_width=image_width,
            image_height=image_height,
        )

    return haversine_distance_m(lon_center, lat_center, lon_edge, lat_edge)


def detections_to_tree_geojson(
    predictions: list[dict[str, Any]],
    center_lon: float,
    center_lat: float,
    zoom: int,
    width: int,
    height: int,
    confidence_threshold: float = 0.35,
    image_bbox: tuple[float, float, float, float] | None = None,
    allowed_classes: set[str] | None = None,
) -> dict[str, Any]:
    """Convert Roboflow tree detections into a GeoJSON FeatureCollection."""
    if allowed_classes is None:
        allowed_classes = {"tree"}

    features = []

    for prediction in predictions:
        confidence = float(prediction.get("confidence", 0))
        if confidence < confidence_threshold:
            continue

        class_name = str(prediction.get("class", "")).strip().lower()
        if class_name not in allowed_classes:
            continue

        # Roboflow x/y is already the center of the detected bounding box.
        pixel_x = float(prediction["x"])
        pixel_y = float(prediction["y"])

        bbox_width = float(prediction.get("width", 0))
        bbox_height = float(prediction.get("height", 0))

        if "lon" in prediction and "lat" in prediction:
            lon = float(prediction["lon"])
            lat = float(prediction["lat"])
        elif image_bbox is None:
            lon, lat = image_pixel_to_lonlat(
                px=pixel_x,
                py=pixel_y,
                center_lon=center_lon,
                center_lat=center_lat,
                zoom=zoom,
                width=width,
                height=height,
            )
        else:
            lon, lat = vienna_image_pixel_to_lonlat(
                px=pixel_x,
                py=pixel_y,
                bbox=image_bbox,
                image_width=width,
                image_height=height,
            )
        canopy_radius_m = estimate_canopy_radius_m(
            pixel_x=pixel_x,
            pixel_y=pixel_y,
            bbox_width=bbox_width,
            bbox_height=bbox_height,
            center_lon=center_lon,
            center_lat=center_lat,
            zoom=zoom,
            image_width=width,
            image_height=height,
            image_bbox=image_bbox,
        )

        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [lon, lat],
                },
                "properties": {
                    "source": "roboflow",
                    "class": class_name,
                    "confidence": confidence,
                    "bbox_width": bbox_width,
                    "bbox_height": bbox_height,
                    "bbox_area_px": bbox_width * bbox_height,
                    "canopy_radius_m": canopy_radius_m,
                    "radius_m": canopy_radius_m,
                    "radius": canopy_radius_m,
                    "canopy_radius_source": "estimated_from_roboflow_bbox",
                    "coordinate_source": "vienna_orthofoto_metadata"
                    if image_bbox is not None
                    else "mapbox_static_image",
                },
            }
        )

    return {
        "type": "FeatureCollection",
        "features": features,
    }
