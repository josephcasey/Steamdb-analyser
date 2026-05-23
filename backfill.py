#!/usr/bin/env python3
"""Backfill historical concurrent-player data from SteamCharts.

SteamCharts exposes an undocumented JSON endpoint at /app/<id>/chart-data.json
that returns the same data Highcharts uses to draw their per-app chart:
a list of [unix_ms, player_count] pairs spanning roughly the past 16 months,
with hourly granularity at the recent end and monthly granularity further
back. Useful for "what did counts look like before our collector started?"

Run this manually (or via the workflow_dispatch button) any time you want to
refresh the history; the live collector continues to gather 30-minute samples
on its own.
"""
from __future__ import annotations

import csv
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

GAMES = {
    "civ7": ("1295660", Path("data/civ7_history.csv")),
    "civ6": ("289070",  Path("data/civ6_history.csv")),
}


def fetch_history(appid: str):
    url = f"https://steamcharts.com/app/{appid}/chart-data.json"
    req = urllib.request.Request(url, headers={"User-Agent": "civ7-tracker"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def write_csv(path: Path, points) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp_utc", "player_count"])
        for ts_ms, count in points:
            ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).replace(microsecond=0).isoformat()
            w.writerow([ts, int(round(count))])


def main() -> int:
    for name, (appid, path) in GAMES.items():
        points = fetch_history(appid)
        write_csv(path, points)
        print(f"{name}: wrote {len(points)} rows to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
