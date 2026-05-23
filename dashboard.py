#!/usr/bin/env python3
"""Build a self-contained HTML dashboard from the collected Civ 7 player-count CSV.

Renders three views: a time-series line chart (with Civ 6 overlay if present),
a day-of-week x hour-of-day heatmap ("weekly hotspots"), and summary statistics.
Uses only the standard library; charts are drawn client-side with Plotly loaded
from a CDN.

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

CIV7_CSV = Path(os.environ.get("CIV7_CSV", "data/civ7_players.csv"))
CIV6_CSV = Path(os.environ.get("CIV6_CSV", "data/civ6_players.csv"))
CIV7_HISTORY_CSV = Path(os.environ.get("CIV7_HISTORY_CSV", "data/civ7_history.csv"))
CIV6_HISTORY_CSV = Path(os.environ.get("CIV6_HISTORY_CSV", "data/civ6_history.csv"))
OUT_PATH = Path(os.environ.get("CIV7_DASHBOARD", "index.html"))
DISPLAY_TZ = os.environ.get("DISPLAY_TZ", "UTC")

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def load_rows(path: Path):
    if not path.exists():
        return []
    rows = []
    with path.open(newline="") as f:
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


def to_local(rows, tz):
    return [(ts.astimezone(tz), c) for ts, c in rows]


def series_payload(local):
    return {
        "x": [ts.isoformat() for ts, _ in local],
        "y": [c for _, c in local],
    }


def merge(history, live):
    # History from SteamCharts (older end is monthly-aggregated); live from
    # our 30-min collector. Prefer live whenever its samples overlap history.
    if not live:
        return history
    cutoff = live[0][0]
    return [r for r in history if r[0] < cutoff] + live


def build_payload(civ7_live, civ6_live, civ7_hist, civ6_hist):
    tz = ZoneInfo(DISPLAY_TZ)
    civ7_series = sorted(merge(civ7_hist, civ7_live), key=lambda r: r[0])
    civ6_series = sorted(merge(civ6_hist, civ6_live), key=lambda r: r[0])
    civ7_local = to_local(civ7_live, tz)  # heatmap + stats: live only

    buckets: dict[tuple[int, int], list[int]] = {}
    for ts, c in civ7_local:
        buckets.setdefault((ts.weekday(), ts.hour), []).append(c)
    z = [[None] * 24 for _ in range(7)]
    for (wd, hr), vals in buckets.items():
        z[wd][hr] = round(mean(vals))

    return {
        "civ7": series_payload(to_local(civ7_series, tz)),
        "civ6": series_payload(to_local(civ6_series, tz)),
        "heatmap": {"z": z, "weekdays": WEEKDAYS, "hours": list(range(24))},
        "stats": compute_stats(civ7_local),
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
  dragmode: false,
}};
const noZoomConfig = {{ responsive: true, displayModeBar: false, scrollZoom: false, doubleClick: false }};
const fixedAxis = {{ fixedrange: true }};

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

  const traces = [{{
    x: DATA.civ7.x, y: DATA.civ7.y, type: 'scatter', mode: 'lines',
    line: {{ color: '#4da3ff', width: 2 }}, fill: 'tozeroy',
    fillcolor: 'rgba(77,163,255,0.12)', name: 'Civ VII',
  }}];
  if (DATA.civ6.x.length) {{
    traces.push({{
      x: DATA.civ6.x, y: DATA.civ6.y, type: 'scatter', mode: 'lines',
      line: {{ color: '#ff9f4d', width: 2, dash: 'solid' }}, name: 'Civ VI',
    }});
  }}
  Plotly.newPlot('timeseries', traces, Object.assign({{}}, layoutBase, {{
    title: 'Players over time',
    xaxis: Object.assign({{ gridcolor: '#222', title: '' }}, fixedAxis),
    yaxis: Object.assign({{ gridcolor: '#222', title: 'Players in game', rangemode: 'tozero' }}, fixedAxis),
    showlegend: true,
    legend: {{ orientation: 'h', y: 1.08, x: 0 }},
  }}), noZoomConfig);

  Plotly.newPlot('heatmap', [{{
    z: DATA.heatmap.z, x: DATA.heatmap.hours, y: DATA.heatmap.weekdays,
    type: 'heatmap', colorscale: 'YlOrRd', hoverongaps: false,
    colorbar: {{ title: 'Avg players' }},
    hovertemplate: '%{{y}} %{{x}}:00<br>avg %{{z:,}} players<extra></extra>',
  }}], Object.assign({{}}, layoutBase, {{
    title: 'Weekly hotspots (Civ VII, average by day &amp; hour)',
    xaxis: Object.assign({{ title: 'Hour of day', dtick: 2, gridcolor: '#222' }}, fixedAxis),
    yaxis: Object.assign({{ title: '', autorange: 'reversed' }}, fixedAxis),
  }}), noZoomConfig);
}}
</script>
</body>
</html>
"""


def main() -> int:
    civ7_live = load_rows(CIV7_CSV)
    civ6_live = load_rows(CIV6_CSV)
    civ7_hist = load_rows(CIV7_HISTORY_CSV)
    civ6_hist = load_rows(CIV6_HISTORY_CSV)
    payload = build_payload(civ7_live, civ6_live, civ7_hist, civ6_hist)
    OUT_PATH.write_text(render_html(payload), encoding="utf-8")
    print(
        f"Wrote {OUT_PATH} from civ7={len(civ7_live)}+hist {len(civ7_hist)} "
        f"civ6={len(civ6_live)}+hist {len(civ6_hist)} rows (tz={DISPLAY_TZ})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
