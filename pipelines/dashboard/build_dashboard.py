#!/usr/bin/env python3
"""Generates dashboard.html with embedded route KML data and sample log."""
import json, re, xml.etree.ElementTree as ET, math as _math, sys
from pathlib import Path
import openpyxl as _opxl

BASE = Path(__file__).parent  # pipelines/dashboard/ — used for co-located imports

# Add repo root to sys.path so shared package is importable
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.paths import (
    ROUTES_DATA_JSON, WALKS_LOG, ROUTES_KML_DIR, PERSISTED_DIR,
    SCHEDULE_OUTPUT_JSON, WEATHER_JSON, ROUTE_GROUPS,
    DASHBOARD_HTML, AVAILABILITY_HEATMAP_HTML,
)
from shared.gcs import pull_if_available as gcs_pull

# Pull the latest bucket copies before reading — the bucket is authoritative.
gcs_pull("Walks_Log.txt",        WALKS_LOG)
gcs_pull("schedule_output.json", SCHEDULE_OUTPUT_JSON)
gcs_pull("weather.json",         WEATHER_JSON)
gcs_pull("upload_failures.json", PERSISTED_DIR / "upload_failures.json")

# Read sources
with open(ROUTES_DATA_JSON, encoding="utf-8") as f:
    routes_json = f.read()

with open(WALKS_LOG, encoding="utf-8") as f:
    sample_log_raw = f.read()

# Escape sample log for JS template literal
sample_log_js = sample_log_raw.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")

affinity_json = json.dumps({
    "SOT": [],
    "AYA": ["MT","LE","DT","WB","BS","CH","SP","CI"],
    "ALX": ["LE","WB","BS","JA","FH","LA"],
    "TAH": ["HT","MT","LE","FU","LI","JH","JA","FH","LA","EE"],
    "JAM": ["JH","FH"],
    "JEN": ["HP","HT","WH","UE","MT","LE","DT","WB","BS","FU","LI","JH","FH","LA","EE"],
    "SCT": ["HT","WH","FU","LI","JH","FH","LA","EE"],
    "TER": ["HT","MT","LE","DT","WB","BS","CH","LI","LA"],
    "PRA": [],
    "NAT": [],
    "NRS": [],
})

# -- Collector home locations from KML ---
_KML_NAME_TO_CID = {
    "Terra":                    "TER",
    "Aya":                      "AYA",
    "Scott":                    "SCT",
    "Alex":                     "ALX",
    "Jennifer":                 "JEN",
    "James":                    "JAM",
    "Taha":                     "TAH",
    "Soteri":                   "SOT",
    "Prof. Naresh Devineni":    "NRS",
    "Prof. Prathap Ramamurthy": "PRA",
    "Angy":                     "ANG",
}
_COLLECTOR_FULL = {
    "SOT":"Soteri","AYA":"Aya Nasri","ALX":"Alex","TAH":"Taha",
    "JAM":"James","JEN":"Jennifer","SCT":"Scott","TER":"Terra",
    "PRA":"Prof. Prathap","NAT":"Nathan","NRS":"Prof. Naresh",
    "ANG":"Angy",
}
_NON_COLLECTORS = {"ANG"}
_collector_homes = {}
_kml_path = ROUTES_KML_DIR / "Collector_Locs.kml"
if _kml_path.exists():
    _ns = {"k": "http://www.opengis.net/kml/2.2"}
    for _pm in ET.parse(_kml_path).findall(".//k:Placemark", _ns):
        _nm  = (_pm.findtext("k:name", "", _ns) or "").strip()
        _crd = (_pm.findtext(".//k:coordinates", "", _ns) or "").strip()
        _cid = _KML_NAME_TO_CID.get(_nm)
        if _cid and _crd:
            _lng, _lat = float(_crd.split(",")[0]), float(_crd.split(",")[1])
            _collector_homes[_cid] = {
                "lat": round(_lat, 6), "lng": round(_lng, 6),
                "name": _COLLECTOR_FULL.get(_cid, _cid),
                "non_collector": _cid in _NON_COLLECTORS,
            }
collector_homes_json = json.dumps(_collector_homes)

# -- Bake schedule_output.json into the dashboard ---
if SCHEDULE_OUTPUT_JSON.exists():
    with open(SCHEDULE_OUTPUT_JSON, encoding="utf-8") as _sf:
        baked_schedule_json = _sf.read()
else:
    baked_schedule_json = "null"

# -- Bake weather into the dashboard (single weather.json file) ---
_weather_path = WEATHER_JSON
_baked_weather: dict = {"weather": {}, "_meta": {}}
if _weather_path.exists():
    try:
        _wd = json.loads(_weather_path.read_text(encoding="utf-8"))
        _baked_weather["weather"] = _wd.get("weather", {})
        _baked_weather["_meta"]   = _wd.get("_meta", {})
        if "current_week_start" in _wd:
            _baked_weather["current_week_start"] = _wd["current_week_start"]
        if "current_week_end" in _wd:
            _baked_weather["current_week_end"] = _wd["current_week_end"]
        if "history_start" in _wd:
            _baked_weather["history_start"] = _wd["history_start"]
    except (json.JSONDecodeError, OSError):
        pass
baked_weather_json = json.dumps(_baked_weather) if _baked_weather["weather"] else "null"

# -- Bake availability heatmap data ---
import sys as _sys
_sys.path.insert(0, str(BASE))
from build_availability_heatmap import load_availability, build_heatmap, GROUP_A, GROUP_B, DAYS as _AVAIL_DAYS, TODS as _AVAIL_TODS, FULL_NAMES as _AVAIL_NAMES
_avail = load_availability()
_cells_a, _cells_b = {}, {}
for _tod in _AVAIL_TODS:
    for _day in _AVAIL_DAYS:
        _k = f'{_day}_{_tod}'
        _free_a = [c for c in GROUP_A if _avail.get(c, {}).get(_k, False)]
        _free_b = [c for c in GROUP_B if _avail.get(c, {}).get(_k, False)]
        _cells_a[_k] = {'count': len(_free_a), 'names': [_AVAIL_NAMES.get(c,c) for c in _free_a]}
        _cells_b[_k] = {'count': len(_free_b), 'names': [_AVAIL_NAMES.get(c,c) for c in _free_b]}
avail_cells_a_json = json.dumps(_cells_a)
avail_cells_b_json = json.dumps(_cells_b)
avail_max_a = len(GROUP_A)
avail_max_b = len(GROUP_B)
avail_days_json = json.dumps(_AVAIL_DAYS)

# -- Compute route group convex hulls from Route_Groups.xlsx ---
def _convex_hull(pts):
    pts = sorted(set(map(tuple, pts)))
    if len(pts) < 3: return [[p[0],p[1]] for p in pts]
    def cross(O,A,B): return (A[0]-O[0])*(B[1]-O[1])-(A[1]-O[1])*(B[0]-O[0])
    lo,hi=[],[]
    for p in pts:
        while len(lo)>=2 and cross(lo[-2],lo[-1],p)<=0: lo.pop()
        lo.append(p)
    for p in reversed(pts):
        while len(hi)>=2 and cross(hi[-2],hi[-1],p)<=0: hi.pop()
        hi.append(p)
    return [[p[0],p[1]] for p in lo[:-1]+hi[:-1]]

def _expand_hull(hull, cx, cy, buf=0.012):
    out=[]
    for (x,y) in hull:
        dx,dy=x-cx,y-cy
        d=_math.hypot(dx,dy) or 1
        out.append([round(cx+(dx/d)*(d+buf),6), round(cy+(dy/d)*(d+buf),6)])
    return out

def _splice_waypoints(hull, cx, cy, waypoints):
    """Replace the angular sector spanned by waypoints with the waypoints themselves."""
    if not waypoints: return hull
    def ang(p): return _math.atan2(p[1]-cy, p[0]-cx)
    wps=[list(p) for p in waypoints]
    if len(wps)==1:
        result=hull+wps; result.sort(key=ang); return result
    wa0,wa1=ang(wps[0]),ang(wps[-1])
    # Remove hull vertices whose angle falls inside the arc wa0→wa1 (CCW)
    if wa0<wa1:
        keep=[p for p in hull if not(wa0<=ang(p)<=wa1)]
    else:  # arc wraps past ±π
        keep=[p for p in hull if ang(p)<wa1 or ang(p)>wa0]
    result=keep+wps
    result.sort(key=ang)
    return result

_routes_geo=json.loads(routes_json)
_suffix_map={k.split('_')[1]:k for k in _routes_geo}

_GROUP_DEFS=[
    {"name":"Group 1","codes":["NW","WH","HT","UE","HP"],"color":"#ffd700"},
    {"name":"Group 2","codes":["JA","FH","FU","EE","JH","LI","LA","WB"],"color":"#ff69b4"},
    {
        "name":"Group 3","codes":["UE","MT","LI","LA","WB","LE","DT"],"color":"#39d353",
        "north_clamp":40.796,
        # Each item is either [lat,lng] (single point) or [[lat,lng],...] (splice path)
        "extra_waypoints":[
            [40.693137744128705,-73.96982354212368],   # avoid Bed-Stuy
            [40.8002841394381,-73.94670695713012],      # north boundary
        ],
    },
    {
        "name":"Group 4","codes":["JA","CI","SP","DT","WB","BS","CH"],"color":"#58a6ff",
        "extra_waypoints":[
            [   # Queens avoidance path (splice segment)
                [40.73248944075242,-73.96267952377683],
                [40.73889095609138,-73.95174642613894],
                [40.73600407444921,-73.9426355114407],
                [40.73667350743619,-73.93413199105566],
                [40.731108641003075,-73.92551803534097],
                [40.700302671372235,-73.84601148413843],
            ],
            [40.68833663959835,-73.84906720430156],    # SE boundary point
        ],
    },
]

_xlsx_path=ROUTE_GROUPS
if _xlsx_path.exists():
    _wb=_opxl.load_workbook(_xlsx_path,read_only=True,data_only=True)
    _ws=_wb.active
    _group_rows,_cur={},None
    for row in _ws.iter_rows(values_only=True):
        vals=[c for c in row if c]
        if not vals: continue
        first=str(vals[0]).strip()
        if first.startswith("Group_"):
            _cur=first.replace("_"," ")
            _group_rows[_cur]=[str(v).strip() for v in vals[1:] if v]
        elif _cur:
            _group_rows[_cur].extend([str(v).strip() for v in vals])
    _wb.close()
    for g in _GROUP_DEFS:
        if g["name"] in _group_rows:
            g["codes"]=_group_rows[g["name"]]

_route_groups=[]
for g in _GROUP_DEFS:
    try:
        all_pts,full_codes=[],[]
        for sc in g["codes"]:
            fc=_suffix_map.get(sc)
            if not fc: continue
            full_codes.append(fc)
            for line in _routes_geo[fc]["lines"]:
                all_pts.extend(line)
        if not all_pts:
            continue
        hull=_convex_hull(all_pts)
        cx=sum(p[0] for p in hull)/len(hull)
        cy=sum(p[1] for p in hull)/len(hull)
        if "north_clamp" in g:
            hull=[[min(p[0], g["north_clamp"]), p[1]] for p in hull]
        hull=_expand_hull(hull, cx, cy, buf=0.012)
        if "north_clamp" in g:
            hull=[[min(p[0], g["north_clamp"]), p[1]] for p in hull]
        for _wp in g.get("extra_waypoints", []):
            # Single point [lat,lng] vs splice path [[lat,lng],...]
            _pts=[_wp] if isinstance(_wp[0],(int,float)) else _wp
            hull=_splice_waypoints(hull, cx, cy, _pts)
        _route_groups.append({"name":g["name"],"routes":full_codes,"color":g["color"],"hull":hull})
    except Exception as _e:
        print(f"[build_dashboard] Warning: skipping route group '{g['name']}': {_e}")

