"""Folium route visualization helpers for CoolRun AI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROUTE_STYLES = {
    "shortest_route": {
        "color": "#5b6472",
        "weight": 5,
        "dash_array": "8, 6",
        "label": "Shortest route",
    },
    "greenest_route": {
        "color": "#147d42",
        "weight": 6,
        "dash_array": None,
        "label": "Greenest route",
    },
    "coolest_route": {
        "color": "#0969da",
        "weight": 6,
        "dash_array": "2, 7",
        "label": "Coolest route",
    },
}


def load_json(path: str) -> dict[str, Any]:
    """Load a JSON or GeoJSON file."""
    json_path = Path(path)
    if not json_path.exists():
        raise FileNotFoundError(f"File does not exist: {json_path}")

    return json.loads(json_path.read_text(encoding="utf-8"))


def route_popup_html(route_label: str, properties: dict[str, Any]) -> str:
    """Build popup HTML for a scored route."""
    average_utci = properties.get("average_utci")
    average_utci_text = "n/a" if average_utci is None else f"{average_utci:.2f} C"

    return f"""
    <strong>{route_label}</strong><br>
    Distance: {properties.get("distance_m", 0):.0f} m<br>
    Tree density: {properties.get("tree_density", 0):.2f}<br>
    Average UTCI: {average_utci_text}<br>
    CoolRun score: {properties.get("coolrun_score", 0):.2f}
    """


def route_positions(feature: dict[str, Any]) -> list[list[float]]:
    """Return route coordinates as Folium [lat, lon] positions."""
    return [
        [lat, lon]
        for lon, lat in feature.get("geometry", {}).get("coordinates", [])
    ]


def tree_positions(tree_geojson: dict[str, Any]) -> list[tuple[float, float]]:
    """Return detected tree coordinates as lat/lon tuples."""
    positions = []

    for feature in tree_geojson.get("features", []):
        geometry = feature.get("geometry", {})
        if geometry.get("type") != "Point":
            continue

        lon, lat = geometry.get("coordinates", [None, None])[:2]
        if lon is not None and lat is not None:
            positions.append((float(lat), float(lon)))

    return positions


def selected_route_roles(scored_routes: dict[str, Any]) -> dict[str, str]:
    """Map route id to selected role name."""
    selected = scored_routes.get("selected", {})
    roles = {}

    for role, route_id in selected.items():
        roles[route_id] = role

    return roles


def save_routes_map(
    scored_routes: dict[str, Any],
    tree_geojson: dict[str, Any],
    output_path: str,
    utci_summary: dict[str, Any] | None = None,
) -> str:
    """Save an interactive Folium map with routes and detected trees."""
    import folium

    features = scored_routes.get("features", [])
    if not features:
        raise ValueError("No route features found in scored route GeoJSON.")

    first_route_positions = route_positions(features[0])
    if not first_route_positions:
        raise ValueError("First route has no coordinates.")

    start = first_route_positions[0]
    end = first_route_positions[-1]
    center = [
        (start[0] + end[0]) / 2,
        (start[1] + end[1]) / 2,
    ]

    fmap = folium.Map(location=center, zoom_start=16, tiles="OpenStreetMap")
    roles_by_route_id = selected_route_roles(scored_routes)

    for feature in features:
        properties = feature.get("properties", {})
        route_id = properties.get("id")
        role = roles_by_route_id.get(route_id, "alternative_route")
        style = ROUTE_STYLES.get(
            role,
            {
                "color": "#8b949e",
                "weight": 4,
                "dash_array": "4, 6",
                "label": "Alternative route",
            },
        )
        positions = route_positions(feature)

        folium.PolyLine(
            positions,
            color=style["color"],
            weight=style["weight"],
            opacity=0.9,
            dash_array=style["dash_array"],
            tooltip=style["label"],
            popup=folium.Popup(route_popup_html(style["label"], properties), max_width=280),
        ).add_to(fmap)

    for lat, lon in tree_positions(tree_geojson):
        folium.CircleMarker(
            location=[lat, lon],
            radius=2.5,
            color="green",
            fill=True,
            fill_color="lime",
            fill_opacity=0.75,
            weight=1,
        ).add_to(fmap)

    folium.Marker(
        location=start,
        tooltip="Start",
        popup="Start",
        icon=folium.Icon(color="green", icon="play"),
    ).add_to(fmap)
    folium.Marker(
        location=end,
        tooltip="End",
        popup="End",
        icon=folium.Icon(color="red", icon="stop"),
    ).add_to(fmap)

    if utci_summary:
        mean_cooling = utci_summary.get("mean_cooling_effect")
        max_cooling = utci_summary.get("max_cooling_effect")
        summary_html = f"""
        <strong>UTCI summary</strong><br>
        Mean cooling: {mean_cooling if mean_cooling is not None else "n/a"}<br>
        Max cooling: {max_cooling if max_cooling is not None else "n/a"}
        """
        folium.Marker(
            location=center,
            tooltip="UTCI summary",
            popup=folium.Popup(summary_html, max_width=260),
            icon=folium.Icon(color="blue", icon="info-sign"),
        ).add_to(fmap)

    folium.LayerControl().add_to(fmap)
    fmap.fit_bounds([start, end])

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    fmap.save(output_file)

    return str(output_file)
