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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

CIV7_CSV = Path(os.environ.get("CIV7_CSV", "data/civ7_players.csv"))
CIV6_CSV = Path(os.environ.get("CIV6_CSV", "data/civ6_players.csv"))
CIV7_HISTORY_CSV = Path(os.environ.get("CIV7_HISTORY_CSV", "data/civ7_history.csv"))
CIV6_HISTORY_CSV = Path(os.environ.get("CIV6_HISTORY_CSV", "data/civ6_history.csv"))
OUT_PATH = Path(os.environ.get("CIV7_DASHBOARD", "index.html"))
DISPLAY_TZ = os.environ.get("DISPLAY_TZ", "UTC")
START_DATE = datetime.fromisoformat(os.environ.get("START_DATE", "2025-01-01")).replace(tzinfo=timezone.utc)

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


def heatmap_z(local):
    buckets: dict[tuple[int, int], list[int]] = {}
    for ts, c in local:
        buckets.setdefault((ts.weekday(), ts.hour), []).append(c)
    z = [[None] * 24 for _ in range(7)]
    for (wd, hr), vals in buckets.items():
        z[wd][hr] = round(mean(vals))
    return z


def recent_window(local, days):
    if not local:
        return []
    cutoff = local[-1][0] - timedelta(days=days)
    return [(ts, c) for ts, c in local if ts >= cutoff]


def _peak_hour(rows):
    by_hour: dict[int, list[int]] = {}
    for ts, c in rows:
        by_hour.setdefault(ts.hour, []).append(c)
    if not by_hour:
        return None, None
    h = max(by_hour, key=lambda k: mean(by_hour[k]))
    return h, round(mean(by_hour[h]))


def _pct(cur, prev):
    if cur is None or prev is None or prev == 0:
        return None
    return round((cur - prev) / prev * 100, 1)


def compute_comparison(local):
    """Last 7 complete local days vs the 7 before them. Anchored on the most
    recent fully-completed local day so both windows are apples-to-apples."""
    if len(local) < 2:
        return {}
    cur_end = local[-1][0].replace(hour=0, minute=0, second=0, microsecond=0)
    cur_start = cur_end - timedelta(days=7)
    prev_start = cur_start - timedelta(days=7)
    cur = [(ts, c) for ts, c in local if cur_start <= ts < cur_end]
    prev = [(ts, c) for ts, c in local if prev_start <= ts < cur_start]
    if not cur:
        return {}

    cur_avg = round(mean(c for _, c in cur))
    prev_avg = round(mean(c for _, c in prev)) if prev else None
    cur_peak = max(c for _, c in cur)
    prev_peak = max((c for _, c in prev), default=None)
    cur_ph, cur_pha = _peak_hour(cur)
    prev_ph, prev_pha = _peak_hour(prev)

    by_weekday = []
    for wd in range(7):
        cv = [c for ts, c in cur if ts.weekday() == wd]
        pv = [c for ts, c in prev if ts.weekday() == wd]
        cur_v = round(mean(cv)) if cv else None
        prev_v = round(mean(pv)) if pv else None
        by_weekday.append({
            "weekday": WEEKDAYS[wd],
            "cur": cur_v, "prev": prev_v, "pct": _pct(cur_v, prev_v),
        })

    return {
        "cur_label": f"{cur_start.date().isoformat()} → {(cur_end - timedelta(days=1)).date().isoformat()}",
        "prev_label": f"{prev_start.date().isoformat()} → {(cur_start - timedelta(days=1)).date().isoformat()}",
        "avg": {"cur": cur_avg, "prev": prev_avg, "pct": _pct(cur_avg, prev_avg)},
        "peak": {"cur": cur_peak, "prev": prev_peak, "pct": _pct(cur_peak, prev_peak)},
        "peak_hour": {
            "cur": cur_ph, "prev": prev_ph,
            "cur_avg": cur_pha, "prev_avg": prev_pha,
            "shift": (cur_ph - prev_ph) if (cur_ph is not None and prev_ph is not None) else None,
        },
        "by_weekday": by_weekday,
    }