route_groups_json=json.dumps(_route_groups)

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NASA EnAACT Field Campaign Data Desk</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&display=swap" rel="stylesheet">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
:root{
  --bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--bg4:#30363d;
  --border:#30363d;--text:#e6edf3;--text2:#8b949e;--text3:#6e7681;
  --accent:#388bfd;--accent2:#1f6feb;
  --red:#f85149;--yellow:#d29922;--green:#3fb950;
  --red-bg:rgba(248,81,73,.15);--yellow-bg:rgba(210,153,34,.15);--green-bg:rgba(63,185,80,.15);
  --tod-am:#60a5fa;--tod-md:#fbbf24;--tod-pm:#c084fc;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;height:100vh;overflow:hidden}
#header{display:flex;align-items:center;gap:8px;padding:0 12px;height:62px;background:var(--bg2);border-bottom:1px solid var(--border);flex-shrink:0;z-index:100}
#header h1{font-size:15px;font-weight:700;white-space:nowrap;letter-spacing:-.2px;font-family:'Space Grotesk',sans-serif;line-height:1.25;-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;text-rendering:geometricPrecision;transform:translateZ(0)}
#header h1 em{font-style:normal;color:var(--accent);font-size:10px;font-weight:600;background:rgba(56,139,253,.15);border:1px solid rgba(56,139,253,.3);border-radius:4px;padding:1px 5px;margin-left:6px;vertical-align:middle}
#header-logos{display:flex;align-items:center;gap:10px;flex-shrink:0}
#nasa-worm-logo{height:26px;width:auto;flex-shrink:0;display:block}
#tempo-logo{height:38px;width:auto;flex-shrink:0;display:block}
#header-divider{width:1px;height:32px;background:var(--border);flex-shrink:0}
#header-title{display:flex;flex-direction:column;gap:1px;flex-shrink:1;min-width:0}
#header-title h1{margin:0}
#tabs{display:flex;gap:6px;margin-left:auto;align-items:flex-end;position:relative;z-index:600}
.tab-group{display:flex;flex-direction:column;gap:2px}
.tab-group-label{font-family:'Space Grotesk',sans-serif;font-size:8px;font-weight:700;text-transform:uppercase;letter-spacing:1.4px;padding:0 5px;color:var(--text3);white-space:nowrap}
.tab-group-label.monitor{color:#60a5fa;opacity:.75}
.tab-group-label.scheduling{color:#a78bfa;opacity:.75}
.tab-group-btns{display:flex;gap:2px}
.tab-sep{width:1px;height:38px;background:var(--border);align-self:center;margin:0 2px;flex-shrink:0}
.tab-btn{padding:4px 13px;background:transparent;border:1px solid transparent;border-radius:6px;color:var(--text2);cursor:pointer;font-size:12px;font-weight:500;transition:all .15s;font-family:'Space Grotesk',sans-serif}
.tab-btn:hover{background:var(--bg3);color:var(--text)}
.tab-btn.active{background:var(--bg3);border-color:var(--border);color:var(--text)}
.tab-group:first-child .tab-btn.active{border-color:rgba(96,165,250,.5);color:#60a5fa}
.tab-group:last-child .tab-btn.active{border-color:rgba(167,139,250,.5);color:#a78bfa}
.force-rebuild-btn{padding:4px 11px;background:transparent;border:1px solid var(--border);border-radius:6px;color:var(--text2);cursor:pointer;font-size:11px;font-weight:600;transition:all .15s;font-family:'Space Grotesk',sans-serif;margin-right:4px;display:flex;align-items:center;gap:3px;white-space:nowrap}
.force-rebuild-btn:hover{background:#4f3a0f;border-color:#d29922;color:#d29922}
.force-rebuild-btn.rebuilding{opacity:.5;cursor:wait;pointer-events:none}
/* -- Admin login button (header) -- */
.sched-unlock-btn{padding:4px 11px;background:transparent;border:1px solid var(--border);border-radius:6px;color:var(--text2);cursor:pointer;font-size:11px;font-weight:600;transition:all .15s;font-family:'Space Grotesk',sans-serif;margin-right:2px;display:flex;align-items:center;gap:3px;white-space:nowrap;flex-shrink:0}
.sched-unlock-btn:hover{background:rgba(56,139,253,.1);border-color:var(--accent);color:var(--accent)}
.sched-unlock-btn.authed{border-color:rgba(63,185,80,.5);color:var(--green);background:rgba(63,185,80,.1)}
.sched-unlock-btn.authed:hover{background:rgba(248,81,73,.08);border-color:var(--red);color:var(--red)}
/* -- Auth modal overlay -- */
#auth-modal-bg{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9000;align-items:center;justify-content:center;backdrop-filter:blur(3px)}
#auth-modal-bg.open{display:flex}
#auth-modal{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:22px 24px;width:300px;display:flex;flex-direction:column;gap:14px;box-shadow:0 16px 48px rgba(0,0,0,.8)}
#auth-modal h3{font-size:14px;font-weight:700;font-family:'Space Grotesk',sans-serif;color:var(--text);margin:0}
#auth-modal label{font-size:10px;color:var(--text3);font-weight:600;text-transform:uppercase;letter-spacing:.5px;display:block;margin-bottom:4px}
#auth-modal select,#auth-modal input[type=password]{width:100%;background:var(--bg3);border:1px solid var(--border);color:var(--text);border-radius:5px;padding:5px 8px;font-size:12px;height:32px;outline:none;box-sizing:border-box}
#auth-modal select:focus,#auth-modal input[type=password]:focus{border-color:var(--accent)}
#auth-modal-notice{font-size:11px;color:var(--yellow);background:var(--yellow-bg);border:1px solid rgba(210,153,34,.35);border-radius:5px;padding:6px 9px;display:none}
#auth-modal-err{font-size:11px;color:var(--red);background:var(--red-bg);border:1px solid rgba(248,81,73,.3);border-radius:5px;padding:6px 9px;display:none}
#auth-modal-actions{display:flex;gap:8px;justify-content:flex-end;margin-top:2px}
#auth-modal-submit{padding:6px 16px;background:var(--accent2);border:1px solid var(--accent);color:#fff;border-radius:5px;cursor:pointer;font-size:12px;font-weight:600;font-family:'Space Grotesk',sans-serif;transition:background .15s}
#auth-modal-submit:hover{background:var(--accent)}
#auth-modal-cancel{padding:6px 14px;background:transparent;border:1px solid var(--border);color:var(--text2);border-radius:5px;cursor:pointer;font-size:12px;font-weight:500;transition:all .15s}
#auth-modal-cancel:hover{background:var(--bg3);color:var(--text)}
/* -- Calibration entry button & modal -- */
.cb-log-btn{padding:3px 9px;background:rgba(255,255,255,.06);border:1px solid var(--border);color:var(--text2);border-radius:5px;cursor:pointer;font-size:10px;font-weight:600;font-family:'Space Grotesk',sans-serif;letter-spacing:.4px;transition:all .15s;white-space:nowrap;align-self:center;flex-shrink:0}
.cb-log-btn:hover{background:rgba(255,255,255,.12);color:var(--text);border-color:rgba(255,255,255,.2)}
.cal-bar[data-bp="A"] .cb-log-btn{border-color:rgba(124,58,237,.4);color:rgba(167,139,250,.9)}
.cal-bar[data-bp="B"] .cb-log-btn{border-color:rgba(220,38,38,.4);color:rgba(252,165,165,.9)}
#recal-modal-bg{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9000;align-items:center;justify-content:center;backdrop-filter:blur(3px)}
#recal-modal-bg.open{display:flex}
#recal-modal{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:22px 24px;width:300px;display:flex;flex-direction:column;gap:14px;box-shadow:0 16px 48px rgba(0,0,0,.8)}
#recal-modal h3{font-size:14px;font-weight:700;font-family:'Space Grotesk',sans-serif;color:var(--text);margin:0}
#recal-modal label{font-size:10px;color:var(--text3);font-weight:600;text-transform:uppercase;letter-spacing:.5px;display:block;margin-bottom:4px}
#recal-modal input[type=date]{width:100%;background:var(--bg3);border:1px solid var(--border);color:var(--text);border-radius:5px;padding:5px 8px;font-size:12px;height:32px;outline:none;box-sizing:border-box}
#recal-modal input[type=date]:focus{border-color:var(--accent)}
#recal-bp-choices{display:flex;gap:10px;margin-top:4px}
#recal-bp-choices label{font-size:12px;color:var(--text);text-transform:none;letter-spacing:0;display:flex;align-items:center;gap:5px;cursor:pointer}
#recal-bp-choices input[type=radio]{accent-color:var(--accent);cursor:pointer}
#recal-modal-msg{font-size:11px;border-radius:5px;padding:6px 9px;display:none}
#recal-modal-msg.err{color:var(--red);background:var(--red-bg);border:1px solid rgba(248,81,73,.3)}
#recal-modal-actions{display:flex;gap:8px;justify-content:flex-end;margin-top:2px}
#recal-modal-submit{padding:6px 16px;background:var(--accent2);border:1px solid var(--accent);color:#fff;border-radius:5px;cursor:pointer;font-size:12px;font-weight:600;font-family:'Space Grotesk',sans-serif;transition:background .15s}
#recal-modal-submit:hover{background:var(--accent)}
#recal-modal-cancel{padding:6px 14px;background:transparent;border:1px solid var(--border);color:var(--text2);border-radius:5px;cursor:pointer;font-size:12px;font-weight:500;transition:all .15s}
#recal-modal-cancel:hover{background:var(--bg3);color:var(--text)}
/* -- Upload Data button & modal -- */
.upload-data-btn{padding:4px 11px;background:rgba(56,139,253,.12);border:1px solid rgba(56,139,253,.4);border-radius:6px;color:#60a5fa;cursor:pointer;font-size:11px;font-weight:600;transition:all .15s;font-family:'Space Grotesk',sans-serif;margin-right:4px;display:flex;align-items:center;gap:4px;white-space:nowrap;flex-shrink:0}
.upload-data-btn:hover{background:rgba(56,139,253,.2);border-color:var(--accent);color:var(--text)}
#upload-modal-bg{display:none;position:fixed;inset:0;background:rgba(0,0,0,.78);z-index:9500;align-items:flex-start;justify-content:center;backdrop-filter:blur(4px);overflow-y:auto;padding:24px 16px}
#upload-modal-bg.open{display:flex}
#upload-modal{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:28px 30px;width:100%;max-width:700px;display:flex;flex-direction:column;gap:20px;box-shadow:0 24px 64px rgba(0,0,0,.9);margin:auto}
#upload-modal h2{font-size:16px;font-weight:700;font-family:'Space Grotesk',sans-serif;color:var(--text);margin:0}
.um-section{display:flex;flex-direction:column;gap:10px}
.um-section-title{font-size:10px;font-weight:700;color:var(--text3);text-transform:uppercase;letter-spacing:1px;padding-bottom:6px;border-bottom:1px solid var(--border)}
.um-fields{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
.um-field{display:flex;flex-direction:column;gap:4px}
.um-field label{font-size:10px;color:var(--text3);font-weight:600;text-transform:uppercase;letter-spacing:.5px}
.um-field select,.um-field input[type=date]{width:100%;background:var(--bg3);border:1px solid var(--border);color:var(--text);border-radius:5px;padding:5px 8px;font-size:12px;height:32px;outline:none;box-sizing:border-box;cursor:pointer}
.um-field select:focus,.um-field input[type=date]:focus{border-color:var(--accent)}
.um-field select option{background:var(--bg3)}
.um-time-group{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.um-time-card{background:var(--bg3);border:1px solid var(--border);border-radius:8px;padding:12px;display:flex;flex-direction:column;gap:8px}
.um-time-card .um-tc-label{font-size:10px;font-weight:700;color:var(--text2);text-transform:uppercase;letter-spacing:.5px}
.um-time-radio{display:flex;gap:10px;font-size:11px;color:var(--text2)}
.um-time-radio label{display:flex;align-items:center;gap:4px;cursor:pointer}
.um-time-split{display:flex;align-items:center;gap:2px}
.um-ts-part{width:36px;background:var(--bg);border:1px solid var(--border);color:var(--text);border-radius:5px;padding:4px 4px;font-size:12px;height:28px;outline:none;box-sizing:border-box;text-align:center;font-family:monospace;-moz-appearance:textfield}
.um-ts-part:focus{border-color:var(--accent)}
.um-ts-part::-webkit-inner-spin-button,.um-ts-part::-webkit-outer-spin-button{-webkit-appearance:none;margin:0}
.um-ts-sep{color:var(--text2);font-size:14px;font-weight:600;line-height:1;user-select:none}
.um-ts-utc{font-size:9px;color:var(--text3);margin-left:4px;align-self:center}
.um-drop-zones{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.um-drop-zone{background:var(--bg3);border:2px dashed var(--border);border-radius:8px;padding:16px 10px;display:flex;flex-direction:column;align-items:center;gap:5px;cursor:pointer;transition:border-color .15s,background .15s;text-align:center;min-height:88px;justify-content:center}
.um-drop-zone:hover,.um-drop-zone.drag-over{border-color:var(--accent);background:rgba(56,139,253,.07)}
.um-drop-zone .um-dz-icon{font-size:20px;line-height:1}
.um-drop-zone .um-dz-label{font-size:11px;font-weight:600;color:var(--text2);font-family:'Space Grotesk',sans-serif}
.um-drop-zone .um-dz-hint{font-size:9px;color:var(--text3)}
.um-dz-files{font-size:9px;color:var(--accent);margin-top:3px;word-break:break-all}
.um-toggle-label{font-size:11px;color:var(--text2);display:flex;align-items:center;gap:6px;cursor:pointer;font-weight:500}
.um-toggle-label input{accent-color:var(--accent);cursor:pointer}
.um-track-notes{display:grid;grid-template-columns:1fr 2fr;gap:12px}
.um-notes-wrap{display:flex;flex-direction:column;gap:4px}
.um-notes-label{font-size:10px;color:var(--text3);font-weight:600;text-transform:uppercase;letter-spacing:.5px}
.um-notes{width:100%;background:var(--bg3);border:1px solid var(--border);color:var(--text);border-radius:8px;padding:10px 12px;font-size:12px;resize:vertical;min-height:88px;outline:none;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;line-height:1.5}
.um-notes:focus{border-color:var(--accent)}
#upload-modal-actions{display:flex;gap:10px;align-items:center;justify-content:flex-end;margin-top:4px;padding-top:16px;border-top:1px solid var(--border)}
#upload-modal-status{font-size:11px;flex:1}
#upload-modal-status.ok{color:var(--green)}
#upload-modal-status.err{color:var(--red)}
#upload-modal-submit{padding:8px 20px;background:var(--accent2);border:1px solid var(--accent);color:#fff;border-radius:6px;cursor:pointer;font-size:12px;font-weight:700;font-family:'Space Grotesk',sans-serif;transition:background .15s}
#upload-modal-submit:hover{background:var(--accent)}
#upload-modal-submit:disabled{opacity:.5;cursor:not-allowed}
#upload-modal-cancel{padding:8px 16px;background:transparent;border:1px solid var(--border);color:var(--text2);border-radius:6px;cursor:pointer;font-size:12px;font-weight:500;transition:all .15s}
#upload-modal-cancel:hover{background:var(--bg3);color:var(--text)}
#toast.warn{border-color:var(--yellow);color:var(--yellow)}
/* Filters dropdown panel — anchored to Campaign Monitor tab group */
#filters{display:none;position:fixed;width:240px;background:var(--bg2);border:1px solid var(--border);border-radius:8px;overflow-y:auto;z-index:1200;padding:14px;gap:10px;flex-direction:column;box-shadow:0 8px 24px rgba(0,0,0,.5)}
#filters.open{display:flex}
select,input[type=date]{background:var(--bg3);border:1px solid var(--border);color:var(--text);border-radius:5px;padding:3px 7px;font-size:11px;height:28px;cursor:pointer;outline:none}
select:hover,input[type=date]:hover{border-color:var(--accent)}
select option{background:var(--bg3)}
.fl{font-size:10px;color:var(--text3);white-space:nowrap}
.fg{display:flex;align-items:center;gap:3px}
.btn{padding:4px 11px;border-radius:5px;border:1px solid var(--border);background:var(--bg3);color:var(--text);cursor:pointer;font-size:11px;font-weight:500;transition:all .15s;white-space:nowrap;height:28px;display:inline-flex;align-items:center;gap:4px}
.btn:hover{background:var(--bg4);border-color:var(--accent)}
#data-status{font-size:10px;color:var(--text3);white-space:nowrap;padding:2px 7px;background:var(--bg3);border-radius:4px;border:1px solid var(--border)}
#app{display:flex;flex-direction:column;height:100vh}
#content{flex:1;display:flex;overflow:hidden;position:relative}
.view{position:absolute;inset:0;display:none}
.view.active{display:flex}
/* MAP */
#map-view{flex-direction:row}
#map-wrap{flex:1;position:relative;overflow:hidden}
#map{width:100%;height:100%}
.leaflet-container{background:#0d1117!important}
/* ROUTE PANEL */
#route-panel{width:370px;flex-shrink:0;background:var(--bg2);border-left:1px solid var(--border);display:flex;flex-direction:column;transform:translateX(100%);transition:transform .22s ease;position:absolute;right:0;top:0;bottom:0;z-index:500}
#route-panel.open{transform:translateX(0)}
#rph{padding:12px 14px 9px;border-bottom:1px solid var(--border);display:flex;align-items:flex-start;gap:8px}
#rpt{flex:1}
#rpt h2{font-size:14px;font-weight:700;line-height:1.3}
.rmeta{font-size:10px;color:var(--text2);margin-top:2px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
#close-panel{background:transparent;border:1px solid var(--border);color:var(--text2);width:24px;height:24px;border-radius:4px;cursor:pointer;font-size:13px;display:flex;align-items:center;justify-content:center;flex-shrink:0}
#close-panel:hover{background:var(--bg3);color:var(--text)}
#rpb{flex:1;overflow-y:auto;padding:10px 14px;display:flex;flex-direction:column;gap:12px}
.psec{background:var(--bg3);border-radius:7px;padding:11px}
.psec h3{font-size:10px;font-weight:700;color:var(--text3);text-transform:uppercase;letter-spacing:.6px;margin-bottom:9px}
.srow{display:flex;gap:7px}
.schip{flex:1;min-width:60px;background:var(--bg4);border-radius:5px;padding:7px 8px;text-align:center}
.schip .sv{font-size:20px;font-weight:700;line-height:1}
.schip .sl{font-size:9px;color:var(--text2);margin-top:2px}
.schip.am .sv{color:var(--tod-am)}
.schip.md .sv{color:var(--tod-md)}
.schip.pm .sv{color:var(--tod-pm)}
.cw{height:130px;position:relative}
.needs{display:flex;flex-wrap:wrap;gap:5px;margin-top:2px}
.ntag{font-size:10px;padding:2px 7px;border-radius:10px;border:1px solid}
.ntag.ok{border-color:var(--green);color:var(--green);background:var(--green-bg)}
.ntag.warn{border-color:var(--yellow);color:var(--yellow);background:var(--yellow-bg)}
.ntag.bad{border-color:var(--red);color:var(--red);background:var(--red-bg)}
.lt{width:100%;border-collapse:collapse;font-size:11px}
.lt th{font-size:9px;font-weight:700;color:var(--text3);text-transform:uppercase;padding:3px 5px;text-align:left;border-bottom:1px solid var(--border)}
.lt td{padding:4px 5px;border-bottom:1px solid rgba(48,54,61,.5)}
.lt tr:last-child td{border-bottom:none}
.lt tr:hover td{background:var(--bg4)}
.tb{font-size:9px;padding:1px 4px;border-radius:3px;font-weight:700}
.tb-am{background:rgba(96,165,250,.2);color:var(--tod-am)}
.tb-md{background:rgba(251,191,36,.2);color:var(--tod-md)}
.tb-pm{background:rgba(192,132,252,.2);color:var(--tod-pm)}
.bb{font-size:9px;padding:1px 4px;border-radius:3px;background:var(--bg4);color:var(--text2)}
/* MAP OVERLAYS */
#mlegend{position:absolute;bottom:calc(22px + env(safe-area-inset-bottom));left:10px;z-index:1000;background:rgba(13,17,23,.9);border:1px solid var(--border);border-radius:7px;padding:9px 11px;pointer-events:none}
#mlegend h4{font-size:9px;font-weight:700;color:var(--text3);text-transform:uppercase;letter-spacing:.5px;margin-bottom:7px}
.li{display:flex;align-items:center;gap:7px;margin-bottom:4px;font-size:11px;color:var(--text2)}
.li:last-child{margin-bottom:0}
.lsw{width:22px;height:4px;border-radius:2px}
#collector-homes-btn{position:absolute;bottom:100px;left:10px;z-index:1001;background:rgba(13,17,23,.88);border:1px solid var(--border);border-radius:7px;padding:5px 10px;font-size:11px;font-weight:600;color:var(--text2);cursor:pointer;display:flex;align-items:center;gap:6px;backdrop-filter:blur(4px);transition:background .15s,color .15s,border-color .15s;user-select:none;white-space:nowrap}
#collector-homes-btn:hover{background:rgba(40,44,65,.95);color:var(--text)}
#collector-homes-btn.chb-on{border-color:#4f8ef7;color:#4f8ef7;background:rgba(15,31,63,.88)}
#route-groups-panel{position:absolute;bottom:140px;left:10px;z-index:1001;background:rgba(13,17,23,.92);border:1px solid var(--border);border-radius:7px;font-size:11px;font-weight:600;backdrop-filter:blur(4px);user-select:none;min-width:148px;overflow:hidden}
.rgb-header{display:flex;align-items:center;justify-content:space-between;padding:5px 10px;color:var(--text);gap:10px;white-space:nowrap;border-bottom:1px solid var(--border)}
#rgb-all-btn{font-size:9px;font-weight:700;padding:1px 7px;background:var(--bg3);border:1px solid var(--border);border-radius:3px;color:var(--text2);cursor:pointer;font-family:inherit;letter-spacing:.3px;transition:all .15s;flex-shrink:0}
#rgb-all-btn:hover{color:var(--text);border-color:var(--text3)}
.rgb-item{display:flex;align-items:center;gap:6px;padding:3px 10px;cursor:pointer;width:100%;box-sizing:border-box}
.rgb-item:hover{background:rgba(255,255,255,.05)}
.rgb-item input[type=checkbox]{cursor:pointer;flex-shrink:0;accent-color:currentColor}
.rgb-dot{width:9px;height:9px;border-radius:50%;flex-shrink:0}
.rgb-lbl{color:var(--text2);font-size:11px;white-space:nowrap}
#mstats{position:absolute;top:10px;left:10px;z-index:1000;display:flex;flex-direction:column;gap:5px;pointer-events:none}
.msc{background:rgba(13,17,23,.88);border:1px solid var(--border);border-radius:6px;padding:6px 10px;font-size:10px;color:var(--text2)}
.msc strong{display:block;font-size:17px;font-weight:700;color:var(--text);line-height:1.1}
/* CALENDAR VIEW */
#calendar-view{flex-direction:column;overflow:hidden}
#cal-nav{display:flex;align-items:center;gap:6px;padding:0 16px;height:54px;background:var(--bg2);border-bottom:1px solid var(--border);flex-shrink:0}
#cal-nav h2{font-size:15px;font-weight:700;margin:0 8px;min-width:230px;text-align:center;white-space:nowrap}
.cal-nav-btn{width:40px;height:40px;border-radius:50%;border:1px solid var(--border);background:transparent;color:var(--text);cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:16px;line-height:1;flex-shrink:0}
.cal-nav-btn:hover{background:var(--bg3);border-color:var(--accent)}
.cal-nav-btn:disabled{opacity:.28;cursor:default}
.bp-toggle{height:40px;padding:0 10px;border-radius:6px;border:1px solid var(--border);background:transparent;color:var(--text2);cursor:pointer;font-size:12px;font-weight:600;transition:all .15s;display:inline-flex;align-items:center;gap:0}
.bp-toggle:hover{background:var(--bg3);border-color:var(--accent);color:var(--text);cursor:pointer;position:relative}
.bp-toggle.active{background:var(--bg3);border-color:var(--accent);color:var(--text)}
.bp-toggle[data-backpack="A"].active{border-color:#f85149;color:#f85149}
.bp-toggle[data-backpack="B"].active{border-color:#388bfd;color:#388bfd}
.bp-toggle[data-backpack="X"].active{border-color:#f0a500;color:#f0a500}
.bp-boro-badge{display:inline-block;font-size:9px;font-weight:700;color:#8b949e;margin-left:4px;letter-spacing:.5px}
.bp-boro-tooltip{position:absolute;background:#161b22;border:1px solid #30363d;border-radius:6px;padding:8px 10px;font-size:9px;z-index:1000;white-space:nowrap;display:none;bottom:-160px;left:50%;transform:translateX(-50%);box-shadow:0 4px 12px rgba(0,0,0,.5);color:#8b949e;pointer-events:none}
.bp-toggle:hover .bp-boro-tooltip{display:block}
.bp-boro-tooltip .week-item{display:flex;align-items:center;gap:4px;padding:2px 0;color:#e6edf3}
.bp-boro-tooltip .week-num{font-weight:700;color:var(--accent);min-width:20px}
#cal-body{flex:1;overflow-y:auto;overflow-x:hidden;min-height:0}
#cal-grid{display:grid;grid-template-columns:54px repeat(7,1fr);grid-template-rows:56px repeat(3,minmax(110px,1fr));min-height:100%;width:100%}
.cal-corner{background:var(--bg2);border-right:1px solid var(--border);border-bottom:2px solid var(--border);position:sticky;top:0;left:0;z-index:20}
.cal-day-head{background:var(--bg2);border-right:1px solid var(--border);border-bottom:2px solid var(--border);padding:8px 6px 6px;text-align:center;position:sticky;top:0;z-index:10}
.cal-dname{font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:.6px;font-weight:600}
.cal-dnum{font-size:22px;font-weight:300;color:var(--text);line-height:1.3;margin-top:1px}
.cal-today-head .cal-dnum{background:var(--accent);color:#fff;border-radius:50%;width:34px;height:34px;display:inline-flex;align-items:center;justify-content:center;font-weight:500;font-size:18px}
.cal-tod-lbl{background:var(--bg2);border-right:1px solid var(--border);border-bottom:1px solid var(--border);display:flex;align-items:flex-start;justify-content:flex-end;padding:10px 6px 0 0;font-size:10px;font-weight:700;letter-spacing:.3px;position:sticky;left:0;z-index:5}
.cal-tod-lbl.am{color:var(--tod-am)}.cal-tod-lbl.md{color:var(--tod-md)}.cal-tod-lbl.pm{color:var(--tod-pm)}
.cal-cell{border-right:1px solid var(--border);border-bottom:1px solid var(--border);padding:5px;display:flex;flex-direction:column;gap:4px;background:var(--bg);position:relative}
.cloud-pct-badge{position:absolute;top:4px;right:4px;font-size:9px;font-weight:700;letter-spacing:.2px;padding:1px 5px;border-radius:10px;pointer-events:none;z-index:3;opacity:.9}
.cloud-pct-badge.good{color:#3fb950;background:rgba(63,185,80,.12);border:1px solid rgba(63,185,80,.28)}
.cloud-pct-badge.bad{color:#ef4444;background:rgba(248,81,73,.12);border:1px solid rgba(248,81,73,.28)}
#wx-cutoff-pill{display:flex;align-items:center;gap:5px;padding:0 10px;height:40px;border-radius:6px;background:rgba(255,255,255,.05);border:1px solid var(--border);font-size:12px;font-weight:700;color:var(--text2);white-space:nowrap;font-family:'Space Grotesk',sans-serif;flex-shrink:0;letter-spacing:.1px}
#wx-cutoff-pill .wx-good{color:var(--green)}
#wx-cutoff-pill .wx-bad{color:var(--red)}
/* -- Days Since Calibration bars -- */
#cal-bars{display:flex;flex-direction:row;gap:6px;flex-shrink:0;margin-left:8px}
.cal-bar{display:flex;align-items:center;gap:12px;padding:0 14px;height:40px;border-radius:7px;background:rgba(255,255,255,.04);border:1px solid var(--border);font-family:'Space Grotesk',sans-serif;flex-shrink:0;box-sizing:border-box}
.cal-bar[data-bp="A"]{border-color:rgba(124,58,237,.35)}
.cal-bar[data-bp="B"]{border-color:rgba(220,38,38,.35)}
.cal-bar .cb-label{display:flex;flex-direction:column;align-items:center;justify-content:space-evenly;height:100%;line-height:1;white-space:nowrap}
.cal-bar .cb-title{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.9px;color:var(--text2)}
.cal-bar[data-bp="A"] .cb-title{color:rgba(167,139,250,.85)}
.cal-bar[data-bp="B"] .cb-title{color:rgba(252,165,165,.85)}
.cal-bar .cb-count{font-size:16px;font-weight:700;color:var(--text);letter-spacing:-.5px;font-variant-numeric:tabular-nums;line-height:1}
.cal-bar .cb-count .cb-count-num{transition:color .2s}
.cal-bar .cb-last{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.9px;color:var(--text2);font-family:'Space Grotesk',sans-serif;line-height:1}
.cal-bar .cb-last b{color:var(--text)}
.cal-bar .cb-track{position:relative;flex-shrink:0;width:170px;height:12px;border-radius:7px;background:linear-gradient(90deg,rgba(63,185,80,.14) 0%,rgba(63,185,80,.14) 40%,rgba(210,153,34,.14) 44%,rgba(210,153,34,.14) 64%,rgba(248,81,73,.16) 68%,rgba(248,81,73,.16) 100%);border:1px solid rgba(255,255,255,.06);overflow:hidden}
.cal-bar .cb-fill{position:absolute;top:0;left:0;bottom:0;width:0%;border-radius:7px 0 0 7px;background:linear-gradient(90deg,#3fb950 0%,#3fb950 38%,#d29922 52%,#d29922 73%,#f85149 85%,#f85149 100%);background-size:170px 100%;background-position:0 0;box-shadow:0 0 8px rgba(63,185,80,.25);transition:width .6s cubic-bezier(.22,.61,.36,1),box-shadow .4s ease}
.cal-bar[data-zone="yellow"] .cb-fill{box-shadow:0 0 10px rgba(210,153,34,.35)}
.cal-bar[data-zone="red"] .cb-fill{box-shadow:0 0 14px rgba(248,81,73,.5)}
.cal-bar .cb-fill::after{content:'';position:absolute;right:0;top:0;bottom:0;width:10px;background:linear-gradient(90deg,transparent,rgba(255,255,255,.35));pointer-events:none}
.cal-bar .cb-zonemark{position:absolute;top:-1px;bottom:-1px;width:1px;background:rgba(255,255,255,.18);pointer-events:none}
.cal-bar[data-zone="green"] .cb-count-num{color:var(--green)}
.cal-bar[data-zone="yellow"] .cb-count-num{color:var(--yellow)}
.cal-bar[data-zone="red"] .cb-count-num{color:var(--red);animation:cb-red-pulse 1.8s ease-in-out infinite}
@keyframes cb-red-pulse{0%,100%{text-shadow:0 0 0 rgba(248,81,73,0)}50%{text-shadow:0 0 8px rgba(248,81,73,.55)}}
.cal-bar .cb-scale{display:flex;gap:0;font-size:8px;color:var(--text3);letter-spacing:.5px;font-weight:600;user-select:none;width:170px;position:relative;height:9px}
.cal-bar .cb-scale-words{margin-top:2px}
.cal-bar .cb-track-wrap{display:flex;flex-direction:column;align-items:flex-start;justify-content:center;gap:4px;height:100%;padding:0 14px}
.cal-bar .cb-scale span{position:absolute;transform:translateX(-50%);white-space:nowrap}
.cal-cell.cal-today-col{background:rgba(56,139,253,.05)}
.cal-cell.cal-past-col{background:rgba(0,0,0,.12)}
.cal-cell.cal-weekend{background:rgba(255,255,255,.012)}
.cal-event{border-radius:5px;padding:5px 8px 6px;font-size:11px;cursor:default;transition:filter .12s;margin-top:22px}
.cal-event:hover{filter:brightness(1.2)}
.cal-event.bpa{background:rgba(248,81,73,.18);border-left:3px solid #f85149}
.cal-event.bpb{background:rgba(56,139,253,.18);border-left:3px solid #388bfd}
.cal-event.bpx{background:rgba(240,165,0,.18);border-left:3px solid #f0a500}
.cal-event.completed{opacity:.5}
.ce-bp{font-size:8.5px;font-weight:700;letter-spacing:.4px;text-transform:uppercase;margin-bottom:2px}
.cal-event.bpa .ce-bp{color:#f87171}.cal-event.bpb .ce-bp{color:#60a5fa}.cal-event.bpx .ce-bp{color:#fbbf24}
.cal-event.completed .ce-bp::before{content:'[OK] '}
.ce-route{font-size:11px;font-weight:600;color:var(--text);line-height:1.3}
.ce-col{font-size:9.5px;color:var(--text2);margin-top:3px}
.cal-recal-tag{border-radius:4px;padding:4px 7px;font-size:9.5px;text-align:center;font-weight:700;letter-spacing:.3px;margin-top:2px}
.cal-recal-tag.proposed{background:rgba(240,165,0,.12);border:1px dashed #f0a500;color:#f0a500}
.cal-recal-tag.bp-a{background:rgba(124,58,237,.15);border:1px solid rgba(124,58,237,.5);color:#a78bfa}
.cal-recal-tag.bp-b{background:rgba(220,38,38,.15);border:1px solid rgba(220,38,38,.5);color:#fca5a5}
/* Weather Bad indicator */
.weather-bad{position:absolute;inset:0;background:rgba(255,140,0,.24);border-radius:5px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;z-index:1;pointer-events:none}
.weather-bad .bad-label{font-size:9px;font-weight:700;color:#ef4444;text-transform:uppercase;letter-spacing:.3px;margin-bottom:1px}
.weather-bad .no-sign{font-size:15px;line-height:1;color:#ef4444;font-weight:800;letter-spacing:.6px;display:flex;align-items:center;justify-content:center}
.weather-bad .weather-label{font-size:8px;font-weight:700;color:#ef4444;text-transform:uppercase;letter-spacing:.2px}
.cal-cell.bad-weather{position:relative;z-index:0}
/* -- Availability heatmap view -- */
#availability-view{flex-direction:column;overflow-y:auto;padding:14px 16px;gap:14px}
#avail-header{flex-shrink:0}
#avail-hm-title{font-size:15px;font-weight:700;margin:0 0 4px;letter-spacing:-.3px;font-family:'Space Grotesk',sans-serif}
#avail-hm-sub{font-size:11px;color:var(--text2)}
#avail-panels{display:flex;flex-direction:column;gap:14px;width:100%}
#avail-panels .cgroup{width:100%}
.avail-tbl-wrap{padding:10px;overflow-x:auto}
.avail-tbl{border-collapse:collapse;width:100%}
.avail-tbl th{font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.6px;color:var(--text2);padding:5px 8px;text-align:center}
.avail-tbl th.avail-row-head{text-align:left;width:36px}
.avail-tbl td{padding:0;text-align:center;font-size:13px;font-weight:700;width:48px;height:44px;border:1px solid var(--bg3);position:relative;cursor:default}
.avail-tbl td .anum{position:relative;z-index:2}
.avail-tbl td .abar{position:absolute;bottom:0;left:0;right:0;border-radius:0 0 2px 2px}
.avail-tod-label{font-size:10px;font-weight:700;color:var(--text2);text-align:left;padding:6px 8px;width:36px}
.avail-legend{display:flex;align-items:center;gap:8px;font-size:10px;color:var(--text2);padding:0 10px 10px}
.avail-legend-grad{width:120px;height:10px;border-radius:3px;background:linear-gradient(to right,#f85149,#d29922,#3fb950)}
#avail-tip{position:fixed;display:none;background:var(--bg3);border:1px solid var(--border);border-radius:8px;padding:10px 13px;font-size:11px;box-shadow:0 6px 24px rgba(0,0,0,.6);pointer-events:none;z-index:9999;min-width:130px}
#avail-tip .atip-head{font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:var(--text3);margin-bottom:6px}
#avail-tip .atip-name{font-size:11px;color:var(--text)}
#avail-tip .atip-none{font-size:10px;color:var(--text3);font-style:italic}
.cal-empty-week{grid-column:1/-1;display:flex;align-items:center;justify-content:center;color:var(--text3);font-size:12px;padding:48px}
/* COLLECTOR VIEW */
#collector-view{flex-direction:column;overflow-y:auto;padding:14px 16px;gap:14px}
/* Collector groups */
#cselector{display:flex;flex-direction:column;gap:14px;width:100%}
.cgroup{background:var(--bg2);border:1px solid var(--border);border-radius:12px;overflow:hidden}
.cgroup-head{display:flex;align-items:center;gap:9px;padding:9px 14px;border-bottom:1px solid var(--border)}
.cgroup-head .cg-dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.cgroup-head .cg-title{font-family:'Space Grotesk',sans-serif;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.8px}
.cgroup-head .cg-sub{font-size:10px;color:var(--text3);margin-left:auto}
.cgroup.ccny{border-color:rgba(124,58,237,.35)}
.cgroup.ccny .cgroup-head{background:rgba(124,58,237,.08);border-bottom-color:rgba(124,58,237,.25)}
.cgroup.ccny .cg-dot{background:#7c3aed}
.cgroup.ccny .cg-title{color:#a78bfa}
.cgroup.lagcc{border-color:rgba(220,38,38,.35)}
.cgroup.lagcc .cgroup-head{background:rgba(220,38,38,.08);border-bottom-color:rgba(220,38,38,.25)}
.cgroup.lagcc .cg-dot{background:#dc2626}
.cgroup.lagcc .cg-title{color:#f87171}
.cgroup.staff{border-color:rgba(107,114,128,.3)}
.cgroup.staff .cgroup-head{background:rgba(107,114,128,.07);border-bottom-color:rgba(107,114,128,.2)}
.cgroup.staff .cg-dot{background:#6b7280}
.cgroup.staff .cg-title{color:#9ca3af}
/* Campus pair: CCNY + LaGCC side by side */
.campus-pair{display:flex;gap:14px;width:100%}
.campus-pair .cgroup{flex:1;min-width:0}
.campus-pair .cgroup-tiles{grid-template-columns:repeat(2,1fr)!important}
/* Professors row sits below */
.prof-row{width:100%}
.prof-row .cgroup-tiles{grid-template-columns:repeat(auto-fit,minmax(0,1fr))!important}
.cgroup-tiles{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;padding:10px}
.cc{height:120px;padding:10px 8px;background:var(--bg3);border:1px solid var(--border);border-radius:10px;cursor:pointer;transition:all .15s;text-align:center;display:flex;flex-direction:column;align-items:center;justify-content:center;overflow:visible}
.cc:hover{border-color:var(--accent);background:var(--bg4)}
.cc.active{border-color:var(--accent);background:rgba(56,139,253,.12)}
.cgroup.ccny .cc.active{border-color:#7c3aed;background:rgba(124,58,237,.15)}
.cgroup.lagcc .cc.active{border-color:#dc2626;background:rgba(220,38,38,.12)}
.cgroup.staff .cc.active{border-color:var(--text3);background:rgba(107,114,128,.12)}
.cc .cn{font-size:12px;font-weight:700;white-space:normal;overflow:visible;line-height:1.2;max-width:100%;word-break:break-word}
.cc .ci{font-size:9px;color:var(--text3);margin-top:2px;font-family:'Space Grotesk',sans-serif;letter-spacing:.5px}
.cc .cw{font-size:26px;font-weight:700;line-height:1.1;margin-top:4px}
.cgroup.ccny .cc .cw{color:#a78bfa}
.cgroup.lagcc .cc .cw{color:#f87171}
.cgroup.staff .cc .cw{color:var(--text2)}
.cc.active .cw{color:var(--accent)!important}
.cc .cwl{font-size:9px;color:var(--text3);margin-top:2px}
#cdetail{display:flex;gap:12px;flex-wrap:wrap}
.dcard{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:13px}
#dstats{flex:0 0 auto;min-width:240px}
#dcharts{flex:1;min-width:260px}
.twg{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-bottom:10px}
.twc{background:var(--bg3);border-radius:6px;padding:9px 11px}
.twc .twv{font-size:24px;font-weight:700;color:var(--text);line-height:1}
.twc .twl{font-size:10px;color:var(--text2);margin-top:2px}
.twc .twsb{margin-top:7px;font-size:10px;display:flex;flex-direction:column;gap:2px}
.twc .twsb span{color:var(--text2);display:flex;justify-content:space-between}
.twc .twsb span strong{color:var(--text)}
.apills{display:flex;flex-wrap:wrap;gap:4px;margin-top:5px}
.ap{font-size:10px;padding:2px 7px;border-radius:9px;background:rgba(56,139,253,.15);border:1px solid rgba(56,139,253,.3);color:var(--accent)}
.ap.none{color:var(--text3);background:var(--bg3);border-color:var(--border)}
.stl{font-size:10px;font-weight:700;color:var(--text2);text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px;margin-top:10px}
.stl:first-child{margin-top:0}
#csection{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:13px}
#csection h3{font-size:13px;font-weight:700;margin-bottom:11px;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.wtabs{display:flex;gap:3px;margin-left:auto}
.wtab{font-size:10px;padding:2px 8px;border-radius:4px;border:1px solid var(--border);background:var(--bg3);color:var(--text2);cursor:pointer}
.wtab.active{background:rgba(56,139,253,.2);border-color:var(--accent);color:var(--accent)}
#ccw{height:150px;margin-bottom:13px}
.ctab{width:100%;border-collapse:collapse;font-size:11px}
.ctab th{font-size:9px;font-weight:700;color:var(--text3);text-transform:uppercase;padding:5px 9px;text-align:left;border-bottom:1px solid var(--border);white-space:nowrap}
.ctab td{padding:6px 9px;border-bottom:1px solid rgba(48,54,61,.5)}
.ctab tr:last-child td{border-bottom:none}
.ctab td:first-child{font-weight:600}
.ctab td.num{text-align:right;font-variant-numeric:tabular-nums}
.ctop{background:var(--green-bg);color:var(--green);font-weight:700;border-radius:3px;padding:1px 6px}
.cbot{background:var(--red-bg);color:var(--red);border-radius:3px;padding:1px 6px}
.cval{padding:1px 6px}
.ctab tr.selrow td{background:rgba(56,139,253,.07)}
::-webkit-scrollbar{width:5px;height:5px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--bg4);border-radius:3px}
::-webkit-scrollbar-thumb:hover{background:var(--text3)}
#loading{position:fixed;inset:0;background:var(--bg);display:flex;align-items:center;justify-content:center;z-index:9999;flex-direction:column;gap:10px}
#loading p{color:var(--text2);font-size:13px}
.spin{width:28px;height:28px;border:3px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:sp .7s linear infinite}
@keyframes sp{to{transform:rotate(360deg)}}
.empty{text-align:center;padding:24px;color:var(--text3);font-size:12px}
#toast{position:fixed;bottom:18px;right:18px;background:var(--bg3);border:1px solid var(--border);border-radius:7px;padding:8px 14px;font-size:12px;z-index:9999;transform:translateY(80px);opacity:0;transition:all .25s;pointer-events:none;max-width:300px}
#toast.show{transform:translateY(0);opacity:1}
#toast.success{border-color:var(--green);color:var(--green)}
#toast.error{border-color:var(--red);color:var(--red)}
/* -- Drive header badges -- */
#live-badges{display:flex;align-items:center;gap:6px;margin-left:auto}
.live-badge{font-size:10px;padding:2px 7px;border-radius:4px;border:1px solid var(--border);background:var(--bg3);color:var(--text3);white-space:nowrap;cursor:default}
.live-badge.ok{border-color:rgba(74,222,128,.4);color:#4ade80}
.live-badge.warn{border-color:rgba(210,153,34,.4);color:#d29922}
.live-badge.err{border-color:rgba(248,81,73,.4);color:#f85149}
.drive-sync-btn{font-size:10px;padding:2px 8px;border-radius:4px;border:1px solid var(--border);background:var(--bg3);color:var(--text2);cursor:pointer;transition:background .15s}
.drive-sync-btn:hover{background:var(--bg4,#2d333b)}
/* -- Filters toggle button (lives in Campaign Monitor tab bar) -- */
.filters-toggle-btn{border-color:rgba(96,165,250,.3);color:var(--text3)}
.filters-toggle-btn:hover{background:var(--bg3);border-color:rgba(96,165,250,.6);color:#60a5fa}
.filters-toggle-btn.active{background:var(--bg3);border-color:rgba(96,165,250,.6);color:#60a5fa}
/* Filter dropdown contents layout */
#filters .fg{width:100%;gap:4px;flex-direction:column}
#filters .fg select,#filters .fg input{width:100%;height:32px;padding:4px 8px;font-size:12px}
#filters .fg span.fl{font-size:10px;color:var(--text3);font-weight:600}
#filters .btn{width:100%;justify-content:center;font-size:11px}
#filters #data-status{width:100%;text-align:center}
/* Filter drawer section headers */
.filter-section-head{font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:var(--text3);padding-bottom:4px;border-bottom:1px solid var(--border);margin-bottom:2px}
@media(max-width:768px){
  #header{flex-wrap:wrap;height:auto;padding:8px 10px;gap:0}
  /* Row 1: logos (left) + buttons (right) */
  #header-logos{order:1;flex-shrink:0;margin-right:auto}
  #nasa-worm-logo{height:20px}
  #tempo-logo{height:30px}
  #header-divider{display:none}
  #wx-cutoff-pill{display:none}
  #cal-bars{display:none}
  .sched-unlock-btn,.force-rebuild-btn{order:1;flex-shrink:0;margin:0}
  /* Row 2: title full width */
  #header-title{order:2;flex-basis:100%;flex-shrink:1;min-width:0;text-align:left;margin-top:6px}
  #header h1{font-size:13px;font-family:'Space Grotesk',sans-serif;white-space:normal;line-height:1.2}
  /* Row 3: tabs full width */
  #tabs{order:3;flex-basis:100%;margin-left:0;gap:3px;margin-top:6px;align-items:stretch}
  .tab-group{flex-direction:row;flex:1}
  .tab-group-label{display:none}
  .tab-group-btns{flex:1;gap:3px}
  .tab-sep{display:none}
  .tab-btn{padding:5px 6px;font-size:11px;flex:1;text-align:center}
  /* Filter drawer on mobile - top offset matches header height */
  #filters{top:auto}
  #route-panel{width:100%;position:fixed;transform:translateX(100%);transition:transform .3s ease;z-index:1100}
  #route-panel.open{transform:translateX(0);width:100%;max-width:85vw}
  #map-view{flex-direction:column}
  #map-wrap{flex:1;position:relative}
  #cal-grid{grid-template-columns:40px repeat(7,1fr);grid-template-rows:40px repeat(3,minmax(80px,1fr))}
  .cal-dnum{font-size:16px}
  .cal-day-head{padding:4px 2px}
  .cal-dname{font-size:9px}
  #collector-view{padding:10px 10px;gap:10px}
  .campus-pair{flex-direction:column}
  .cgroup-tiles{gap:6px;padding:8px}
  .cc{height:100px;padding:8px 6px}
  .cc .cw{font-size:26px}
  .cc .cn{font-size:13px}
  #cdetail{flex-direction:column}
  #dstats{min-width:auto;width:100%}
  #dcharts{min-width:auto;width:100%}
  body{font-size:13px}
  .tab-btn{font-size:10px}
  select,input[type=date]{font-size:11px;padding:4px 6px}
  .btn{font-size:10px;padding:3px 8px}
}
@media(max-width:480px){
  #header h1{font-size:12px;font-family:'Space Grotesk',sans-serif;letter-spacing:-.3px}
  #tabs{gap:2px}
  .tab-btn{padding:4px 5px;font-size:9px}
  select,input[type=date]{font-size:10px;padding:3px 5px;height:28px}
  .btn{font-size:9px;padding:2px 6px;height:28px}
  #cal-grid{grid-template-columns:30px repeat(7,1fr);grid-template-rows:32px repeat(3,minmax(60px,1fr))}
  .cal-dnum{font-size:12px}
  .cal-event{padding:3px 5px;font-size:9px}
  body{font-size:12px}
  .psec{padding:8px}
  .schip{padding:5px 6px}
  .schip .sv{font-size:18px}
  .schip .sl{font-size:8px}
  .cw{height:80px}
  #route-panel.open{max-width:95vw}
  #mstats{display:grid;grid-template-columns:repeat(auto-fit,minmax(80px,1fr));gap:8px;width:fit-content}
  .msc{width:80px;height:80px;display:flex;flex-direction:column;justify-content:center;align-items:center;text-align:center;padding:6px}
}
</style>
</head>
<body>
<div id="loading"><div class="spin"></div><p id="load-msg">Loading dashboard...</p></div>
<div id="toast"></div>
<script>
window.onerror=function(msg,src,line,col,err){
  var p=document.getElementById('load-msg');
  if(p){p.textContent='JS Error: '+msg+' (line '+line+')';p.style.color='#f85149';}
  return true;
};
setTimeout(function(){
  var ld=document.getElementById('loading');
  if(ld&&ld.style.display!=='none'){
    var p=document.getElementById('load-msg');
    if(p&&p.textContent==='Loading dashboard...'){
      p.textContent='Timeout - check browser console (F12)';
      p.style.color='#d29922';
    }
  }
},5000);
</script>

<div id="app">
  <div id="header">
    <div id="header-logos">
      <!-- NASA Worm logo -->
      <svg id="nasa-worm-logo" xmlns="http://www.w3.org/2000/svg" viewBox="130 215 545 165" role="img" aria-label="NASA">
        <path d="M237.89,332.33c1.57,6,4.12,8.27,8.61,8.27,4.66,0,7.1-2.8,7.1-8.27V230.83h29.19v101.5c0,14.31-1.86,20.51-9.11,27.77-5.23,5.22-14.87,9.27-27.05,9.27-9.84,0-19.25-3.26-25.25-9.27-5.27-5.28-8.16-10.69-12.67-27.77L190.8,264.67c-1.58-6-4.12-8.27-8.62-8.27-4.66,0-7.1,2.8-7.1,8.27v101.5H145.9V264.67c0-14.31,1.85-20.51,9.11-27.76,5.22-5.23,14.87-9.28,27.05-9.28,9.83,0,19.25,3.26,25.25,9.27,5.26,5.28,8.15,10.69,12.67,27.77Z" fill="#fc3d21"/>
        <path d="M372.23,236.5c-6-5.82-13-8.87-24.11-8.87S330,230.68,324,236.5c-3.48,3.4-6.22,8.49-8.13,14.49L279.06,366.17h30.17l33.7-105.44a8.78,8.78,0,0,1,1.26-2.81,5.35,5.35,0,0,1,7.86,0,8.67,8.67,0,0,1,1.27,2.81L387,366.17h30.23L380.37,251C378.45,245,375.71,239.9,372.23,236.5Z" fill="#fc3d21"/>
        <path d="M511.84,295.55c-8.53-8.48-19.12-11.15-36.38-11.15H451.27c-9.24,0-12.93-1.11-15.84-4-2-2-2.94-4.88-2.94-8.32s.86-7.08,3.3-9.48c2.17-2.13,5.13-3.11,10.82-3.11h69.9V230.83H452c-19.12,0-28.45,4.07-35.82,11.39-8.15,8.11-12.05,17-12.05,30.21,0,11.71,4.28,22.54,10.8,29,8.53,8.48,19.12,11.16,36.39,11.16h24.19c9.24,0,12.92,1.11,15.84,4,2,2,2.93,4.88,2.93,8.32s-.85,7.08-3.3,9.48c-2.17,2.13-5.13,3.11-10.81,3.11H406.25v28.67h68.53c19.12,0,28.44-4.07,35.81-11.39,8.15-8.11,12.05-17,12.05-30.21C522.64,312.87,518.36,302,511.84,295.55Z" fill="#fc3d21"/>
        <path d="M623.94,366.17,590.23,260.73a8.78,8.78,0,0,0-1.26-2.81,5.35,5.35,0,0,0-7.86,0,8.78,8.78,0,0,0-1.26,2.81L546.14,366.17H516L552.79,251c1.92-6,4.66-11.09,8.14-14.49,6-5.82,13-8.87,24.11-8.87s18.14,3.05,24.11,8.87c3.48,3.4,6.22,8.49,8.14,14.49L654.1,366.17Z" fill="#fc3d21"/>
      </svg>
      <div id="header-divider"></div>
      <!-- TEMPO mission patch -->
      <svg id="tempo-logo" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 220" role="img" aria-label="TEMPO Mission">
        <!-- Shield/badge outer border -->
        <path d="M100,8 L185,35 L195,105 Q195,165 100,212 Q5,165 5,105 L15,35 Z" fill="#1a2a5e" stroke="#c0c8d8" stroke-width="3"/>
        <!-- Inner ring -->
        <path d="M100,16 L178,40 L187,105 Q187,158 100,200 Q13,158 13,105 L22,40 Z" fill="none" stroke="#ffffff" stroke-width="1.5" opacity="0.5"/>
        <!-- Compass spoke points -->
        <line x1="100" y1="8" x2="100" y2="2" stroke="#c0c8d8" stroke-width="2"/>
        <line x1="185" y1="35" x2="190" y2="31" stroke="#c0c8d8" stroke-width="2"/>
        <line x1="195" y1="105" x2="201" y2="105" stroke="#c0c8d8" stroke-width="2"/>
        <line x1="5" y1="105" x2="-1" y2="105" stroke="#c0c8d8" stroke-width="2"/>
        <line x1="15" y1="35" x2="10" y2="31" stroke="#c0c8d8" stroke-width="2"/>
        <!-- Satellite solar panels at top -->
        <rect x="62" y="14" width="28" height="7" rx="1.5" fill="#8090a8" opacity="0.9"/>
        <rect x="110" y="14" width="28" height="7" rx="1.5" fill="#8090a8" opacity="0.9"/>
        <!-- Satellite body -->
        <rect x="88" y="11" width="24" height="13" rx="2" fill="#9aabbc"/>
        <!-- TEMPO text bar -->
        <rect x="22" y="30" width="156" height="26" rx="4" fill="#0d1f5c"/>
        <text x="100" y="48" font-family="'Space Grotesk',Arial,sans-serif" font-size="20" font-weight="700" fill="white" text-anchor="middle" letter-spacing="3">TEMPO</text>
        <!-- Globe with grid -->
        <!-- Globe base circle -->
        <clipPath id="globeClip"><path d="M100,16 L178,40 L187,105 Q187,158 100,200 Q13,158 13,105 L22,40 Z"/></clipPath>
        <ellipse cx="100" cy="130" rx="78" ry="60" fill="#1a3a6e" clip-path="url(#globeClip)"/>
        <!-- Grid squares representing air quality data cells -->
        <!-- Row 1 (top) -->
        <rect x="32" y="72" width="18" height="14" fill="#4ade80" opacity="0.85"/><rect x="52" y="72" width="18" height="14" fill="#86efac" opacity="0.85"/><rect x="72" y="72" width="18" height="14" fill="#4ade80" opacity="0.85"/><rect x="92" y="72" width="18" height="14" fill="#facc15" opacity="0.85"/><rect x="112" y="72" width="18" height="14" fill="#4ade80" opacity="0.85"/><rect x="132" y="72" width="18" height="14" fill="#86efac" opacity="0.85"/><rect x="152" y="72" width="14" height="14" fill="#4ade80" opacity="0.85"/>
        <!-- Row 2 -->
        <rect x="28" y="88" width="18" height="14" fill="#86efac" opacity="0.85"/><rect x="48" y="88" width="18" height="14" fill="#facc15" opacity="0.85"/><rect x="68" y="88" width="18" height="14" fill="#4ade80" opacity="0.85"/><rect x="88" y="88" width="18" height="14" fill="#4ade80" opacity="0.85"/><rect x="108" y="88" width="18" height="14" fill="#991b1b" opacity="0.85"/><rect x="128" y="88" width="18" height="14" fill="#facc15" opacity="0.85"/><rect x="148" y="88" width="18" height="14" fill="#4ade80" opacity="0.85"/>
        <!-- Row 3 -->
        <rect x="26" y="104" width="18" height="14" fill="#4ade80" opacity="0.85"/><rect x="46" y="104" width="18" height="14" fill="#991b1b" opacity="0.85"/><rect x="66" y="104" width="18" height="14" fill="#facc15" opacity="0.85"/><rect x="86" y="104" width="18" height="14" fill="#4ade80" opacity="0.85"/><rect x="106" y="104" width="18" height="14" fill="#86efac" opacity="0.85"/><rect x="126" y="104" width="18" height="14" fill="#4ade80" opacity="0.85"/><rect x="146" y="104" width="18" height="14" fill="#991b1b" opacity="0.85"/>
        <!-- Row 4 -->
        <rect x="28" y="120" width="18" height="14" fill="#facc15" opacity="0.85"/><rect x="48" y="120" width="18" height="14" fill="#4ade80" opacity="0.85"/><rect x="68" y="120" width="18" height="14" fill="#86efac" opacity="0.85"/><rect x="88" y="120" width="18" height="14" fill="#991b1b" opacity="0.85"/><rect x="108" y="120" width="18" height="14" fill="#4ade80" opacity="0.85"/><rect x="128" y="120" width="18" height="14" fill="#facc15" opacity="0.85"/><rect x="148" y="120" width="14" height="14" fill="#4ade80" opacity="0.85"/>
        <!-- Row 5 -->
        <rect x="34" y="136" width="18" height="14" fill="#4ade80" opacity="0.85"/><rect x="54" y="136" width="18" height="14" fill="#86efac" opacity="0.85"/><rect x="74" y="136" width="18" height="14" fill="#facc15" opacity="0.85"/><rect x="94" y="136" width="18" height="14" fill="#4ade80" opacity="0.85"/><rect x="114" y="136" width="18" height="14" fill="#4ade80" opacity="0.85"/><rect x="134" y="136" width="18" height="14" fill="#991b1b" opacity="0.85"/>
        <!-- Row 6 (bottom, truncated) -->
        <rect x="44" y="152" width="18" height="12" fill="#4ade80" opacity="0.85"/><rect x="64" y="152" width="18" height="12" fill="#facc15" opacity="0.85"/><rect x="84" y="152" width="18" height="12" fill="#4ade80" opacity="0.85"/><rect x="104" y="152" width="18" height="12" fill="#86efac" opacity="0.85"/>
        <!-- Grid lines overlay -->
        <line x1="26" y1="72" x2="166" y2="72" stroke="#1a2a5e" stroke-width="1.5" opacity="0.7"/>
        <line x1="26" y1="86" x2="168" y2="86" stroke="#1a2a5e" stroke-width="1.5" opacity="0.7"/>
        <line x1="24" y1="102" x2="166" y2="102" stroke="#1a2a5e" stroke-width="1.5" opacity="0.7"/>
        <line x1="24" y1="118" x2="166" y2="118" stroke="#1a2a5e" stroke-width="1.5" opacity="0.7"/>
        <line x1="26" y1="134" x2="154" y2="134" stroke="#1a2a5e" stroke-width="1.5" opacity="0.7"/>
        <line x1="32" y1="150" x2="124" y2="150" stroke="#1a2a5e" stroke-width="1.5" opacity="0.7"/>
        <!-- Outer badge border redraw on top -->
        <path d="M100,8 L185,35 L195,105 Q195,165 100,212 Q5,165 5,105 L15,35 Z" fill="none" stroke="#c0c8d8" stroke-width="3"/>
      </svg>
    </div>
    <div id="header-title">
      <h1>NASA EnAACT Field Campaign Data Desk</h1>
    </div>
    <button id="sched-unlock-btn" class="sched-unlock-btn" title="Log in to Admin Mode">&#x1F511; Admin Login</button>
    <button id="force-rebuild-btn" class="force-rebuild-btn" title="Force rebuild: build weather, run scheduler, rebuild dashboard">&#x27F3; Rebuild</button>
    <button class="upload-data-btn" onclick="openUploadModal()">&#x2B06; Upload Data</button>
    <div id="tabs">
      <div class="tab-group" id="campaign-tab-group">
        <span class="tab-group-label monitor">Campaign Monitor</span>
        <div class="tab-group-btns">
          <button class="tab-btn active" data-view="map-view">&#x1F5FA;&#xFE0F; Map</button>
          <button class="tab-btn" data-view="collector-view">&#x1F465; Collectors</button>
          <button id="filters-btn" class="tab-btn filters-toggle-btn" title="Toggle walk filters">&#x2699; Filters &#x25BE;</button>
        </div>
      </div>
      <div class="tab-sep"></div>
      <div class="tab-group">
        <span class="tab-group-label scheduling">Scheduling</span>
        <div class="tab-group-btns">
          <button class="tab-btn" data-view="calendar-view">&#x1F4C6; Calendar</button>
          <button class="tab-btn" data-view="availability-view">&#x1F4C5; Availability</button>
        </div>
      </div>
    </div>
  </div>
  <div id="content">
    <!--- MAP VIEW --->
    <div id="map-view" class="view active">
      <div id="map-wrap">
        <div id="map"></div>
        <div id="mstats"></div>
        <div id="route-groups-panel"><div class="rgb-header"><span>&#9632; Route Groups</span><button id="rgb-all-btn">All</button></div><div id="rgb-list"></div></div>
        <button id="collector-homes-btn" title="Toggle collector areas">&#x1F3E0; Collector Areas</button>
        <div id="mlegend">
          <h4>Completion Progress</h4>
          <div style="width:100%;height:7px;border-radius:4px;background:linear-gradient(to right,hsl(0,100%,40%),hsl(30,100%,40%),hsl(60,100%,40%),hsl(90,100%,40%),hsl(120,100%,40%));margin-bottom:6px"></div>
          <div style="display:flex;justify-content:space-between;font-size:9px;color:var(--text3)"><span>0</span><span>3</span><span>Target (6+)</span></div>
        </div>
      </div>
      <div id="route-panel">
        <div id="rph">
          <div id="rpt">
            <h2 id="pname">Route</h2>
            <div class="rmeta"><span id="pcode"></span><span id="pstatus"></span></div>
          </div>
          <button id="close-panel">&#x2715;</button>
        </div>
        <div id="rpb">
          <div class="psec">
            <h3>Completions (current filter)</h3>
            <div class="srow">
              <div class="schip"><div class="sv" id="ps-tot">0</div><div class="sl">Total</div></div>
              <div class="schip am"><div class="sv" id="ps-am">0</div><div class="sl">AM</div></div>
              <div class="schip md"><div class="sv" id="ps-md">0</div><div class="sl">MD</div></div>
              <div class="schip pm"><div class="sv" id="ps-pm">0</div><div class="sl">PM</div></div>
            </div>
          </div>
          <div class="psec">
            <h3>TOD vs Target (8)</h3>
            <div class="cw"><canvas id="tod-chart"></canvas></div>
          </div>
          <div class="psec">
            <h3>Walk Log</h3>
            <div id="plog"></div>
          </div>
        </div>
      </div>
    </div>
    <!--- COLLECTOR VIEW --->
    <div id="collector-view" class="view">
      <div id="cselector"></div>
      <div id="cdetail">
        <div class="dcard" id="dstats"></div>
        <div class="dcard" id="dcharts"></div>
      </div>
      <div id="csection">
        <h3>All Collectors - Side by Side
          <div class="wtabs">
            <button class="wtab active" data-win="2w">Last 2 Wks</button>
            <button class="wtab" data-win="mo">This Month</button>
            <button class="wtab" data-win="all">Whole Project</button>
          </div>
        </h3>
        <div id="ccw"><canvas id="comp-chart"></canvas></div>
        <table class="ctab">
          <thead><tr>
            <th>Collector</th>
            <th class="num">Last 2 Wks</th>
            <th class="num">This Month</th>
            <th class="num">Whole Project</th>
          </tr></thead>
          <tbody id="ctbody"></tbody>
        </table>
      </div>
    </div>
    <!--- CALENDAR VIEW --->
    <div id="calendar-view" class="view">
      <div id="cal-nav">
        <button class="cal-nav-btn" id="cal-prev" title="Previous week">&#x2039;</button>
        <button class="cal-nav-btn" id="cal-next" title="Next week">&#x203A;</button>
        <h2 id="cal-title">-</h2>
        <div id="cal-bp-toggles" style="display:flex;gap:4px;margin-left:12px">
          <button class="bp-toggle active" data-backpack="A" title="Toggle Backpack A">BP A<span class="bp-boro-badge" data-bp="A"></span><div class="bp-boro-tooltip" data-bp="A"></div></button>
          <button class="bp-toggle active" data-backpack="B" title="Toggle Backpack B">BP B<span class="bp-boro-badge" data-bp="B"></span><div class="bp-boro-tooltip" data-bp="B"></div></button>
        </div>
        <div id="wx-cutoff-pill" title="Cloud cover threshold &mdash; slots at or below 50% are marked GO" style="margin-left:auto">
          &#x2601; <span class="wx-good">&#x2264;50%&nbsp;GO</span>&nbsp;<span style="color:var(--border)">|</span>&nbsp;<span class="wx-bad">&gt;50%&nbsp;NO&nbsp;GO</span>
        </div>
        <div id="cal-bars">
          <div class="cal-bar" data-bp="A" data-zone="green">
            <div class="cb-label">
              <span class="cb-title">BP A &middot; CCNY &middot; Days Since Cal</span>
              <span class="cb-count"><span class="cb-count-num">0</span></span>
              <div class="cb-last"><b class="cb-last-date">&mdash;</b></div>
            </div>
            <div class="cb-track-wrap">
              <div class="cb-track">
                <div class="cb-zonemark" style="left:40%"></div>
                <div class="cb-zonemark" style="left:64%"></div>
                <div class="cb-fill"></div>
              </div>
              <div class="cb-scale cb-scale-words">
                <span class="cb-scale-good" style="left:20%;color:var(--green)">GOOD</span>
                <span class="cb-scale-soon" style="left:52%;color:var(--yellow)">SOON</span>
                <span class="cb-scale-over" style="left:82%;color:var(--red)">OVERDUE</span>
              </div>
            </div>
            <button class="cb-log-btn" data-bp="A" title="Record Backpack A calibration">+ Log Cal A</button>
          </div>
          <div class="cal-bar" data-bp="B" data-zone="green">
            <div class="cb-label">
              <span class="cb-title">BP B &middot; LaGCC &middot; Days Since Cal</span>
              <span class="cb-count"><span class="cb-count-num">0</span></span>
              <div class="cb-last"><b class="cb-last-date">&mdash;</b></div>
            </div>
            <div class="cb-track-wrap">
              <div class="cb-track">
                <div class="cb-zonemark" style="left:40%"></div>
                <div class="cb-zonemark" style="left:64%"></div>
                <div class="cb-fill"></div>
              </div>
              <div class="cb-scale cb-scale-words">
                <span class="cb-scale-good" style="left:20%;color:var(--green)">GOOD</span>
                <span class="cb-scale-soon" style="left:52%;color:var(--yellow)">SOON</span>
                <span class="cb-scale-over" style="left:82%;color:var(--red)">OVERDUE</span>
              </div>
            </div>
            <button class="cb-log-btn" data-bp="B" title="Record Backpack B calibration">+ Log Cal B</button>
          </div>
        </div>
      </div>
      <div id="cal-body">
        <div id="cal-grid"></div>
      </div>
    </div>
    <!--- AVAILABILITY VIEW --->
    <div id="availability-view" class="view">
      <div id="avail-header">
        <h1 id="avail-hm-title">Collector Availability</h1>
        <div id="avail-hm-sub">Built from Availability.xlsx &nbsp;&middot;&nbsp; Numbers = collectors available per slot &nbsp;&middot;&nbsp; Hover a cell to see who is free</div>
      </div>
      <div id="avail-panels">
        <div class="cgroup ccny">
          <div class="cgroup-head">
            <span class="cg-dot"></span>
            <span class="cg-title">CCNY &mdash; Backpack A</span>
            <span class="cg-sub">SOT &middot; AYA &middot; JEN &middot; TAH &middot; ANG &nbsp;&nbsp;max __MAX_A__</span>
          </div>
          <div class="avail-tbl-wrap">
            <table class="avail-tbl" id="avail-tbl-a"></table>
            <div class="avail-legend">
              <span>0</span><div class="avail-legend-grad"></div><span>max</span>
              &nbsp;&middot;&nbsp; Cell height = fill %
            </div>
          </div>
        </div>
        <div class="cgroup lagcc">
          <div class="cgroup-head">
            <span class="cg-dot"></span>
            <span class="cg-title">LaGCC &mdash; Backpack B</span>
            <span class="cg-sub">TER &middot; ALX &middot; SCT &middot; JAM &middot; JEN &nbsp;&nbsp;max __MAX_B__</span>
          </div>
          <div class="avail-tbl-wrap">
            <table class="avail-tbl" id="avail-tbl-b"></table>
            <div class="avail-legend">
              <span>0</span><div class="avail-legend-grad"></div><span>max</span>
              &nbsp;&middot;&nbsp; Cell height = fill %
            </div>
          </div>
        </div>
      </div>
      <div id="avail-tip"><div class="atip-head" id="atip-head"></div><div id="atip-names"></div></div>
    </div>
    </div>
  </div>
</div>

<script>
// --- DATA ---
const ROUTES_GEO = __ROUTES_JSON__;
const COLLECTOR_HOMES = __COLLECTOR_HOMES__;
const ROUTE_GROUPS = __ROUTE_GROUPS_JSON__;
const BAKED_SCHEDULE = __BAKED_SCHEDULE__;
const BAKED_WEATHER = __BAKED_WEATHER__;
let RUNTIME_SCHEDULE = BAKED_SCHEDULE;
let RUNTIME_WEATHER = BAKED_WEATHER;

function _schedStamp(s){
  if(!s) return '';
  return String(s.generated_at || s.generated || s.week_end || '');
}
async function _fetchJsonFresh(path){
  const sep = path.includes('?') ? '&' : '?';
  const resp = await fetch(path + sep + '_ts=' + Date.now(), {cache:'no-store'});
  if(!resp.ok) throw new Error(String(resp.status));
  return await resp.json();
}
async function refreshRuntimeData(){
  try{
    const latestSchedule = await _fetchJsonFresh('schedule_output.json');
    if(latestSchedule && latestSchedule.assignments){
      RUNTIME_SCHEDULE = latestSchedule;
    }
  }catch(_e){}
  try{
    const latestWeather = await _fetchJsonFresh('weather.json');
    if(latestWeather && latestWeather.weather){
      RUNTIME_WEATHER = latestWeather;
    }
  }catch(_e){}
}
// Campus affiliation -> pin color  (purple = CCNY, red = LaGCC, amber = staff)
const COLLECTOR_PIN_COLOR = {
  'SOT':'#7c3aed','AYA':'#7c3aed','JEN':'#7c3aed','TAH':'#7c3aed','ANG':'#7c3aed',
  'TER':'#dc2626','ALX':'#dc2626','SCT':'#dc2626','JAM':'#dc2626',
};
const ROUTE_LABELS = {
  "MN_HT":"Manhattan - Harlem","MN_WH":"Manhattan - Washington Hts",
  "MN_UE":"Manhattan - Upper East Side","MN_MT":"Manhattan - Midtown",
  "MN_LE":"Manhattan - Union Sq / LES","BX_HP":"Bronx - Hunts Point",
  "BX_NW":"Bronx - Norwood","BK_DT":"Brooklyn - Downtown BK",
  "BK_WB":"Brooklyn - Williamsburg","BK_BS":"Brooklyn - Bed Stuy",
  "BK_CH":"Brooklyn - Crown Heights","BK_SP":"Brooklyn - Sunset Park",
  "BK_CI":"Brooklyn - Coney Island","QN_FU":"Queens - Flushing",
  "QN_LI":"Queens - Astoria / LIC","QN_JH":"Queens - Jackson Heights",
  "QN_JA":"Queens - Jamaica","QN_FH":"Queens - Forest Hills",
  "QN_LA":"Queens - LaGuardia CC","QN_EE":"Queens - East Elmhurst",
};
const ALL_ROUTES = new Set(Object.keys(ROUTE_LABELS));
const COLLECTORS = ["SOT","AYA","ALX","TAH","JAM","JEN","SCT","TER","PRA","NAT","NRS"];
const STUDENT_COLLECTORS = COLLECTORS.filter(c => !["NRS","PRA","NAT"].includes(c));
const CNAMES = {
  SOT:"Soteri",AYA:"Aya Nasri",ALX:"Alex",TAH:"Taha",JAM:"James",
  JEN:"Jennifer",SCT:"Scott",TER:"Terra",
  PRA:"Prathap",NAT:"Nathan",NRS:"Naresh"
};
const AFFINITY = __AFFINITY_JSON__;
const SAMPLE_LOG = `__SAMPLE_LOG__`;
const TARGET=6, MINC=6;
const TODS=["AM","MD","PM"];

// --- STATE ---
let allWalks=[], filteredWalks=[], logText=SAMPLE_LOG;
let currentRoute=null, currentCollector=COLLECTORS[0], currentWin='2w';
let filters={season:'',tod:'',backpack:'',from:null,to:null};
let visibleBackpacks={A:true,B:true,X:true};
let map=null, routeLayers={}, routeCentroids={}, charts={};
let collectorHomeLayer=null, collectorHomesVisible=false, collectorHomeMarkers={};
let routeGroupLayers=[], routeGroupLabels=[], routeGroupVisible=[];

// --- UTIL ---
function getSeason(d){const m=d.getMonth()+1;return m>=3&&m<=5?'Spring':m>=6&&m<=8?'Summer':m>=9&&m<=11?'Fall':'Winter';}
function today(){return new Date();}
function fmtDate(d){return d.toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'});}
function isL2W(d){const t=today(),c=new Date(t);c.setDate(t.getDate()-14);return d>=c&&d<=t;}
function isThisMo(d){const t=today();return d.getMonth()===t.getMonth()&&d.getFullYear()===t.getFullYear();}
function isThisSea(d){return getSeason(d)===getSeason(today());}
function inWin(w,win){return win==='2w'?isL2W(w.date):win==='mo'?isThisMo(w.date):win==='sea'?isThisSea(w.date):true;}
function dc(id){if(charts[id]){charts[id].destroy();delete charts[id];}}
function toast(msg,tp=''){
  const el=document.getElementById('toast');
  el.textContent=msg;el.className='show '+tp;
  setTimeout(()=>{el.className='';},3000);
}
// --- PARSE ---
function parseLog(txt){
  const ws=[];
  for(const raw of txt.split('\\n')){
    const line=raw.trim();if(!line)continue;
    const aiAscii=line.indexOf('->');
    const aiUtf=line.indexOf(String.fromCharCode(0x2192));
    const ai=aiAscii>=0?aiAscii:aiUtf;
    const cut=aiAscii>=0?2:aiUtf>=0?1:0;
    const code=(ai>=0?line.slice(ai+cut):line).trim();
    const p=code.split('_');
    if(p.length!==8&&p.length!==6)continue;
    let bp,col,boro,neigh,tod,date;
    if(p.length===8){
      [bp,col,boro,neigh]=p;tod=p[7];
      date=new Date(+p[6],+p[4]-1,+p[5]);
    }else{
      [bp,col,boro,neigh]=p;tod=p[5];
      const r=p[4];date=new Date(+r.slice(0,4),+r.slice(4,6)-1,+r.slice(6,8));
    }
    if(isNaN(date.getTime()))continue;
    const route=boro+'_'+neigh;
    if(!ALL_ROUTES.has(route))continue;
    if(!TODS.includes(tod))continue;
    ws.push({bp:bp.trim(),collector:col.trim(),route,boro,neigh,date,tod:tod.trim(),season:getSeason(date)});
  }
  return ws;
}
// --- FILTER ---
function updateCollectorHomePins(){
  for(const[cid,marker]of Object.entries(collectorHomeMarkers)){
    const h=COLLECTOR_HOMES[cid];
    if(h&&h.non_collector){marker.setIcon(makeHomeIcon(cid,0));continue;}
    const count=filteredWalks.filter(w=>w.collector===cid).length;
    marker.setIcon(makeHomeIcon(cid,count));
  }
}
function applyFilters(){
  filteredWalks=allWalks.filter(w=>{
    if(filters.season&&w.season!==filters.season)return false;
    if(filters.tod&&w.tod!==filters.tod)return false;
    if(filters.backpack&&w.bp!==filters.backpack)return false;
    if(!visibleBackpacks[w.bp])return false;
    if(filters.from&&w.date<filters.from)return false;
    if(filters.to&&w.date>filters.to)return false;
    return true;
  });
  updateMapColors();
  updateCollectorHomePins();
  updateMapStats();
  if(currentRoute)updateRoutePanel(currentRoute);
  if(document.getElementById('collector-view').classList.contains('active'))renderCV();
  if(document.getElementById('calendar-view').classList.contains('active'))renderCalendar();
}
// --- MAP ---
function makeHomeIcon(cid,count){
  const h=COLLECTOR_HOMES[cid];
  const lbl=h?(h.name.startsWith('Prof.')?h.name.split(' ')[1]:h.name.split(' ')[0]):cid;
  const pinColor=COLLECTOR_PIN_COLOR[cid]||'#fbbf24';
  if(h&&h.non_collector){
    // Non-collector: name inside the pin, no count, no label below
    const fontSize=lbl.length>5?9:11;
    return L.divIcon({className:'',iconSize:[44,50],iconAnchor:[22,46],
      html:'<div style="display:flex;flex-direction:column;align-items:center;width:44px">'+
           '<svg width="36" height="46" viewBox="0 0 36 46" xmlns="http://www.w3.org/2000/svg">'+
           '<path d="M18 2C9.2 2 2 9.2 2 18C2 28.5 18 44 18 44S34 28.5 34 18C34 9.2 26.8 2 18 2Z"'+
           ' fill="'+pinColor+'" stroke="rgba(255,255,255,.35)" stroke-width="1.2"/>'+
           '<circle cx="18" cy="18" r="11" fill="rgba(0,0,0,.2)"/>'+
           '<text x="18" y="22" text-anchor="middle" dominant-baseline="middle" fill="white"'+
           ' font-family="-apple-system,BlinkMacSystemFont,sans-serif" font-weight="800" font-size="'+fontSize+'">'+lbl+'</text>'+
           '</svg>'+
           '</div>'});
  }
  return L.divIcon({className:'',iconSize:[52,66],iconAnchor:[26,46],
    html:'<div style="display:flex;flex-direction:column;align-items:center;width:52px">'+
         '<svg width="36" height="46" viewBox="0 0 36 46" xmlns="http://www.w3.org/2000/svg">'+
         '<path d="M18 2C9.2 2 2 9.2 2 18C2 28.5 18 44 18 44S34 28.5 34 18C34 9.2 26.8 2 18 2Z"'+
         ' fill="'+pinColor+'" stroke="rgba(255,255,255,.35)" stroke-width="1.2"/>'+
         '<circle cx="18" cy="18" r="11" fill="rgba(0,0,0,.2)"/>'+
         '<text x="18" y="23" text-anchor="middle" fill="white"'+
         ' font-family="-apple-system,BlinkMacSystemFont,sans-serif" font-weight="800" font-size="13">'+count+'</text>'+
         '</svg>'+
         '<div style="color:white;font-weight:700;font-size:10px;line-height:1.2;'+
         'text-shadow:0 1px 3px rgba(0,0,0,.95),0 0 8px rgba(0,0,0,.8);'+
         'white-space:nowrap;margin-top:1px">'+lbl+'</div>'+
         '</div>'});
}
// Chaikin corner-cutting: stays inside control polygon, no overshoot
function _smoothLoop(pts,iters=4){
  let p=pts.slice();
  for(let it=0;it<iters;it++){
    const np=[];
    for(let i=0;i<p.length;i++){
      const a=p[i],b=p[(i+1)%p.length];
      np.push([a[0]*0.6+b[0]*0.4,a[1]*0.6+b[1]*0.4]);
      np.push([a[0]*0.4+b[0]*0.6,a[1]*0.4+b[1]*0.6]);
    }
    p=np;
  }
  return p;
}
function initMap(){
  map=L.map('map',{center:[40.72,-73.96],zoom:11,zoomControl:false});
  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',{
    attribution:'&copy; OSM &copy; CARTO',maxZoom:20,subdomains:'abcd'
  }).addTo(map);
  for(const[code,geo]of Object.entries(ROUTES_GEO)){
    const pts=geo.lines.flat();if(!pts.length)continue;
    routeCentroids[code]=[pts.reduce((s,p)=>s+p[0],0)/pts.length,pts.reduce((s,p)=>s+p[1],0)/pts.length];
    routeLayers[code]=[];
    for(const line of geo.lines){
      const L2=L.polyline(line,{color:'#888',weight:4,opacity:.8,interactive:true});
      L2.on('click',function(e){e.originalEvent.stopPropagation();openPanel(code);});
      L2.on('mouseover',function(){this.setStyle({weight:6,opacity:1});});
      L2.on('mouseout',function(){styleRoute(code);});
      L2.addTo(map);
      routeLayers[code].push(L2);
    }
  }
  // -- Collector home layer (toggled) ---
  collectorHomeLayer=L.layerGroup();
  for(const[cid,h]of Object.entries(COLLECTOR_HOMES)){
    const initCount=filteredWalks.filter(w=>w.collector===cid).length;
    const m=L.marker([h.lat,h.lng],{icon:makeHomeIcon(cid,initCount),zIndexOffset:500})
     .bindPopup(`<b>${h.name}</b> (${cid})<br><small style="color:#fbbf24">Home location</small>`);
    m.addTo(collectorHomeLayer);
    collectorHomeMarkers[cid]=m;
  }
  document.getElementById('collector-homes-btn').addEventListener('click',()=>{
    if(!requireAuth('Collector Areas'))return;
    collectorHomesVisible=!collectorHomesVisible;
    if(collectorHomesVisible){collectorHomeLayer.addTo(map);}
    else{collectorHomeLayer.remove();}
    document.getElementById('collector-homes-btn').classList.toggle('chb-on',collectorHomesVisible);
  });
  ROUTE_GROUPS.forEach((g,i)=>{
    const poly=L.polygon(_smoothLoop(g.hull),{
      color:g.color,fillColor:g.color,
      fillOpacity:0.07,opacity:0.55,
      weight:2,dashArray:'7,5',interactive:false
    });
    routeGroupLayers.push(poly);
    routeGroupVisible.push(false);
    const cx=g.hull.reduce((s,p)=>s+p[0],0)/g.hull.length;
    const cy=g.hull.reduce((s,p)=>s+p[1],0)/g.hull.length;
    routeGroupLabels.push(L.marker([cx,cy],{
      icon:L.divIcon({
        html:`<div style="font-size:38px;font-weight:900;color:${g.color};text-shadow:-2px -2px 0 #000,2px -2px 0 #000,-2px 2px 0 #000,2px 2px 0 #000,0 0 14px rgba(0,0,0,.95);line-height:1;pointer-events:none">${i+1}</div>`,
        iconSize:[44,44],iconAnchor:[22,22],className:''
      }),interactive:false,zIndexOffset:500
    }));
    const row=document.createElement('label');
    row.className='rgb-item';
    row.innerHTML=`<input type="checkbox" data-gi="${i}"><span class="rgb-dot" style="background:${g.color}"></span><span class="rgb-lbl">${g.name}</span>`;
    row.querySelector('input').addEventListener('change',e=>{
      routeGroupVisible[i]=e.target.checked;
      if(e.target.checked){routeGroupLayers[i].addTo(map);routeGroupLabels[i].addTo(map);}
      else{routeGroupLayers[i].remove();routeGroupLabels[i].remove();}
      const allOn=routeGroupVisible.every(Boolean),allOff=routeGroupVisible.every(v=>!v);
      document.getElementById('rgb-all-btn').textContent=allOn?'None':'All';
    });
    document.getElementById('rgb-list').appendChild(row);
  });
  document.getElementById('rgb-all-btn').addEventListener('click',()=>{
    const newState=!routeGroupVisible.every(Boolean);
    routeGroupVisible.fill(newState);
    document.querySelectorAll('#rgb-list input').forEach((cb,i)=>{
      cb.checked=newState;
      if(newState){routeGroupLayers[i].addTo(map);routeGroupLabels[i].addTo(map);}
      else{routeGroupLayers[i].remove();routeGroupLabels[i].remove();}
    });
    document.getElementById('rgb-all-btn').textContent=newState?'None':'All';
  });
}
function gradientColor(n){
  const hue=Math.round(Math.min(n,6)/6*120);
  return`hsl(${hue},100%,40%)`;
}
function routeStatus(code,ws){
  const n=ws.filter(w=>w.route===code).length;
  const s=n>=TARGET?'green':'red';
  return{s,c:gradientColor(n),n};
}
function styleRoute(code){
  const{c}=routeStatus(code,filteredWalks);
  const sel=code===currentRoute;
  for(const l of(routeLayers[code]||[]))l.setStyle({color:c,weight:sel?7:4,opacity:sel?1:.82});
}
function updateMapColors(){for(const c of Object.keys(routeLayers))styleRoute(c);}
function updateMapStats(){
  const cnt={green:0,yellow:0,red:0};
  for(const c of ALL_ROUTES)cnt[routeStatus(c,filteredWalks).s]++;
  document.getElementById('mstats').innerHTML=
    `<div class="msc"><strong style="color:#58a6ff">${filteredWalks.length}</strong>Total Walks In Selected Window</div>
     <div class="msc"><strong style="color:#15803d">${cnt.green}</strong>At target (6+)</div>
     <div class="msc"><strong style="color:#f85149">${cnt.red}</strong>Below target (&lt;6)</div>`;
}
// --- ROUTE PANEL ---
function openPanel(code){
  currentRoute=code;
  document.getElementById('route-panel').classList.add('open');
  updateRoutePanel(code);
  for(const c of Object.keys(routeLayers))styleRoute(c);
  if(routeCentroids[code])map.panTo(routeCentroids[code],{animate:true,duration:.4});
}
function closePanel(){
  currentRoute=null;
  document.getElementById('route-panel').classList.remove('open');
  for(const c of Object.keys(routeLayers))styleRoute(c);
}
function updateRoutePanel(code){
  const lbl=ROUTE_LABELS[code]||code;
  document.getElementById('pname').textContent=lbl;
  document.getElementById('pcode').textContent=code+'  |  '+ROUTE_LABELS[code]?.split('-')[0]?.trim();
  const rw=filteredWalks.filter(w=>w.route===code);
  const byTod={AM:0,MD:0,PM:0};
  for(const w of rw)byTod[w.tod]++;
  const tot=rw.length;
  const{s,c}=routeStatus(code,filteredWalks);
  const sLbl=s==='green'?'At Target':s==='yellow'?'At Minimum':'Below Minimum';
  document.getElementById('pstatus').innerHTML=
    `<span style="font-size:9px;padding:1px 6px;border-radius:9px;background:${c}22;border:1px solid ${c};color:${c}">${sLbl}</span>`;
  document.getElementById('ps-tot').textContent=tot;
  document.getElementById('ps-am').textContent=byTod.AM;
  document.getElementById('ps-md').textContent=byTod.MD;
  document.getElementById('ps-pm').textContent=byTod.PM;
  // Chart
  dc('tod-chart');
  const ctx=document.getElementById('tod-chart').getContext('2d');
  charts['tod-chart']=new Chart(ctx,{
    type:'bar',
    data:{
      labels:['AM','MD','PM'],
      datasets:[
        {label:'Completions',data:[byTod.AM,byTod.MD,byTod.PM],
         backgroundColor:['rgba(96,165,250,.7)','rgba(251,191,36,.7)','rgba(192,132,252,.7)'],
         borderColor:['#60a5fa','#fbbf24','#c084fc'],borderWidth:1,borderRadius:4},
        {label:`Target (${TARGET})`,data:[TARGET,TARGET,TARGET],
         type:'line',borderColor:'rgba(255,255,255,.25)',borderDash:[4,3],
         borderWidth:1.5,pointRadius:0,fill:false,tension:0}
      ]
    },
    options:{responsive:true,maintainAspectRatio:false,
      plugins:{legend:{display:false},tooltip:{mode:'index'}},
      scales:{
        x:{grid:{color:'rgba(255,255,255,.04)'},ticks:{color:'#8b949e',font:{size:10}}},
        y:{grid:{color:'rgba(255,255,255,.04)'},ticks:{color:'#8b949e',font:{size:10}},
           min:0,max:Math.max(TARGET+1,tot+1),stepSize:1}
      }}
  });
  // Log
  const pl=document.getElementById('plog');
  if(!rw.length){pl.innerHTML='<div class="empty">No walks match current filters</div>';return;}
  const sorted=[...rw].sort((a,b)=>b.date-a.date);
  pl.innerHTML=`<table class="lt"><thead><tr><th>Date</th><th>Collector</th><th>TOD</th><th>Pack</th></tr></thead>
  <tbody>${sorted.map(w=>`<tr>
    <td>${fmtDate(w.date)}</td>
    <td>${CNAMES[w.collector]||w.collector}</td>
    <td><span class="tb tb-${w.tod.toLowerCase()}">${w.tod}</span></td>
    <td><span class="bb">${w.bp}</span></td>
  </tr>`).join('')}</tbody></table>`;
}
// --- COLLECTOR VIEW ---
function getWinsFor(cid,win){
  let base=allWalks.filter(w=>w.collector===cid);
  if(filters.tod)base=base.filter(w=>w.tod===filters.tod);
  if(filters.backpack)base=base.filter(w=>w.bp===filters.backpack);
  return base.filter(w=>inWin(w,win));
}
const COLLECTOR_GROUPS=[
  {id:'ccny', cls:'ccny', title:'CCNY', sub:'Backpack A', members:['SOT','AYA','JEN','TAH']},
  {id:'lagcc',cls:'lagcc',title:'LaGCC',sub:'Backpack B', members:['TER','ALX','SCT','JAM']},
  {id:'staff',cls:'staff',title:'Professors',sub:'Non-scheduled',members:['NRS','PRA','NAT']},
];
function _buildGroupHTML(g){
  const tiles=g.members.map(cid=>{
    const tot=filteredWalks.filter(w=>w.collector===cid).length;
    return`<div class="cc${cid===currentCollector?' active':''}" data-cid="${cid}">
      <div class="cn">${CNAMES[cid]}</div>
      <div class="cw">${tot}</div>
      <div class="ci">${cid}</div>
    </div>`;
  }).join('');
  return`<div class="cgroup ${g.cls}">
    <div class="cgroup-head">
      <div class="cg-dot"></div>
      <span class="cg-title">${g.title}</span>
      <span class="cg-sub">${g.sub}</span>
    </div>
    <div class="cgroup-tiles">${tiles}</div>
  </div>`;
}
function renderCollectorSelector(){
  const el=document.getElementById('cselector');
  const campus=COLLECTOR_GROUPS.filter(g=>g.id!=='staff');
  const profs=COLLECTOR_GROUPS.find(g=>g.id==='staff');
  el.innerHTML=`<div class="campus-pair">${campus.map(_buildGroupHTML).join('')}</div>`
    +(profs?`<div class="prof-row">${_buildGroupHTML(profs)}</div>`:'');
  el.querySelectorAll('.cc').forEach(c=>c.addEventListener('click',()=>{
    currentCollector=c.dataset.cid;renderCV();
  }));
}
function renderCollectorDetail(cid){
  const w2=getWinsFor(cid,'2w').length, mo=getWinsFor(cid,'mo').length;
  const sea=getWinsFor(cid,'sea').length, all=allWalks.filter(w=>w.collector===cid).length;
  const bySea={};
  for(const w of allWalks.filter(w=>w.collector===cid))bySea[w.season]=(bySea[w.season]||0)+1;
  const byTod={AM:0,MD:0,PM:0};
  for(const w of allWalks.filter(w=>w.collector===cid))byTod[w.tod]++;
  const afHtml=(AFFINITY[cid]||[]).map(n=>{
    const rc=Object.keys(ROUTE_LABELS).find(r=>r.split('_')[1]===n);
    return rc?`<span class="ap">${ROUTE_LABELS[rc]}</span>`:'';
  }).filter(Boolean).join('');
  document.getElementById('dstats').innerHTML=`
    <div class="stl">${CNAMES[cid]} <span style="color:var(--text3);font-size:10px">(${cid})</span></div>
    <div class="twg">
      <div class="twc"><div class="twv">${w2}</div><div class="twl">Last 2 Weeks</div></div>
      <div class="twc"><div class="twv">${mo}</div><div class="twl">This Month</div></div>
      <div class="twc"><div class="twv">${sea}</div><div class="twl">This Season</div></div>
      <div class="twc"><div class="twv">${all}</div><div class="twl">Whole Project</div>
        <div class="twsb">${Object.entries(bySea).sort().map(([s,n])=>
          `<span><span>${s}</span><strong>${n}</strong></span>`).join('')}</div>
      </div>
    </div>
    <div class="stl">TOD Distribution (all-time)</div>
    <div style="display:flex;gap:16px;margin-top:4px">
      ${TODS.map(t=>`<div style="text-align:center">
        <div style="font-size:20px;font-weight:700;color:var(--tod-${t.toLowerCase()})">${byTod[t]}</div>
        <div style="font-size:10px;color:var(--text2)">${t}</div>
      </div>`).join('')}
    </div>
  `;
  // Charts
  const routeWalks={};
  for(const w of allWalks.filter(w=>w.collector===cid))routeWalks[w.route]=(routeWalks[w.route]||0)+1;
  const topRoutes=Object.entries(routeWalks).sort((a,b)=>b[1]-a[1]).slice(0,10);
  dc('rhm');dc('tdist');
  document.getElementById('dcharts').innerHTML=`
    <div class="stl">Routes Walked Most</div>
    <div style="height:190px;position:relative;margin-bottom:12px"><canvas id="rhm"></canvas></div>
    <div class="stl">TOD Distribution</div>
    <div style="height:110px;position:relative"><canvas id="tdist"></canvas></div>
  `;
  const bc={'MN':'#388bfd','BX':'#f85149','BK':'#3fb950','QN':'#f0883e'};
  const hmCtx=document.getElementById('rhm').getContext('2d');
  charts['rhm']=new Chart(hmCtx,{
    type:'bar',
    data:{labels:topRoutes.map(([rc])=>ROUTE_LABELS[rc]||rc),
      datasets:[{data:topRoutes.map(([,n])=>n),
        backgroundColor:topRoutes.map(([rc])=>bc[rc.split('_')[0]]||'#888'),
        borderRadius:4,borderSkipped:false}]},
    options:{indexAxis:'y',responsive:true,maintainAspectRatio:false,
      plugins:{legend:{display:false}},
      scales:{x:{grid:{color:'rgba(255,255,255,.04)'},ticks:{color:'#8b949e',font:{size:9},stepSize:1}},
              y:{grid:{display:false},ticks:{color:'#8b949e',font:{size:9}}}}}
  });
  const tdCtx=document.getElementById('tdist').getContext('2d');
  charts['tdist']=new Chart(tdCtx,{
    type:'doughnut',
    data:{labels:['AM','MD','PM'],
      datasets:[{data:[byTod.AM,byTod.MD,byTod.PM],
        backgroundColor:['rgba(96,165,250,.8)','rgba(251,191,36,.8)','rgba(192,132,252,.8)'],
        borderWidth:0,hoverOffset:4}]},
    options:{responsive:true,maintainAspectRatio:false,cutout:'60%',
      plugins:{legend:{position:'right',labels:{color:'#8b949e',font:{size:10},boxWidth:10,padding:8}}}}
  });
}
function renderComparison(){
  const data=STUDENT_COLLECTORS.map(cid=>({
    cid,name:CNAMES[cid],
    vals:['2w','mo','all'].map(w=>getWinsFor(cid,w).length)
  }));
  const tops=data[0].vals.map((_,ci)=>Math.max(...data.map(r=>r.vals[ci])));
  const bots=data[0].vals.map((_,ci)=>{
    const nz=data.map(r=>r.vals[ci]).filter(v=>v>0);
    return nz.length?Math.min(...nz):-1;
  });
  document.getElementById('ctbody').innerHTML=data.map(row=>`
    <tr class="${row.cid===currentCollector?'selrow':''}">
      <td>${row.name} <span style="font-size:9px;color:var(--text3)">${row.cid}</span></td>
      ${row.vals.map((v,ci)=>{
        const cls=v===tops[ci]&&v>0?'ctop':v===bots[ci]&&bots[ci]>=0?'cbot':'cval';
        return`<td class="num"><span class="${cls}">${v}</span></td>`;
      }).join('')}
    </tr>`).join('');
  const wi=['2w','mo','all'].indexOf(currentWin);
  const vals=data.map(r=>r.vals[wi]);
  dc('comp-chart');
  const ctx=document.getElementById('comp-chart').getContext('2d');
  charts['comp-chart']=new Chart(ctx,{
    type:'bar',
    data:{labels:data.map(r=>r.cid),datasets:[{
      data:vals,borderRadius:4,borderSkipped:false,
      backgroundColor:data.map(r=>r.cid===currentCollector?'#388bfd':'rgba(56,139,253,.35)')
    }]},
    options:{responsive:true,maintainAspectRatio:false,
      plugins:{legend:{display:false},
        tooltip:{callbacks:{label:c=>`${CNAMES[data[c.dataIndex].cid]}: ${c.raw} walks`}}},
      scales:{x:{grid:{display:false},ticks:{color:'#8b949e',font:{size:10}}},
              y:{grid:{color:'rgba(255,255,255,.04)'},ticks:{color:'#8b949e',font:{size:10}},min:0}}}
  });
}
function renderCV(){
  renderCollectorSelector();
  renderCollectorDetail(currentCollector);
  renderComparison();
}
// --- UPDATE STATUS ---
function updateStatus(src){
  const n=allWalks.length;
  const el=document.getElementById('data-status');
  el.textContent=`${n} walks  |  ${src||'embedded sample'}`;
  el.style.color=n>0?'var(--green)':'var(--text3)';
}
// --- SCHEDULE ---
let schedMap=null, schedData=null, schedLayers={};
let schedAuth={unlocked:false, scheduler:null, pin:null};

function assignId(a){return `${a.route}_${a.tod}_${a.date}`;}
let schedStep=-1, schedPlaying=false, schedPlayTimer=null;
let tlWeekIdx=0;   // index into tlWeeks array (0 = most recent)

const TOD_ORDER={AM:0,MD:1,PM:2};

// -- Helper: snap any date to the Sunday that starts its week ---
function toWeekSunday(d){
  const s=new Date(d); s.setDate(d.getDate()-d.getDay()); s.setHours(0,0,0,0); return s;
}

// -- Build sorted list of distinct Sun-Sat weeks across completed+scheduled walks --
function buildTlWeeks(){
  const byWeek={};
  // Completed walks from log - key by the Sunday of the walk's week
  for(const w of allWalks){
    const d=new Date(w.date.getFullYear(),w.date.getMonth(),w.date.getDate());
    const sun=toWeekSunday(d);
    const key=sun.toISOString().slice(0,10);
    if(!byWeek[key])byWeek[key]={weekStart:key,walks:[],source:'log'};
    byWeek[key].walks.push({
      date:w.date.toISOString().slice(0,10),
      tod:w.tod, backpack:w.bp||'X', route:w.route,
      collector:w.collector, source:'completed'
    });
  }
  // Scheduled assignments - each assignment keyed to the Sunday of ITS OWN date
  // (schedule week_start may span multiple Sun-Sat weeks)
  if(schedData&&schedData.assignments&&schedData.assignments.length){
    for(const a of schedData.assignments){
      const ad=new Date(a.date+'T00:00:00');
      const sun=toWeekSunday(ad);
      const key=sun.toISOString().slice(0,10);
      if(!byWeek[key])byWeek[key]={weekStart:key,walks:[],source:'schedule'};
      else if(byWeek[key].source==='log')byWeek[key].source='schedule';
      if(!byWeek[key].walks.find(w=>w.date===a.date&&w.tod===a.tod&&w.route===a.route))
        byWeek[key].walks.push({...a,source:'scheduled'});
    }
    // Tag recal_day to the week it falls in
    if(schedData.recal_day){
      const rd=new Date(schedData.recal_day+'T00:00:00');
      const key=toWeekSunday(rd).toISOString().slice(0,10);
      if(byWeek[key])byWeek[key].recal_day=schedData.recal_day;
    }
  }
  // Include weather-only weeks so calendar navigation stays continuous even
  // when a week has no completed walks and no scheduled assignments.
  if(schedData&&schedData.weather){
    for(const weatherKey of Object.keys(schedData.weather)){
      const cut=weatherKey.lastIndexOf('_');
      if(cut<0)continue;
      const dateStr=weatherKey.slice(0,cut);
      const wd=new Date(dateStr+'T00:00:00');
      if(Number.isNaN(wd.getTime()))continue;
      const key=toWeekSunday(wd).toISOString().slice(0,10);
      if(!byWeek[key])byWeek[key]={weekStart:key,walks:[],source:'weather'};
    }
  }
  // Always include the current week so the calendar anchors to today even when the schedule is stale
  const _todayMon=toWeekSunday(new Date());
  const _todayKey=_todayMon.toISOString().slice(0,10);
  if(!byWeek[_todayKey])byWeek[_todayKey]={weekStart:_todayKey,walks:[],source:'log'};
  // Sort descending (index 0 = most recent / furthest future)
  return Object.values(byWeek).sort((a,b)=>b.weekStart.localeCompare(a.weekStart));
}

// Returns the index of the current (this week's Sunday) entry in a weeks array
function findCurrentWeekIdx(weeks){
  const today=new Date(); today.setHours(0,0,0,0);
  const sun=toWeekSunday(today);
  const key=sun.toISOString().slice(0,10);
  const exact=weeks.findIndex(w=>w.weekStart===key);
  if(exact>=0)return exact;
  // Fallback: most-recent week that isn't strictly in the future
  return Math.max(0,weeks.findIndex(w=>w.weekStart<=today.toISOString().slice(0,10)));
}

function getTlWeek(){
  const weeks=buildTlWeeks();
  if(!weeks.length)return null;
  const idx=Math.max(0,Math.min(tlWeekIdx,weeks.length-1));
  return weeks[idx];
}

function getSortedAssignments(){
  const wk=getTlWeek();
  if(!wk)return[];
  return[...wk.walks].sort((a,b)=>a.date.localeCompare(b.date)||TOD_ORDER[a.tod]-TOD_ORDER[b.tod]||a.backpack.localeCompare(b.backpack));
}

function initSchedMap(){
  if(schedMap)return;
  schedMap=L.map('sched-map',{center:[40.72,-73.96],zoom:11,zoomControl:false});
  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',{
    attribution:'&copy; OSM &copy; CARTO',maxZoom:20,subdomains:'abcd'
  }).addTo(schedMap);
  // Draw all routes dimmed by default
  for(const[code,geo]of Object.entries(ROUTES_GEO)){
    schedLayers[code]=[];
    for(const line of geo.lines){
      const pl=L.polyline(line,{color:'#333c47',weight:3,opacity:.6}).addTo(schedMap);
      schedLayers[code].push(pl);
    }
  }
  // CCNY recalibration pin - 160 Convent Ave
  const ccnyIcon=L.divIcon({
    className:'',
    html:`<div style="background:#f0a500;border:2px solid #fff;border-radius:50% 50% 50% 0;width:18px;height:18px;transform:rotate(-45deg);box-shadow:0 0 6px #f0a50099"></div>`,
    iconSize:[18,18],iconAnchor:[9,18]
  });
  L.marker([40.8196,-73.9499],{icon:ccnyIcon,zIndexOffset:1000})
   .bindPopup('<b>CCNY - 160 Convent Ave</b><br>Backpack recalibration site<br><span style="color:#f0a500">* Recal day: both backpacks return here</span>')
   .addTo(schedMap);
}

function applyScheduleColors(){
  if(!schedMap)return;
  const sorted=getSortedAssignments();
  if(schedStep<0||!sorted.length){
    // Show all assigned routes at full color
    const aR=new Set(),bR=new Set();
    if(schedData)for(const a of schedData.assignments)(a.backpack==='A'?aR:bR).add(a.route);
    for(const[code,layers]of Object.entries(schedLayers)){
      const color=aR.has(code)?'#f85149':bR.has(code)?'#388bfd':'#333c47';
      const weight=aR.has(code)||bR.has(code)?5:2;
      const opacity=aR.has(code)||bR.has(code)?1:.35;
      for(const l of layers)l.setStyle({color,weight,opacity});
    }
    return;
  }
  // Step mode: past=dim, current=bright+thick, future=very dim
  const cur=sorted[schedStep];
  const past=new Map();
  for(let i=0;i<schedStep;i++)past.set(sorted[i].route,sorted[i].backpack);
  for(const[code,layers]of Object.entries(schedLayers)){
    let color,weight,opacity;
    if(code===cur.route){
      color=cur.backpack==='A'?'#f85149':cur.backpack==='B'?'#388bfd':'#f0a500';weight=8;opacity=1;
    } else if(past.has(code)){
      color=past.get(code)==='A'?'#f85149':'#388bfd';weight=3;opacity=.4;
    } else {
      color='#1e2530';weight=2;opacity:.2;
    }
    for(const l of layers)l.setStyle({color,weight,opacity});
  }
}

function renderTimelineBar(){
  const daysEl=document.getElementById('sched-tl-days');
  if(!daysEl)return; // Schedule view removed

  const wk=getTlWeek();
  const weeks=buildTlWeeks();
  const sorted=getSortedAssignments();
  const today=new Date(); today.setHours(0,0,0,0);

  const playBtn=document.getElementById('tl-play');
  const wkLbl=document.getElementById('sched-tl-week-label');
  const detail=document.getElementById('sched-tl-detail');
  const prevWkBtn=document.getElementById('tl-wk-prev');
  const nextWkBtn=document.getElementById('tl-wk-next');

  if(playBtn)playBtn.innerHTML=schedPlaying?'&#x23F8;':'&#x25B6;';

  // Week nav state
  if(prevWkBtn)prevWkBtn.disabled=tlWeekIdx>=weeks.length-1;
  if(nextWkBtn)nextWkBtn.disabled=tlWeekIdx<=0;

  if(!wk){
    if(wkLbl)wkLbl.textContent='No data';
    if(detail)detail.textContent='Load a schedule or walk log';
    if(daysEl)daysEl.innerHTML='';
    return;
  }

  // Week label
  const ws=new Date(wk.weekStart+'T00:00:00');
  const we=new Date(ws); we.setDate(ws.getDate()+6);
  const fmtOpts={month:'short',day:'numeric'};
  const wkStr=`${ws.toLocaleDateString('en-US',fmtOpts)} - ${we.toLocaleDateString('en-US',{...fmtOpts,year:'numeric'})}`;
  const srcBadge=wk.source==='schedule'?' [scheduled]':wk.source==='weather'?' [weather]':' [completed]';
  const nowBadge=tlWeekIdx===0?' [current]':'';
  if(wkLbl)wkLbl.textContent=wkStr+srcBadge+nowBadge;

  // Detail label for selected step
  if(schedStep>=0&&sorted[schedStep]){
    const c=sorted[schedStep];
    const d=new Date(c.date+'T00:00:00');
    const ds=d.toLocaleDateString('en-US',{weekday:'short',month:'numeric',day:'numeric'});
    const src=c.source==='scheduled'?'scheduled':'completed';
    if(detail)detail.textContent=`${schedStep+1}/${sorted.length}  ${ds} | ${c.tod} | BP ${c.backpack} | ${ROUTE_LABELS[c.route]||c.route} | ${c.collector||'-'} | ${src}`;
  } else {
    const n=sorted.length, comp=sorted.filter(w=>w.source==='completed').length, sched=sorted.filter(w=>w.source==='scheduled').length;
    if(detail)detail.textContent=`${n} walk${n!==1?'s':''} this week${comp?` | ${comp} completed`:''}${sched?` | ${sched} scheduled`:''} - click a dot or press Play`;
  }

  // Build day columns - derive day name from actual date, not from offset index,
  // because week_start may not always be a Monday (schedule weeks start on Friday).
  const DAY_NAMES=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  // Flatten sorted to a lookup: date -> list of {walk, globalIdx}
  const byDate={};
  sorted.forEach((w,i)=>{
    if(!byDate[w.date])byDate[w.date]=[];
    byDate[w.date].push({walk:w,idx:i});
  });

  const cols=[];
  for(let d=0;d<7;d++){
    const dayDate=new Date(ws); dayDate.setDate(ws.getDate()+d);
    // Build date string in local time (avoid UTC-offset day-shift from toISOString)
    const dateStr=`${dayDate.getFullYear()}-${String(dayDate.getMonth()+1).padStart(2,'0')}-${String(dayDate.getDate()).padStart(2,'0')}`;
    const isToday=dayDate.getTime()===today.getTime();
    const isPast=dayDate<today;
    const isRecal=wk.recal_day===dateStr;
    const dayLbl=`${DAY_NAMES[dayDate.getDay()]} ${dayDate.getMonth()+1}/${dayDate.getDate()}`;
    const walks=byDate[dateStr]||[];

    let dotsHtml='';
    for(const{walk:w,idx:i}of walks){
      const bp=w.backpack==='A'?'bp-a':w.backpack==='B'?'bp-b':'bp-x';
      const stateCls=schedStep<0?'tl-future':i<schedStep?'tl-past':i===schedStep?'tl-current':'tl-future';
      const d2=new Date(w.date+'T00:00:00').toLocaleDateString('en-US',{weekday:'short',month:'numeric',day:'numeric'});
      const tip=`${d2} ${w.tod} BP${w.backpack}: ${ROUTE_LABELS[w.route]||w.route}${w.collector?' ('+w.collector+')':''}`;
      dotsHtml+=`<div class="tl-dot ${bp} ${stateCls}" title="${tip}" onclick="setSchedStep(${i})"></div>`;
    }
    if(!dotsHtml&&isRecal)dotsHtml='';  // recal label handles it

    const lblCls=isToday?'tl-day-lbl tl-today':'tl-day-lbl';
    const recalHtml=isRecal?`<div class="tl-day-recal">*RECAL</div>`:'';
    cols.push(`<div class="tl-day"><div class="${lblCls}">${dayLbl}</div><div class="tl-day-dots">${dotsHtml}</div>${recalHtml}</div>`);
    if(d<6)cols.push(`<div class="tl-day-sep"></div>`);
  }
  if(daysEl)daysEl.innerHTML=cols.join('');
}

function setSchedStep(n){
  const sorted=getSortedAssignments();
  schedStep=Math.max(-1,Math.min(n,sorted.length-1));
  applyScheduleColors();
  renderTimelineBar();
  // Highlight current row in panel
  document.querySelectorAll('.sched-row').forEach(r=>r.style.outline='');
  if(schedStep>=0){
    const rows=document.querySelectorAll('.sched-row');
    // Find matching row by route+tod+date
    const cur=sorted[schedStep];
    let found=null,idx=0;
    for(const r of rows){
      const routeEl=r.querySelector('.sr-route');
      const todEl=r.querySelector('.sr-tod');
      const lbl=ROUTE_LABELS[cur.route]||cur.route;
      if(routeEl&&todEl&&routeEl.textContent===lbl&&todEl.textContent===cur.tod){
        found=r;break;
      }
    }
    if(found){found.style.outline='2px solid '+(cur.backpack==='A'?'#f85149':'#388bfd');found.scrollIntoView({block:'nearest'});}
    // Pan map
    const layers=schedLayers[cur.route];
    if(schedMap&&layers&&layers[0]){
      try{schedMap.panTo(layers[0].getLatLngs()[0],{animate:true,duration:.3});}catch(e){}
    }
  }
}

function playSchedule(){
  const sorted=getSortedAssignments();
  if(!sorted.length)return;
  if(schedPlaying){
    schedPlaying=false;
    if(schedPlayTimer){clearInterval(schedPlayTimer);schedPlayTimer=null;}
    renderTimelineBar();
    return;
  }
  if(schedStep>=sorted.length-1)schedStep=-1;
  schedPlaying=true;
  renderTimelineBar();
  schedPlayTimer=setInterval(()=>{
    const s=getSortedAssignments();
    if(schedStep>=s.length-1){
      schedPlaying=false;clearInterval(schedPlayTimer);schedPlayTimer=null;
      renderTimelineBar();return;
    }
    setSchedStep(schedStep+1);
  },1400);
  setSchedStep(schedStep<0?0:schedStep+1);
}

function renderSchedulePanel(){
  const body=document.getElementById('sched-panel-body');
  const meta=document.getElementById('sched-meta');
  if(!body||!meta)return; // Schedule view removed
  if(!schedData){
    body.innerHTML='<div id="sched-no-data">No schedule loaded.<br>Run walk_scheduler.py then click Load above.</div>';
    meta.textContent='Run the scheduler to load assignments';
    return;
  }
  meta.textContent=`Week: ${schedData.week_start} -> ${schedData.week_end}   |   Generated: ${schedData.generated}`;
  const byBP={A:[],B:[]};
  for(const a of schedData.assignments)(byBP[a.backpack]||byBP['A']).push(a);
  byBP.A.sort((a,b)=>a.date.localeCompare(b.date)||a.tod.localeCompare(b.tod));
  byBP.B.sort((a,b)=>a.date.localeCompare(b.date)||a.tod.localeCompare(b.tod));
  function fmtDate(s){const d=new Date(s+'T00:00:00');return d.toLocaleDateString('en-US',{weekday:'short',month:'numeric',day:'numeric'});}
  function section(bp,rows){
    if(!rows.length)return '';
    if(!visibleBackpacks[bp])return '';
    const cls=bp==='A'?'bpa':'bpb';
    let html=`<div class="sbp-section"><h3><span class="bpbadge ${cls}">Backpack ${bp}</span> ${rows.length} walk${rows.length>1?'s':''}</h3>`;
    for(const r of rows){
      const label=ROUTE_LABELS[r.route]||r.route;
      const todCls=r.tod==='AM'?'tb-am':r.tod==='MD'?'tb-md':'tb-pm';
      html+=`<div class="sched-row" onclick="highlightSchedRoute('${r.route}','${bp}')">
        <span class="sr-date">${fmtDate(r.date)}</span>
        <span class="sr-tod tb ${todCls}">${r.tod}</span>
        <span class="sr-route">${label}</span>
        <span class="sr-col">${r.collector}</span>
      </div>`;
    }
    html+='</div>';
    return html;
  }
  body.innerHTML=section('A',byBP.A)+section('B',byBP.B);
}

function highlightSchedRoute(code,bp){
  const color=bp==='A'?'#f85149':'#388bfd';
  for(const[c,layers]of Object.entries(schedLayers)){
    const active=c===code;
    for(const l of layers)l.setStyle({
      color:active?color:schedData&&[...schedData.assignments].some(a=>a.route===c)?(schedData.assignments.find(a=>a.route===c).backpack==='A'?'#f85149':'#388bfd'):'#333c47',
      weight:active?7:c===code?5:2,
      opacity:active?1:0.4
    });
  }
  if(schedMap&&schedLayers[code]&&schedLayers[code][0]){
    schedMap.panTo(schedLayers[code][0].getLatLngs()[0]);
  }
}

function loadScheduleJSON(text){
  try{
    schedData=JSON.parse(text);
    schedStep=-1;const _initW=buildTlWeeks();tlWeekIdx=findCurrentWeekIdx(_initW);calWeekIdx=findCurrentWeekIdx(_initW);
    if(schedPlaying){schedPlaying=false;if(schedPlayTimer){clearInterval(schedPlayTimer);schedPlayTimer=null;}}
    applyScheduleColors();
    renderSchedulePanel();
    renderTimelineBar();
    if(document.getElementById('calendar-view').classList.contains('active'))renderCalendar();
    toast(`Schedule loaded: ${schedData.assignments.length} assignments`,'success');
  }catch(e){
    toast('Invalid schedule JSON: '+e.message,'');
  }
}

// --- CALENDAR ---
let calWeekIdx=0;

function getCloudPct(dateStr,tod){
  const key=`${dateStr}_${tod}`;
  const m=(RUNTIME_WEATHER&&RUNTIME_WEATHER._meta&&RUNTIME_WEATHER._meta[key]);
  return(m&&m.cloud_pct!=null)?m.cloud_pct:null;
}

function isBadWeatherSlot(dateStr,tod){
  const weatherKey=`${dateStr}_${tod}`;
  if(
    schedData &&
    schedData.weather &&
    Object.prototype.hasOwnProperty.call(schedData.weather,weatherKey)
  ){
    return schedData.weather[weatherKey]===false;
  }
  return !!(
    RUNTIME_WEATHER &&
    RUNTIME_WEATHER.weather &&
    RUNTIME_WEATHER.weather[weatherKey]===false
  );
}

function getBoroForWeek(backpack, weekIdx, weeks){
  if(!weeks||weekIdx<0||weekIdx>=weeks.length)return null;
  const wk=weeks[weekIdx];
  const boros={};
  for(const w of wk.walks){
    if(w.backpack===backpack&&w.boro){
      boros[w.boro]=(boros[w.boro]||0)+1;
    }
  }
  if(Object.keys(boros).length===0)return null;
  return Object.entries(boros).sort((a,b)=>b[1]-a[1])[0][0];
}

function getBoroForecast(backpack, weekIdx, weeks){
  const forecast=[];
  for(let i=0;i<4&&weekIdx+i<weeks.length;i++){
    const boro=getBoroForWeek(backpack,weekIdx+i,weeks);
    if(boro){
      forecast.push({week:i+1,boro});
    }
  }
  return forecast;
}

function BORO_NAMES(){
  return{MN:'Manhattan',BK:'Brooklyn',QN:'Queens',BX:'Bronx',SI:'Staten Island',OTH:'Other'};
}

function updateBoroIndicators(weekIdx, weeks){
  const names=BORO_NAMES();
  for(const bp of['A','B','X']){
    const boro=getBoroForWeek(bp,weekIdx,weeks);
    const badge=document.querySelector(`[data-bp="${bp}"].bp-boro-badge`);
    const tooltip=document.querySelector(`[data-bp="${bp}"].bp-boro-tooltip`);
    if(badge){
      badge.textContent=boro?boro:'-';
      badge.style.color=boro?'#e6edf3':'#6e7681';
    }
    if(tooltip){
      const forecast=getBoroForecast(bp,weekIdx,weeks);
      let html='';
      if(forecast.length>0){
        html=forecast.map(f=>`<div class="week-item"><span class="week-num">W${f.week}:</span><span>${names[f.boro]||f.boro}</span></div>`).join('');
      }else{
        html='<div style="color:#6e7681">No assignments</div>';
      }
      tooltip.innerHTML=html;
    }
  }
}

const TWEAK_DEFAULTS = /*EDITMODE-START*/{"calBarMax":22,"calBarGreenEnd":10,"calBarYellowEnd":18}/*EDITMODE-END*/;
let CAL_BAR_MAX = TWEAK_DEFAULTS.calBarMax;
let CAL_BAR_GREEN_END = TWEAK_DEFAULTS.calBarGreenEnd;
let CAL_BAR_YELLOW_END = TWEAK_DEFAULTS.calBarYellowEnd;
let recalEntries={A:[],B:[]};

async function loadRecalLog(){
  try{
    const r=await fetch('Recal_Log.txt?_ts='+Date.now(),{cache:'no-store'});
    if(!r.ok)return;
    const text=await r.text();
    const next={A:[],B:[]};
    const re=/^RECAL_([AB])_(\\d{4})(\\d{2})(\\d{2})\\s*$/;
    for(const line of text.split(/\\r?\\n/)){
      const m=re.exec(line.trim());
      if(!m)continue;
      const d=new Date(+m[2],+m[3]-1,+m[4]);
      if(!isNaN(d))next[m[1]].push(d);
    }
    next.A.sort((a,b)=>a-b);
    next.B.sort((a,b)=>a-b);
    recalEntries=next;
  }catch(_e){}
}

function getLastCalibrationDate(bp){
  const arr=(recalEntries&&recalEntries[bp])||[];
  return arr.length?arr[arr.length-1]:null;
}

function _isSameDay(a,b){
  return a.getFullYear()===b.getFullYear()&&a.getMonth()===b.getMonth()&&a.getDate()===b.getDate();
}

function updateCalibrationBar(bp){
  const bar=document.querySelector(`.cal-bar[data-bp="${bp}"]`);
  if(!bar)return;
  const numEl=bar.querySelector('.cb-count-num');
  const fill=bar.querySelector('.cb-fill');
  if(!numEl||!fill)return;
  const zMarks=bar.querySelectorAll('.cb-zonemark');
  if(zMarks.length>=2){
    zMarks[0].style.left=(CAL_BAR_GREEN_END/CAL_BAR_MAX*100).toFixed(2)+'%';
    zMarks[1].style.left=(CAL_BAR_YELLOW_END/CAL_BAR_MAX*100).toFixed(2)+'%';
  }
  const gLbl=bar.querySelector('.cb-scale-good');
  const sLbl=bar.querySelector('.cb-scale-soon');
  const oLbl=bar.querySelector('.cb-scale-over');
  if(gLbl)gLbl.style.left=((CAL_BAR_GREEN_END/2)/CAL_BAR_MAX*100).toFixed(2)+'%';
  if(sLbl)sLbl.style.left=(((CAL_BAR_GREEN_END+CAL_BAR_YELLOW_END)/2)/CAL_BAR_MAX*100).toFixed(2)+'%';
  if(oLbl)oLbl.style.left=(((CAL_BAR_YELLOW_END+CAL_BAR_MAX)/2)/CAL_BAR_MAX*100).toFixed(2)+'%';
  const track=bar.querySelector('.cb-track');
  if(track){
    const g1=(CAL_BAR_GREEN_END/CAL_BAR_MAX*100).toFixed(2);
    const g2=((CAL_BAR_GREEN_END+0.8)/CAL_BAR_MAX*100).toFixed(2);
    const y1=(CAL_BAR_YELLOW_END/CAL_BAR_MAX*100).toFixed(2);
    const y2=((CAL_BAR_YELLOW_END+0.8)/CAL_BAR_MAX*100).toFixed(2);
    track.style.background=
      `linear-gradient(90deg,rgba(63,185,80,.14) 0%,rgba(63,185,80,.14) ${g1}%,rgba(210,153,34,.14) ${g2}%,rgba(210,153,34,.14) ${y1}%,rgba(248,81,73,.16) ${y2}%,rgba(248,81,73,.16) 100%)`;
  }
  const last=getLastCalibrationDate(bp);
  let days=0;
  if(last){
    const today=new Date();today.setHours(0,0,0,0);
    const l=new Date(last);l.setHours(0,0,0,0);
    days=Math.max(0,Math.round((today-l)/86400000));
  }
  const shown=Math.min(days,CAL_BAR_MAX);
  numEl.textContent=last?String(days):'?';
  const lastEl=bar.querySelector('.cb-last-date');
  if(lastEl){
    lastEl.textContent=last
      ? last.toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'})
      : 'No entries yet';
  }
  let zone='green';
  if(days>CAL_BAR_YELLOW_END)zone='red';
  else if(days>CAL_BAR_GREEN_END)zone='yellow';
  bar.dataset.zone=zone;
  const pct=(shown/CAL_BAR_MAX)*100;
  fill.style.width=pct+'%';
  fill.style.opacity=days===0?'.25':'1';
  if(!last)fill.style.width='0%';
}

function updateCalibrationBars(){updateCalibrationBar('A');updateCalibrationBar('B');}

function renderCalendar(){
  const grid=document.getElementById('cal-grid');
  const title=document.getElementById('cal-title');
  if(!grid)return;

  updateCalibrationBars();

  const weeks=buildTlWeeks();
  if(!weeks.length){
    grid.innerHTML='<div class="cal-empty-week">No walk data or schedule loaded yet - run the scheduler or load a log file.</div>';
    if(title)title.textContent='-';
    return;
  }

  const idx=Math.max(0,Math.min(calWeekIdx,weeks.length-1));
  const wk=weeks[idx];
  const ws=new Date(wk.weekStart+'T00:00:00');
  const we=new Date(ws); we.setDate(ws.getDate()+6);
  const tod=new Date(); tod.setHours(0,0,0,0);

  updateBoroIndicators(idx,weeks);

  if(title)title.textContent=
    ws.toLocaleDateString('en-US',{month:'short',day:'numeric'})+' - '+
    we.toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'});

  const prevBtn=document.getElementById('cal-prev');
  const nextBtn=document.getElementById('cal-next');
  if(prevBtn)prevBtn.disabled=idx>=weeks.length-1;
  if(nextBtn)nextBtn.disabled=idx<=0;

  // Build lookup: dateStr -> {AM:[], MD:[], PM:[]}
  const byDayTod={};
  for(const w of wk.walks){
    if(!visibleBackpacks[w.backpack])continue;
    if(!byDayTod[w.date])byDayTod[w.date]={AM:[],MD:[],PM:[]};
    if(byDayTod[w.date][w.tod])byDayTod[w.date][w.tod].push(w);
  }

  const DAY_NAMES=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  const CAL_TODS=['AM','MD','PM'];
  let html='';

  // Sticky corner
  html+='<div class="cal-corner"></div>';

  // Day header row
  for(let d=0;d<7;d++){
    const dd=new Date(ws); dd.setDate(ws.getDate()+d);
    const isToday=dd.getTime()===tod.getTime();
    html+=`<div class="cal-day-head${isToday?' cal-today-head':''}">
      <div class="cal-dname">${DAY_NAMES[dd.getDay()]}</div>
      <div class="cal-dnum">${dd.getDate()}</div>
    </div>`;
  }

  // TOD rows
  for(const ctod of CAL_TODS){
    html+=`<div class="cal-tod-lbl ${ctod.toLowerCase()}">${ctod}</div>`;
    for(let d=0;d<7;d++){
      const dd=new Date(ws); dd.setDate(ws.getDate()+d);
      const dateStr=`${dd.getFullYear()}-${String(dd.getMonth()+1).padStart(2,'0')}-${String(dd.getDate()).padStart(2,'0')}`;
      const isToday=dd.getTime()===tod.getTime();
      const isPast=dd<tod;
      const isWeekend=dd.getDay()===0||dd.getDay()===6;
      const isRecal=wk.recal_day===dateStr;
      const walks=(byDayTod[dateStr]||{})[ctod]||[];
      const isBadWeather=isBadWeatherSlot(dateStr,ctod);

      let cellContent='';
      // Cloud cover % badge (top-right corner, all slots)
      const cloudPct=getCloudPct(dateStr,ctod);
      if(cloudPct!=null){
        const isGoodWx=cloudPct<=50;
        cellContent+=`<div class="cloud-pct-badge ${isGoodWx?'good':'bad'}">&#x2601; ${cloudPct}%</div>`;
      }
      // Weather indicator overlay for bad weather
      if(isBadWeather){
        cellContent+=`<div class="weather-bad"><div class="bad-label">BAD</div><div class="no-sign">NO GO</div><div class="weather-label">WEATHER</div></div>`;
      }
      // Recalibration tags in AM cell
      if(ctod==='AM'){
        if(isRecal){
          cellContent+=`<div class="cal-recal-tag proposed">&#x2605; Recal proposed</div>`;
        }
        for(const bp of['A','B']){
          if((recalEntries[bp]||[]).some(d=>_isSameDay(d,dd))){
            const campus=bp==='A'?'CCNY':'LaGCC';
            cellContent+=`<div class="cal-recal-tag bp-${bp.toLowerCase()}">&#x2713; Recal ${bp} \xb7 ${campus}</div>`;
          }
        }
      }
      // Walk event cards
      for(const w of walks){
        const bpCls=w.backpack==='A'?'bpa':w.backpack==='B'?'bpb':'bpx';
        const compCls=w.source==='completed'?' completed':'';
        const bpLabel=w.backpack==='A'?'Backpack A':w.backpack==='B'?'Backpack B':'Legacy X';
        const routeLbl=ROUTE_LABELS[w.route]||w.route;
        const colLbl=CNAMES[w.collector]||w.collector||'-';
        cellContent+=`<div class="cal-event ${bpCls}${compCls}">
          <div class="ce-bp">${bpLabel}</div>
          <div class="ce-route">${routeLbl}</div>
          <div class="ce-col">${colLbl}</div>
        </div>`;
      }

      const cls=['cal-cell',
        isToday?'cal-today-col':'',
        isPast?'cal-past-col':'',
        isWeekend?'cal-weekend':'',
        isBadWeather?'bad-weather':''
      ].filter(Boolean).join(' ');
      html+=`<div class="${cls}">${cellContent}</div>`;
    }
  }

  grid.innerHTML=html;
}

// --- AVAILABILITY HEATMAP ---
const _AVAIL_DAYS=__AVAIL_DAYS_JSON__;
const _AVAIL_TODS=['AM','MD','PM'];
const _CELLS_A=__AVAIL_CELLS_A__;
const _CELLS_B=__AVAIL_CELLS_B__;
const _MAX_A=__MAX_A__, _MAX_B=__MAX_B__;
let _availBuilt=false;
function avCellColor(val,max){
  const t=val/max;
  if(t===0)return{bg:'rgba(248,81,73,.18)',text:'#f85149'};
  if(t<0.35)return{bg:'rgba(248,81,73,.10)',text:'#d29922'};
  if(t<0.6)return{bg:'rgba(210,153,34,.14)',text:'#d29922'};
  if(t<0.85)return{bg:'rgba(63,185,80,.12)',text:'#3fb950'};
  return{bg:'rgba(63,185,80,.22)',text:'#3fb950'};
}
function buildAvailTable(id,data,max){
  const tbl=document.getElementById(id);
  if(!tbl)return;
  let hdr='<tr><th class="avail-row-head"></th>';
  _AVAIL_DAYS.forEach(d=>hdr+=`<th>${d}</th>`);
  hdr+='</tr>';
  tbl.innerHTML=hdr;
  const tip=document.getElementById('avail-tip');
  const tipHead=document.getElementById('atip-head');
  const tipNames=document.getElementById('atip-names');
  _AVAIL_TODS.forEach(tod=>{
    let row=`<tr><td class="avail-tod-label">${tod}</td>`;
    _AVAIL_DAYS.forEach(day=>{
      const key=day+'_'+tod;
      const cell=data[key]||{count:0,names:[]};
      const {bg,text}=avCellColor(cell.count,max);
      const pct=Math.round((cell.count/max)*100);
      row+=`<td style="background:${bg};color:${text}"><div class="abar" style="height:${pct}%;background:${text};opacity:.18"></div><div class="anum">${cell.count}</div></td>`;
    });
    row+='</tr>';
    tbl.innerHTML+=row;
  });
  // bind tooltip to data cells only
  tbl.querySelectorAll('td:not(.avail-tod-label)').forEach((td,idx)=>{
    const tod=_AVAIL_TODS[Math.floor(idx/_AVAIL_DAYS.length)];
    const day=_AVAIL_DAYS[idx%_AVAIL_DAYS.length];
    const cell=data[day+'_'+tod]||{count:0,names:[]};
    td.addEventListener('mouseenter',e=>{
      tipHead.textContent=day+' '+tod+' - '+cell.count+'/'+max+' available';
      tipNames.innerHTML=cell.names.length?cell.names.map(n=>`<div class="atip-name">${n}</div>`).join(''):'<div class="atip-none">No one available</div>';
      tip.style.display='block';
      const tw=tip.offsetWidth||150,th2=tip.offsetHeight||80,vw=window.innerWidth,vh=window.innerHeight;
      tip.style.left=(e.clientX+14+tw>vw?e.clientX-tw-8:e.clientX+14)+'px';
      tip.style.top=(e.clientY+14+th2>vh?e.clientY-th2-8:e.clientY+14)+'px';
    });
    td.addEventListener('mousemove',e=>{
      const tw=tip.offsetWidth||150,th2=tip.offsetHeight||80,vw=window.innerWidth,vh=window.innerHeight;
      tip.style.left=(e.clientX+14+tw>vw?e.clientX-tw-8:e.clientX+14)+'px';
      tip.style.top=(e.clientY+14+th2>vh?e.clientY-th2-8:e.clientY+14)+'px';
    });
    td.addEventListener('mouseleave',()=>tip.style.display='none');
  });
}
function renderAvailHeatmap(){
  if(_availBuilt)return;
  buildAvailTable('avail-tbl-a',_CELLS_A,_MAX_A);
  buildAvailTable('avail-tbl-b',_CELLS_B,_MAX_B);
  _availBuilt=true;
}

// --- AUTH MODAL (top-level so any gated feature can call it) ---
function openAuthModal(notice=''){
  const mb=document.getElementById('auth-modal-bg');
  const pi=document.getElementById('auth-pin');
  const me=document.getElementById('auth-modal-err');
  const mn=document.getElementById('auth-modal-notice');
  if(mb)mb.classList.add('open');
  if(pi){pi.value='';pi.focus();}
  if(me)me.style.display='none';
  if(mn){mn.textContent=notice||'';mn.style.display=notice?'block':'none';}
}
function closeAuthModal(){
  const mb=document.getElementById('auth-modal-bg');
  if(mb)mb.classList.remove('open');
}
function requireAuth(feature='this feature'){
  if(schedAuth.unlocked)return true;
  openAuthModal('Login required to use '+feature+'.');
  return false;
}

// --- EVENTS ---
function bindEvents(){
  document.querySelectorAll('.tab-btn[data-view]').forEach(b=>b.addEventListener('click',async()=>{
    document.querySelectorAll('.tab-btn').forEach(x=>x.classList.remove('active'));
    document.querySelectorAll('.view').forEach(x=>x.classList.remove('active'));
    b.classList.add('active');
    document.getElementById(b.dataset.view).classList.add('active');
    if(b.dataset.view==='map-view')setTimeout(()=>map&&map.invalidateSize(),50);
    else if(b.dataset.view==='calendar-view'){
      await refreshRuntimeData();
      if(RUNTIME_SCHEDULE&&RUNTIME_SCHEDULE.assignments
         &&_schedStamp(RUNTIME_SCHEDULE)!==_schedStamp(schedData)){
        loadScheduleJSON(JSON.stringify(RUNTIME_SCHEDULE));
      }
      calWeekIdx=tlWeekIdx;
      renderCalendar();
    } else if(b.dataset.view==='availability-view'){
      renderAvailHeatmap();
    } else renderCV();
  }));
  document.getElementById('fseason').addEventListener('change',e=>{filters.season=e.target.value;applyFilters();});
  document.getElementById('ftod').addEventListener('change',e=>{filters.tod=e.target.value;applyFilters();});
  document.getElementById('fbp').addEventListener('change',e=>{filters.backpack=e.target.value;applyFilters();});
  document.getElementById('ffrom').addEventListener('change',e=>{filters.from=e.target.value?new Date(e.target.value+'T00:00:00'):null;applyFilters();});
  document.getElementById('fto').addEventListener('change',e=>{filters.to=e.target.value?new Date(e.target.value+'T23:59:59'):null;applyFilters();});
  document.querySelectorAll('.bp-toggle').forEach(btn=>{btn.addEventListener('click',e=>{const bp=e.currentTarget.dataset.backpack;visibleBackpacks[bp]=!visibleBackpacks[bp];e.currentTarget.classList.toggle('active');applyFilters();});});
  document.getElementById('btn-refresh').addEventListener('click',()=>{
    allWalks=parseLog(logText);applyFilters();updateStatus();
    toast(`Refreshed: ${allWalks.length} walks`,'success');
  });
  document.getElementById('ffile').addEventListener('change',e=>{
    const f=e.target.files[0];if(!f)return;
    new FileReader().onload=ev=>{
      logText=ev.target.result;allWalks=parseLog(logText);
      applyFilters();updateStatus(f.name);
      toast(`Loaded ${f.name}: ${allWalks.length} walks`,'success');
      e.target.value='';
    };(()=>{const r=new FileReader();r.onload=ev=>{
      logText=ev.target.result;allWalks=parseLog(logText);
      applyFilters();updateStatus(f.name);
      toast(`Loaded ${f.name}: ${allWalks.length} walks`,'success');
      e.target.value='';};r.readAsText(f);})();
  });
  document.getElementById('cal-prev').addEventListener('click',()=>{
    const weeks=buildTlWeeks();
    if(calWeekIdx<weeks.length-1){calWeekIdx++;renderCalendar();}
  });
  document.getElementById('cal-next').addEventListener('click',()=>{
    if(calWeekIdx>0){calWeekIdx--;renderCalendar();}
  });
  document.getElementById('close-panel').addEventListener('click',closePanel);
  document.querySelectorAll('.wtab').forEach(b=>b.addEventListener('click',()=>{
    document.querySelectorAll('.wtab').forEach(x=>x.classList.remove('active'));
    b.classList.add('active');currentWin=b.dataset.win;renderComparison();
  }));

  // -- Scheduler auth ---
  const unlockBtn=document.getElementById('sched-unlock-btn');
  const modalBg=document.getElementById('auth-modal-bg');
  const modalErr=document.getElementById('auth-modal-err');
  const pinInput=document.getElementById('auth-pin');

  if(unlockBtn) unlockBtn.addEventListener('click',()=>{
    if(schedAuth.unlocked){
      // Log out
      schedAuth={unlocked:false,scheduler:null,pin:null};
      document.body.classList.remove('scheduler-mode');
      unlockBtn.classList.remove('authed');
      unlockBtn.innerHTML='&#x1F511; Admin Login';
      renderSchedulePanel();
      if(document.getElementById('calendar-view').classList.contains('active'))renderCalendar();
    } else { openAuthModal(); }
  });

  document.getElementById('auth-modal-cancel').addEventListener('click',closeAuthModal);
  modalBg.addEventListener('click',e=>{if(e.target===modalBg)closeAuthModal();});
  pinInput.addEventListener('keydown',e=>{if(e.key==='Enter')document.getElementById('auth-modal-submit').click();});

  document.getElementById('auth-modal-submit').addEventListener('click',async()=>{
    const pin=pinInput.value.trim();
    const who=document.getElementById('auth-who').value;
    modalErr.style.display='none';
    // Verify PIN against server
    try{
      const resp=await fetch('/api/confirm',{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        // Send a no-op "probe" - use a dummy id that won't match any real assignment
        body:JSON.stringify({id:'__probe__',status:'pending',scheduler:who,pin})
      });
      if(resp.status===403){
        modalErr.textContent='Incorrect PIN. Try again.';
        modalErr.style.display='block';
        pinInput.value='';pinInput.focus();
        return;
      }
    }catch(e){
      // Server unreachable - allow PIN-less mode for static viewing
    }
    schedAuth={unlocked:true,scheduler:who,pin};
    document.body.classList.add('scheduler-mode');
    unlockBtn.classList.add('authed');
    unlockBtn.innerHTML=`&#x1F511; ${who} <span style="font-size:8px;opacity:.7">(click to lock)</span>`;
    closeAuthModal();
    renderSchedulePanel();
    if(document.getElementById('calendar-view').classList.contains('active'))renderCalendar();
  });

  // -- Force Rebuild Button ---
  const forceRebuildBtn=document.getElementById('force-rebuild-btn');
  if(forceRebuildBtn){
    forceRebuildBtn.addEventListener('click',async()=>{
      if(forceRebuildBtn.classList.contains('rebuilding'))return;
      if(!requireAuth('Rebuild'))return;
      const pin=schedAuth.pin||'';
      forceRebuildBtn.classList.add('rebuilding');
      forceRebuildBtn.textContent='... Building...';
      try{
        const resp=await fetch('/api/force-rebuild',{
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({pin})
        });
        if(resp.status===403){
          toast('Incorrect PIN.','error');
          forceRebuildBtn.classList.remove('rebuilding');
          forceRebuildBtn.innerHTML='&#x27F3; Rebuild';
          return;
        }
        if(!resp.ok)throw new Error('Server error: '+resp.status);
        toast('Rebuild started - page will refresh when complete.','success');
        let attempts=0;
        const maxAttempts=30;
        const origMtime=schedData&&schedData.generated?schedData.generated:null;
        const poll=setInterval(async()=>{
          attempts++;
          try{
            const sr=await fetch('/api/status');
            if(sr.ok){
              const st=await sr.json();
              const newMtime=st.schedule_output&&st.schedule_output.mtime?st.schedule_output.mtime:null;
              if(newMtime&&newMtime!==origMtime){
                clearInterval(poll);
                toast('Rebuild complete! Refreshing...','success');
                setTimeout(()=>window.location.reload(),500);
                return;
              }
            }
          }catch(e){}
          if(attempts>=maxAttempts){
            clearInterval(poll);
            toast('Build in progress - please refresh manually.','info');
            forceRebuildBtn.classList.remove('rebuilding');
            forceRebuildBtn.innerHTML='&#x27F3; Rebuild';
          }
        },2000);
      }catch(err){
        console.error('Force rebuild error:',err);
        toast('Error triggering rebuild: '+err.message,'error');
        forceRebuildBtn.classList.remove('rebuilding');
        forceRebuildBtn.innerHTML='&#x27F3; Rebuild';
      }
    });
  }
  // --- Calibration entry modal ---
  function openRecalModal(prefillBp){
    if(!requireAuth('Log Calibration'))return;
    document.getElementById('recal-date').value=new Date().toISOString().slice(0,10);
    document.getElementById('recal-modal-msg').style.display='none';
    // Reset radio selection, then optionally prefill
    document.querySelectorAll('input[name="recal-bp"]').forEach(r=>r.checked=false);
    if(prefillBp){
      const r=document.querySelector(`input[name="recal-bp"][value="${prefillBp}"]`);
      if(r)r.checked=true;
    }
    document.getElementById('recal-modal-bg').classList.add('open');
  }
  document.querySelectorAll('.cb-log-btn').forEach(btn=>{
    btn.addEventListener('click',()=>openRecalModal(btn.dataset.bp));
  });
  document.getElementById('recal-modal-cancel').addEventListener('click',()=>{
    document.getElementById('recal-modal-bg').classList.remove('open');
  });
  document.getElementById('recal-modal-bg').addEventListener('click',e=>{
    if(e.target===document.getElementById('recal-modal-bg'))document.getElementById('recal-modal-bg').classList.remove('open');
  });
  document.getElementById('recal-modal-submit').addEventListener('click',async()=>{
    const dateVal=document.getElementById('recal-date').value;
    const bpSel=document.querySelector('input[name="recal-bp"]:checked');
    const msg=document.getElementById('recal-modal-msg');
    if(!dateVal){msg.className='err';msg.textContent='Please select a date.';msg.style.display='block';return;}
    if(!bpSel){msg.className='err';msg.textContent='Please select which backpack was calibrated.';msg.style.display='block';return;}
    const bp=bpSel.value;
    const pin=schedAuth.pin||'';
    const btn=document.getElementById('recal-modal-submit');
    btn.disabled=true;btn.textContent='Saving\u2026';
    try{
      const r=await fetch('/api/record-calibration',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({date:dateVal,backpack:bp,pin})});
      if(r.ok){
        document.getElementById('recal-modal-bg').classList.remove('open');
        await loadRecalLog();
        updateCalibrationBars();
        renderCalendar();
        const campus=bp==='A'?'CCNY':'LaGCC';
        toast(`Calibration logged for Backpack ${bp} \xb7 ${campus} on ${dateVal}`,'success');
      }else{
        const j=await r.json().catch(()=>({}));
        msg.className='err';msg.textContent=j.error||'Failed to save.';msg.style.display='block';
      }
    }catch(e){msg.className='err';msg.textContent='Network error.';msg.style.display='block';}
    finally{btn.disabled=false;btn.textContent='Record';}
  });
}
// --- INIT ---
async function init(){
  await refreshRuntimeData();
  let src=null;
  try{
    const r=await fetch('Walks_Log.txt?_ts='+Date.now(),{cache:'no-store'});
    if(r.ok){logText=await r.text();src='Walks_Log.txt';}
  }catch(e){}
  await loadRecalLog();
  let schedLoaded=false;
  if(RUNTIME_SCHEDULE&&RUNTIME_SCHEDULE.assignments){
    schedData=RUNTIME_SCHEDULE;schedLoaded=true;loadScheduleJSON(JSON.stringify(RUNTIME_SCHEDULE));
  }
  try{
    allWalks=parseLog(logText);
    applyFilters();
    updateStatus(src);
    initMap();
    updateMapColors();
    updateMapStats();
    bindEvents();
    const msgs=[src?'Log loaded from disk':'Using embedded sample data'];
    if(schedLoaded)msgs.push('Schedule auto-loaded');
    toast(msgs.join(' - '),'success');
  }catch(err){
    console.error('Dashboard init error:',err);
    document.querySelector('#loading p').textContent='Error: '+err.message;
    document.querySelector('#loading p').style.color='#f85149';
    return;
  }
  document.getElementById('loading').style.display='none';
}
function _relTime(isoTs){
  if(!isoTs)return'-';
  const sec=Math.round((Date.now()-new Date(isoTs).getTime())/1000);
  if(sec<5)return'just now';
  if(sec<60)return`${sec}s ago`;
  if(sec<3600)return`${Math.round(sec/60)}m ago`;
  return`${Math.round(sec/3600)}h ago`;
}

// --- DRIVE SYNC UI ---
async function refreshDriveStatus(){
  try{
    const r=await fetch('/api/status');
    if(!r.ok)return;
    const d=await r.json();
    const badge=document.getElementById('drive-badge');
    if(d.drive_last_poll){
      badge.textContent='Drive: '+_relTime(d.drive_last_poll);
      badge.className='live-badge ok';
      badge.title='Last Google Drive poll: '+new Date(d.drive_last_poll).toLocaleString()+
        (d.drive_new_files_today?'\\n'+d.drive_new_files_today+' new file(s) today':'');
    }else{
      badge.textContent='Drive: not configured';
      badge.className='live-badge warn';
    }
  }catch(e){}
}

document.addEventListener('DOMContentLoaded',()=>{
  const syncBtn=document.getElementById('drive-sync-btn');
  if(syncBtn){
    syncBtn.addEventListener('click',async()=>{
      syncBtn.disabled=true;syncBtn.textContent='...';
      try{
        const r=await fetch('/api/drive/poll',{method:'POST'});
        const d=await r.json();
        if(d.status==='ok'){
          toast(d.new_files?`Drive: ${d.new_files} new file(s) found`:'Drive: no new files','success');
          if(d.new_files>0){
            const lr=await fetch('Walks_Log.txt?t='+Date.now());
            if(lr.ok){logText=await lr.text();allWalks=parseLog(logText);applyFilters();updateStatus('Walks_Log.txt');}
          }
        }else{toast('Drive sync error: '+(d.message||'unknown'),'');}
      }catch(e){toast('Drive sync failed: '+e.message,'');}
      finally{syncBtn.disabled=false;syncBtn.textContent='Sync Sync';}
      refreshDriveStatus();
    });
  }
  // Poll Drive status every 30s
  setInterval(refreshDriveStatus,30000);
  refreshDriveStatus();
});

document.addEventListener('DOMContentLoaded',init);

// Filters dropdown — scoped to Campaign Monitor tab group
document.addEventListener('DOMContentLoaded', function() {
  const filtersBtn = document.getElementById('filters-btn');
  const filtersDropdown = document.getElementById('filters');
  const campaignGroup = document.getElementById('campaign-tab-group');

  const campaignViews = new Set(['map-view', 'collector-view']);

  function openFilters() {
    if (!filtersDropdown || !filtersBtn) return;
    const rect = filtersBtn.getBoundingClientRect();
    filtersDropdown.style.top = (rect.bottom + 4) + 'px';
    // Keep dropdown within viewport
    const dropW = 240;
    const left = Math.min(rect.left, window.innerWidth - dropW - 8);
    filtersDropdown.style.left = Math.max(8, left) + 'px';
    filtersDropdown.classList.add('open');
    filtersBtn.classList.add('active');
  }
  function closeFilters() {
    if (!filtersDropdown) return;
    filtersDropdown.classList.remove('open');
    if (filtersBtn) filtersBtn.classList.remove('active');
  }
  function isFiltersOpen() {
    return filtersDropdown && filtersDropdown.classList.contains('open');
  }

  if (filtersBtn) {
    filtersBtn.addEventListener('click', function(e) {
      e.stopPropagation();
      isFiltersOpen() ? closeFilters() : openFilters();
    });
  }

  // Close dropdown when clicking outside the campaign tab group or the dropdown itself
  document.addEventListener('click', function(e) {
    if (!isFiltersOpen()) return;
    if (campaignGroup && campaignGroup.contains(e.target)) return;
    if (filtersDropdown && filtersDropdown.contains(e.target)) return;
    closeFilters();
  });

  // Show/hide filters button and close dropdown when switching tab groups
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', function() {
      const view = btn.dataset.view;
      if (view && !campaignViews.has(view)) {
        // Switched to a non-Campaign Monitor tab — hide the button
        closeFilters();
        if (filtersBtn) filtersBtn.style.display = 'none';
      } else if (view && campaignViews.has(view)) {
        // Switched between Campaign Monitor tabs — show button, close dropdown
        closeFilters();
        if (filtersBtn) filtersBtn.style.display = '';
      }
    });
  });
});

// --- UPLOAD DATA MODAL ---
var _umRoutes = {
  MN:[['HT','Harlem'],['WH','Washington Heights'],['UE','Upper East Side'],['MT','Midtown'],['LE','Union Sq / LES']],
  BX:[['HP','Hunts Point'],['NW','Norwood']],
  BK:[['DT','Downtown BK'],['WB','Williamsburg'],['BS','Bed Stuy'],['CH','Crown Heights'],['SP','Sunset Park'],['CI','Coney Island']],
  QN:[['FU','Flushing'],['LI','Astoria / LIC'],['JH','Jackson Heights'],['JA','Jamaica'],['FH','Forest Hills'],['LA','LaGuardia CC'],['EE','East Elmhurst']]
};
function openUploadModal(){
  var t=new Date(),m=String(t.getMonth()+1).padStart(2,'0'),d=String(t.getDate()).padStart(2,'0');
  document.getElementById('um-date').value=t.getFullYear()+'-'+m+'-'+d;
  document.getElementById('upload-modal-status').textContent='';
  document.getElementById('upload-modal-status').className='';
  document.getElementById('upload-modal-submit').disabled=false;
  document.getElementById('upload-modal-bg').classList.add('open');
}
function closeUploadModal(){document.getElementById('upload-modal-bg').classList.remove('open');}
function umUpdateRoutes(){
  var boro=document.getElementById('um-borough').value;
  var sel=document.getElementById('um-route');
  if(boro&&_umRoutes[boro]){
    sel.innerHTML='<option value="">Select route...</option>';
    _umRoutes[boro].forEach(function(r){
      var o=document.createElement('option');o.value=r[0];o.textContent=r[1]+' ('+r[0]+')';sel.appendChild(o);
    });
    sel.disabled=false;
  } else {
    sel.innerHTML='<option value="">Select borough first</option>';
    sel.disabled=true;
  }
}
function umToggleTime(field,mode){
  var dz=document.getElementById('um-'+field+'-dz');
  var nm=document.getElementById('um-'+field+'-names');
  var mn=document.getElementById('um-'+field+'-manual');
  if(mode==='img'){dz.style.display='';nm.style.display='';mn.style.display='none';}
  else{dz.style.display='none';nm.style.display='none';mn.style.display='flex';}
}
function umTsAdvance(inp,nextId){
  if(String(inp.value).replace(/\\D/g,'').length>=2) document.getElementById(nextId).focus();
}
function umDragOver(e,el){e.preventDefault();el.classList.add('drag-over');}
function umDragLeave(el){el.classList.remove('drag-over');}
function umDrop(e,el,fileInputId,namesId){
  e.preventDefault();el.classList.remove('drag-over');
  var fi=document.getElementById(fileInputId);
  var dt=e.dataTransfer;
  if(dt&&dt.files&&dt.files.length){
    try{var c=new DataTransfer();for(var i=0;i<dt.files.length;i++)c.items.add(dt.files[i]);fi.files=c.files;}catch(x){}
    umShowNames(dt.files,namesId);
  }
}
function umFileChosen(inp,namesId){umShowNames(inp.files,namesId);}
function umShowNames(files,namesId){
  var el=document.getElementById(namesId);
  if(!files||!files.length){el.textContent='';return;}
  var n=[];for(var i=0;i<files.length;i++)n.push(files[i].name);
  el.textContent=n.join(', ');
}
function umSubmit(){
  var date=document.getElementById('um-date').value;
  var bp=document.getElementById('um-backpack').value;
  var tod=document.getElementById('um-tod').value;
  var col=document.getElementById('um-collector').value;
  var bor=document.getElementById('um-borough').value;
  var rt=document.getElementById('um-route').value;
  if(!date||!bp||!tod||!col||!bor||!rt){umStatus('Please fill all walk metadata fields.','err');return;}
  var timeErrs=[];
  ['start','walk','end'].forEach(function(f){
    var mode=document.querySelector('input[name="um-'+f+'-mode"]:checked').value;
    if(mode==='img'){
      var fi=document.getElementById('um-'+f+'-file');
      if(!(fi.files&&fi.files[0]))timeErrs.push(f);
    }else{
      var hh=document.getElementById('um-'+f+'-hh').value;
      var mm=document.getElementById('um-'+f+'-mm').value;
      var ss=document.getElementById('um-'+f+'-ss').value;
      if(hh===''||mm===''||ss==='')timeErrs.push(f);
    }
  });
  if(timeErrs.length){umStatus('Please provide '+timeErrs.join(', ')+' time(s).','err');return;}
  var gpxReq=document.getElementById('um-gpx-file');
  if(!(gpxReq.files&&gpxReq.files[0])){umStatus('Please attach a GPX/KML/KMZ track.','err');return;}
  var fd=new FormData();
  fd.append('date',date.replace(/-/g,''));
  fd.append('backpack',bp);fd.append('tod',tod);fd.append('collector',col);fd.append('borough',bor);fd.append('route',rt);
  ['start','walk','end'].forEach(function(f){
    var mode=document.querySelector('input[name="um-'+f+'-mode"]:checked').value;
    if(mode==='img'){var fi=document.getElementById('um-'+f+'-file');if(fi.files&&fi.files[0])fd.append(f+'_time_img',fi.files[0],fi.files[0].name);}
    else{
      var hh=String(document.getElementById('um-'+f+'-hh').value||'').padStart(2,'0');
      var mm=String(document.getElementById('um-'+f+'-mm').value||'').padStart(2,'0');
      var ss=String(document.getElementById('um-'+f+'-ss').value||'').padStart(2,'0');
      fd.append(f+'_time_manual',hh+':'+mm+':'+ss);
    }
  });
  ['pom','pop','pam'].forEach(function(p){
    var fi=document.getElementById('um-'+p+'-files');
    if(fi.files)for(var i=0;i<fi.files.length;i++)fd.append(p,fi.files[i],fi.files[i].name);
  });
  var gpx=document.getElementById('um-gpx-file');
  if(gpx.files&&gpx.files[0])fd.append('gpx_file',gpx.files[0],gpx.files[0].name);
  var notes=document.getElementById('um-notes').value.trim();
  if(notes)fd.append('notes',notes);
  document.getElementById('upload-modal-submit').disabled=true;
  umStatus('Uploading…','');
  fetch('/api/upload-walk',{method:'POST',body:fd})
    .then(function(r){return r.json().then(function(d){return{ok:r.ok,data:d};});})
    .then(function(r){
      if(r.ok){umStatus('Walk '+r.data.walk+' recorded.','ok');setTimeout(closeUploadModal,2000);}
      else{umStatus('Error: '+(r.data.error||'unknown'),'err');document.getElementById('upload-modal-submit').disabled=false;}
    })
    .catch(function(e){umStatus('Network error: '+e.message,'err');document.getElementById('upload-modal-submit').disabled=false;});
}
function umStatus(msg,cls){var el=document.getElementById('upload-modal-status');el.textContent=msg;el.className=cls;}
document.addEventListener('DOMContentLoaded',function(){
  document.getElementById('upload-modal-bg').addEventListener('click',function(e){if(e.target===this)closeUploadModal();});
});

</script>
<!--- Auth modal --->
<div id="auth-modal-bg">
  <div id="auth-modal">
    <h3>&#x1F511; Admin Login</h3>
    <div id="auth-modal-notice"></div>
    <div>
      <label for="auth-who">Your role</label>
      <select id="auth-who">
        <option value="CCNY">CCNY Scheduler</option>
        <option value="LaGCC">LaGCC Scheduler</option>
      </select>
    </div>
    <div>
      <label for="auth-pin">Scheduler PIN</label>
      <input type="password" id="auth-pin" placeholder="Enter PIN" autocomplete="current-password">
    </div>
    <div id="auth-modal-err"></div>
    <div id="auth-modal-actions">
      <button id="auth-modal-cancel">Cancel</button>
      <button id="auth-modal-submit">Unlock</button>
    </div>
  </div>
</div>
<!--- Walk data upload modal --->
<div id="upload-modal-bg">
  <div id="upload-modal">
    <h2>&#x2B06; Upload Walk Data</h2>
    <div class="um-section">
      <div class="um-section-title">Walk Metadata</div>
      <div class="um-fields">
        <div class="um-field"><label for="um-date">Date</label><input type="date" id="um-date"></div>
        <div class="um-field"><label for="um-backpack">Backpack</label>
          <select id="um-backpack"><option value="">Select...</option><option value="A">A — CCNY</option><option value="B">B — LaGCC</option></select></div>
        <div class="um-field"><label for="um-tod">Time of Day</label>
          <select id="um-tod"><option value="">Select...</option><option value="AM">AM</option><option value="MD">MD</option><option value="PM">PM</option></select></div>
        <div class="um-field"><label for="um-collector">Collector</label>
          <select id="um-collector"><option value="">Select...</option>
            <option value="SOT">Soteri (SOT)</option><option value="AYA">Aya Nasri (AYA)</option>
            <option value="ALX">Alex (ALX)</option><option value="TAH">Taha (TAH)</option>
            <option value="JAM">James (JAM)</option><option value="JEN">Jennifer (JEN)</option>
            <option value="SCT">Scott (SCT)</option><option value="TER">Terra (TER)</option>
            <option value="ANG">Angy (ANG)</option><option value="NRS">Prof. Naresh (NRS)</option>
            <option value="PRA">Prof. Prathap (PRA)</option><option value="NAT">Nathan (NAT)</option>
          </select></div>
        <div class="um-field"><label for="um-borough">Borough</label>
          <select id="um-borough" onchange="umUpdateRoutes()"><option value="">Select...</option>
            <option value="MN">Manhattan (MN)</option><option value="BX">Bronx (BX)</option>
            <option value="BK">Brooklyn (BK)</option><option value="QN">Queens (QN)</option>
          </select></div>
        <div class="um-field"><label for="um-route">Route</label>
          <select id="um-route" disabled><option value="">Select borough first</option></select></div>
      </div>
    </div>
    <div class="um-section">
      <div class="um-section-title">UTC Times</div>
      <div class="um-time-group">
        <div class="um-time-card">
          <div class="um-tc-label">Start Time *</div>
          <div class="um-time-radio">
            <label><input type="radio" name="um-start-mode" value="img" checked onchange="umToggleTime('start',this.value)"> Image</label>
            <label><input type="radio" name="um-start-mode" value="manual" onchange="umToggleTime('start',this.value)"> Manual</label>
          </div>
          <div class="um-drop-zone" id="um-start-dz" onclick="document.getElementById('um-start-file').click()" ondragover="umDragOver(event,this)" ondragleave="umDragLeave(this)" ondrop="umDrop(event,this,'um-start-file','um-start-names')">
            <div class="um-dz-icon">&#x1F4F7;</div><div class="um-dz-label">Drop image</div><div class="um-dz-hint">or click to browse</div>
          </div>
          <input type="file" id="um-start-file" accept="image/*" style="display:none" onchange="umFileChosen(this,'um-start-names')">
          <div class="um-dz-files" id="um-start-names"></div>
          <div id="um-start-manual" class="um-time-split" style="display:none">
            <input type="number" class="um-ts-part" id="um-start-hh" min="0" max="23" placeholder="HH" oninput="umTsAdvance(this,'um-start-mm')">
            <span class="um-ts-sep">:</span>
            <input type="number" class="um-ts-part" id="um-start-mm" min="0" max="59" placeholder="MM" oninput="umTsAdvance(this,'um-start-ss')">
            <span class="um-ts-sep">:</span>
            <input type="number" class="um-ts-part" id="um-start-ss" min="0" max="59" placeholder="SS">
            <span class="um-ts-utc">UTC</span>
          </div>
        </div>
        <div class="um-time-card">
          <div class="um-tc-label">Walk Time *</div>
          <div class="um-time-radio">
            <label><input type="radio" name="um-walk-mode" value="img" checked onchange="umToggleTime('walk',this.value)"> Image</label>
            <label><input type="radio" name="um-walk-mode" value="manual" onchange="umToggleTime('walk',this.value)"> Manual</label>
          </div>
          <div class="um-drop-zone" id="um-walk-dz" onclick="document.getElementById('um-walk-file').click()" ondragover="umDragOver(event,this)" ondragleave="umDragLeave(this)" ondrop="umDrop(event,this,'um-walk-file','um-walk-names')">
            <div class="um-dz-icon">&#x1F4F7;</div><div class="um-dz-label">Drop image</div><div class="um-dz-hint">or click to browse</div>
          </div>
          <input type="file" id="um-walk-file" accept="image/*" style="display:none" onchange="umFileChosen(this,'um-walk-names')">
          <div class="um-dz-files" id="um-walk-names"></div>
          <div id="um-walk-manual" class="um-time-split" style="display:none">
            <input type="number" class="um-ts-part" id="um-walk-hh" min="0" max="23" placeholder="HH" oninput="umTsAdvance(this,'um-walk-mm')">
            <span class="um-ts-sep">:</span>
            <input type="number" class="um-ts-part" id="um-walk-mm" min="0" max="59" placeholder="MM" oninput="umTsAdvance(this,'um-walk-ss')">
            <span class="um-ts-sep">:</span>
            <input type="number" class="um-ts-part" id="um-walk-ss" min="0" max="59" placeholder="SS">
            <span class="um-ts-utc">UTC</span>
          </div>
        </div>
        <div class="um-time-card">
          <div class="um-tc-label">End Time *</div>
          <div class="um-time-radio">
            <label><input type="radio" name="um-end-mode" value="img" checked onchange="umToggleTime('end',this.value)"> Image</label>
            <label><input type="radio" name="um-end-mode" value="manual" onchange="umToggleTime('end',this.value)"> Manual</label>
          </div>
          <div class="um-drop-zone" id="um-end-dz" onclick="document.getElementById('um-end-file').click()" ondragover="umDragOver(event,this)" ondragleave="umDragLeave(this)" ondrop="umDrop(event,this,'um-end-file','um-end-names')">
            <div class="um-dz-icon">&#x1F4F7;</div><div class="um-dz-label">Drop image</div><div class="um-dz-hint">or click to browse</div>
          </div>
          <input type="file" id="um-end-file" accept="image/*" style="display:none" onchange="umFileChosen(this,'um-end-names')">
          <div class="um-dz-files" id="um-end-names"></div>
          <div id="um-end-manual" class="um-time-split" style="display:none">
            <input type="number" class="um-ts-part" id="um-end-hh" min="0" max="23" placeholder="HH" oninput="umTsAdvance(this,'um-end-mm')">
            <span class="um-ts-sep">:</span>
            <input type="number" class="um-ts-part" id="um-end-mm" min="0" max="59" placeholder="MM" oninput="umTsAdvance(this,'um-end-ss')">
            <span class="um-ts-sep">:</span>
            <input type="number" class="um-ts-part" id="um-end-ss" min="0" max="59" placeholder="SS">
            <span class="um-ts-utc">UTC</span>
          </div>
        </div>
      </div>
    </div>
    <div class="um-section">
      <div class="um-section-title">Data Uploads</div>
      <div class="um-drop-zones">
        <div>
          <label class="um-toggle-label" style="margin-bottom:6px"><input type="checkbox" id="um-pom-toggle" onchange="document.getElementById('um-pom-zone').style.display=this.checked?'flex':'none'"> POM</label>
          <div id="um-pom-zone" style="display:none;flex-direction:column">
            <div class="um-drop-zone" onclick="document.getElementById('um-pom-files').click()" ondragover="umDragOver(event,this)" ondragleave="umDragLeave(this)" ondrop="umDrop(event,this,'um-pom-files','um-pom-names')">
              <div class="um-dz-icon">&#x1F4C1;</div><div class="um-dz-label">POM</div><div class="um-dz-hint">Any type · Multiple</div>
            </div>
            <input type="file" id="um-pom-files" multiple style="display:none" onchange="umFileChosen(this,'um-pom-names')">
            <div class="um-dz-files" id="um-pom-names"></div>
          </div>
        </div>
        <div>
          <label class="um-toggle-label" style="margin-bottom:6px"><input type="checkbox" id="um-pop-toggle" onchange="document.getElementById('um-pop-zone').style.display=this.checked?'flex':'none'"> POP</label>
          <div id="um-pop-zone" style="display:none;flex-direction:column">
            <div class="um-drop-zone" onclick="document.getElementById('um-pop-files').click()" ondragover="umDragOver(event,this)" ondragleave="umDragLeave(this)" ondrop="umDrop(event,this,'um-pop-files','um-pop-names')">
              <div class="um-dz-icon">&#x1F4C1;</div><div class="um-dz-label">POP</div><div class="um-dz-hint">Any type · Multiple</div>
            </div>
            <input type="file" id="um-pop-files" multiple style="display:none" onchange="umFileChosen(this,'um-pop-names')">
            <div class="um-dz-files" id="um-pop-names"></div>
          </div>
        </div>
        <div>
          <label class="um-toggle-label" style="margin-bottom:6px"><input type="checkbox" id="um-pam-toggle" onchange="document.getElementById('um-pam-zone').style.display=this.checked?'flex':'none'"> PAM</label>
          <div id="um-pam-zone" style="display:none;flex-direction:column">
            <div class="um-drop-zone" onclick="document.getElementById('um-pam-files').click()" ondragover="umDragOver(event,this)" ondragleave="umDragLeave(this)" ondrop="umDrop(event,this,'um-pam-files','um-pam-names')">
              <div class="um-dz-icon">&#x1F4C1;</div><div class="um-dz-label">PAM</div><div class="um-dz-hint">Any type · Multiple</div>
            </div>
            <input type="file" id="um-pam-files" multiple style="display:none" onchange="umFileChosen(this,'um-pam-names')">
            <div class="um-dz-files" id="um-pam-names"></div>
          </div>
        </div>
      </div>
    </div>
    <div class="um-section">
      <div class="um-section-title">Track &amp; Notes</div>
      <div class="um-track-notes">
        <div class="um-notes-wrap">
          <label class="um-notes-label">GPX Track *</label>
          <div class="um-drop-zone" onclick="document.getElementById('um-gpx-file').click()" ondragover="umDragOver(event,this)" ondragleave="umDragLeave(this)" ondrop="umDrop(event,this,'um-gpx-file','um-gpx-names')">
            <div class="um-dz-icon">&#x1F5FA;</div><div class="um-dz-hint">.gpx, .kml, .kmz</div>
          </div>
          <input type="file" id="um-gpx-file" accept=".gpx,.kml,.kmz" style="display:none" onchange="umFileChosen(this,'um-gpx-names')">
          <div class="um-dz-files" id="um-gpx-names"></div>
        </div>
        <div class="um-notes-wrap">
          <label class="um-notes-label" for="um-notes">Walk Notes</label>
          <textarea id="um-notes" class="um-notes" placeholder="Describe the walk — conditions, observations, issues..."></textarea>
        </div>
      </div>
    </div>
    <div id="upload-modal-actions">
      <div id="upload-modal-status"></div>
      <button id="upload-modal-cancel" onclick="closeUploadModal()">Cancel</button>
      <button id="upload-modal-submit" onclick="umSubmit()">&#x2B06; Submit Walk</button>
    </div>
  </div>
</div>
<!--- Calibration entry modal --->
<div id="recal-modal-bg">
  <div id="recal-modal">
    <h3>&#x1F4CB; Log Calibration</h3>
    <div>
      <label for="recal-date">Calibration Date</label>
      <input type="date" id="recal-date">
    </div>
    <div>
      <label>Backpack *</label>
      <div id="recal-bp-choices" role="radiogroup">
        <label><input type="radio" name="recal-bp" value="A"> Backpack A &mdash; CCNY</label>
        <label><input type="radio" name="recal-bp" value="B"> Backpack B &mdash; LaGCC</label>
      </div>
    </div>
    <div id="recal-modal-msg"></div>
    <div id="recal-modal-actions">
      <button id="recal-modal-cancel">Cancel</button>
      <button id="recal-modal-submit">Record</button>
    </div>
  </div>
</div>
<div id="filters">
  <div class="filter-section-head">Walk Filters</div>
  <div class="fg"><span class="fl">Season</span>
    <select id="fseason"><option value="">All seasons</option><option value="Spring">Spring</option><option value="Summer">Summer</option><option value="Fall">Fall</option><option value="Winter">Winter</option></select>
  </div>
  <div class="fg"><span class="fl">Time of Day</span>
    <select id="ftod"><option value="">All</option><option value="AM">AM</option><option value="MD">MD</option><option value="PM">PM</option></select>
  </div>
  <div class="fg"><span class="fl">Backpack</span>
    <select id="fbp"><option value="">All</option><option value="A">A - CCNY</option><option value="B">B - LaGCC</option><option value="X">X (legacy)</option></select>
  </div>
  <div class="fg"><span class="fl">Date From</span><input type="date" id="ffrom"></div>
  <div class="fg"><span class="fl">Date To</span><input type="date" id="fto"></div>
  <div style="display:flex;gap:6px">
    <button class="btn" id="btn-refresh" style="flex:1">&#x27F3; Refresh</button>
    <label class="btn" for="ffile" style="flex:1;justify-content:center">&#x1F4C2; Load Log</label>
  </div>
  <input type="file" id="ffile" accept=".txt" style="display:none">
  <div id="data-status">loading...</div>
</div>
</body>
</html>
"""

# Replace placeholders
HTML_TEMPLATE = HTML_TEMPLATE.replace('__ROUTES_JSON__', routes_json)
HTML_TEMPLATE = HTML_TEMPLATE.replace('__AFFINITY_JSON__', affinity_json)
HTML_TEMPLATE = HTML_TEMPLATE.replace('__SAMPLE_LOG__', sample_log_js)
HTML_TEMPLATE = HTML_TEMPLATE.replace('__COLLECTOR_HOMES__', collector_homes_json)
HTML_TEMPLATE = HTML_TEMPLATE.replace('__ROUTE_GROUPS_JSON__', route_groups_json)
HTML_TEMPLATE = HTML_TEMPLATE.replace('__BAKED_SCHEDULE__', baked_schedule_json)
HTML_TEMPLATE = HTML_TEMPLATE.replace('__BAKED_WEATHER__', baked_weather_json)
HTML_TEMPLATE = HTML_TEMPLATE.replace('__AVAIL_DAYS_JSON__', avail_days_json)
HTML_TEMPLATE = HTML_TEMPLATE.replace('__AVAIL_CELLS_A__', avail_cells_a_json)
HTML_TEMPLATE = HTML_TEMPLATE.replace('__AVAIL_CELLS_B__', avail_cells_b_json)
HTML_TEMPLATE = HTML_TEMPLATE.replace('__MAX_A__', str(avail_max_a))
HTML_TEMPLATE = HTML_TEMPLATE.replace('__MAX_B__', str(avail_max_b))

# -- Upload-failure banner (rendered if upload_failures.json has recent entries) --
import datetime as _dt
_failure_banner_html = ""
_failures_path = PERSISTED_DIR / "upload_failures.json"
if _failures_path.exists():
    try:
        _records = json.loads(_failures_path.read_text(encoding="utf-8"))
        _cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=7)
        _recent = []
        for _r in _records if isinstance(_records, list) else []:
            try:
                _ft = _dt.datetime.strptime(
                    _r.get("failed_at", ""), "%Y-%m-%dT%H:%M:%SZ"
                ).replace(tzinfo=_dt.timezone.utc)
            except Exception:
                continue
            if _ft >= _cutoff:
                _recent.append(_r)
        if _recent:
            _items = "".join(
                f"<li><code>{_r.get('walk_code','?')}</code> — "
                f"{_r.get('failed_at','?')} — {_r.get('error','?')[:200]}</li>"
                for _r in _recent[-10:]
            )
            _failure_banner_html = (
                "<div id=\"upload-failure-banner\" style=\""
                "background:#5a1d1d;color:#ffd6d6;border-bottom:2px solid #f85149;"
                "padding:10px 16px;font-family:system-ui,sans-serif;font-size:13px;"
                "z-index:9999;position:relative\">"
                f"<strong>⚠ {len(_recent)} upload(s) failed to sync to Drive in the last 7 days.</strong> "
                "Files remain in <code>upload_holding_bucket/failed/</code>. "
                f"<details style=\"display:inline-block;margin-left:8px\"><summary>show recent</summary>"
                f"<ul style=\"margin:6px 0 0 18px\">{_items}</ul></details>"
                "</div>"
            )
    except Exception as _exc:
        print(f"[dashboard] upload-failure banner read warning: {_exc}")
HTML_TEMPLATE = HTML_TEMPLATE.replace("<body>", "<body>\n" + _failure_banner_html, 1)

# Fix double file reader issue
HTML_TEMPLATE = HTML_TEMPLATE.replace(
    """    new FileReader().onload=ev=>{
      logText=ev.target.result;allWalks=parseLog(logText);
      applyFilters();updateStatus(f.name);
      toast(`Loaded ${f.name}: ${allWalks.length} walks`,'success');
      e.target.value='';
    };(()=>{const r=new FileReader();r.onload=ev=>{
      logText=ev.target.result;allWalks=parseLog(logText);
      applyFilters();updateStatus(f.name);
      toast(`Loaded ${f.name}: ${allWalks.length} walks`,'success');
      e.target.value='';};r.readAsText(f);})();""",
    """    const r=new FileReader();
    r.onload=ev=>{
      logText=ev.target.result;allWalks=parseLog(logText);
      applyFilters();updateStatus(f.name);
      toast('Loaded '+f.name+': '+allWalks.length+' walks','success');
      e.target.value='';
    };
    r.readAsText(f);"""
)

def build():
    DASHBOARD_HTML.parent.mkdir(parents=True, exist_ok=True)
    with open(DASHBOARD_HTML, 'w', encoding='utf-8') as f:
        f.write(HTML_TEMPLATE)
    size = DASHBOARD_HTML.stat().st_size
    print(f"dashboard.html written: {size:,} bytes ({size//1024} KB)")

    # Also rebuild the availability heatmap
    import subprocess, sys
    subprocess.run([sys.executable, str(BASE / "build_availability_heatmap.py")], check=True)

if __name__ == "__main__":
    build()

