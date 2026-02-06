#!/usr/bin/env python3
"""Local web app: count CSV points inside KML polygons."""

import csv
import io
import os
import tempfile
import uuid
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB

KML_NS = {"kml": "http://www.opengis.net/kml/2.2"}
Point = Tuple[float, float]  # (lon, lat)
Ring = List[Point]

STORE: Dict[str, Dict] = {}


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

        outer = poly.find(".//kml:outerBoundaryIs//kml:LinearRing//kml:coordinates", KML_NS)
        outer_coords = parse_coordinates(outer.text if outer is not None else "")
        if not outer_coords:
            continue

        holes = []
        for inner in poly.findall(".//kml:innerBoundaryIs//kml:LinearRing//kml:coordinates", KML_NS):
            ring = parse_coordinates(inner.text or "")
            if ring:
                holes.append(ring)

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


def polygons_to_geojson(polygons):
    features = []
    for poly in polygons:
        rings = [poly["outer"]] + poly["holes"]
        coords = [[[lon, lat] for lon, lat in ring] for ring in rings]
        features.append({
            "type": "Feature",
            "properties": {"name": poly["name"]},
            "geometry": {"type": "Polygon", "coordinates": coords},
        })
    return {"type": "FeatureCollection", "features": features}


def write_results_csv(results) -> io.StringIO:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["polygon", "count", "weight_sum", "weight_percent"])
    writer.writeheader()
    writer.writerows(results)
    output.seek(0)
    return output


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/process")
def process():
    kml_file = request.files.get("kml_file")
    csv_file = request.files.get("csv_file")
    lat_col = request.form.get("lat_col", "LAT")
    lon_col = request.form.get("lon_col", "LON")
    weight_col = request.form.get("weight_col", "FF") or None

    if not kml_file or not csv_file:
        return render_template("index.html", error="Please upload both KML and CSV files."), 400

    run_id = str(uuid.uuid4())
    workdir = tempfile.mkdtemp(prefix="kml_count_")
    kml_path = os.path.join(workdir, "polygons.kml")
    csv_path = os.path.join(workdir, "points.csv")
    kml_file.save(kml_path)
    csv_file.save(csv_path)

    polygons = parse_kml_polygons(kml_path)
    if not polygons:
        return render_template("index.html", error="No polygons found in KML."), 400

    points = load_points(csv_path, lat_col, lon_col, weight_col)
    if not points:
        return render_template("index.html", error="No valid points found in CSV."), 400

    results = []
    for poly in polygons:
        minx, miny, maxx, maxy = poly["bbox"]
        count = 0
        weight_sum = 0.0

        for lon, lat, weight in points:
            if lon < minx or lon > maxx or lat < miny or lat > maxy:
                continue
            if point_in_polygon((lon, lat), poly["outer"], poly["holes"]):
                count += 1
                if weight is not None:
                    weight_sum += weight

        results.append({
            "polygon": poly["name"],
            "count": count,
            "weight_sum": round(weight_sum, 6),
        })

    total_weight = sum(r["weight_sum"] for r in results) or 0.0
    for r in results:
        r["weight_percent"] = round((r["weight_sum"] / total_weight) * 100, 2) if total_weight else 0.0

    results.sort(key=lambda r: r["weight_sum"], reverse=True)

    weights = [w for _, _, w in points if w is not None]
    weight_min = min(weights) if weights else 0.0
    weight_max = max(weights) if weights else 0.0

    STORE[run_id] = {
        "results": results,
        "results_csv": write_results_csv(results).getvalue(),
        "polygons_geojson": polygons_to_geojson(polygons),
        "points": points,
        "weight_min": weight_min,
        "weight_max": weight_max,
        "point_count": len(points),
    }

    return redirect(url_for("results", run_id=run_id))


@app.get("/results/<run_id>")
def results(run_id: str):
    data = STORE.get(run_id)
    if not data:
        return render_template("index.html", error="Results not found. Please re-run."), 404
    return render_template(
        "results.html",
        run_id=run_id,
        results=data["results"],
        point_count=data["point_count"],
    )


@app.get("/results/<run_id>/data")
def results_data(run_id: str):
    data = STORE.get(run_id)
    if not data:
        return jsonify({"error": "not found"}), 404

    # Leaflet.heat expects [lat, lon, intensity]
    heat_points = []
    for lon, lat, weight in data["points"]:
        if weight is None:
            heat_points.append([lat, lon, 0.0])
        else:
            heat_points.append([lat, lon, weight])

    return jsonify({
        "polygons": data["polygons_geojson"],
        "points": heat_points,
        "weight_min": data["weight_min"],
        "weight_max": data["weight_max"],
    })


@app.get("/download/<run_id>.csv")
def download(run_id: str):
    data = STORE.get(run_id)
    if not data:
        return render_template("index.html", error="Results not found. Please re-run."), 404
    return send_file(
        io.BytesIO(data["results_csv"].encode("utf-8")),
        mimetype="text/csv",
        as_attachment=True,
        download_name="polygon_counts.csv",
    )


if __name__ == "__main__":
    app.run(debug=True)
