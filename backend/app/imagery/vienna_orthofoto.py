"""Helpers for downloading ViennaGIS orthophoto imagery.

The City of Vienna orthophoto is an aerial image product: aircraft photos are
geometrically corrected so map positions line up with real ground positions.
This is different from generic satellite imagery and should be the primary
imagery source for the Vienna-only workflow.
"""

from __future__ import annotations

import math
from io import BytesIO
from pathlib import Path
from typing import Any

import requests
from PIL import Image


WEB_MERCATOR_CRS = "EPSG:3857"
MAX_MERCATOR_LAT = 85.05112878
WEB_MERCATOR_HALF_WORLD = 20037508.342789244
VIENNA_ORTHOFOTO_TILE_SIZE = 256
VIENNA_ORTHOFOTO_LAYER = "lb"
VIENNA_ORTHOFOTO_STYLE = "farbe"
VIENNA_ORTHOFOTO_TILE_MATRIX_SET = "google3857"
VIENNA_ORTHOFOTO_TILE_URL = (
    "https://mapsneu.wien.gv.at/wmts/"
    f"{VIENNA_ORTHOFOTO_LAYER}/{VIENNA_ORTHOFOTO_STYLE}/"
    f"{VIENNA_ORTHOFOTO_TILE_MATRIX_SET}/{{zoom}}/{{row}}/{{col}}.jpeg"
)


def lonlat_to_webmercator(lon: float, lat: float) -> tuple[float, float]:
    """Convert longitude/latitude degrees to Web Mercator meters."""
    if not -180 <= lon <= 180:
        raise ValueError("lon must be between -180 and 180.")

    if not -90 <= lat <= 90:
        raise ValueError("lat must be between -90 and 90.")

    clipped_lat = max(min(lat, MAX_MERCATOR_LAT), -MAX_MERCATOR_LAT)
    x = lon * WEB_MERCATOR_HALF_WORLD / 180.0
    y = math.log(math.tan((90.0 + clipped_lat) * math.pi / 360.0)) / math.pi
    y *= WEB_MERCATOR_HALF_WORLD
    return x, y


def webmercator_to_lonlat(x: float, y: float) -> tuple[float, float]:
    """Convert Web Mercator meters back to longitude/latitude degrees."""
    lon = x / WEB_MERCATOR_HALF_WORLD * 180.0
    lat = y / WEB_MERCATOR_HALF_WORLD * 180.0
    lat = 180.0 / math.pi * (2.0 * math.atan(math.exp(lat * math.pi / 180.0)) - math.pi / 2.0)
    return lon, lat


def image_pixel_to_lonlat(
    px: float,
    py: float,
    bbox: tuple[float, float, float, float],
    image_width: int,
    image_height: int,
) -> tuple[float, float]:
    """Convert a pixel in a downloaded orthophoto image to lon/lat.

    The bbox is expected in Web Mercator meters as (min_x, min_y, max_x, max_y).
    Image pixels start at the top-left corner, so y increases downward.
    """
    if image_width <= 0 or image_height <= 0:
        raise ValueError("image_width and image_height must be positive.")

    min_x, min_y, max_x, max_y = bbox
    x = min_x + (px / image_width) * (max_x - min_x)
    y = max_y - (py / image_height) * (max_y - min_y)
    return webmercator_to_lonlat(x, y)


def lonlat_to_image_pixel(
    lon: float,
    lat: float,
    bbox: tuple[float, float, float, float],
    image_width: int,
    image_height: int,
) -> tuple[float, float]:
    """Convert lon/lat to a pixel in a downloaded orthophoto image."""
    if image_width <= 0 or image_height <= 0:
        raise ValueError("image_width and image_height must be positive.")

    min_x, min_y, max_x, max_y = bbox
    x, y = lonlat_to_webmercator(lon, lat)
    px = (x - min_x) / (max_x - min_x) * image_width
    py = (max_y - y) / (max_y - min_y) * image_height
    return px, py


def create_bbox_polygon(bbox_lonlat: dict[str, float]) -> dict[str, Any]:
    """Create a GeoJSON polygon from Vienna orthophoto lon/lat bounds."""
    west = float(bbox_lonlat["west"])
    south = float(bbox_lonlat["south"])
    east = float(bbox_lonlat["east"])
    north = float(bbox_lonlat["north"])

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


