#!/usr/bin/env python3
"""
Branch divergence monitor — generates branch_monitor.html at repo root.
Run from anywhere: python scripts/dev/build_branch_monitor.py
"""
import subprocess, os, html as _html
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT  = os.path.join(REPO, "branch_monitor.html")
BASE = "origin/main"

GROUP_STYLE = {
    "main":    {"color": "#22c55e", "dark": "#14532d", "label": "Main"},
    "dev":     {"color": "#3b82f6", "dark": "#1e3a5f", "label": "Dev"},
    "feature": {"color": "#f97316", "dark": "#431407", "label": "Feature"},
    "claude":  {"color": "#a855f7", "dark": "#2e1065", "label": "Claude"},
    "other":   {"color": "#94a3b8", "dark": "#1e293b", "label": "Other"},
}


def git(*args):
    r = subprocess.run(["git"] + list(args), capture_output=True, text=True, cwd=REPO)
    return r.stdout.strip()


def classify(name):
    if name in ("main", ""):
        return "main"
    prefix = name.split("/")[0]
    return prefix if prefix in GROUP_STYLE else "other"


def fetch_branches():
    raw = git("branch", "-r")
    refs = []
    for line in raw.splitlines():
        ref = line.strip()
        if not ref or "HEAD" in ref or "->" in ref:
            continue
        refs.append(ref)

    branches = []
    for ref in refs:
        name = ref.replace("origin/", "", 1)
        sha  = git("rev-parse", "--short", ref)
        ahead  = int(git("rev-list", f"{BASE}..{ref}", "--count") or 0)
        behind = int(git("rev-list", f"{ref}..{BASE}", "--count") or 0)
        log_raw = git("log", "-1", "--format=%ci|||%s|||%an", ref).split("|||")
        date_str = log_raw[0].strip()[:10] if log_raw else ""
        msg_raw  = log_raw[1].strip() if len(log_raw) > 1 else ""
        msg      = (msg_raw[:62] + "…") if len(msg_raw) > 62 else msg_raw
        author   = log_raw[2].strip() if len(log_raw) > 2 else ""
        branches.append({
            "name": name, "ref": ref, "sha": sha,
            "ahead": ahead, "behind": behind,
            "date": date_str, "msg": msg, "author": author,
            "group": classify(name),
            "synced": ahead == 0 and behind == 0,
        })

    order = ["main", "dev", "feature", "claude", "other"]
    branches.sort(key=lambda b: (order.index(b["group"]) if b["group"] in order else 99, b["name"]))
    return branches


