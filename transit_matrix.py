#!/usr/bin/env python3
"""
transit_matrix.py — Build a route-to-route transit travel-time matrix
=====================================================================
Parses MTA GTFS subway data and KML route endpoints to produce
``transit_matrix.json``, consumed by walk_scheduler.py for
transit-aware backpack clustering and continuity scoring.

Run standalone:
    python transit_matrix.py

Or import and call ``load_transit_matrix()`` from walk_scheduler.py.
"""

import csv
import heapq
import json
import math
import os
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────────────────────────────────────

BASE_DIR  = Path(__file__).parent
GTFS_DIR  = BASE_DIR / "Subway_gtfs"
KML_DIR   = BASE_DIR / "Route_KMLs"
MATRIX_OUT = BASE_DIR / "transit_matrix.json"

# KML Placemark name -> route code (same mapping as walk_scheduler.py)
KML_NAME_TO_ROUTE = {
    "Harlem":                      "MN_HT",
    "Washington Heights":          "MN_WH",
    "Upper East Side":             "MN_UE",
    "Midtown":                     "MN_MT",
    "Union Square/LES":            "MN_LE",
    "Norwood":                     "BX_NW",
    "Hunts Point":                 "BX_HP",
    "Downtown Brooklyn":           "BK_DT",
    "Williamsburg":                "BK_WB",
    "Bed Sty":                     "BK_BS",
    "Crown Heights":               "BK_CH",
    "Sunset Park":                 "BK_SP",
    "Coney Island":                "BK_CI",
    "Flushing":                    "QN_FU",
    "Astoria/LIC":                 "QN_LI",
    "Jackson Heights":             "QN_JH",
    "Jamaica":                     "QN_JA",
    "Forest Hills":                "QN_FH",
    "LaGuardia Community College": "QN_LA",
    "East Elmhurst":               "QN_EE",
}


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1))
         * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _time_to_seconds(t: str) -> int:
    """Parse HH:MM:SS (may exceed 24h for GTFS overnight trips)."""
    parts = t.strip().split(":")
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])


# ─────────────────────────────────────────────────────────────────────────────
# 1.  PARSE GTFS
# ─────────────────────────────────────────────────────────────────────────────