def download_vienna_orthofoto(
    lat: float,
    lon: float,
    output_path: str,
    zoom: int = 18,
    width: int = 512,
    height: int = 512,
) -> dict[str, Any]:
    """Download a centered ViennaGIS orthophoto crop and return metadata."""
    if not -90 <= lat <= 90:
        raise ValueError("lat must be between -90 and 90.")

    if not -180 <= lon <= 180:
        raise ValueError("lon must be between -180 and 180.")

    if not 0 <= zoom <= 22:
        raise ValueError("zoom must be between 0 and 22.")

    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive integers.")

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    center_world_x, center_world_y = _lonlat_to_world_pixel(lon, lat, zoom)
    top_left_world_x = center_world_x - width / 2
    top_left_world_y = center_world_y - height / 2
    bottom_right_world_x = center_world_x + width / 2
    bottom_right_world_y = center_world_y + height / 2

    first_col = math.floor(top_left_world_x / VIENNA_ORTHOFOTO_TILE_SIZE)
    last_col = math.floor((bottom_right_world_x - 1) / VIENNA_ORTHOFOTO_TILE_SIZE)
    first_row = math.floor(top_left_world_y / VIENNA_ORTHOFOTO_TILE_SIZE)
    last_row = math.floor((bottom_right_world_y - 1) / VIENNA_ORTHOFOTO_TILE_SIZE)

    mosaic_width = (last_col - first_col + 1) * VIENNA_ORTHOFOTO_TILE_SIZE
    mosaic_height = (last_row - first_row + 1) * VIENNA_ORTHOFOTO_TILE_SIZE
    mosaic = Image.new("RGB", (mosaic_width, mosaic_height))
    tile_urls: list[str] = []

    for row in range(first_row, last_row + 1):
        for col in range(first_col, last_col + 1):
            tile_url = VIENNA_ORTHOFOTO_TILE_URL.format(zoom=zoom, row=row, col=col)
            tile_urls.append(tile_url)
            tile = _download_tile(tile_url)
            paste_x = (col - first_col) * VIENNA_ORTHOFOTO_TILE_SIZE
            paste_y = (row - first_row) * VIENNA_ORTHOFOTO_TILE_SIZE
            mosaic.paste(tile, (paste_x, paste_y))

    crop_left = round(top_left_world_x - first_col * VIENNA_ORTHOFOTO_TILE_SIZE)
    crop_top = round(top_left_world_y - first_row * VIENNA_ORTHOFOTO_TILE_SIZE)
    crop = mosaic.crop((crop_left, crop_top, crop_left + width, crop_top + height))
    crop.save(output_file)

    min_x, max_y = _world_pixel_to_webmercator(top_left_world_x, top_left_world_y, zoom)
    max_x, min_y = _world_pixel_to_webmercator(bottom_right_world_x, bottom_right_world_y, zoom)
    west, north = webmercator_to_lonlat(min_x, max_y)
    east, south = webmercator_to_lonlat(max_x, min_y)

    return {
        "source": "City of Vienna Orthofoto / ViennaGIS WMTS",
        "tile_url_template": VIENNA_ORTHOFOTO_TILE_URL,
        "layer": VIENNA_ORTHOFOTO_LAYER,
        "style": VIENNA_ORTHOFOTO_STYLE,
        "crs": WEB_MERCATOR_CRS,
        "tile_matrix_set": VIENNA_ORTHOFOTO_TILE_MATRIX_SET,
        "zoom": zoom,
        "tile_matrix": str(zoom),
        "tile_size": VIENNA_ORTHOFOTO_TILE_SIZE,
        "center": {"lat": lat, "lon": lon},
        "bbox": {
            "min_x": min_x,
            "min_y": min_y,
            "max_x": max_x,
            "max_y": max_y,
            "crs": WEB_MERCATOR_CRS,
        },
        "bbox_lonlat": {
            "west": west,
            "south": south,
            "east": east,
            "north": north,
            "crs": "EPSG:4326",
        },
        "image_width": width,
        "image_height": height,
        "downloaded_tiles": tile_urls,
        "output_path": str(output_file),
    }


def _download_tile(tile_url: str) -> Image.Image:
    try:
        response = requests.get(tile_url, timeout=30)
        response.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        raise RuntimeError(
            f"ViennaGIS WMTS tile request failed with status {response.status_code}: {response.text}"
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f"ViennaGIS WMTS tile request failed: {exc}") from exc

    return Image.open(BytesIO(response.content)).convert("RGB")


def _lonlat_to_world_pixel(lon: float, lat: float, zoom: int) -> tuple[float, float]:
    x, y = lonlat_to_webmercator(lon, lat)
    world_size = VIENNA_ORTHOFOTO_TILE_SIZE * (2**zoom)
    world_x = (x + WEB_MERCATOR_HALF_WORLD) / (2 * WEB_MERCATOR_HALF_WORLD) * world_size
    world_y = (WEB_MERCATOR_HALF_WORLD - y) / (2 * WEB_MERCATOR_HALF_WORLD) * world_size
    return world_x, world_y


def _world_pixel_to_webmercator(
    world_x: float,
    world_y: float,
    zoom: int,
) -> tuple[float, float]:
    world_size = VIENNA_ORTHOFOTO_TILE_SIZE * (2**zoom)
    x = world_x / world_size * (2 * WEB_MERCATOR_HALF_WORLD) - WEB_MERCATOR_HALF_WORLD
    y = WEB_MERCATOR_HALF_WORLD - world_y / world_size * (2 * WEB_MERCATOR_HALF_WORLD)
    return x, y
