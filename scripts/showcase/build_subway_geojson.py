#!/usr/bin/env python3
"""Turn GTFS shapes.txt into a small simplified GeoJSON of subway lines (stand-in basemap)."""
import csv, json, sys
from collections import defaultdict

src, out = sys.argv[1], sys.argv[2]
shapes = defaultdict(list)
with open(src, newline="") as f:
    for row in csv.DictReader(f):
        shapes[row["shape_id"]].append((int(row["shape_pt_sequence"]), float(row["shape_pt_lon"]), float(row["shape_pt_lat"])))

def rdp(pts, eps):
    if len(pts) < 3:
        return pts
    (x1, y1), (x2, y2) = pts[0], pts[-1]
    dx, dy = x2 - x1, y2 - y1
    norm = (dx * dx + dy * dy) ** 0.5 or 1e-12
    dmax, idx = 0.0, 0
    for i in range(1, len(pts) - 1):
        x0, y0 = pts[i]
        d = abs(dy * x0 - dx * y0 + x2 * y1 - y2 * x1) / norm
        if d > dmax:
            dmax, idx = d, i
    if dmax > eps:
        return rdp(pts[: idx + 1], eps)[:-1] + rdp(pts[idx:], eps)
    return [pts[0], pts[-1]]

seen, lines = set(), []
for sid, pts in shapes.items():
    pts.sort()
    line = [(round(x, 5), round(y, 5)) for _, x, y in pts]
    key = (line[0], line[-1], len(line) // 20)
    rkey = (line[-1], line[0], len(line) // 20)
    if key in seen or rkey in seen:
        continue
    seen.add(key)
    lines.append([list(p) for p in rdp(line, 0.00025)])

geo = {"type": "Feature", "properties": {}, "geometry": {"type": "MultiLineString", "coordinates": lines}}
with open(out, "w") as f:
    json.dump(geo, f, separators=(",", ":"))
print(f"{len(lines)} lines, {sum(len(l) for l in lines)} points")