def make_svg(branches):
    ROW_H    = 52
    PAD_TOP  = 40
    PAD_BOT  = 20
    PAD_L    = 220   # left label area
    PAD_R    = 100   # right count area
    SCALE    = 5     # px per commit
    CENTER_X = 340   # px from left edge of chart area to center line
    CHART_W  = 700   # total chart drawing area width
    SVG_W    = PAD_L + CHART_W + PAD_R

    max_ahead  = max((b["ahead"]  for b in branches), default=1) or 1
    max_behind = max((b["behind"] for b in branches), default=1) or 1
    # scale so the longest bar fills ~half the chart area comfortably
    ahead_scale  = min(SCALE, (CENTER_X - 20) / max(max_ahead,  1))
    behind_scale = min(SCALE, (CHART_W - CENTER_X - 20) / max(max_behind, 1))

    h = PAD_TOP + len(branches) * ROW_H + PAD_BOT
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{SVG_W}" height="{h}" '
        f'viewBox="0 0 {SVG_W} {h}" style="display:block;max-width:100%">'
    ]

    # background
    lines.append(f'<rect width="{SVG_W}" height="{h}" fill="#0f172a"/>')

    cx = PAD_L + CENTER_X  # absolute center-line x

    # column headers
    lines.append(f'<text x="{PAD_L + 4}" y="24" fill="#64748b" font-size="11" font-family="monospace">← behind main</text>')
    lines.append(f'<text x="{cx + 6}" y="24" fill="#64748b" font-size="11" font-family="monospace">ahead of main →</text>')

    # center line
    lines.append(
        f'<line x1="{cx}" y1="{PAD_TOP - 8}" x2="{cx}" y2="{h - PAD_BOT + 4}" '
        f'stroke="#334155" stroke-width="1" stroke-dasharray="4,3"/>'
    )

    for i, b in enumerate(branches):
        y_mid = PAD_TOP + i * ROW_H + ROW_H // 2
        color = GROUP_STYLE[b["group"]]["color"]
        y_bar_top    = y_mid - 11
        bar_h        = 22

        # row separator
        if i > 0:
            lines.append(
                f'<line x1="{PAD_L}" y1="{PAD_TOP + i * ROW_H}" '
                f'x2="{SVG_W - PAD_R + 60}" y2="{PAD_TOP + i * ROW_H}" '
                f'stroke="#1e293b" stroke-width="1"/>'
            )

        # branch name label
        display = _html.escape(b["name"])
        lines.append(
            f'<text x="{PAD_L - 8}" y="{y_mid + 4}" fill="{color}" '
            f'font-size="12" font-family="monospace" text-anchor="end">{display}</text>'
        )

        if b["synced"]:
            # diamond at center for synced branches
            d = 7
            pts = f"{cx},{y_mid - d} {cx + d},{y_mid} {cx},{y_mid + d} {cx - d},{y_mid}"
            lines.append(f'<polygon points="{pts}" fill="{color}" opacity="0.9"/>')
            lines.append(
                f'<text x="{cx + d + 4}" y="{y_mid + 4}" fill="{color}" '
                f'font-size="11" font-family="monospace" opacity="0.7">synced</text>'
            )
        else:
            # behind bar (extends LEFT from center)
            if b["behind"] > 0:
                bw = max(b["behind"] * behind_scale, 4)
                lines.append(
                    f'<rect x="{cx - bw}" y="{y_bar_top}" width="{bw}" height="{bar_h}" '
                    f'fill="#ef4444" opacity="0.75" rx="3"/>'
                )
                if b["behind"] > 0:
                    lx = cx - bw - 4
                    lines.append(
                        f'<text x="{lx}" y="{y_mid + 4}" fill="#f87171" '
                        f'font-size="11" font-family="monospace" text-anchor="end">-{b["behind"]}</text>'
                    )

            # ahead bar (extends RIGHT from center)
            if b["ahead"] > 0:
                aw = max(b["ahead"] * ahead_scale, 4)
                lines.append(
                    f'<rect x="{cx}" y="{y_bar_top}" width="{aw}" height="{bar_h}" '
                    f'fill="{color}" opacity="0.8" rx="3"/>'
                )
                lines.append(
                    f'<text x="{cx + aw + 4}" y="{y_mid + 4}" fill="{color}" '
                    f'font-size="11" font-family="monospace">+{b["ahead"]}</text>'
                )

        # SHA + date on right
        meta = f'{b["sha"]}  {b["date"]}'
        lines.append(
            f'<text x="{SVG_W - PAD_R + 62}" y="{y_mid + 4}" fill="#475569" '
            f'font-size="10" font-family="monospace">{_html.escape(meta)}</text>'
        )

    lines.append("</svg>")
    return "\n".join(lines)


def make_cards(branches):
    groups_order = ["main", "dev", "feature", "claude", "other"]
    from collections import defaultdict
    by_group = defaultdict(list)
    for b in branches:
        by_group[b["group"]].append(b)

    parts = []
    for g in groups_order:
        if g not in by_group:
            continue
        gs = GROUP_STYLE[g]
        parts.append(f'<div class="group-section">')
        parts.append(
            f'<div class="group-label" style="color:{gs["color"]};border-left:3px solid {gs["color"]}">'
            f'  {gs["label"]} branches</div>'
        )
        parts.append('<div class="cards-row">')
        for b in by_group[g]:
            color = gs["color"]
            dark  = gs["dark"]
            status_cls = "synced" if b["synced"] else ("ahead" if b["ahead"] > 0 else "behind")
            if not b["synced"] and b["ahead"] > 0 and b["behind"] > 0:
                status_cls = "diverged"

            if b["synced"]:
                badge = '<span class="badge synced-badge">✓ synced</span>'
            elif status_cls == "diverged":
                badge = f'<span class="badge diverged-badge">⚡ {b["ahead"]}↑ {b["behind"]}↓</span>'
            elif b["ahead"] > 0:
                badge = f'<span class="badge ahead-badge">↑ {b["ahead"]} ahead</span>'
            else:
                badge = f'<span class="badge behind-badge">↓ {b["behind"]} behind</span>'

            parts.append(f'''
<div class="card" style="border-top:3px solid {color};background:{dark}20">
  <div class="card-name" style="color:{color}">{_html.escape(b["name"])}</div>
  <div class="card-sha">{_html.escape(b["sha"])}</div>
  {badge}
  <div class="card-msg" title="{_html.escape(b["msg"])}">{_html.escape(b["msg"])}</div>
  <div class="card-meta">{_html.escape(b["date"])}  ·  {_html.escape(b["author"])}</div>
</div>''')
        parts.append("</div></div>")
    return "\n".join(parts)


