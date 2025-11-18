# congestion_features.py
from __future__ import annotations
import math
import time
from typing import Dict, List, Tuple
import requests
import numpy as np
from shapely.geometry import Point, Polygon, LineString
from shapely.validation import make_valid
from pyproj import CRS, Transformer

# CONFIG
OVERPASS_ENDPOINTS = ["https://overpass-api.de/api/interpreter"]
USER_AGENT = "UrbanCongestionDetector-IN/1.0"
TIMEOUT = 90
RETRY = 3

DEFAULT_WIDTHS = {
    "motorway": 24, "trunk": 22, "primary": 18, "secondary": 14,
    "tertiary": 10, "residential": 7, "unclassified": 7, "service": 6,
}

def _projector(lat: float, lng: float) -> Tuple[Transformer, Transformer]:
    wgs84 = CRS.from_epsg(4326)
    zone = int((math.floor((lng + 180) / 6) % 60) + 1)
    south = lat < 0
    utm = CRS.from_string(f"+proj=utm +zone={zone} +{'south' if south else 'north'} +datum=WGS84 +units=m +no_defs")
    fwd = Transformer.from_crs(wgs84, utm, always_xy=True)
    inv = Transformer.from_crs(utm, wgs84, always_xy=True)
    return fwd, inv

def _buffer_circle(lat: float, lng: float, radius_m: float) -> Polygon:
    fwd, _ = _projector(lat, lng)
    x, y = fwd.transform(lng, lat)
    return Point(x, y).buffer(radius_m, resolution=32)

def _overpass(query: str) -> Dict:
    headers = {"User-Agent": USER_AGENT}
    for _ in range(RETRY):
        for url in OVERPASS_ENDPOINTS:
            try:
                r = requests.post(url, data={"data": query}, headers=headers, timeout=TIMEOUT)
                if r.status_code in (429, 500, 502, 503, 504):
                    time.sleep(2)
                    continue
                r.raise_for_status()
                return r.json()
            except:
                time.sleep(1)
    raise RuntimeError("Overpass failed")

# NEW: Return raw building tag or fallback
def _get_building_type(tags: dict) -> str:
    b = tags.get("building")
    if not b:
        return "None"
    # Clean up common variations
    b = str(b).strip().lower()
    if b in ("yes", "1", "true"):
        return "building=yes"
    return f"building={b}"

def _collect_buildings(data: Dict, fwd: Transformer, buf: Polygon) -> List[Tuple[Polygon, str]]:
    buildings = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        if not tags.get("building"): 
            continue  # Only buildings with building=*
        geom = el.get("geometry")
        if not geom or len(geom) < 3: 
            continue

        coords = [(p["lon"], p["lat"]) for p in geom]
        xs, ys = zip(*[fwd.transform(lon, lat) for lon, lat in coords])
        try:
            poly = Polygon(list(zip(xs, ys)))
            if not poly.is_valid: 
                poly = make_valid(poly)
            if not poly.is_valid: 
                continue

            inter = poly.intersection(buf)
            if inter.is_empty: 
                continue
            if not inter.is_valid: 
                inter = make_valid(inter)
            if not inter.is_valid: 
                continue

            btype = _get_building_type(tags)
            buildings.append((inter, btype))  # INCLUDE ALL
        except: 
            continue
    return buildings

def _collect_roads(data: Dict, fwd: Transformer) -> List[dict]:
    roads = []
    for el in data.get("elements", []):
        if el.get("type") != "way": continue
        tags = el.get("tags", {})
        if "highway" not in tags: continue
        geom = el.get("geometry")
        if not geom or len(geom) < 2: continue
        coords = [(p["lon"], p["lat"]) for p in geom]
        xs, ys = zip(*[fwd.transform(lon, lat) for lon, lat in coords])
        line = LineString(list(zip(xs, ys)))
        if line.length < 0.1: continue

        width = None
        if "width" in tags:
            try: width = float(str(tags["width"]).split()[0])
            except: pass
        if width is None and "lanes" in tags:
            try: width = max(3.0, float(str(tags["lanes"]).split()[0]) * 3.3)
            except: pass
        if width is None:
            width = DEFAULT_WIDTHS.get(tags.get("highway"))

        roads.append({"geom": line, "width": width})
    return roads