def multi_week_profiles(local, n_weeks=4):
    """For each of the last N complete weeks, return the 24-hour profile
    (avg players per hour-of-day) for overlay comparison."""
    if len(local) < 2:
        return []
    cur_end = local[-1][0].replace(hour=0, minute=0, second=0, microsecond=0)
    labels = {0: "Last 7 days", 1: "Prior week", 2: "2 weeks ago", 3: "3 weeks ago"}
    out = []
    for w in range(n_weeks):
        end = cur_end - timedelta(days=7 * w)
        start = end - timedelta(days=7)
        rows = [(ts, c) for ts, c in local if start <= ts < end]
        if not rows:
            continue
        by_hour: dict[int, list[int]] = {}
        for ts, c in rows:
            by_hour.setdefault(ts.hour, []).append(c)
        avg = [round(mean(by_hour[h])) if h in by_hour else None for h in range(24)]
        out.append({
            "label": labels.get(w, f"{w+1} weeks ago"),
            "avg": avg,
            "range": f"{start.date()} → {(end - timedelta(days=1)).date()}",
        })
    return out


def build_payload(civ7_live, civ6_live, civ7_hist, civ6_hist):
    tz = ZoneInfo(DISPLAY_TZ)
    civ7_series = [r for r in sorted(merge(civ7_hist, civ7_live), key=lambda r: r[0]) if r[0] >= START_DATE]
    civ6_series = [r for r in sorted(merge(civ6_hist, civ6_live), key=lambda r: r[0]) if r[0] >= START_DATE]
    civ7_merged_local = to_local(civ7_series, tz)
    civ6_merged_local = to_local(civ6_series, tz)
    # Heatmaps + comparisons use the hourly-dense tail (last 28 days). Older
    # SteamCharts history is monthly-aggregated and would distort the buckets.
    civ7_recent = recent_window(civ7_merged_local, 28)
    civ6_recent = recent_window(civ6_merged_local, 28)

    return {
        "civ7": series_payload(civ7_merged_local),
        "civ6": series_payload(civ6_merged_local),
        "heatmap_civ7": {"z": heatmap_z(civ7_recent), "weekdays": WEEKDAYS, "hours": list(range(24))},
        "heatmap_civ6": {"z": heatmap_z(civ6_recent), "weekdays": WEEKDAYS, "hours": list(range(24))},
        "stats": compute_stats(civ7_recent),
        "comparison": compute_comparison(civ7_recent),
        "weekly_profiles": multi_week_profiles(civ7_recent, n_weeks=4),
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
  .card .delta {{ font-size:13px; margin-top:4px; font-weight:600; }}
  .delta.up {{ color:#4cc38a; }}
  .delta.down {{ color:#ff6b6b; }}
  .delta.flat {{ color:#8a93a2; }}
  .section {{ padding: 4px 24px 16px; }}
  .section h2 {{ font-size:14px; text-transform:uppercase; letter-spacing:.05em; color:#8a93a2; margin:8px 0 10px; font-weight:600; }}
  .section .window {{ font-size:12px; color:#8a93a2; margin-bottom:12px; }}
  table.weekday {{ border-collapse:collapse; width:100%; max-width:560px; background:#1a212c; border:1px solid #2a2f3a; border-radius:10px; overflow:hidden; }}
  table.weekday th, table.weekday td {{ padding:8px 12px; text-align:right; border-bottom:1px solid #2a2f3a; font-size:13px; }}
  table.weekday th {{ background:#141a23; color:#8a93a2; font-weight:600; text-transform:uppercase; font-size:11px; letter-spacing:.04em; }}
  table.weekday th:first-child, table.weekday td:first-child {{ text-align:left; }}
  table.weekday tr:last-child td {{ border-bottom:none; }}
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
<section class="section" id="comparison"></section>
<section class="section" id="weekday"></section>
<div class="chart"><div id="timeseries"></div></div>
<div class="chart"><div id="profiles"></div></div>
<div class="chart"><div id="heatmap_civ7"></div></div>
<div class="chart"><div id="heatmap_civ6"></div></div>
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
function hr(h) {{ return h == null ? '—' : String(h).padStart(2, '0') + ':00'; }}
function deltaHTML(pct) {{
  if (pct == null) return '<span class="delta flat">vs prior week: no data</span>';
  if (pct === 0)  return '<span class="delta flat">unchanged vs prior week</span>';
  const cls = pct > 0 ? 'up' : 'down';
  const arrow = pct > 0 ? '▲' : '▼';
  return `<span class="delta ${{cls}}">${{arrow}} ${{Math.abs(pct).toFixed(1)}}% vs prior week</span>`;
}}
function deltaCell(pct) {{
  if (pct == null) return '<td class="delta flat">—</td>';
  if (pct === 0)  return '<td class="delta flat">0%</td>';
  const cls = pct > 0 ? 'up' : 'down';
  const arrow = pct > 0 ? '▲' : '▼';
  return `<td class="delta ${{cls}}">${{arrow}} ${{Math.abs(pct).toFixed(1)}}%</td>`;
}}

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
    line: {{ color: '#ff9f4d', width: 2 }}, fill: 'tozeroy',
    fillcolor: 'rgba(255,159,77,0.12)', name: 'Civ VII',
  }}];
  if (DATA.civ6.x.length) {{
    traces.push({{
      x: DATA.civ6.x, y: DATA.civ6.y, type: 'scatter', mode: 'lines',
      line: {{ color: '#4da3ff', width: 2 }}, name: 'Civ VI',
    }});
  }}
  Plotly.newPlot('timeseries', traces, Object.assign({{}}, layoutBase, {{
    title: 'Players over time',
    xaxis: Object.assign({{ gridcolor: '#222', title: '' }}, fixedAxis),
    yaxis: Object.assign({{ gridcolor: '#222', title: 'Players in game', rangemode: 'tozero' }}, fixedAxis),
    showlegend: true,
    legend: {{ orientation: 'h', y: 1.08, x: 0 }},
  }}), noZoomConfig);

  function drawHeatmap(divId, hm, colorscale, title) {{
    const hasData = hm.z.some(row => row.some(v => v != null));
    if (!hasData) {{
      document.getElementById(divId).innerHTML =
        `<div class="empty" style="padding:24px 0;">${{title}} — no live samples yet (collector needs to run a few times to populate).</div>`;
      return;
    }}
    Plotly.newPlot(divId, [{{
      z: hm.z, x: hm.hours, y: hm.weekdays,
      type: 'heatmap', colorscale: colorscale, hoverongaps: false,
      colorbar: {{ title: 'Avg players' }},
      hovertemplate: '%{{y}} %{{x}}:00<br>avg %{{z:,}} players<extra></extra>',
    }}], Object.assign({{}}, layoutBase, {{
      title: title,
      xaxis: Object.assign({{ title: 'Hour of day', dtick: 2, gridcolor: '#222' }}, fixedAxis),
      yaxis: Object.assign({{ title: '', autorange: 'reversed' }}, fixedAxis),
    }}), noZoomConfig);
  }}
  drawHeatmap('heatmap_civ7', DATA.heatmap_civ7, 'YlOrRd', 'Weekly hotspots — Civ VII (average by day &amp; hour, last 28 days)');
  drawHeatmap('heatmap_civ6', DATA.heatmap_civ6, 'Blues',  'Weekly hotspots — Civ VI (average by day &amp; hour, last 28 days)');

  const cmp = DATA.comparison;
  const compEl = document.getElementById('comparison');
  if (cmp && cmp.avg) {{
    const phShift = cmp.peak_hour.shift;
    const phNote = phShift == null
      ? (cmp.peak_hour.prev == null ? 'no prior week to compare' : 'unchanged')
      : (phShift === 0 ? 'unchanged' : `was ${{hr(cmp.peak_hour.prev)}} (${{phShift > 0 ? '+' : ''}}${{phShift}}h)`);
    compEl.innerHTML =
      `<h2>This week vs last week — Civ VII</h2>` +
      `<div class="window">${{cmp.cur_label}} compared to ${{cmp.prev_label}}</div>` +
      `<div class="cards" style="padding:0;">` +
        `<div class="card"><div class="label">Avg concurrent</div>` +
          `<div class="value">${{fmt(cmp.avg.cur)}}</div>` +
          `<div class="sub">prev ${{fmt(cmp.avg.prev)}}</div>` +
          `<div>${{deltaHTML(cmp.avg.pct)}}</div></div>` +
        `<div class="card"><div class="label">Weekly peak</div>` +
          `<div class="value">${{fmt(cmp.peak.cur)}}</div>` +
          `<div class="sub">prev ${{fmt(cmp.peak.prev)}}</div>` +
          `<div>${{deltaHTML(cmp.peak.pct)}}</div></div>` +
        `<div class="card"><div class="label">Peak hour of day</div>` +
          `<div class="value">${{hr(cmp.peak_hour.cur)}}</div>` +
          `<div class="sub">${{fmt(cmp.peak_hour.cur_avg)}} avg at peak</div>` +
          `<div class="delta flat">${{phNote}}</div></div>` +
      `</div>`;
  }}

  const wd = (cmp && cmp.by_weekday) || [];
  const wdEl = document.getElementById('weekday');
  if (wd.length && wd.some(r => r.cur != null || r.prev != null)) {{
    const rows = wd.map(r =>
      `<tr><td>${{r.weekday}}</td><td>${{fmt(r.cur)}}</td><td>${{fmt(r.prev)}}</td>${{deltaCell(r.pct)}}</tr>`
    ).join('');
    wdEl.innerHTML =
      `<h2>Same-day comparison — Civ VII</h2>` +
      `<div class="window">avg concurrent players, this week's <em>day</em> vs last week's same day</div>` +
      `<table class="weekday"><thead><tr>` +
        `<th>Day</th><th>This week</th><th>Last week</th><th>Δ</th>` +
      `</tr></thead><tbody>${{rows}}</tbody></table>`;
  }}

  const profiles = DATA.weekly_profiles || [];
  if (profiles.length >= 2) {{
    const palette = ['#ff9f4d', '#ffd166', '#a3a3a3', '#5a6373'];
    const widths  = [3, 2, 2, 2];
    const dashes  = ['solid', 'solid', 'dot', 'dot'];
    const traces = profiles.map((p, i) => ({{
      x: Array.from({{length: 24}}, (_, h) => h),
      y: p.avg,
      type: 'scatter', mode: 'lines+markers',
      name: `${{p.label}} (${{p.range}})`,
      line: {{ color: palette[i] || '#888', width: widths[i] || 1.5, dash: dashes[i] || 'solid' }},
      marker: {{ size: 4 }},
    }}));
    Plotly.newPlot('profiles', traces, Object.assign({{}}, layoutBase, {{
      title: 'Hour-of-day profile — last 4 weeks overlaid (Civ VII)',
      xaxis: Object.assign({{ title: 'Hour of day', dtick: 2, gridcolor: '#222' }}, fixedAxis),
      yaxis: Object.assign({{ title: 'Avg players', gridcolor: '#222', rangemode: 'tozero' }}, fixedAxis),
      showlegend: true,
      legend: {{ orientation: 'h', y: -0.25, x: 0 }},
    }}), noZoomConfig);
  }} else {{
    document.getElementById('profiles').innerHTML =
      '<div class="empty" style="padding:24px 0;">Hour-of-day profile — need at least 2 weeks of data to overlay.</div>';
  }}
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
