#!/usr/bin/env python3
"""Count CSV points inside KML polygons (no external deps)."""

import argparse
import csv
import sys
import xml.etree.ElementTree as ET
from typing import Iterable, List, Tuple, Optional

KML_NS = {
    "kml": "http://www.opengis.net/kml/2.2",
}

Point = Tuple[float, float]  # (lon, lat)
Ring = List[Point]


def parse_coordinates(text: str) -> Ring:
    coords: Ring = []
    if not text:
        return coords
    for token in text.strip().split():
        parts = token.split(",")
        if len(parts) < 2:
            continue
        lon = float(parts[0])
        lat = float(parts[1])
        coords.append((lon, lat))
    return coords


def parse_kml_polygons(kml_path: str):
    tree = ET.parse(kml_path)
    root = tree.getroot()
    placemarks = root.findall(".//kml:Placemark", KML_NS)
    polygons = []

    for pm in placemarks:
        name = pm.findtext("kml:name", default="(unnamed)", namespaces=KML_NS)
        poly = pm.find(".//kml:Polygon", KML_NS)
        if poly is None:
            continue

        outer_coords = None
        outer = poly.find(".//kml:outerBoundaryIs//kml:LinearRing//kml:coordinates", KML_NS)
        if outer is not None and outer.text:
            outer_coords = parse_coordinates(outer.text)

        if not outer_coords:
            continue

        holes = []
        for inner in poly.findall(".//kml:innerBoundaryIs//kml:LinearRing//kml:coordinates", KML_NS):
            ring = parse_coordinates(inner.text or "")
            if ring:
                holes.append(ring)

        # Precompute bbox for quick rejection
        lons = [p[0] for p in outer_coords]
        lats = [p[1] for p in outer_coords]
        bbox = (min(lons), min(lats), max(lons), max(lats))

        polygons.append({
            "name": name,
            "outer": outer_coords,
            "holes": holes,
            "bbox": bbox,
        })

    return polygons


def point_in_ring(point: Point, ring: Ring) -> bool:
    # Ray casting; ring is list of (lon, lat)
    x, y = point
    inside = False
    n = len(ring)
    if n < 3:
        return False

    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        intersect = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / (yj - yi + 1e-16) + xi
        )
        if intersect:
            inside = not inside
        j = i

    return inside


def point_in_polygon(point: Point, outer: Ring, holes: List[Ring]) -> bool:
    if not point_in_ring(point, outer):
        return False
    for hole in holes:
        if point_in_ring(point, hole):
            return False
    return True


def parse_float(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    s = value.strip()
    if not s:
        return None
    if s.endswith("%"):
        s = s[:-1]
    try:
        return float(s)
    except ValueError:
        return None


def load_points(csv_path: str, lat_col: str, lon_col: str, weight_col: Optional[str]):
    points = []
    with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("CSV has no header row.")

        # Handle BOM headers and case-insensitivity
        field_map = {name.strip().lstrip("\ufeff").lower(): name for name in reader.fieldnames}
        lat_key = field_map.get(lat_col.lower())
        lon_key = field_map.get(lon_col.lower())
        weight_key = field_map.get(weight_col.lower()) if weight_col else None

        if not lat_key or not lon_key:
            raise ValueError(f"CSV missing columns. Found: {reader.fieldnames}")

        for row in reader:
            lat = parse_float(row.get(lat_key))
            lon = parse_float(row.get(lon_key))
            if lat is None or lon is None:
                continue
            weight = parse_float(row.get(weight_key)) if weight_key else None
            points.append((lon, lat, weight))

    return points


def main():
    ap = argparse.ArgumentParser(description="Count CSV points inside KML polygons")
    ap.add_argument("--kml", required=True, help="Path to KML file")
    ap.add_argument("--csv", required=True, help="Path to CSV file with LAT/LON columns")
    ap.add_argument("--lat-col", default="LAT", help="Latitude column name (default: LAT)")
    ap.add_argument("--lon-col", default="LON", help="Longitude column name (default: LON)")
    ap.add_argument("--weight-col", default="FF", help="Optional weight column (default: FF)")
    ap.add_argument("--output", required=True, help="Output CSV path")
    args = ap.parse_args()

    polygons = parse_kml_polygons(args.kml)
    if not polygons:
        raise SystemExit("No polygons found in KML.")

    points = load_points(args.csv, args.lat_col, args.lon_col, args.weight_col)
    if not points:
        raise SystemExit("No valid points found in CSV.")

    results = []
    for poly in polygons:
        minx, miny, maxx, maxy = poly["bbox"]
        count = 0
        weight_sum = 0.0
        weight_count = 0

        for lon, lat, weight in points:
            if lon < minx or lon > maxx or lat < miny or lat > maxy:
                continue
            if point_in_polygon((lon, lat), poly["outer"], poly["holes"]):
                count += 1
                if weight is not None:
                    weight_sum += weight
                    weight_count += 1

        results.append({
            "polygon": poly["name"],
            "count": count,
            "weight_sum": round(weight_sum, 6),
            "weight_count": weight_count,
        })

    with open(args.output, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["polygon", "count", "weight_sum", "weight_count"])
        writer.writeheader()
        writer.writerows(results)

    print(f"Wrote {len(results)} polygon results to {args.output}")


if __name__ == "__main__":
    main()
