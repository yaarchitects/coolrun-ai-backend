"""Backend-only smoke test for the Infrared UTCI area path.

Run from the project root with the Python environment used by FastAPI:

    .venv311\Scripts\python.exe backend\tests\test_infrared_area.py

This calls the same internal function used by /analyze-area and prints the
debug summary. It may call the live Infrared API and may take time.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from backend.app.imagery.vienna_orthofoto import create_bbox_polygon
from backend.app.simulation.infrared_runner import run_utci_for_polygon


def main() -> None:
    center_lat = 48.22057422849521
    center_lon = 16.411494407045154
    half_size = 0.00045
    polygon = create_bbox_polygon(
        {
            "west": center_lon - half_size,
            "south": center_lat - half_size,
            "east": center_lon + half_size,
            "north": center_lat + half_size,
        }
    )

    result = run_utci_for_polygon(
        polygon=polygon,
        time_period={"date": "2026-07-15", "hour": 15},
        vegetation_mode="merged",
        detected_tree_geojson_path=PROJECT_ROOT / "outputs" / "detected_trees.geojson",
        output_dir=PROJECT_ROOT / "outputs" / "test_infrared_area",
    )
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
