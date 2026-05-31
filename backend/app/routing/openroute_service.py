"""OpenRouteService helpers for walking route alternatives."""

from __future__ import annotations

import os
from typing import Any

import requests
from dotenv import load_dotenv


ORS_DIRECTIONS_URL = "https://api.openrouteservice.org/v2/directions/foot-walking/geojson"


def load_openrouteservice_api_key() -> str:
    """Load OpenRouteService API key from .env or environment variables."""
    load_dotenv(override=True)
    api_key = os.getenv("OPENROUTESERVICE_API_KEY")

    if not api_key:
        raise ValueError(
            "OPENROUTESERVICE_API_KEY is missing. Add it to your .env file or environment variables."
        )

    return api_key


def request_walking_routes(
    start_lon: float,
    start_lat: float,
    end_lon: float,
    end_lat: float,
    alternatives: int = 3,
) -> dict[str, Any]:
    """Request walking route alternatives from OpenRouteService."""
    api_key = load_openrouteservice_api_key()

    body = {
        "coordinates": [
            [start_lon, start_lat],
            [end_lon, end_lat],
        ],
        "instructions": False,
        "geometry": True,
        "alternative_routes": {
            "target_count": alternatives,
            "share_factor": 0.6,
            "weight_factor": 1.8,
        },
    }

    try:
        response = requests.post(
            ORS_DIRECTIONS_URL,
            json=body,
            headers={
                "Authorization": api_key,
                "Content-Type": "application/json",
            },
            timeout=60,
        )
        response.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        raise RuntimeError(
            f"OpenRouteService request failed with status {response.status_code}: {response.text}"
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f"OpenRouteService request failed: {exc}") from exc

    return response.json()