def generate(branches):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total    = len(branches)
    synced   = sum(1 for b in branches if b["synced"])
    diverged = total - synced
    most_div = max(branches, key=lambda b: b["ahead"] + b["behind"], default=None)
    most_div_str = (
        f'{_html.escape(most_div["name"])} (+{most_div["ahead"]} / -{most_div["behind"]})'
        if most_div and not most_div["synced"] else "—"
    )

    svg   = make_svg(branches)
    cards = make_cards(branches)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Branch Divergence Monitor</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: #0a0f1a;
    color: #e2e8f0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    min-height: 100vh;
    padding: 32px 24px;
  }}
  h1 {{
    font-size: 20px;
    font-weight: 600;
    letter-spacing: 0.02em;
    color: #f8fafc;
    margin-bottom: 4px;
  }}
  .subtitle {{
    font-size: 12px;
    color: #475569;
    margin-bottom: 28px;
    font-family: monospace;
  }}
  .stats-row {{
    display: flex;
    gap: 16px;
    margin-bottom: 32px;
    flex-wrap: wrap;
  }}
  .stat-box {{
    background: #0f172a;
    border: 1px solid #1e293b;
    border-radius: 8px;
    padding: 14px 20px;
    min-width: 160px;
  }}
  .stat-label {{
    font-size: 11px;
    color: #64748b;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 6px;
  }}
  .stat-value {{
    font-size: 22px;
    font-weight: 700;
    color: #f1f5f9;
    font-family: monospace;
  }}
  .stat-sub {{
    font-size: 11px;
    color: #475569;
    margin-top: 4px;
    font-family: monospace;
  }}
  .chart-section {{
    background: #0f172a;
    border: 1px solid #1e293b;
    border-radius: 10px;
    padding: 20px;
    margin-bottom: 36px;
    overflow-x: auto;
  }}
  .section-title {{
    font-size: 13px;
    font-weight: 600;
    color: #94a3b8;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 16px;
  }}
  .group-section {{
    margin-bottom: 28px;
  }}
  .group-label {{
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    padding-left: 8px;
    margin-bottom: 12px;
  }}
  .cards-row {{
    display: flex;
    gap: 12px;
    flex-wrap: wrap;
  }}
  .card {{
    background: #0f172a;
    border: 1px solid #1e293b;
    border-radius: 8px;
    padding: 14px 16px;
    min-width: 220px;
    max-width: 300px;
    flex: 1;
  }}
  .card-name {{
    font-family: monospace;
    font-size: 13px;
    font-weight: 600;
    margin-bottom: 4px;
    word-break: break-all;
  }}
  .card-sha {{
    font-family: monospace;
    font-size: 11px;
    color: #475569;
    margin-bottom: 8px;
  }}
  .badge {{
    display: inline-block;
    font-size: 11px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 4px;
    margin-bottom: 10px;
    font-family: monospace;
  }}
  .synced-badge   {{ background: #14532d; color: #4ade80; }}
  .ahead-badge    {{ background: #1e3a5f; color: #60a5fa; }}
  .behind-badge   {{ background: #450a0a; color: #f87171; }}
  .diverged-badge {{ background: #2e1065; color: #c084fc; }}
  .card-msg {{
    font-size: 11px;
    color: #94a3b8;
    margin-bottom: 6px;
    font-style: italic;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }}
  .card-meta {{
    font-size: 10px;
    color: #475569;
    font-family: monospace;
  }}
  hr {{
    border: none;
    border-top: 1px solid #1e293b;
    margin: 32px 0;
  }}
</style>
</head>
<body>

<h1>Branch Divergence Monitor</h1>
<div class="subtitle">NASA EnAACT Field Campaign Monitor · generated {now}</div>

<div class="stats-row">
  <div class="stat-box">
    <div class="stat-label">Total branches</div>
    <div class="stat-value">{total}</div>
  </div>
  <div class="stat-box">
    <div class="stat-label">Synced with main</div>
    <div class="stat-value" style="color:#22c55e">{synced}</div>
  </div>
  <div class="stat-box">
    <div class="stat-label">Diverged</div>
    <div class="stat-value" style="color:#f97316">{diverged}</div>
  </div>
  <div class="stat-box" style="min-width:280px">
    <div class="stat-label">Most diverged</div>
    <div class="stat-value" style="font-size:13px;padding-top:4px;color:#a855f7">{most_div_str}</div>
  </div>
</div>

<div class="chart-section">
  <div class="section-title">Commit divergence from main</div>
  {svg}
</div>

<div class="section-title">Branch details</div>
{cards}

</body>
</html>"""


def main():
    print("Fetching branch data…")
    branches = fetch_branches()
    print(f"  {len(branches)} branches found")
    for b in branches:
        status = "synced" if b["synced"] else f"+{b['ahead']} / -{b['behind']}"
        print(f"  {b['name']:<45} {status}")
    print(f"\nBuilding {OUT} …")
    html = generate(branches)
    with open(OUT, "w") as f:
        f.write(html)
    print(f"Done — {len(html):,} bytes written")


if __name__ == "__main__":
    main()
