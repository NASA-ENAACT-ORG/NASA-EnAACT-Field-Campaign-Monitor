#!/usr/bin/env python3
"""Generates dashboard.html with embedded route KML data and sample log."""
import json, re, xml.etree.ElementTree as ET
from pathlib import Path

BASE = Path(__file__).parent

# Read sources
with open(BASE / "routes_data.json", encoding="utf-8") as f:
    routes_json = f.read()

with open(BASE / "Walks_Log.txt", encoding="utf-8") as f:
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

# ── Collector home locations from KML ─────────────────────────────────────────
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
_kml_path = BASE / "Route_KMLs" / "Collector_Locs.kml"
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

# ── Bake schedule_output.json into the dashboard ─────────────────────────────
_sched_path = BASE / "schedule_output.json"
if _sched_path.exists():
    with open(_sched_path, encoding="utf-8") as _sf:
        baked_schedule_json = _sf.read()
else:
    baked_schedule_json = "null"

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
#header{display:flex;align-items:center;gap:8px;padding:0 12px;height:56px;background:var(--bg2);border-bottom:1px solid var(--border);flex-shrink:0;z-index:100}
#header h1{font-size:15px;font-weight:700;white-space:nowrap;letter-spacing:-.2px;font-family:'Space Grotesk',sans-serif;line-height:1.25;-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;text-rendering:geometricPrecision;transform:translateZ(0)}
#header h1 em{font-style:normal;color:var(--accent);font-size:10px;font-weight:600;background:rgba(56,139,253,.15);border:1px solid rgba(56,139,253,.3);border-radius:4px;padding:1px 5px;margin-left:6px;vertical-align:middle}
#header-logos{display:flex;align-items:center;gap:10px;flex-shrink:0}
#nasa-worm-logo{height:26px;width:auto;flex-shrink:0;display:block}
#tempo-logo{height:38px;width:auto;flex-shrink:0;display:block}
#header-divider{width:1px;height:32px;background:var(--border);flex-shrink:0}
#header-title{display:flex;flex-direction:column;gap:1px;flex-shrink:1;min-width:0}
#header-title h1{margin:0}
#tabs{display:flex;gap:6px;margin-left:auto;align-items:flex-end}
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
/* Filters always live in a drawer panel, opened by hamburger on all screen sizes */
#filters{display:none;position:fixed;left:-100%;top:56px;width:280px;max-width:calc(100% - 16px);height:calc(100vh - 56px);background:var(--bg2);border-right:1px solid var(--border);border-top:1px solid var(--border);overflow-y:auto;z-index:580;padding:14px;gap:10px;flex-direction:column;transition:left .25s ease;box-shadow:4px 0 20px rgba(0,0,0,.4)}
#filters.open{display:flex!important;left:0!important}
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
#mstats{position:absolute;top:10px;left:10px;z-index:1000;display:flex;flex-direction:column;gap:5px;pointer-events:none}
.msc{background:rgba(13,17,23,.88);border:1px solid var(--border);border-radius:6px;padding:6px 10px;font-size:10px;color:var(--text2)}
.msc strong{display:block;font-size:17px;font-weight:700;color:var(--text);line-height:1.1}
/* CALENDAR VIEW */
#calendar-view{flex-direction:column;overflow:hidden}
#cal-nav{display:flex;align-items:center;gap:6px;padding:0 16px;height:52px;background:var(--bg2);border-bottom:1px solid var(--border);flex-shrink:0}
#cal-nav h2{font-size:13px;font-weight:700;margin:0 8px;min-width:230px;text-align:center;white-space:nowrap}
.cal-nav-btn{width:28px;height:28px;border-radius:50%;border:1px solid var(--border);background:transparent;color:var(--text);cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:16px;line-height:1;flex-shrink:0}
.cal-nav-btn:hover{background:var(--bg3);border-color:var(--accent)}
.cal-nav-btn:disabled{opacity:.28;cursor:default}
#cal-today-btn{padding:3px 12px;border-radius:14px;border:1px solid var(--border);background:transparent;color:var(--text2);cursor:pointer;font-size:11px;font-weight:500}
#cal-today-btn:hover{background:var(--bg3);border-color:var(--accent);color:var(--text)}
#cal-src-badge{font-size:10px;color:var(--text3);margin-left:auto;padding:2px 8px;background:var(--bg3);border-radius:9px;border:1px solid var(--border);white-space:nowrap}
#cal-body{flex:1;overflow:auto;min-height:0}
#cal-grid{display:grid;grid-template-columns:54px repeat(7,1fr);grid-template-rows:56px repeat(3,minmax(110px,1fr));min-height:100%}
.cal-corner{background:var(--bg2);border-right:1px solid var(--border);border-bottom:2px solid var(--border);position:sticky;top:0;left:0;z-index:20}
.cal-day-head{background:var(--bg2);border-right:1px solid var(--border);border-bottom:2px solid var(--border);padding:8px 6px 6px;text-align:center;position:sticky;top:0;z-index:10}
.cal-dname{font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:.6px;font-weight:600}
.cal-dnum{font-size:22px;font-weight:300;color:var(--text);line-height:1.3;margin-top:1px}
.cal-today-head .cal-dnum{background:var(--accent);color:#fff;border-radius:50%;width:34px;height:34px;display:inline-flex;align-items:center;justify-content:center;font-weight:500;font-size:18px}
.cal-tod-lbl{background:var(--bg2);border-right:1px solid var(--border);border-bottom:1px solid var(--border);display:flex;align-items:flex-start;justify-content:flex-end;padding:10px 6px 0 0;font-size:10px;font-weight:700;letter-spacing:.3px;position:sticky;left:0;z-index:5}
.cal-tod-lbl.am{color:var(--tod-am)}.cal-tod-lbl.md{color:var(--tod-md)}.cal-tod-lbl.pm{color:var(--tod-pm)}
.cal-cell{border-right:1px solid var(--border);border-bottom:1px solid var(--border);padding:5px;display:flex;flex-direction:column;gap:4px;background:var(--bg)}
.cal-cell.cal-today-col{background:rgba(56,139,253,.05)}
.cal-cell.cal-past-col{background:rgba(0,0,0,.12)}
.cal-cell.cal-weekend{background:rgba(255,255,255,.012)}
.cal-event{border-radius:5px;padding:5px 8px 6px;font-size:11px;cursor:default;transition:filter .12s}
.cal-event:hover{filter:brightness(1.2)}
.cal-event.bpa{background:rgba(248,81,73,.18);border-left:3px solid #f85149}
.cal-event.bpb{background:rgba(56,139,253,.18);border-left:3px solid #388bfd}
.cal-event.bpx{background:rgba(240,165,0,.18);border-left:3px solid #f0a500}
.cal-event.completed{opacity:.5}
.ce-bp{font-size:8.5px;font-weight:700;letter-spacing:.4px;text-transform:uppercase;margin-bottom:2px}
.cal-event.bpa .ce-bp{color:#f87171}.cal-event.bpb .ce-bp{color:#60a5fa}.cal-event.bpx .ce-bp{color:#fbbf24}
.cal-event.completed .ce-bp::before{content:'✓ '}
.ce-route{font-size:11px;font-weight:600;color:var(--text);line-height:1.3}
.ce-col{font-size:9.5px;color:var(--text2);margin-top:3px}
.cal-recal-tag{background:rgba(240,165,0,.12);border:1px dashed #f0a500;border-radius:4px;padding:5px 8px;font-size:9.5px;color:#f0a500;text-align:center;font-weight:700;letter-spacing:.3px}
.cal-empty-week{grid-column:1/-1;display:flex;align-items:center;justify-content:center;color:var(--text3);font-size:12px;padding:48px}
/* COLLECTOR VIEW */
#collector-view{flex-direction:column;overflow-y:auto;padding:14px 16px;gap:14px}
/* SCHEDULE VIEW */
#schedule-view{flex-direction:row}
#sched-map-wrap{flex:1;position:relative;overflow:hidden}
#sched-map{width:100%;height:100%}
#sched-panel{width:400px;flex-shrink:0;background:var(--bg2);border-left:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden}
#sched-panel-head{padding:12px 14px 9px;border-bottom:1px solid var(--border)}
#sched-panel-head h2{font-size:13px;font-weight:700}
#sched-panel-head .smeta{font-size:10px;color:var(--text2);margin-top:3px}
#sched-panel-body{flex:1;overflow-y:auto;padding:10px 12px;display:flex;flex-direction:column;gap:10px}
.sbp-section h3{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.6px;margin-bottom:7px;display:flex;align-items:center;gap:6px}
.sbp-section h3 .bpbadge{font-size:9px;padding:1px 6px;border-radius:10px;font-weight:700}
.bpa{background:rgba(248,81,73,.2);color:#f85149;border:1px solid rgba(248,81,73,.4)}
.bpb{background:rgba(56,139,253,.2);color:#388bfd;border:1px solid rgba(56,139,253,.4)}
.sched-row{display:flex;align-items:center;gap:6px;padding:5px 7px;background:var(--bg3);border-radius:5px;margin-bottom:4px;cursor:pointer;transition:background .15s}
.sched-row:hover{background:var(--bg4)}
.sched-row .sr-date{font-size:9px;color:var(--text3);width:48px;flex-shrink:0}
.sched-row .sr-tod{font-size:9px;font-weight:700;width:22px;flex-shrink:0}
.sched-row .sr-route{flex:1;font-size:11px;font-weight:600}
.sched-row .sr-col{font-size:9px;color:var(--text2)}
#sched-no-data{padding:20px;text-align:center;color:var(--text3);font-size:12px}
#sched-legend{position:absolute;bottom:108px;left:12px;background:rgba(13,17,23,.85);border:1px solid var(--border);border-radius:7px;padding:9px 12px;z-index:400;backdrop-filter:blur(4px)}
#sched-legend h4{font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:var(--text3);margin-bottom:7px}
.sli{display:flex;align-items:center;gap:7px;font-size:11px;margin-bottom:4px}
.slsw{width:24px;height:3px;border-radius:2px;flex-shrink:0}
#sched-btn-row{display:flex;gap:7px;margin:10px 12px;align-items:center}
/* ── Scheduler auth & status ── */
#sched-unlock-btn{padding:4px 10px;border-radius:5px;border:1px solid var(--border);background:var(--bg3);color:var(--text2);font-size:10px;cursor:pointer;white-space:nowrap;display:flex;align-items:center;gap:4px}
#sched-unlock-btn:hover{border-color:var(--accent);color:var(--text)}
#sched-unlock-btn.authed{border-color:#3fb950;color:#3fb950;background:rgba(63,185,80,.1)}
.status-badge{display:inline-flex;align-items:center;gap:3px;font-size:9px;font-weight:700;padding:1px 6px;border-radius:8px;flex-shrink:0}
.status-badge.pending{background:rgba(201,209,217,.12);color:#8b949e}
.status-badge.confirmed{background:rgba(63,185,80,.15);color:#3fb950}
.status-badge.denied{background:rgba(248,81,73,.15);color:#f85149}
.sched-row .sr-actions{display:flex;gap:4px;flex-shrink:0;visibility:hidden}
.sched-row .sr-actions button{padding:2px 6px;border-radius:4px;font-size:9px;font-weight:600;cursor:pointer;border:1px solid}
body.scheduler-mode .sched-row{flex-wrap:wrap}
body.scheduler-mode .sched-row .sr-route{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
body.scheduler-mode .sched-row .sr-actions{width:100%;margin-left:0;justify-content:flex-end;margin-top:3px;padding-left:70px}
.sr-confirm-btn{background:rgba(63,185,80,.15);border-color:#3fb950!important;color:#3fb950!important}
.sr-deny-btn{background:rgba(248,81,73,.15);border-color:#f85149!important;color:#f85149!important}
.sr-reset-btn{background:var(--bg3);border-color:var(--border)!important;color:var(--text2)!important}
body.scheduler-mode .sched-row .sr-actions{visibility:visible}
.cal-event .ce-actions{display:none;margin-top:4px;gap:3px}
.cal-event .ce-actions button{flex:1;padding:2px 4px;border-radius:3px;font-size:9px;font-weight:600;cursor:pointer;border:1px solid}
body.scheduler-mode .cal-event .ce-actions{display:flex}
/* Auth modal */
#auth-modal-bg{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9000;align-items:center;justify-content:center}
#auth-modal-bg.open{display:flex}
#auth-modal{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:22px 24px;width:280px;display:flex;flex-direction:column;gap:12px}
#auth-modal h3{font-size:13px;font-weight:700;margin:0}
#auth-modal label{font-size:10px;color:var(--text2);display:block;margin-bottom:3px}
#auth-modal select,#auth-modal input{width:100%;padding:6px 8px;background:var(--bg3);border:1px solid var(--border);border-radius:5px;color:var(--text);font-size:12px;box-sizing:border-box}
#auth-modal-err{font-size:10px;color:#f85149;display:none}
#auth-modal-submit{padding:7px;border-radius:5px;background:var(--accent);border:none;color:#fff;font-size:12px;font-weight:600;cursor:pointer}
#auth-modal-cancel{padding:7px;border-radius:5px;background:var(--bg3);border:1px solid var(--border);color:var(--text2);font-size:12px;cursor:pointer}
#sched-timeline{position:absolute;bottom:0;left:0;right:0;background:rgba(13,17,23,.95);border-top:1px solid var(--border);padding:5px 12px calc(10px + env(safe-area-inset-bottom));z-index:401;display:flex;flex-direction:column;gap:3px}
#sched-map-wrap .leaflet-control-attribution{margin-bottom:88px!important}
#sched-tl-top{display:flex;align-items:center;gap:6px}
#sched-tl-controls{display:flex;gap:3px;flex-shrink:0}
.tl-btn{width:24px;height:24px;border-radius:4px;border:1px solid var(--border);background:var(--bg3);color:var(--text);cursor:pointer;font-size:11px;display:flex;align-items:center;justify-content:center;padding:0;flex-shrink:0}.tl-btn:hover{background:var(--bg4);border-color:var(--accent)}
#sched-tl-divider{width:1px;height:20px;background:var(--border);flex-shrink:0;margin:0 2px}
#sched-tl-week-nav{display:flex;align-items:center;gap:4px;flex-shrink:0}
#sched-tl-week-label{font-size:10.5px;color:var(--text);font-weight:600;min-width:168px;text-align:center;white-space:nowrap}
.tl-wk-btn{width:22px;height:22px;border-radius:4px;border:1px solid var(--border);background:var(--bg3);color:var(--text2);cursor:pointer;font-size:13px;display:flex;align-items:center;justify-content:center;padding:0}.tl-wk-btn:hover{background:var(--bg4);color:var(--text);border-color:var(--accent)}.tl-wk-btn:disabled{opacity:.28;cursor:default}
#sched-tl-detail{font-size:10px;color:var(--text2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1;min-width:0;padding-left:6px;border-left:1px solid var(--border);height:20px;display:flex;align-items:center}
#sched-tl-days{display:flex;gap:3px;align-items:flex-end;min-height:36px}
.tl-day{display:flex;flex-direction:column;align-items:center;gap:2px;flex:1;min-width:34px;padding:0 2px}
.tl-day-lbl{font-size:8.5px;color:var(--text3);white-space:nowrap;letter-spacing:.2px}
.tl-day-lbl.tl-today{color:var(--accent);font-weight:700}
.tl-day-dots{display:flex;gap:3px;justify-content:center;flex-wrap:wrap;min-height:11px}
.tl-dot{width:11px;height:11px;border-radius:50%;cursor:pointer;transition:transform .15s,box-shadow .15s;flex-shrink:0}
.tl-dot:hover{transform:scale(1.35)}
.tl-dot.bp-a{background:#f85149}.tl-dot.bp-b{background:#388bfd}.tl-dot.bp-x{background:#f0a500}
.tl-dot.tl-past{opacity:.38}.tl-dot.tl-current{opacity:1;box-shadow:0 0 0 2px #fff4}.tl-dot.tl-future{opacity:.75}
.tl-day-recal{font-size:8px;color:#f0a500;letter-spacing:.3px}
.tl-day-sep{width:1px;background:var(--border);align-self:stretch;flex-shrink:0}
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
/* ── Live GPS markers ── */
.gps-dot{width:14px;height:14px;border-radius:50%;border:2px solid #fff;box-shadow:0 0 0 0 rgba(248,81,73,.6);transition:background .3s}
.gps-dot-a{background:#f85149;animation:gps-pulse-a 1.6s ease-out infinite}
.gps-dot-b{background:#388bfd;animation:gps-pulse-b 1.6s ease-out infinite}
.gps-dot-stale{background:#6e7681;animation:none;box-shadow:none}
@keyframes gps-pulse-a{0%{box-shadow:0 0 0 0 rgba(248,81,73,.5)}70%{box-shadow:0 0 0 8px rgba(248,81,73,0)}100%{box-shadow:0 0 0 0 rgba(248,81,73,0)}}
@keyframes gps-pulse-b{0%{box-shadow:0 0 0 0 rgba(56,139,253,.5)}70%{box-shadow:0 0 0 8px rgba(56,139,253,0)}100%{box-shadow:0 0 0 0 rgba(56,139,253,0)}}
/* ── Drive / GPS header badges ── */
#live-badges{display:flex;align-items:center;gap:6px;margin-left:auto}
.live-badge{font-size:10px;padding:2px 7px;border-radius:4px;border:1px solid var(--border);background:var(--bg3);color:var(--text3);white-space:nowrap;cursor:default}
.live-badge.ok{border-color:rgba(74,222,128,.4);color:#4ade80}
.live-badge.warn{border-color:rgba(210,153,34,.4);color:#d29922}
.live-badge.err{border-color:rgba(248,81,73,.4);color:#f85149}
.drive-sync-btn{font-size:10px;padding:2px 8px;border-radius:4px;border:1px solid var(--border);background:var(--bg3);color:var(--text2);cursor:pointer;transition:background .15s}
.drive-sync-btn:hover{background:var(--bg4,#2d333b)}
/* ── Hamburger (visible on ALL screen sizes) ── */
.mobile-menu-btn{display:flex;background:transparent;border:1px solid var(--border);color:var(--text);font-size:18px;width:32px;height:32px;border-radius:6px;cursor:pointer;padding:0;align-items:center;justify-content:center;flex-shrink:0;transition:all .15s;margin-left:6px}
.mobile-menu-btn:hover{background:var(--bg3);border-color:var(--accent)}
.mobile-menu-btn.filters-open{background:var(--bg3);border-color:var(--accent);color:var(--accent)}
/* Filter drawer contents layout */
#filters .fg{width:100%;gap:4px;flex-direction:column}
#filters .fg select,#filters .fg input{width:100%;height:32px;padding:4px 8px;font-size:12px}
#filters .fg span.fl{font-size:10px;color:var(--text3);font-weight:600}
#filters .btn{width:100%;justify-content:center;font-size:11px}
#filters #data-status{width:100%;text-align:center}
#filters #live-badges{width:100%;flex-wrap:wrap;gap:4px;margin-left:0}
#filters .live-badge{flex:1;text-align:center;font-size:9px;padding:3px 5px}
#filters .drive-sync-btn{flex:1;height:32px}
/* Filter drawer section headers */
.filter-section-head{font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:var(--text3);padding-bottom:4px;border-bottom:1px solid var(--border);margin-bottom:2px}
@media(max-width:768px){
  #header{flex-wrap:wrap;height:auto;padding:8px 10px;gap:0}
  /* Row 1: logos (left) + title (right) + hamburger */
  #header-logos{order:1;flex-shrink:0;margin-right:auto}
  #nasa-worm-logo{height:20px}
  #tempo-logo{height:30px}
  #header-divider{display:none}
  #header-title{order:1;flex-shrink:1;min-width:0;text-align:right}
  #header h1{font-size:13px;font-family:'Space Grotesk',sans-serif;white-space:normal;line-height:1.2}
  #mobile-menu-btn{order:1;margin-left:8px}
  /* Row 2: tabs full width */
  #tabs{order:2;flex-basis:100%;margin-left:0;gap:3px;margin-top:6px;align-items:stretch}
  .tab-group{flex-direction:row;flex:1}
  .tab-group-label{display:none}
  .tab-group-btns{flex:1;gap:3px}
  .tab-sep{display:none}
  .tab-btn{padding:5px 6px;font-size:11px;flex:1;text-align:center}
  /* Filter drawer on mobile — top offset matches header height */
  #filters{top:auto}
  #route-panel{width:100%;position:fixed;transform:translateX(100%);transition:transform .3s ease;z-index:1100}
  #route-panel.open{transform:translateX(0);width:100%;max-width:85vw}
  #sched-panel{width:100%;position:fixed;transform:translateX(100%);transition:transform .3s ease;z-index:1100;height:100%}
  #sched-panel.open{transform:translateX(0);width:100%;max-width:85vw}
  #map-view{flex-direction:column}
  #map-wrap{flex:1;position:relative}
  #schedule-view{flex-direction:column}
  #sched-map-wrap{flex:1;position:relative}
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
  #route-panel.open,#sched-panel.open{max-width:95vw}
}
</style>
</head>
<body>
<div id="loading"><div class="spin"></div><p id="load-msg">Loading dashboard…</p></div>
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
    if(p&&p.textContent==='Loading dashboard…'){
      p.textContent='Timeout – check browser console (F12)';
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
    <div id="tabs">
      <div class="tab-group">
        <span class="tab-group-label monitor">Campaign Monitor</span>
        <div class="tab-group-btns">
          <button class="tab-btn active" data-view="map-view">&#x1F5FA;&#xFE0F; Map</button>
          <button class="tab-btn" data-view="collector-view">&#x1F465; Collectors</button>
        </div>
      </div>
      <div class="tab-sep"></div>
      <div class="tab-group">
        <span class="tab-group-label scheduling">Scheduling</span>
        <div class="tab-group-btns">
          <button class="tab-btn" data-view="schedule-view">&#x1F5FA;&#xFE0F; Map</button>
          <button class="tab-btn" data-view="calendar-view">&#x1F4C6; Calendar</button>
        </div>
      </div>
    </div>
    <button id="mobile-menu-btn" class="mobile-menu-btn" title="Toggle filters">&#x2630;</button>
    <div id="filters">
      <div class="filter-section-head">Walk Filters</div>
      <div class="fg"><span class="fl">Season</span>
        <select id="fseason"><option value="">All seasons</option><option value="Spring">Spring</option><option value="Summer">Summer</option><option value="Fall">Fall</option><option value="Winter">Winter</option></select>
      </div>
      <div class="fg"><span class="fl">Time of Day</span>
        <select id="ftod"><option value="">All</option><option value="AM">AM</option><option value="MD">Midday</option><option value="PM">PM</option></select>
      </div>
      <div class="fg"><span class="fl">Backpack</span>
        <select id="fbp"><option value="">All</option><option value="A">A — CCNY</option><option value="B">B — LaGCC</option><option value="X">X (legacy)</option></select>
      </div>
      <div class="fg"><span class="fl">Date From</span><input type="date" id="ffrom"></div>
      <div class="fg"><span class="fl">Date To</span><input type="date" id="fto"></div>
      <div style="display:flex;gap:6px">
        <button class="btn" id="btn-refresh" style="flex:1">&#x27F3; Refresh</button>
        <label class="btn" for="ffile" style="flex:1;justify-content:center">&#x1F4C2; Load Log</label>
      </div>
      <input type="file" id="ffile" accept=".txt" style="display:none">
      <div id="data-status">loading…</div>
      <div class="filter-section-head" style="margin-top:4px">Live Status</div>
      <div id="live-badges">
        <span class="live-badge" id="gps-badge-a" title="Backpack A GPS">BP-A: —</span>
        <span class="live-badge" id="gps-badge-b" title="Backpack B GPS">BP-B: —</span>
        <span class="live-badge" id="drive-badge" title="Google Drive last sync">Drive: —</span>
        <button class="drive-sync-btn" id="drive-sync-btn" title="Sync Google Drive now">&#x21BB; Sync</button>
      </div>
    </div>
  </div>
  <div id="content">
    <!-- ── MAP VIEW ── -->
    <div id="map-view" class="view active">
      <div id="map-wrap">
        <div id="map"></div>
        <div id="mstats"></div>
        <button id="collector-homes-btn" title="Toggle collector areas">&#x1F3E0; Collector Areas</button>
        <div id="mlegend">
          <h4>Completion Progress</h4>
          <div style="width:100%;height:7px;border-radius:4px;background:linear-gradient(to right,#f85149 0%,#d29922 30%,#4ade80 60%,#15803d 100%);margin-bottom:6px"></div>
          <div style="display:flex;justify-content:space-between;font-size:9px;color:var(--text3)"><span>0</span><span>3</span><span>Min (6)</span><span>Target (8+)</span></div>
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
    <!-- ── COLLECTOR VIEW ── -->
    <div id="collector-view" class="view">
      <div id="cselector"></div>
      <div id="cdetail">
        <div class="dcard" id="dstats"></div>
        <div class="dcard" id="dcharts"></div>
      </div>
      <div id="csection">
        <h3>All Collectors — Side by Side
          <div class="wtabs">
            <button class="wtab active" data-win="2w">Last 2 Wks</button>
            <button class="wtab" data-win="mo">This Month</button>
            <button class="wtab" data-win="sea">This Season</button>
            <button class="wtab" data-win="all">Whole Project</button>
          </div>
        </h3>
        <div id="ccw"><canvas id="comp-chart"></canvas></div>
        <table class="ctab">
          <thead><tr>
            <th>Collector</th>
            <th class="num">Last 2 Wks</th>
            <th class="num">This Month</th>
            <th class="num">This Season</th>
            <th class="num">Whole Project</th>
          </tr></thead>
          <tbody id="ctbody"></tbody>
        </table>
      </div>
    </div>
    <!-- ── SCHEDULE VIEW ── -->
    <div id="schedule-view" class="view">
      <div id="sched-map-wrap">
        <div id="sched-map"></div>
        <div id="sched-legend">
          <h4>Backpack Assignment</h4>
          <div class="sli"><div class="slsw" style="background:#f85149"></div>Backpack A</div>
          <div class="sli"><div class="slsw" style="background:#388bfd"></div>Backpack B</div>
          <div class="sli"><div class="slsw" style="background:#333c47"></div>Not scheduled</div>
        </div>
        <div id="sched-timeline">
          <div id="sched-tl-top">
            <div id="sched-tl-controls">
              <button class="tl-btn" id="tl-reset" title="Reset to all">&#x23EE;</button>
              <button class="tl-btn" id="tl-prev" title="Previous walk">&#x2039;</button>
              <button class="tl-btn" id="tl-play" title="Play">&#x25B6;</button>
              <button class="tl-btn" id="tl-next" title="Next walk">&#x203A;</button>
            </div>
            <div id="sched-tl-divider"></div>
            <div id="sched-tl-week-nav">
              <button class="tl-wk-btn" id="tl-wk-prev" title="Older week">&#x2190;</button>
              <div id="sched-tl-week-label">—</div>
              <button class="tl-wk-btn" id="tl-wk-next" title="Newer week">&#x2192;</button>
              <button class="tl-wk-btn" id="tl-wk-now" title="Jump to current week" style="margin-left:2px;font-size:9px;width:auto;padding:0 5px">Now</button>
            </div>
            <div id="sched-tl-detail">No schedule loaded</div>
          </div>
          <div id="sched-tl-days"></div>
        </div>
      </div>
      <div id="sched-panel">
        <div id="sched-panel-head">
          <h2>Weekly Schedule</h2>
          <div class="smeta" id="sched-meta">Run the scheduler to load assignments</div>
        </div>
        <div id="sched-btn-row">
          <button id="sched-unlock-btn" title="Enter scheduler PIN to confirm/deny assignments">&#x1F511; Scheduler Mode</button>
        </div>
        <input type="file" id="sched-file" accept=".json" style="display:none">
        <div id="sched-panel-body">
          <div id="sched-no-data">No schedule loaded — schedule is auto-generated when new forecast data arrives.</div>
        </div>
      </div>
    </div>
    <!-- ── CALENDAR VIEW ── -->
    <div id="calendar-view" class="view">
      <div id="cal-nav">
        <button class="cal-nav-btn" id="cal-prev" title="Previous week">&#x2039;</button>
        <button class="cal-nav-btn" id="cal-next" title="Next week">&#x203A;</button>
        <h2 id="cal-title">—</h2>
        <button id="cal-today-btn">Today</button>
        <div id="cal-src-badge">No data loaded</div>
      </div>
      <div id="cal-body">
        <div id="cal-grid"></div>
      </div>
    </div>
    </div>
  </div>
</div>

<script>
// ─── DATA ───
const ROUTES_GEO = __ROUTES_JSON__;
const COLLECTOR_HOMES = __COLLECTOR_HOMES__;
const BAKED_SCHEDULE = __BAKED_SCHEDULE__;
// Campus affiliation → pin color  (purple = CCNY, red = LaGCC, amber = staff)
const COLLECTOR_PIN_COLOR = {
  'SOT':'#7c3aed','AYA':'#7c3aed','JEN':'#7c3aed','TAH':'#7c3aed','ANG':'#7c3aed',
  'TER':'#dc2626','ALX':'#dc2626','SCT':'#dc2626','JAM':'#dc2626',
};
const ROUTE_LABELS = {
  "MN_HT":"Manhattan – Harlem","MN_WH":"Manhattan – Washington Hts",
  "MN_UE":"Manhattan – Upper East Side","MN_MT":"Manhattan – Midtown",
  "MN_LE":"Manhattan – Union Sq / LES","BX_HP":"Bronx – Hunts Point",
  "BX_NW":"Bronx – Norwood","BK_DT":"Brooklyn – Downtown BK",
  "BK_WB":"Brooklyn – Williamsburg","BK_BS":"Brooklyn – Bed Stuy",
  "BK_CH":"Brooklyn – Crown Heights","BK_SP":"Brooklyn – Sunset Park",
  "BK_CI":"Brooklyn – Coney Island","QN_FU":"Queens – Flushing",
  "QN_LI":"Queens – Astoria / LIC","QN_JH":"Queens – Jackson Heights",
  "QN_JA":"Queens – Jamaica","QN_FH":"Queens – Forest Hills",
  "QN_LA":"Queens – LaGuardia CC","QN_EE":"Queens – East Elmhurst",
};
const ALL_ROUTES = new Set(Object.keys(ROUTE_LABELS));
const COLLECTORS = ["SOT","AYA","ALX","TAH","JAM","JEN","SCT","TER","PRA","NAT","NRS"];
const CNAMES = {
  SOT:"Soteri",AYA:"Aya Nasri",ALX:"Alex",TAH:"Taha",JAM:"James",
  JEN:"Jennifer",SCT:"Scott",TER:"Terra",
  PRA:"Prathap",NAT:"Nathan",NRS:"Naresh"
};
const AFFINITY = __AFFINITY_JSON__;
const SAMPLE_LOG = `__SAMPLE_LOG__`;
const TARGET=8, MINC=6;
const TODS=["AM","MD","PM"];

// ─── STATE ───
let allWalks=[], filteredWalks=[], logText=SAMPLE_LOG;
let currentRoute=null, currentCollector=COLLECTORS[0], currentWin='2w';
let filters={season:'',tod:'',backpack:'',from:null,to:null};
let map=null, routeLayers={}, routeCentroids={}, charts={};
let collectorHomeLayer=null, collectorHomesVisible=false, collectorHomeMarkers={};

// ─── UTIL ───
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
// ─── PARSE ───
function parseLog(txt){
  const ws=[];
  for(const raw of txt.split('\\n')){
    const line=raw.trim();if(!line)continue;
    const ai=line.indexOf('\u2192');
    const code=(ai>=0?line.slice(ai+1):line).trim();
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
// ─── FILTER ───
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
    if(filters.from&&w.date<filters.from)return false;
    if(filters.to&&w.date>filters.to)return false;
    return true;
  });
  updateMapColors();
  updateCollectorHomePins();
  updateMapStats();
  if(currentRoute)updateRoutePanel(currentRoute);
  if(document.getElementById('collector-view').classList.contains('active'))renderCV();
}
// ─── MAP ───
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
  // ── Collector home layer (toggled) ──────────────────────────────────────────
  collectorHomeLayer=L.layerGroup();
  for(const[cid,h]of Object.entries(COLLECTOR_HOMES)){
    const initCount=filteredWalks.filter(w=>w.collector===cid).length;
    const m=L.marker([h.lat,h.lng],{icon:makeHomeIcon(cid,initCount),zIndexOffset:500})
     .bindPopup(`<b>${h.name}</b> (${cid})<br><small style="color:#fbbf24">Home location</small>`);
    m.addTo(collectorHomeLayer);
    collectorHomeMarkers[cid]=m;
  }
  document.getElementById('collector-homes-btn').addEventListener('click',()=>{
    collectorHomesVisible=!collectorHomesVisible;
    if(collectorHomesVisible){collectorHomeLayer.addTo(map);}
    else{collectorHomeLayer.remove();}
    document.getElementById('collector-homes-btn').classList.toggle('chb-on',collectorHomesVisible);
  });
}
function gradientColor(n){
  function h2r(h){return[parseInt(h.slice(1,3),16),parseInt(h.slice(3,5),16),parseInt(h.slice(5,7),16)];}
  function lp(a,b,t){return Math.round(a+(b-a)*t);}
  function r2h(r,g,b){return'#'+[r,g,b].map(v=>v.toString(16).padStart(2,'0')).join('');}
  const RED=h2r('#ff0000'),GRN=h2r('#22c55e'),LBL=h2r('#60a5fa'),DBL=h2r('#1e3a8a');
  if(n<=0)return'#ff0000';
  if(n>=8)return'#1e3a8a';
  if(n<=6){const t=n/6;return r2h(lp(RED[0],GRN[0],t),lp(RED[1],GRN[1],t),lp(RED[2],GRN[2],t));}
  if(n<=7){const t=(n-6)/1;return r2h(lp(GRN[0],LBL[0],t),lp(GRN[1],LBL[1],t),lp(GRN[2],LBL[2],t));}
  const t=(n-7);return r2h(lp(LBL[0],DBL[0],t),lp(LBL[1],DBL[1],t),lp(LBL[2],DBL[2],t));
}
function routeStatus(code,ws){
  const n=ws.filter(w=>w.route===code).length;
  const s=n>=TARGET?'green':n>=MINC?'yellow':'red';
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
    `<div class="msc"><strong style="color:#15803d">${cnt.green}</strong>At target (8+)</div>
     <div class="msc"><strong style="color:#4ade80">${cnt.yellow}</strong>Near minimum (6–7)</div>
     <div class="msc"><strong style="color:#f85149">${cnt.red}</strong>Below minimum (&lt;6)</div>`;
}
// ─── ROUTE PANEL ───
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
  document.getElementById('pcode').textContent=code+' · '+ROUTE_LABELS[code]?.split('–')[0]?.trim();
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
// ─── COLLECTOR VIEW ───
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
  const data=COLLECTORS.map(cid=>({
    cid,name:CNAMES[cid],
    vals:['2w','mo','sea','all'].map(w=>getWinsFor(cid,w).length)
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
  const wi=['2w','mo','sea','all'].indexOf(currentWin);
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
// ─── UPDATE STATUS ───
function updateStatus(src){
  const n=allWalks.length;
  const el=document.getElementById('data-status');
  el.textContent=`${n} walks · ${src||'embedded sample'}`;
  el.style.color=n>0?'var(--green)':'var(--text3)';
}
// ─── SCHEDULE ───
let schedMap=null, schedData=null, schedLayers={};
// ── Confirmation state ──────────────────────────────────────────────────────
let schedConfirmations={};   // {assignId: {status,scheduler,timestamp}}
let schedAuth={unlocked:false, scheduler:null, pin:null};

function assignId(a){return `${a.route}_${a.tod}_${a.date}`;}

function statusBadgeHTML(aid){
  const c=schedConfirmations[aid];
  const st=c?c.status:'pending';
  const label=st==='confirmed'?'✅ Confirmed':st==='denied'?'❌ Denied':'🟡 Pending';
  return `<span class="status-badge ${st}">${label}</span>`;
}

async function fetchConfirmations(){
  try{
    const r=await fetch('/api/confirmations');
    if(r.ok){schedConfirmations=await r.json();}
  }catch(e){}
}

async function doConfirm(aid, status){
  try{
    const resp=await fetch('/api/confirm',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({id:aid,status,scheduler:schedAuth.scheduler,pin:schedAuth.pin||''})
    });
    const data=await resp.json();
    if(!resp.ok){toast(data.error||'Error','');return;}
    schedConfirmations=data.confirmations||schedConfirmations;
    // Re-render both panels
    renderSchedulePanel();
    if(document.getElementById('calendar-view').classList.contains('active'))renderCalendar();
    toast(status==='confirmed'?'Assignment confirmed ✅':status==='denied'?'Assignment denied ❌':'Reset to pending','success');
  }catch(e){toast('Could not reach server','');}
}

function actionBtns(aid){
  // Returns inner button HTML only — caller wraps with appropriate class
  const st=(schedConfirmations[aid]||{}).status||'pending';
  if(st==='pending'){
    return `<button class="sr-confirm-btn" onclick="event.stopPropagation();doConfirm('${aid}','confirmed')">✓ Confirm</button>`
          +`<button class="sr-deny-btn"    onclick="event.stopPropagation();doConfirm('${aid}','denied')">✗ Deny</button>`;
  }
  return `<button class="sr-reset-btn" onclick="event.stopPropagation();doConfirm('${aid}','pending')">↩ Reset</button>`;
}
let schedStep=-1, schedPlaying=false, schedPlayTimer=null;
let tlWeekIdx=0;   // index into tlWeeks array (0 = most recent)

const TOD_ORDER={AM:0,MD:1,PM:2};

// ── Helper: snap any date to the Sunday that starts its week ──────────────────
function toWeekSunday(d){
  const s=new Date(d); s.setDate(d.getDate()-d.getDay()); s.setHours(0,0,0,0); return s;
}

// ── Build sorted list of distinct Sun-Sat weeks across completed+scheduled walks ──
function buildTlWeeks(){
  const byWeek={};
  // Completed walks from log — key by the Sunday of the walk's week
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
  // Scheduled assignments — each assignment keyed to the Sunday of ITS OWN date
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
  // Sort descending (index 0 = most recent / furthest future)
  // Future weeks only appear if they have assignment data — no blank placeholders
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
  // CCNY recalibration pin — 160 Convent Ave
  const ccnyIcon=L.divIcon({
    className:'',
    html:`<div style="background:#f0a500;border:2px solid #fff;border-radius:50% 50% 50% 0;width:18px;height:18px;transform:rotate(-45deg);box-shadow:0 0 6px #f0a50099"></div>`,
    iconSize:[18,18],iconAnchor:[9,18]
  });
  L.marker([40.8196,-73.9499],{icon:ccnyIcon,zIndexOffset:1000})
   .bindPopup('<b>CCNY — 160 Convent Ave</b><br>Backpack recalibration site<br><span style="color:#f0a500">★ Recal day: both backpacks return here</span>')
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
  const wk=getTlWeek();
  const weeks=buildTlWeeks();
  const sorted=getSortedAssignments();
  const today=new Date(); today.setHours(0,0,0,0);

  const playBtn=document.getElementById('tl-play');
  const wkLbl=document.getElementById('sched-tl-week-label');
  const detail=document.getElementById('sched-tl-detail');
  const daysEl=document.getElementById('sched-tl-days');
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
  const wkStr=`${ws.toLocaleDateString('en-US',fmtOpts)} – ${we.toLocaleDateString('en-US',{...fmtOpts,year:'numeric'})}`;
  const srcBadge=wk.source==='schedule'?' 📅':' 📋';
  const nowBadge=tlWeekIdx===0?' ◀ current':'';
  if(wkLbl)wkLbl.textContent=wkStr+srcBadge+nowBadge;

  // Detail label for selected step
  if(schedStep>=0&&sorted[schedStep]){
    const c=sorted[schedStep];
    const d=new Date(c.date+'T00:00:00');
    const ds=d.toLocaleDateString('en-US',{weekday:'short',month:'numeric',day:'numeric'});
    const src=c.source==='scheduled'?'🗓 scheduled':'✓ completed';
    if(detail)detail.textContent=`${schedStep+1}/${sorted.length}  ${ds} · ${c.tod} · BP ${c.backpack} · ${ROUTE_LABELS[c.route]||c.route} · ${c.collector||'—'} · ${src}`;
  } else {
    const n=sorted.length, comp=sorted.filter(w=>w.source==='completed').length, sched=sorted.filter(w=>w.source==='scheduled').length;
    if(detail)detail.textContent=`${n} walk${n!==1?'s':''} this week${comp?` · ${comp} completed`:''}${sched?` · ${sched} scheduled`:''}  — click a dot or press ▶`;
  }

  // Build day columns — derive day name from actual date, not from offset index,
  // because week_start may not always be a Monday (schedule weeks start on Friday).
  const DAY_NAMES=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  // Flatten sorted to a lookup: date → list of {walk, globalIdx}
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
    const recalHtml=isRecal?`<div class="tl-day-recal">★RECAL</div>`:'';
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
  if(!schedData){
    body.innerHTML='<div id="sched-no-data">No schedule loaded.<br>Run walk_scheduler.py then click Load above.</div>';
    meta.textContent='Run the scheduler to load assignments';
    return;
  }
  meta.textContent=`Week: ${schedData.week_start} → ${schedData.week_end}  ·  Generated: ${schedData.generated}`;
  const byBP={A:[],B:[]};
  for(const a of schedData.assignments)(byBP[a.backpack]||byBP['A']).push(a);
  byBP.A.sort((a,b)=>a.date.localeCompare(b.date)||a.tod.localeCompare(b.tod));
  byBP.B.sort((a,b)=>a.date.localeCompare(b.date)||a.tod.localeCompare(b.tod));
  function fmtDate(s){const d=new Date(s+'T00:00:00');return d.toLocaleDateString('en-US',{weekday:'short',month:'numeric',day:'numeric'});}
  function section(bp,rows){
    if(!rows.length)return '';
    const cls=bp==='A'?'bpa':'bpb';
    let html=`<div class="sbp-section"><h3><span class="bpbadge ${cls}">Backpack ${bp}</span> ${rows.length} walk${rows.length>1?'s':''}</h3>`;
    for(const r of rows){
      const label=ROUTE_LABELS[r.route]||r.route;
      const todCls=r.tod==='AM'?'tb-am':r.tod==='MD'?'tb-md':'tb-pm';
      const aid=assignId(r);
      html+=`<div class="sched-row" onclick="highlightSchedRoute('${r.route}','${bp}')">
        <span class="sr-date">${fmtDate(r.date)}</span>
        <span class="sr-tod tb ${todCls}">${r.tod}</span>
        <span class="sr-route">${label}</span>
        <span class="sr-col">${r.collector}</span>
        ${statusBadgeHTML(aid)}
        <div class="sr-actions">${actionBtns(aid)}</div>
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

// ─── CALENDAR ───
let calWeekIdx=0;

function renderCalendar(){
  const grid=document.getElementById('cal-grid');
  const title=document.getElementById('cal-title');
  const srcBadge=document.getElementById('cal-src-badge');
  if(!grid)return;

  const weeks=buildTlWeeks();
  if(!weeks.length){
    grid.innerHTML='<div class="cal-empty-week">No walk data or schedule loaded yet — run the scheduler or load a log file.</div>';
    if(title)title.textContent='—';
    if(srcBadge)srcBadge.textContent='No data loaded';
    return;
  }

  const idx=Math.max(0,Math.min(calWeekIdx,weeks.length-1));
  const wk=weeks[idx];
  const ws=new Date(wk.weekStart+'T00:00:00');
  const we=new Date(ws); we.setDate(ws.getDate()+6);
  const tod=new Date(); tod.setHours(0,0,0,0);


  if(title)title.textContent=
    ws.toLocaleDateString('en-US',{month:'short',day:'numeric'})+' – '+
    we.toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'});
  if(srcBadge)srcBadge.textContent=
    wk.source==='schedule'?'📅 Scheduled week':'📋 Completed walks';

  const prevBtn=document.getElementById('cal-prev');
  const nextBtn=document.getElementById('cal-next');
  if(prevBtn)prevBtn.disabled=idx>=weeks.length-1;
  if(nextBtn)nextBtn.disabled=idx<=0;

  // Build lookup: dateStr → {AM:[], MD:[], PM:[]}
  const byDayTod={};
  for(const w of wk.walks){
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

      let cellContent='';
      // Recalibration tag in AM cell
      if(isRecal&&ctod==='AM'){
        cellContent+=`<div class="cal-recal-tag">★ Recalibration — CCNY</div>`;
      }
      // Walk event cards
      for(const w of walks){
        const bpCls=w.backpack==='A'?'bpa':w.backpack==='B'?'bpb':'bpx';
        const compCls=w.source==='completed'?' completed':'';
        const bpLabel=w.backpack==='A'?'Backpack A':w.backpack==='B'?'Backpack B':'Legacy X';
        const routeLbl=ROUTE_LABELS[w.route]||w.route;
        const colLbl=CNAMES[w.collector]||w.collector||'—';
        const aid=w.source==='scheduled'?assignId(w):'';
        const badge=aid?statusBadgeHTML(aid):'';
        const actions=aid?`<div class="ce-actions">${actionBtns(aid)}</div>`:'';
        cellContent+=`<div class="cal-event ${bpCls}${compCls}">
          <div class="ce-bp">${bpLabel}</div>
          <div class="ce-route">${routeLbl}</div>
          <div class="ce-col">${colLbl}</div>
          ${badge}${actions}
        </div>`;
      }

      const cls=['cal-cell',
        isToday?'cal-today-col':'',
        isPast?'cal-past-col':'',
        isWeekend?'cal-weekend':''
      ].filter(Boolean).join(' ');
      html+=`<div class="${cls}">${cellContent}</div>`;
    }
  }

  grid.innerHTML=html;
}

// ─── EVENTS ───
function bindEvents(){
  document.querySelectorAll('.tab-btn').forEach(b=>b.addEventListener('click',()=>{
    document.querySelectorAll('.tab-btn').forEach(x=>x.classList.remove('active'));
    document.querySelectorAll('.view').forEach(x=>x.classList.remove('active'));
    b.classList.add('active');
    document.getElementById(b.dataset.view).classList.add('active');
    if(b.dataset.view==='map-view')setTimeout(()=>map&&map.invalidateSize(),50);
    else if(b.dataset.view==='schedule-view'){
      setTimeout(async()=>{
        await fetchConfirmations();
        initSchedMap();
        setTimeout(()=>schedMap.invalidateSize(),150);
        applyScheduleColors();
        renderSchedulePanel();
        renderTimelineBar();
        if(!schedData&&BAKED_SCHEDULE&&BAKED_SCHEDULE.assignments){
          loadScheduleJSON(JSON.stringify(BAKED_SCHEDULE));
        }
      },50);
    } else if(b.dataset.view==='calendar-view'){
      calWeekIdx=tlWeekIdx;
      fetchConfirmations().then(()=>renderCalendar());
    } else renderCV();
  }));
  document.getElementById('tl-play').addEventListener('click',playSchedule);
  document.getElementById('tl-next').addEventListener('click',()=>{
    if(schedPlaying){playSchedule();}
    const s=getSortedAssignments();
    setSchedStep(schedStep>=s.length-1?s.length-1:schedStep+1);
  });
  document.getElementById('tl-prev').addEventListener('click',()=>{
    if(schedPlaying){playSchedule();}
    setSchedStep(schedStep<=0?-1:schedStep-1);
  });
  document.getElementById('tl-reset').addEventListener('click',()=>{
    if(schedPlaying){playSchedule();}
    schedStep=-1;applyScheduleColors();renderTimelineBar();
    document.querySelectorAll('.sched-row').forEach(r=>r.style.outline='');
  });
  document.getElementById('tl-wk-prev').addEventListener('click',()=>{
    const weeks=buildTlWeeks();
    if(tlWeekIdx<weeks.length-1){tlWeekIdx++;schedStep=-1;applyScheduleColors();renderSchedulePanel();renderTimelineBar();}
  });
  document.getElementById('tl-wk-next').addEventListener('click',()=>{
    if(tlWeekIdx>0){tlWeekIdx--;schedStep=-1;applyScheduleColors();renderSchedulePanel();renderTimelineBar();}
  });
  document.getElementById('tl-wk-now').addEventListener('click',()=>{
    tlWeekIdx=findCurrentWeekIdx(buildTlWeeks());schedStep=-1;applyScheduleColors();renderSchedulePanel();renderTimelineBar();
  });
  document.getElementById('fseason').addEventListener('change',e=>{filters.season=e.target.value;applyFilters();});
  document.getElementById('ftod').addEventListener('change',e=>{filters.tod=e.target.value;applyFilters();});
  document.getElementById('fbp').addEventListener('change',e=>{filters.backpack=e.target.value;applyFilters();});
  document.getElementById('ffrom').addEventListener('change',e=>{filters.from=e.target.value?new Date(e.target.value+'T00:00:00'):null;applyFilters();});
  document.getElementById('fto').addEventListener('change',e=>{filters.to=e.target.value?new Date(e.target.value+'T23:59:59'):null;applyFilters();});
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
  document.getElementById('cal-today-btn').addEventListener('click',()=>{
    calWeekIdx=findCurrentWeekIdx(buildTlWeeks());renderCalendar();
  });
  document.getElementById('close-panel').addEventListener('click',closePanel);
  document.querySelectorAll('.wtab').forEach(b=>b.addEventListener('click',()=>{
    document.querySelectorAll('.wtab').forEach(x=>x.classList.remove('active'));
    b.classList.add('active');currentWin=b.dataset.win;renderComparison();
  }));

  // ── Scheduler auth ────────────────────────────────────────────────────────
  const unlockBtn=document.getElementById('sched-unlock-btn');
  const modalBg=document.getElementById('auth-modal-bg');
  const modalErr=document.getElementById('auth-modal-err');
  const pinInput=document.getElementById('auth-pin');

  function openAuthModal(){modalBg.classList.add('open');pinInput.value='';modalErr.style.display='none';pinInput.focus();}
  function closeAuthModal(){modalBg.classList.remove('open');}

  unlockBtn.addEventListener('click',()=>{
    if(schedAuth.unlocked){
      // Log out
      schedAuth={unlocked:false,scheduler:null,pin:null};
      document.body.classList.remove('scheduler-mode');
      unlockBtn.classList.remove('authed');
      unlockBtn.innerHTML='&#x1F511; Scheduler Mode';
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
        // Send a no-op "probe" — use a dummy id that won't match any real assignment
        body:JSON.stringify({id:'__probe__',status:'pending',scheduler:who,pin})
      });
      if(resp.status===403){
        modalErr.textContent='Incorrect PIN. Try again.';
        modalErr.style.display='block';
        pinInput.value='';pinInput.focus();
        return;
      }
    }catch(e){
      // Server unreachable — allow PIN-less mode for static viewing
    }
    schedAuth={unlocked:true,scheduler:who,pin};
    document.body.classList.add('scheduler-mode');
    unlockBtn.classList.add('authed');
    unlockBtn.innerHTML=`&#x1F511; ${who} <span style="font-size:8px;opacity:.7">(click to lock)</span>`;
    closeAuthModal();
    renderSchedulePanel();
    if(document.getElementById('calendar-view').classList.contains('active'))renderCalendar();
  });
}
// ─── INIT ───
async function init(){
  let src=null;
  try{
    const r=await fetch('Walks_Log.txt');
    if(r.ok){logText=await r.text();src='Walks_Log.txt';}
  }catch(e){}
  await fetchConfirmations();
  let schedLoaded=false;
  if(BAKED_SCHEDULE&&BAKED_SCHEDULE.assignments){
    schedData=BAKED_SCHEDULE;schedLoaded=true;loadScheduleJSON(JSON.stringify(BAKED_SCHEDULE));
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
    toast(msgs.join(' — '),'success');
  }catch(err){
    console.error('Dashboard init error:',err);
    document.querySelector('#loading p').textContent='Error: '+err.message;
    document.querySelector('#loading p').style.color='#f85149';
    return;
  }
  document.getElementById('loading').style.display='none';
}
// ─── LIVE GPS TRACKING ───
let gpsMarkers={}, gpsTrailLayers={};

function makeGpsIcon(bp,stale){
  const cls=stale?'gps-dot-stale':(bp==='BP_A'?'gps-dot-a':'gps-dot-b');
  return L.divIcon({className:'',iconSize:[14,14],iconAnchor:[7,7],
    html:`<div class="gps-dot ${cls}"></div>`});
}

function _relTime(isoTs){
  if(!isoTs)return'—';
  const sec=Math.round((Date.now()-new Date(isoTs).getTime())/1000);
  if(sec<5)return'just now';
  if(sec<60)return`${sec}s ago`;
  if(sec<3600)return`${Math.round(sec/60)}m ago`;
  return`${Math.round(sec/3600)}h ago`;
}

async function refreshGps(){
  if(!map)return;
  let data;
  try{
    const r=await fetch('/api/gps/status');
    if(!r.ok)throw new Error(r.status);
    data=await r.json();
  }catch(e){
    document.getElementById('gps-badge-a').textContent='BP-A: offline';
    document.getElementById('gps-badge-b').textContent='BP-B: offline';
    return;
  }
  for(const[bp,pos]of Object.entries(data)){
    const badgeId=bp==='BP_A'?'gps-badge-a':'gps-badge-b';
    const badge=document.getElementById(badgeId);
    if(!pos||pos.lat===null){
      badge.textContent=(bp==='BP_A'?'BP-A':'BP-B')+': no fix';
      badge.className='live-badge warn';
      continue;
    }
    const lbl=(bp==='BP_A'?'BP-A':'BP-B');
    if(pos.stale){
      badge.textContent=lbl+': stale';
      badge.className='live-badge err';
    }else{
      const spd=pos.speed!=null?` ${pos.speed.toFixed(1)}m/s`:'';
      badge.textContent=`${lbl}: live${spd}`;
      badge.className='live-badge ok';
    }
    badge.title=`${bp} | Last: ${pos.ts?new Date(pos.ts).toLocaleTimeString():'—'}${pos.batt!=null?' | Batt: '+pos.batt+'%':''}`;
    const latLng=L.latLng(pos.lat,pos.lon);
    if(gpsMarkers[bp]){
      gpsMarkers[bp].setLatLng(latLng);
      gpsMarkers[bp].setIcon(makeGpsIcon(bp,pos.stale));
    }else{
      const label=bp==='BP_A'?'Backpack A':'Backpack B';
      gpsMarkers[bp]=L.marker(latLng,{icon:makeGpsIcon(bp,pos.stale),zIndexOffset:1000})
        .bindPopup(`<b>${label}</b><br><small>Last: ${pos.ts?new Date(pos.ts).toLocaleTimeString():'—'}</small>${pos.speed!=null?'<br><small>Speed: '+pos.speed.toFixed(1)+' m/s</small>':''}${pos.batt!=null?'<br><small>Battery: '+pos.batt+'%</small>':''}`)
        .addTo(map);
    }
  }
  // Refresh trails
  for(const bp of['BP_A','BP_B']){
    try{
      const r=await fetch(`/api/gps/trail?id=${bp}`);
      if(!r.ok)continue;
      const trail=await r.json();
      if(trail.length<2)continue;
      const pts=trail.map(p=>[p.lat,p.lon]);
      if(gpsTrailLayers[bp]){gpsTrailLayers[bp].setLatLngs(pts);}
      else{
        const color=bp==='BP_A'?'#f85149':'#388bfd';
        gpsTrailLayers[bp]=L.polyline(pts,{color,weight:2,opacity:.45,dashArray:'4 4'}).addTo(map);
      }
    }catch(e){}
  }
}

// ─── DRIVE SYNC UI ───
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
      syncBtn.disabled=true;syncBtn.textContent='⏳';
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
      finally{syncBtn.disabled=false;syncBtn.textContent='↻ Sync';}
      refreshDriveStatus();
    });
  }
  // Poll GPS every 5s, Drive status every 30s
  setInterval(refreshGps,5000);
  setInterval(refreshDriveStatus,30000);
  refreshDriveStatus();
});

document.addEventListener('DOMContentLoaded',init);

// Filter drawer toggle — hamburger button is always visible on all screen sizes
document.addEventListener('DOMContentLoaded', function() {
  const menuBtn = document.getElementById('mobile-menu-btn');
  const filtersDrawer = document.getElementById('filters');

  function openFilters() {
    if (!filtersDrawer) return;
    filtersDrawer.classList.add('open');
    if (menuBtn) menuBtn.classList.add('filters-open');
    // Ensure drawer top offset matches current header height
    const hdr = document.getElementById('header');
    if (hdr) filtersDrawer.style.top = hdr.offsetHeight + 'px';
  }
  function closeFilters() {
    if (!filtersDrawer) return;
    filtersDrawer.classList.remove('open');
    if (menuBtn) menuBtn.classList.remove('filters-open');
  }
  function isFiltersOpen() {
    return filtersDrawer && filtersDrawer.classList.contains('open');
  }

  // Hamburger toggles filters drawer
  if (menuBtn && filtersDrawer) {
    menuBtn.addEventListener('click', function(e) {
      e.stopPropagation();
      isFiltersOpen() ? closeFilters() : openFilters();
    });
  }

  // Close drawer when clicking outside
  document.addEventListener('click', function(e) {
    if (!isFiltersOpen()) return;
    const isBtn = menuBtn && menuBtn.contains(e.target);
    const isDrawer = filtersDrawer && filtersDrawer.contains(e.target);
    if (!isBtn && !isDrawer) closeFilters();
  });

  // Close filters drawer when switching views
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', function() {
      setTimeout(closeFilters, 80);
    });
  });
});

</script>
<!-- ── Auth modal ── -->
<div id="auth-modal-bg">
  <div id="auth-modal">
    <h3>&#x1F511; Scheduler Mode</h3>
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
    <button id="auth-modal-submit">Unlock</button>
    <button id="auth-modal-cancel">Cancel</button>
  </div>
</div>
</body>
</html>
"""

# Replace placeholders
HTML_TEMPLATE = HTML_TEMPLATE.replace('__ROUTES_JSON__', routes_json)
HTML_TEMPLATE = HTML_TEMPLATE.replace('__AFFINITY_JSON__', affinity_json)
HTML_TEMPLATE = HTML_TEMPLATE.replace('__SAMPLE_LOG__', sample_log_js)
HTML_TEMPLATE = HTML_TEMPLATE.replace('__COLLECTOR_HOMES__', collector_homes_json)
HTML_TEMPLATE = HTML_TEMPLATE.replace('__BAKED_SCHEDULE__', baked_schedule_json)

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
    out = BASE / "dashboard.html"
    with open(out, 'w', encoding='utf-8') as f:
        f.write(HTML_TEMPLATE)
    size = out.stat().st_size
    print(f"dashboard.html written: {size:,} bytes ({size//1024} KB)")

if __name__ == "__main__":
    build()
