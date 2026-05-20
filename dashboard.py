#!/usr/bin/env python3
"""Build a self-contained HTML dashboard from the collected Civ 7 player-count CSV.

Renders three views: a time-series line chart, a day-of-week x hour-of-day
heatmap ("weekly hotspots"), and summary statistics. Uses only the standard
library; charts are drawn client-side with Plotly loaded from a CDN.

Set DISPLAY_TZ (an IANA name like "Europe/London" or "America/New_York") to
bucket the heatmap/hours in local time. Defaults to UTC.
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

CSV_PATH = Path(os.environ.get("CIV7_CSV", "data/civ7_players.csv"))
OUT_PATH = Path(os.environ.get("CIV7_DASHBOARD", "dashboard.html"))
DISPLAY_TZ = os.environ.get("DISPLAY_TZ", "UTC")

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def load_rows():
    if not CSV_PATH.exists():
        return []
    rows = []
    with CSV_PATH.open(newline="") as f:
        for row in csv.DictReader(f):
            try:
                ts = datetime.fromisoformat(row["timestamp_utc"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                count = int(row["player_count"])
            except (KeyError, ValueError):
                continue
            rows.append((ts, count))
    rows.sort(key=lambda r: r[0])
    return rows


def build_payload(rows):
    tz = ZoneInfo(DISPLAY_TZ)
    local = [(ts.astimezone(tz), c) for ts, c in rows]

    series = {
        "x": [ts.isoformat() for ts, _ in local],
        "y": [c for _, c in local],
    }

    # Heatmap: average player count per (weekday, hour) bucket.
    buckets: dict[tuple[int, int], list[int]] = {}
    for ts, c in local:
        buckets.setdefault((ts.weekday(), ts.hour), []).append(c)
    z = [[None] * 24 for _ in range(7)]
    for (wd, hr), vals in buckets.items():
        z[wd][hr] = round(mean(vals))

    stats = compute_stats(local)
    return {
        "series": series,
        "heatmap": {"z": z, "weekdays": WEEKDAYS, "hours": list(range(24))},
        "stats": stats,
        "tz": DISPLAY_TZ,
        "generated": datetime.now(tz).replace(microsecond=0).isoformat(),
    }


def compute_stats(local):
    if not local:
        return {}
    counts = [c for _, c in local]
    peak_ts, peak = max(local, key=lambda r: r[1])
    low_ts, low = min(local, key=lambda r: r[1])

    by_weekday: dict[int, list[int]] = {}
    by_hour: dict[int, list[int]] = {}
    for ts, c in local:
        by_weekday.setdefault(ts.weekday(), []).append(c)
        by_hour.setdefault(ts.hour, []).append(c)
    busiest_wd = max(by_weekday, key=lambda k: mean(by_weekday[k]))
    busiest_hr = max(by_hour, key=lambda k: mean(by_hour[k]))

    return {
        "samples": len(local),
        "first": local[0][0].isoformat(),
        "last": local[-1][0].isoformat(),
        "latest": counts[-1],
        "peak": peak,
        "peak_at": peak_ts.isoformat(),
        "low": low,
        "low_at": low_ts.isoformat(),
        "average": round(mean(counts)),
        "busiest_day": WEEKDAYS[busiest_wd],
        "busiest_hour": f"{busiest_hr:02d}:00",
    }


def render_html(payload) -> str:
    data_json = json.dumps(payload)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Civ VII Player Tracker</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js" charset="utf-8"></script>
<style>
  :root {{ color-scheme: dark; }}
  body {{ font-family: system-ui, sans-serif; margin: 0; background:#0f1419; color:#e6e6e6; }}
  header {{ padding: 20px 24px; border-bottom: 1px solid #2a2f3a; }}
  h1 {{ margin: 0 0 4px; font-size: 20px; }}
  .meta {{ color:#8a93a2; font-size: 13px; }}
  .cards {{ display:flex; flex-wrap:wrap; gap:12px; padding:20px 24px; }}
  .card {{ background:#1a212c; border:1px solid #2a2f3a; border-radius:10px; padding:14px 18px; min-width:140px; }}
  .card .label {{ color:#8a93a2; font-size:12px; text-transform:uppercase; letter-spacing:.04em; }}
  .card .value {{ font-size:24px; font-weight:600; margin-top:4px; }}
  .card .sub {{ color:#8a93a2; font-size:12px; margin-top:2px; }}
  .chart {{ padding: 8px 24px 24px; }}
  .empty {{ padding:40px 24px; color:#8a93a2; }}
</style>
</head>
<body>
<header>
  <h1>Sid Meier's Civilization VII &mdash; Concurrent Players</h1>
  <div class="meta" id="meta"></div>
</header>
<div id="cards" class="cards"></div>
<div class="chart"><div id="timeseries"></div></div>
<div class="chart"><div id="heatmap"></div></div>
<script>
const DATA = {data_json};

const layoutBase = {{
  paper_bgcolor: '#0f1419', plot_bgcolor: '#0f1419',
  font: {{ color: '#e6e6e6' }}, margin: {{ t: 40, r: 20, b: 50, l: 60 }},
}};

function fmt(n) {{ return n == null ? '—' : n.toLocaleString(); }}
function when(iso) {{ return iso ? iso.replace('T', ' ').slice(0, 16) : '—'; }}

const s = DATA.stats;
document.getElementById('meta').textContent =
  s.samples ? `${{fmt(s.samples)}} samples · ${{when(s.first)}} → ${{when(s.last)}} · times in ${{DATA.tz}} · generated ${{when(DATA.generated)}}`
            : `No data yet · times in ${{DATA.tz}}`;

if (!s.samples) {{
  document.getElementById('cards').innerHTML =
    '<div class="empty">No data collected yet. Run <code>collect.py</code> (or wait for the scheduled workflow) to start gathering player counts.</div>';
}} else {{
  const cards = [
    {{ label: 'Latest', value: fmt(s.latest) }},
    {{ label: 'All-time peak', value: fmt(s.peak), sub: when(s.peak_at) }},
    {{ label: 'Average', value: fmt(s.average) }},
    {{ label: 'Low', value: fmt(s.low), sub: when(s.low_at) }},
    {{ label: 'Busiest day', value: s.busiest_day }},
    {{ label: 'Busiest hour', value: s.busiest_hour }},
  ];
  document.getElementById('cards').innerHTML = cards.map(c =>
    `<div class="card"><div class="label">${{c.label}}</div>` +
    `<div class="value">${{c.value}}</div>` +
    `<div class="sub">${{c.sub || ''}}</div></div>`).join('');

  Plotly.newPlot('timeseries', [{{
    x: DATA.series.x, y: DATA.series.y, type: 'scatter', mode: 'lines',
    line: {{ color: '#4da3ff', width: 2 }}, fill: 'tozeroy',
    fillcolor: 'rgba(77,163,255,0.12)', name: 'Players',
  }}], Object.assign({{}}, layoutBase, {{
    title: 'Players over time',
    xaxis: {{ gridcolor: '#222', title: '' }},
    yaxis: {{ gridcolor: '#222', title: 'Players in game', rangemode: 'tozero' }},
  }}), {{ responsive: true, displayModeBar: false }});

  Plotly.newPlot('heatmap', [{{
    z: DATA.heatmap.z, x: DATA.heatmap.hours, y: DATA.heatmap.weekdays,
    type: 'heatmap', colorscale: 'YlOrRd', hoverongaps: false,
    colorbar: {{ title: 'Avg players' }},
    hovertemplate: '%{{y}} %{{x}}:00<br>avg %{{z:,}} players<extra></extra>',
  }}], Object.assign({{}}, layoutBase, {{
    title: 'Weekly hotspots (average by day &amp; hour)',
    xaxis: {{ title: 'Hour of day', dtick: 2, gridcolor: '#222' }},
    yaxis: {{ title: '', autorange: 'reversed' }},
  }}), {{ responsive: true, displayModeBar: false }});
}}
</script>
</body>
</html>
"""


def main() -> int:
    rows = load_rows()
    payload = build_payload(rows)
    OUT_PATH.write_text(render_html(payload), encoding="utf-8")
    print(f"Wrote {OUT_PATH} from {len(rows)} rows (tz={DISPLAY_TZ})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
