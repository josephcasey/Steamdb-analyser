#!/usr/bin/env python3
"""Poll the Steam API for Civ 7's current in-game player count and append it to a CSV.

Designed to be run on a schedule (GitHub Actions cron). Uses only the standard
library so it needs no dependencies installed.
"""
from __future__ import annotations

import csv
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

APPID = os.environ.get("CIV7_APPID", "1295660")  # Sid Meier's Civilization VII
CSV_PATH = Path(os.environ.get("CIV7_CSV", "data/civ7_players.csv"))
API_URL = (
    "https://api.steampowered.com/ISteamUserStats/"
    f"GetNumberOfCurrentPlayers/v1/?appid={APPID}"
)
HEADER = ["timestamp_utc", "player_count"]


def fetch_player_count() -> int:
    req = urllib.request.Request(API_URL, headers={"User-Agent": "civ7-tracker"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.load(resp)
    response = payload.get("response", {})
    if response.get("result") != 1 or "player_count" not in response:
        raise RuntimeError(f"Unexpected Steam API response: {payload}")
    return int(response["player_count"])


def append_row(timestamp: str, count: int) -> None:
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    is_new = not CSV_PATH.exists() or CSV_PATH.stat().st_size == 0
    with CSV_PATH.open("a", newline="") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(HEADER)
        writer.writerow([timestamp, count])


def main() -> int:
    count = fetch_player_count()
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    append_row(now, count)
    print(f"{now}  appid={APPID}  players={count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