def parse_stops() -> Tuple[
    Dict[str, dict],            # parent_id -> {name, lat, lon}
    Dict[str, str],             # platform_id -> parent_id
]:
    """
    Read stops.txt.  Return:
      parents  — only location_type=1 rows (station complexes)
      child_to_parent — maps every stop_id (including platform ids like 101N)
                        to its parent_station id
    """
    parents: Dict[str, dict] = {}
    child_to_parent: Dict[str, str] = {}

    with open(GTFS_DIR / "stops.txt", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sid  = row["stop_id"].strip()
            name = row["stop_name"].strip()
            lat  = float(row["stop_lat"])
            lon  = float(row["stop_lon"])
            loc  = row.get("location_type", "").strip()
            par  = row.get("parent_station", "").strip()

            if loc == "1":
                parents[sid] = {"name": name, "lat": lat, "lon": lon}
                child_to_parent[sid] = sid
            else:
                if par:
                    child_to_parent[sid] = par
                else:
                    # Standalone stop with no parent — treat as its own parent
                    parents[sid] = {"name": name, "lat": lat, "lon": lon}
                    child_to_parent[sid] = sid

    return parents, child_to_parent


def parse_transfers(child_to_parent: Dict[str, str]) -> List[Tuple[str, str, float]]:
    """
    Read transfers.txt and return (parent_a, parent_b, minutes) edges
    for inter-station transfers (skip same-station self-transfers).
    """
    edges: List[Tuple[str, str, float]] = []
    with open(GTFS_DIR / "transfers.txt", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            a = row["from_stop_id"].strip()
            b = row["to_stop_id"].strip()
            secs = int(row.get("min_transfer_time", "180") or "180")

            pa = child_to_parent.get(a, a)
            pb = child_to_parent.get(b, b)
            if pa != pb:
                edges.append((pa, pb, secs / 60.0))

    return edges


# Time-of-day windows (seconds from midnight) matching walk_scheduler.py TODS
TOD_WINDOWS = {
    "AM": (7 * 3600, 10 * 3600),     # 07:00–10:00  (morning rush, express active)
    "MD": (10 * 3600, 15 * 3600),    # 10:00–15:00  (midday, often local-only)
    "PM": (15 * 3600, 19 * 3600),    # 15:00–19:00  (evening rush, express active)
}


def build_trip_edges(
    child_to_parent: Dict[str, str],
    tod_filter: Optional[str] = None,
) -> Dict[Tuple[str, str], float]:
    """
    Scan stop_times.txt to extract minimum travel time (minutes) between
    consecutive parent stations on the same trip.

    If *tod_filter* is given ("AM", "MD", or "PM"), only edges from trips
    whose first stop departs within that window are included.  This captures
    express/local service patterns that change by time of day.

    Returns {(parent_a, parent_b): min_minutes}.
    """
    label = f" ({tod_filter})" if tod_filter else " (all)"
    print(f"  Parsing stop_times.txt{label} …")

    # If filtering by TOD, first scan to find qualifying trip IDs
    qualifying_trips: Optional[Set[str]] = None
    if tod_filter and tod_filter in TOD_WINDOWS:
        lo, hi = TOD_WINDOWS[tod_filter]
        qualifying_trips = set()
        # Collect the first departure time per trip
        trip_first_dep: Dict[str, int] = {}
        with open(GTFS_DIR / "stop_times.txt", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                trip = row["trip_id"].strip()
                if trip not in trip_first_dep:
                    dep = _time_to_seconds(row["departure_time"])
                    trip_first_dep[trip] = dep
        for trip, dep in trip_first_dep.items():
            if lo <= dep < hi:
                qualifying_trips.add(trip)
        print(f"    {len(qualifying_trips)} trips in {tod_filter} window "
              f"({lo//3600:02d}:00–{hi//3600:02d}:00)")

    edge_min: Dict[Tuple[str, str], float] = {}

    with open(GTFS_DIR / "stop_times.txt", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        prev_trip: Optional[str] = None
        prev_parent: Optional[str] = None
        prev_dep: int = 0

        for row in reader:
            trip = row["trip_id"].strip()

            # Skip trips outside the TOD window
            if qualifying_trips is not None and trip not in qualifying_trips:
                prev_trip = trip
                prev_parent = None
                prev_dep = 0
                continue

            sid  = row["stop_id"].strip()
            arr  = _time_to_seconds(row["arrival_time"])

            parent = child_to_parent.get(sid, sid)

            if trip == prev_trip and prev_parent is not None and parent != prev_parent:
                dt = (arr - prev_dep) / 60.0
                if dt < 0:
                    dt += 24 * 60  # overnight wrap
                if dt > 0:
                    key = (prev_parent, parent)
                    if key not in edge_min or dt < edge_min[key]:
                        edge_min[key] = dt
                    # Also add reverse direction with same weight
                    rkey = (parent, prev_parent)
                    if rkey not in edge_min or dt < edge_min[rkey]:
                        edge_min[rkey] = dt

            prev_trip   = trip
            prev_parent = parent
            prev_dep    = _time_to_seconds(row["departure_time"])

    return edge_min


def build_graph(
    trip_edges: Dict[Tuple[str, str], float],
    transfer_edges: List[Tuple[str, str, float]],
) -> Dict[str, List[Tuple[str, float]]]:
    """
    Adjacency list:  node -> [(neighbor, minutes), ...]
    Combines in-trip edges and transfer edges, keeping the minimum weight
    for each pair.
    """
    combined: Dict[Tuple[str, str], float] = dict(trip_edges)

    for pa, pb, mins in transfer_edges:
        key = (pa, pb)
        if key not in combined or mins < combined[key]:
            combined[key] = mins
        rkey = (pb, pa)
        if rkey not in combined or mins < combined[rkey]:
            combined[rkey] = mins

    adj: Dict[str, List[Tuple[str, float]]] = defaultdict(list)
    for (a, b), w in combined.items():
        adj[a].append((b, w))

    return dict(adj)


# ─────────────────────────────────────────────────────────────────────────────
# 2.  DIJKSTRA
# ─────────────────────────────────────────────────────────────────────────────

def dijkstra(
    graph: Dict[str, List[Tuple[str, float]]],
    source: str,
    targets: Optional[Set[str]] = None,
) -> Dict[str, float]:
    """
    Standard Dijkstra from *source*.
    If *targets* is given, stop early once all targets are reached.
    Returns {node: minutes}.
    """
    dist: Dict[str, float] = {source: 0.0}
    heap = [(0.0, source)]
    found: Set[str] = set()

    while heap:
        d, u = heapq.heappop(heap)
        if d > dist.get(u, float("inf")):
            continue
        if targets is not None:
            if u in targets:
                found.add(u)
                if found == targets:
                    break
        for v, w in graph.get(u, []):
            nd = d + w
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                heapq.heappush(heap, (nd, v))

    return dist


# ─────────────────────────────────────────────────────────────────────────────
# 3.  KML ENDPOINT EXTRACTION
# ─────────────────────────────────────────────────────────────────────────────

KML_NS = {"kml": "http://www.opengis.net/kml/2.2"}

# Collector KML first-name -> collector ID (mirrors walk_scheduler.py)
COLLECTOR_KML_NAMES = {
    "SOT": "Soto",
    "AYA": "Aya",
    "ALX": "Alex",
    "JAM": "James",
    "JEN": "Jennifer",
    "SCT": "Scott",
    "TER": "Terra",
}


def _extract_route_endpoints() -> Dict[str, Tuple[Tuple[float, float], Tuple[float, float]]]:
    """
    Parse all KML boro files and extract start/end <Point> coordinates
    for each route.  Returns {route_code: ((start_lat, start_lon), (end_lat, end_lon))}.

    In the KML, each route is a <Placemark> with a <LineString> (the walk path),
    followed by two <Placemark>s with <Point> geometries (start & end subway stops).
    """
    result: Dict[str, Tuple[Tuple[float, float], Tuple[float, float]]] = {}

    for kml_file in KML_DIR.glob("aq routes - *.kml"):
        tree = ET.parse(kml_file)
        root = tree.getroot()

        # Collect all placemarks in order
        placemarks = root.findall(".//kml:Placemark", KML_NS)

        i = 0
        while i < len(placemarks):
            pm = placemarks[i]
            name_el = pm.find("kml:name", KML_NS)
            name = name_el.text.strip() if name_el is not None and name_el.text else ""

            # Check if this placemark has a LineString (= a route path)
            ls = pm.find(".//kml:LineString", KML_NS)
            if ls is not None and name in KML_NAME_TO_ROUTE:
                route_code = KML_NAME_TO_ROUTE[name]
                # Next two placemarks should be Points (start, end)
                points: List[Tuple[float, float]] = []
                for j in range(i + 1, min(i + 3, len(placemarks))):
                    pt = placemarks[j].find(".//kml:Point/kml:coordinates", KML_NS)
                    if pt is not None and pt.text:
                        coords = pt.text.strip().split(",")
                        lon, lat = float(coords[0]), float(coords[1])
                        points.append((lat, lon))

                if len(points) >= 2:
                    result[route_code] = (points[0], points[1])
                elif len(points) == 1:
                    result[route_code] = (points[0], points[0])

                i += 1 + len(points)
                continue

            i += 1

    return result


# ─────────────────────────────────────────────────────────────────────────────
# 4.  SNAP KML ENDPOINTS TO NEAREST GTFS PARENT STATION
# ─────────────────────────────────────────────────────────────────────────────

def snap_to_station(
    lat: float, lon: float,
    parents: Dict[str, dict],
    max_km: float = 5.0,
) -> Optional[str]:
    """Find the nearest parent station within *max_km* km.

    Default 5 km to accommodate routes like QN_EE (East Elmhurst)
    that are far from any subway station.
    """
    best_id: Optional[str] = None
    best_d = float("inf")
    for sid, info in parents.items():
        d = haversine_km(lat, lon, info["lat"], info["lon"])
        if d < best_d:
            best_d, best_id = d, sid
    if best_d <= max_km:
        return best_id
    return None


def snap_routes(
    endpoints: Dict[str, Tuple[Tuple[float, float], Tuple[float, float]]],
    parents: Dict[str, dict],
) -> Dict[str, dict]:
    """
    For each route, snap start & end coordinates to the nearest GTFS parent
    station.  Returns {route_code: {start_stop, end_stop, start_name, end_name}}.
    """
    mapping: Dict[str, dict] = {}
    for route, (start_coord, end_coord) in endpoints.items():
        s_id = snap_to_station(start_coord[0], start_coord[1], parents)
        e_id = snap_to_station(end_coord[0], end_coord[1], parents)
        if s_id and e_id:
            mapping[route] = {
                "start_stop": s_id,
                "end_stop":   e_id,
                "start_name": parents[s_id]["name"],
                "end_name":   parents[e_id]["name"],
                "start_lat":  start_coord[0],
                "start_lon":  start_coord[1],
                "end_lat":    end_coord[0],
                "end_lon":    end_coord[1],
            }
        else:
            print(f"  WARNING: Could not snap {route}: start={start_coord} end={end_coord}")
    return mapping


# ─────────────────────────────────────────────────────────────────────────────
# 4b. PARSE COLLECTOR HOME LOCATIONS FROM KML
# ─────────────────────────────────────────────────────────────────────────────

def _parse_collector_homes() -> Dict[str, Tuple[float, float]]:
    """
    Parse Collector_Locs.kml and return {collector_id: (lat, lon)}.
    """
    kml_file = KML_DIR / "Collector_Locs.kml"
    if not kml_file.exists():
        print(f"  WARNING: {kml_file} not found — skipping collector home transit")
        return {}

    locs: Dict[str, Tuple[float, float]] = {}
    tree = ET.parse(kml_file)
    root = tree.getroot()

    for pm in root.findall(".//kml:Placemark", KML_NS):
        name_el = pm.find("kml:name", KML_NS)
        name = (name_el.text or "").strip() if name_el is not None else ""
        if name not in COLLECTOR_KML_NAMES.values():
            continue
        pt = pm.find(".//kml:Point/kml:coordinates", KML_NS)
        if pt is None:
            continue
        parts = (pt.text or "").strip().split(",")
        if len(parts) >= 2:
            try:
                lon, lat = float(parts[0]), float(parts[1])
                for cid, kname in COLLECTOR_KML_NAMES.items():
                    if kname == name:
                        locs[cid] = (lat, lon)
            except ValueError:
                pass

    return locs


def snap_collector_homes(
    collector_locs: Dict[str, Tuple[float, float]],
    parents: Dict[str, dict],
) -> Dict[str, str]:
    """
    Snap each collector's home to the nearest GTFS parent station.
    Returns {collector_id: parent_station_id}.
    """
    mapping: Dict[str, str] = {}
    for cid, (lat, lon) in collector_locs.items():
        sid = snap_to_station(lat, lon, parents)
        if sid:
            mapping[cid] = sid
            print(f"           {cid:>3} -> {parents[sid]['name']} ({sid})")
        else:
            print(f"  WARNING: Could not snap {cid} home to any station")
    return mapping


def compute_collector_to_route_matrix(
    collector_stations: Dict[str, str],
    route_stops: Dict[str, dict],
    graph: Dict[str, List[Tuple[str, float]]],
) -> Dict[str, Dict[str, float]]:
    """
    For each collector C and route R, compute transit time (minutes) from
    C's home station to R's start stop.

    Returns {collector_id: {route_code: minutes}}.
    """
    start_stops: Set[str] = {info["start_stop"] for info in route_stops.values()}

    # Dijkstra from each unique collector home station
    dist_cache: Dict[str, Dict[str, float]] = {}
    unique_homes = set(collector_stations.values())

    for idx, stop_id in enumerate(sorted(unique_homes), 1):
        print(f"  Dijkstra (collector) {idx}/{len(unique_homes)}: from {stop_id} …", end="\r")
        dist_cache[stop_id] = dijkstra(graph, stop_id, targets=start_stops)

    print(f"  Dijkstra (collector) complete — {len(unique_homes)} home stops.        ")

    matrix: Dict[str, Dict[str, float]] = {}
    for cid, home_stop in collector_stations.items():
        dists = dist_cache.get(home_stop, {})
        matrix[cid] = {}
        for rc, info in route_stops.items():
            t = dists.get(info["start_stop"])
            if t is not None:
                matrix[cid][rc] = round(t, 1)
            else:
                # Fallback: rough haversine estimate
                home_info = next(
                    (p for sid, p in graph.items() if sid == home_stop), None
                )
                matrix[cid][rc] = 999.0  # unreachable fallback
    return matrix


# ─────────────────────────────────────────────────────────────────────────────
# 5.  COMPUTE ROUTE-TO-ROUTE MATRIX
# ─────────────────────────────────────────────────────────────────────────────

def compute_route_matrix(
    route_stops: Dict[str, dict],
    graph: Dict[str, List[Tuple[str, float]]],
) -> Dict[str, Dict[str, float]]:
    """
    For each pair of routes (X, Y), compute transit time in minutes from
    X's END stop to Y's START stop.  This represents: a collector finishes
    walking route X and needs to get to route Y's starting station.

    Returns {route_x: {route_y: minutes}}.
    """
    # Collect all unique end-stops we need to run Dijkstra from
    end_stops: Dict[str, List[str]] = defaultdict(list)   # stop_id -> [route_codes that end here]
    start_stops: Set[str] = set()

    for rc, info in route_stops.items():
        end_stops[info["end_stop"]].append(rc)
        start_stops.add(info["start_stop"])

    # Run Dijkstra from each unique end-stop
    dist_from: Dict[str, Dict[str, float]] = {}
    unique_ends = set(end_stops.keys())
    total = len(unique_ends)

    for idx, stop_id in enumerate(sorted(unique_ends), 1):
        print(f"  Dijkstra {idx}/{total}: from {stop_id} …", end="\r")
        dist_from[stop_id] = dijkstra(graph, stop_id, targets=start_stops)

    print(f"  Dijkstra complete — {total} source stops processed.        ")

    # Build route-to-route matrix
    matrix: Dict[str, Dict[str, float]] = {}
    for rx, rx_info in route_stops.items():
        matrix[rx] = {}
        end_id = rx_info["end_stop"]
        dists  = dist_from.get(end_id, {})
        for ry, ry_info in route_stops.items():
            start_id = ry_info["start_stop"]
            t = dists.get(start_id, None)
            if t is not None:
                matrix[rx][ry] = round(t, 1)
            else:
                # Fallback: haversine estimate at 25 km/h effective subway speed
                d_km = haversine_km(
                    rx_info["end_lat"], rx_info["end_lon"],
                    ry_info["start_lat"], ry_info["start_lon"],
                )
                matrix[rx][ry] = round(d_km / 25 * 60, 1)  # rough minutes

    return matrix


# ─────────────────────────────────────────────────────────────────────────────
# 6.  MAIN / API
# ─────────────────────────────────────────────────────────────────────────────

def _build_tod_graph(
    child_to_parent: Dict[str, str],
    transfer_edges: List[Tuple[str, str, float]],
    tod: str,
) -> Dict[str, List[Tuple[str, float]]]:
    """Build a graph filtered to trips running in the given TOD window."""
    trip_edges = build_trip_edges(child_to_parent, tod_filter=tod)
    print(f"         {len(trip_edges)} directed edges for {tod}")
    return build_graph(trip_edges, transfer_edges)


def build_and_save() -> dict:
    """Full pipeline: parse GTFS -> snap KML -> Dijkstra -> save JSON."""
    # Force UTF-8 on Windows
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    print("\n>> transit_matrix.py -- Building transit travel-time matrix\n")

    # 1. Parse GTFS stops
    print("  [1/8] Parsing stops.txt …")
    parents, child_to_parent = parse_stops()
    print(f"         {len(parents)} parent stations loaded")

    # 2. Build ALL-day trip edges (used as the baseline)
    print("  [2/8] Building trip edges (all day) …")
    trip_edges_all = build_trip_edges(child_to_parent)
    print(f"         {len(trip_edges_all)} directed edges from all trips")

    # 3. Parse transfers
    print("  [3/8] Parsing transfers.txt …")
    transfer_edges = parse_transfers(child_to_parent)
    print(f"         {len(transfer_edges)} inter-station transfer edges")

    # Build all-day graph (baseline)
    graph_all = build_graph(trip_edges_all, transfer_edges)
    print(f"         Graph: {len(graph_all)} nodes with adjacency lists")

    # 4. Build per-TOD graphs
    print("  [4/8] Building time-of-day graphs …")
    tod_graphs: Dict[str, Dict[str, List[Tuple[str, float]]]] = {}
    for tod in ("AM", "MD", "PM"):
        tod_graphs[tod] = _build_tod_graph(child_to_parent, transfer_edges, tod)

    # 5. Extract and snap KML endpoints
    print("  [5/8] Extracting KML route endpoints and snapping to stations …")
    endpoints = _extract_route_endpoints()
    print(f"         {len(endpoints)} routes extracted from KML")
    route_stops = snap_routes(endpoints, parents)
    print(f"         {len(route_stops)} routes snapped to GTFS stations:")
    for rc in sorted(route_stops):
        info = route_stops[rc]
        if info["start_stop"] == info["end_stop"]:
            print(f"           {rc:>6} (loop) {info['start_name']} ({info['start_stop']})")
        else:
            print(
                f"           {rc:>6} -> {info['start_name']} ({info['start_stop']}) "
                f"-> {info['end_name']} ({info['end_stop']})"
            )

    # 6. Compute route-to-route matrices (all-day + per-TOD)
    print("  [6/8] Computing route-to-route transit times …")
    print("         All-day baseline:")
    matrix_all = compute_route_matrix(route_stops, graph_all)
    tod_matrices: Dict[str, Dict[str, Dict[str, float]]] = {}
    for tod in ("AM", "MD", "PM"):
        print(f"         {tod} matrix:")
        tod_matrices[tod] = compute_route_matrix(route_stops, tod_graphs[tod])

    # 7. Parse collector home locations and snap to stations
    print("  [7/8] Snapping collector homes to nearest stations …")
    collector_locs = _parse_collector_homes()
    print(f"         {len(collector_locs)} collector homes found")
    collector_stations = snap_collector_homes(collector_locs, parents)

    # 8. Compute collector-to-route transit times (all-day + per-TOD)
    collector_route_matrix: Dict[str, Dict[str, float]] = {}
    tod_collector_matrices: Dict[str, Dict[str, Dict[str, float]]] = {}
    if collector_stations:
        print("  [8/8] Computing collector home → route start transit times …")
        collector_route_matrix = compute_collector_to_route_matrix(
            collector_stations, route_stops, graph_all,
        )
        for tod in ("AM", "MD", "PM"):
            print(f"         {tod} collector matrix:")
            tod_collector_matrices[tod] = compute_collector_to_route_matrix(
                collector_stations, route_stops, tod_graphs[tod],
            )
    else:
        print("  [8/8] No collector homes — skipping collector→route matrix")

    # Assemble output
    output = {
        "description": (
            "Transit travel-time matrices (minutes) between walk routes, "
            "with time-of-day variants (AM/MD/PM) and collector home → route times. "
            "route_to_route_minutes = all-day baseline. "
            "tod_route_to_route_minutes.{AM,MD,PM} = per-TOD matrices. "
            "collector_to_route_minutes = all-day collector→route. "
            "tod_collector_to_route_minutes.{AM,MD,PM} = per-TOD collector→route."
        ),
        "route_stops": route_stops,
        "route_to_route_minutes": matrix_all,
        "tod_route_to_route_minutes": tod_matrices,
        "collector_stations": {
            cid: {"station_id": sid, "station_name": parents[sid]["name"]}
            for cid, sid in collector_stations.items()
        },
        "collector_to_route_minutes": collector_route_matrix,
        "tod_collector_to_route_minutes": tod_collector_matrices,
    }

    with open(MATRIX_OUT, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"\n  Saved -> {MATRIX_OUT}")
    print(f"    {len(matrix_all)} × {len(matrix_all)} route matrix (all-day + 3 TOD variants)\n")

    # Print a readable summary for each TOD
    for label, matrix in [("ALL-DAY", matrix_all)] + [(t, tod_matrices[t]) for t in ("AM", "MD", "PM")]:
        routes = sorted(matrix.keys())
        print(f"  {label} transit time matrix (minutes):")
        hdr = "          " + "".join(f"{r:>8}" for r in routes)
        print(hdr)
        for rx in routes:
            row = f"  {rx:>6}  " + "".join(
                f"{matrix[rx].get(ry, -1):>8.1f}" for ry in routes
            )
            print(row)
        print()
    print()

    return output


def load_transit_matrix(path: Optional[Path] = None) -> dict:
    """Load a previously generated transit_matrix.json."""
    p = path or MATRIX_OUT
    if not p.exists():
        raise FileNotFoundError(
            f"transit_matrix.json not found at {p}. "
            f"Run `python transit_matrix.py` first to generate it."
        )
    with open(p, encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    build_and_save()
