"""Helpers for downloading satellite images from the Mapbox Static Images API."""

import os
from pathlib import Path

import requests
from dotenv import load_dotenv


MAPBOX_STATIC_IMAGES_URL = "https://api.mapbox.com/styles/v1/mapbox/satellite-v9/static"


def download_mapbox_static_image(
    lat: float,
    lon: float,
    zoom: int,
    width: int,
    height: int,
    output_path: str,
) -> str:
    """Download one Mapbox satellite image, save it, and return the saved path."""
    load_dotenv()
    mapbox_token = os.getenv("MAPBOX_TOKEN")

    if not mapbox_token:
        raise ValueError(
            "MAPBOX_TOKEN is missing. Add MAPBOX_TOKEN to your .env file or environment variables."
        )

    if not -90 <= lat <= 90:
        raise ValueError("lat must be between -90 and 90.")

    if not -180 <= lon <= 180:
        raise ValueError("lon must be between -180 and 180.")

    if not 0 <= zoom <= 22:
        raise ValueError("zoom must be between 0 and 22 for Mapbox Static Images.")

    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive integers.")

    saved_path = Path(output_path)
    saved_path.parent.mkdir(parents=True, exist_ok=True)

    image_size = f"{width}x{height}@2x"
    url = f"{MAPBOX_STATIC_IMAGES_URL}/{lon},{lat},{zoom},0/{image_size}"

    try:
        response = requests.get(
            url,
            params={"access_token": mapbox_token},
            timeout=30,
        )
        response.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        raise RuntimeError(
            f"Mapbox request failed with status {response.status_code}: {response.text}"
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f"Mapbox request failed: {exc}") from exc

    saved_path.write_bytes(response.content)
    return str(saved_path)