def _collect_water(data: Dict, fwd: Transformer, buf: Polygon) -> float:
    water_area = 0.0
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        if tags.get("natural") != "water" and tags.get("waterway") != "riverbank":
            continue
        geom = el.get("geometry")
        if not geom or len(geom) < 3: continue
        coords = [(p["lon"], p["lat"]) for p in geom]
        xs, ys = zip(*[fwd.transform(lon, lat) for lon, lat in coords])
        try:
            poly = Polygon(list(zip(xs, ys)))
            if not poly.is_valid: poly = make_valid(poly)
            if not poly.is_valid: continue
            inter = poly.intersection(buf)
            if not inter.is_empty:
                water_area += inter.area
        except: continue
    return water_area

def _collect_water_polygons(data: Dict, fwd: Transformer, buf: Polygon) -> List[Polygon]:
    waters = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        if tags.get("natural") != "water" and tags.get("waterway") != "riverbank":
            continue
        geom = el.get("geometry")
        if not geom or len(geom) < 3: continue
        coords = [(p["lon"], p["lat"]) for p in geom]
        xs, ys = zip(*[fwd.transform(lon, lat) for lon, lat in coords])
        try:
            poly = Polygon(list(zip(xs, ys)))
            if not poly.is_valid: poly = make_valid(poly)
            if not poly.is_valid: continue
            inter = poly.intersection(buf)
            if not inter.is_empty:
                waters.append(inter)
        except: continue
    return waters

def get_congestion_features(lat: float, lng: float, radius: int = 500):
    fwd, inv = _projector(lat, lng)
    buf = _buffer_circle(lat, lng, radius)
    delta = radius / 111000 * 2.0
    bbox = (lat-delta, lng-delta, lat+delta, lng+delta)

    q = f"""
    [out:json][timeout:90];
    (
      way["building"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
      way["highway"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
      way["natural"="water"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
      way["waterway"="riverbank"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
    );
    out geom;
    """
    data = _overpass(q)
    buildings_with_type = _collect_buildings(data, fwd, buf)
    roads_raw = _collect_roads(data, fwd)
    water_area = _collect_water(data, fwd, buf)
    water_polygons = _collect_water_polygons(data, fwd, buf)

    # Clip roads
    road_area_total = 0.0
    road_details = []
    for rd in roads_raw:
        inter = rd["geom"].intersection(buf)
        if inter.is_empty or inter.length < 0.1: continue
        parts = inter.geoms if hasattr(inter, 'geoms') else [inter]
        for part in parts:
            length = part.length
            width = rd["width"]
            if width:
                road_area_total += length * width
            road_details.append({"geom": part, "width": width})

    # Area
    area_m2 = np.pi * radius ** 2
    total_building_area = sum(p.area for p, _ in buildings_with_type)
    true_open_space = area_m2 - total_building_area - road_area_total - water_area

    # Building Type Stats (raw tags)
    type_areas = {}
    for geom, btype in buildings_with_type:
        area = geom.area
        type_areas[btype] = type_areas.get(btype, 0) + area

    # Metrics
    metrics = {
        "analysis_area_m2": area_m2,
        "total_building_area_m2": total_building_area,
        "building_coverage_ratio": total_building_area / area_m2,
        "total_road_area_m2": road_area_total,
        "road_area_coverage": road_area_total / area_m2,
        "water_area_m2": water_area,
        "water_coverage_ratio": water_area / area_m2,
        "true_open_space_m2": true_open_space,
        "true_open_space_ratio": true_open_space / area_m2,
        "detected_buildings": len(buildings_with_type),
        "building_types_area": type_areas,
    }

    return (
        metrics,
        buildings_with_type,
        road_details,
        buf,
        fwd,
        inv,
        water_polygons,
    )