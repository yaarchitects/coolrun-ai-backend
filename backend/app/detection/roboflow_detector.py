"""Helpers for running Roboflow tree detection on local images."""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont


ROBOFLOW_DETECT_URL = "https://detect.roboflow.com"


def load_roboflow_settings() -> tuple[str, str]:
    """Load Roboflow API settings from .env or environment variables."""
    load_dotenv(override=True)

    api_key = os.getenv("ROBOFLOW_API_KEY")
    model_id = os.getenv("ROBOFLOW_MODEL_ID")

    if not api_key:
        raise ValueError(
            "ROBOFLOW_API_KEY is missing. Add it to your .env file or environment variables."
        )

    if not model_id:
        raise ValueError(
            "ROBOFLOW_MODEL_ID is missing. Add it to your .env file or environment variables."
        )

    return api_key, model_id


def run_tree_detection(
    image_path: str,
    api_key: str,
    model_id: str,
    confidence: int = 40,
    overlap: int = 30,
) -> dict[str, Any]:
    """Run Roboflow hosted object detection on a local image."""
    image_file = Path(image_path)

    if not image_file.exists():
        raise FileNotFoundError(f"Input image does not exist: {image_file}")

    if not api_key:
        raise ValueError("api_key is required.")

    if not model_id:
        raise ValueError("model_id is required. Example format: 'tree-detection/1'.")

    if not 0 <= confidence <= 100:
        raise ValueError("confidence must be between 0 and 100.")

    if not 0 <= overlap <= 100:
        raise ValueError("overlap must be between 0 and 100.")

    endpoint = f"{ROBOFLOW_DETECT_URL}/{model_id.strip('/')}"
    params = {
        "api_key": api_key,
        "confidence": confidence,
        "overlap": overlap,
    }

    encoded_image = base64.b64encode(image_file.read_bytes()).decode("utf-8")

    try:
        response = requests.post(
            endpoint,
            params=params,
            data=encoded_image,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=60,
        )
        response.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        raise RuntimeError(
            f"Roboflow request failed with status {response.status_code}: {response.text}"
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f"Roboflow request failed: {exc}") from exc

    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(f"Roboflow returned a non-JSON response: {response.text}") from exc


def draw_bounding_boxes(
    image_path: str,
    predictions: list[dict[str, Any]],
    output_path: str,
) -> str:
    """Draw Roboflow bounding boxes on an image and save the result."""
    source_path = Path(image_path)
    result_path = Path(output_path)

    if not source_path.exists():
        raise FileNotFoundError(f"Input image does not exist: {source_path}")

    result_path.parent.mkdir(parents=True, exist_ok=True)

    image = Image.open(source_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()

    for prediction in predictions:
        x = float(prediction["x"])
        y = float(prediction["y"])
        width = float(prediction["width"])
        height = float(prediction["height"])

        left = x - width / 2
        top = y - height / 2
        right = x + width / 2
        bottom = y + height / 2

        label = prediction.get("class", "tree")
        confidence = prediction.get("confidence")
        if confidence is not None:
            label = f"{label} {confidence:.2f}"

        draw.rectangle((left, top, right, bottom), outline="lime", width=3)
        draw.text((left, max(0, top - 12)), label, fill="lime", font=font)

    image.save(result_path)
    return str(result_path)
