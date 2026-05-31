"""Coordinate helpers for Mapbox Static Images.

The Vienna test image is centered at:
- latitude: 48.18461202879178
- longitude: 16.400399172025814
- zoom: 16
- image size: 1024 x 1024 pixels

The functions below also accept those values as arguments so they can be reused
for other images later.
"""

from __future__ import annotations

import math


MAPBOX_TILE_SIZE = 512
MAX_MERCATOR_LAT = 85.05112878


def lonlat_to_world_pixel(lon: float, lat: float, zoom: int) -> tuple[float, float]:
    """Convert longitude/latitude to Mapbox world pixel coordinates."""
    lat = max(min(lat, MAX_MERCATOR_LAT), -MAX_MERCATOR_LAT)
    world_size = MAPBOX_TILE_SIZE * (2**zoom)

    # Longitude maps linearly from -180..180 degrees to 0..world_size pixels.
    world_x = (lon + 180.0) / 360.0 * world_size

    # Latitude uses Web Mercator, so north/south distances are not linear.
    # This is the same projection used by Mapbox, Leaflet, and most web maps.
    lat_rad = math.radians(lat)
    world_y = (
        0.5
        - math.log((1 + math.sin(lat_rad)) / (1 - math.sin(lat_rad)))
        / (4 * math.pi)
    ) * world_size

    return world_x, world_y


def world_pixel_to_lonlat(
    world_x: float,
    world_y: float,
    zoom: int,
) -> tuple[float, float]:
    """Convert Mapbox world pixel coordinates back to longitude/latitude."""
    world_size = MAPBOX_TILE_SIZE * (2**zoom)

    lon = world_x / world_size * 360.0 - 180.0

    # This reverses the Web Mercator latitude formula used above.
    mercator_y = math.pi * (1 - 2 * world_y / world_size)
    lat = math.degrees(math.atan(math.sinh(mercator_y)))

    return lon, lat


def image_pixel_to_lonlat(
    px: float,
    py: float,
    center_lon: float,
    center_lat: float,
    zoom: int,
    width: int,
    height: int,
) -> tuple[float, float]:
    """Convert an image pixel position to longitude/latitude.

    Roboflow returns detections in image pixels, where (0, 0) is the top-left
    corner of the downloaded satellite image. Mapbox positions the image around
    a known center lon/lat.

    The conversion is:
    1. Convert the image center lon/lat to Mapbox world pixels.
    2. Find the world pixel for the image's top-left corner.
    3. Add the Roboflow image pixel x/y offset.
    4. Convert that world pixel back to lon/lat.
    """
    center_world_x, center_world_y = lonlat_to_world_pixel(
        center_lon,
        center_lat,
        zoom,
    )

    top_left_world_x = center_world_x - width / 2
    top_left_world_y = center_world_y - height / 2

    point_world_x = top_left_world_x + px
    point_world_y = top_left_world_y + py

    return world_pixel_to_lonlat(point_world_x, point_world_y, zoom)


def create_image_bounds_polygon(
    center_lon: float,
    center_lat: float,
    zoom: int,
    width: int,
    height: int,
) -> dict:
    """Create a GeoJSON polygon matching the Mapbox Static image footprint.

    The polygon uses the satellite image's four corners, so the simulation area
    can match the exact image used for tree detection.
    """
    top_left = image_pixel_to_lonlat(
        0,
        0,
        center_lon,
        center_lat,
        zoom,
        width,
        height,
    )
    top_right = image_pixel_to_lonlat(
        width,
        0,
        center_lon,
        center_lat,
        zoom,
        width,
        height,
    )
    bottom_right = image_pixel_to_lonlat(
        width,
        height,
        center_lon,
        center_lat,
        zoom,
        width,
        height,
    )
    bottom_left = image_pixel_to_lonlat(
        0,
        height,
        center_lon,
        center_lat,
        zoom,
        width,
        height,
    )

    return {
        "type": "Polygon",
        "coordinates": [
            [
                [top_left[0], top_left[1]],
                [top_right[0], top_right[1]],
                [bottom_right[0], bottom_right[1]],
                [bottom_left[0], bottom_left[1]],
                [top_left[0], top_left[1]],
            ]
        ],
    }
