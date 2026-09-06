"""
football_data_org.py
football-data.org v4 API wrapper — PRIMARY source for the 12 leagues covered
by their free tier: Premier League, Championship, La Liga, Serie A,
Bundesliga, Ligue 1, Primeira Liga, Eredivisie, Brazil Serie A,
UEFA Champions League, World Cup, Euro Championship.

Free-tier limits (as documented):
- 10 requests / minute
- No per-day cap, but only these 12 competitions are visible
- No team-statistics or injuries endpoints (those stay on api-sports.io)

This module always deals in RAW football-data.org ids (ints for teams/
matches, string codes like "PL"/"SA" for competitions). It is the
football_api.py router's job to wrap these in the "fdo:<id>" composite
id scheme so they never collide with api-sports.io ids.
"""

import os
import time
import requests
from datetime import date, timedelta, datetime
from typing import Optional

BASE_URL = "https://api.football-data.org/v4"

# The 12 competitions available on the free plan (competition "code").
FREE_COMPETITIONS = ["PL", "ELC", "PD", "SA", "BL1", "PPL", "DED", "FL1", "BSA", "CL", "WC", "EC"]

# --------------------------------------------------------------------------
# RATE LIMITING: football-data.org free plan allows 10 requests/minute
# (no documented daily cap). 6.5s between calls keeps us safely under that
# (~9.2 req/min) even with some jitter.
# --------------------------------------------------------------------------
MIN_REQUEST_INTERVAL = 6.5
_last_request_time = 0.0

# Best-known remaining per-minute quota, taken from the
# "X-Requests-Available-Minute" response header when present. There is no
# daily cap on this API, so this is informational only (generate_predictions.py
# gates on api_sports.py's daily quota, not this one).
last_known_remaining_minute_quota = None

# --------------------------------------------------------------------------
# IN-MEMORY CACHE (per run) — same idea as api_sports.py: avoid re-fetching
# the same team/match/competition twice within one script run.
# --------------------------------------------------------------------------
_team_form_cache: dict = {}
_h2h_cache: dict = {}
_standings_cache: dict = {}


def cache_stats() -> dict:
    return {
        "teams_cached": len(_team_form_cache),
        "h2h_matches_cached": len(_h2h_cache),
        "standings_cached": len(_standings_cache),
    }


def _throttle():
    global _last_request_time
    now = time.monotonic()
    elapsed = now - _last_request_time
    if elapsed < MIN_REQUEST_INTERVAL:
        time.sleep(MIN_REQUEST_INTERVAL - elapsed)
    _last_request_time = time.monotonic()


def _headers():
    token = os.getenv("FOOTBALL_DATA_ORG_TOKEN")
    return {"X-Auth-Token": token}


def _get(path, params=None, timeout=15):
    global last_known_remaining_minute_quota
    _throttle()
    resp = requests.get(f"{BASE_URL}{path}", headers=_headers(), params=params or {}, timeout=timeout)
    remaining = resp.headers.get("X-Requests-Available-Minute")
    if remaining is not None:
        try:
            last_known_remaining_minute_quota = int(remaining)
        except ValueError:
            pass
    if resp.status_code == 429:
        print(f"DEBUG: football-data.org 429 rate-limited on {path}; params={params}")
        return {}
    resp.raise_for_status()
    return resp.json()


def _season_year(season_obj):
    """football-data.org গুলো একটা season object (id/startDate/endDate) দেয়, একটা
    সাধারণ integer বছর না। আমরা startDate থেকে বছরটা বের করি যাতে api-sports.io-এর
    সাথে সামঞ্জস্যপূর্ণ ফরম্যাট থাকে।"""
    if not season_obj:
        return None
    start = season_obj.get("startDate")
    if not start:
        return None
    try:
        return int(start[:4])
    except (ValueError, TypeError):
        return None


def _adapt_match(m):
    comp = m.get("competition", {}) or {}
    home = m.get("homeTeam", {}) or {}
    away = m.get("awayTeam", {}) or {}
    score = m.get("score", {}) or {}
    full_time = score.get("fullTime", {}) or {}
    return {
        "id": m.get("id"),
        "utcDate": m.get("utcDate"),
        "status": m.get("status"),
        "competition": {
            "name": comp.get("name"),
            "country": (m.get("area") or {}).get("name"),
            "code": comp.get("code"),
            "id": comp.get("id"),
            "season": _season_year(m.get("season")),
        },
        "homeTeam": {"id": home.get("id"), "name": home.get("name")},
        "awayTeam": {"id": away.get("id"), "name": away.get("name")},
        "score": {"fullTime": {"home": full_time.get("home"), "away": full_time.get("away")}},
    }


def get_matches_for_date(date_iso: str):
    """একটা নির্দিষ্ট (UTC) ক্যালেন্ডার তারিখের সব ১২টা ফ্রি লিগের নির্ধারিত (SCHEDULED)
    ম্যাচ ফিরিয়ে দেয়। এক তারিখে ১টা মাত্র API call (সব competitions একসাথে)।"""
    d = date.fromisoformat(date_iso)
    date_to = (d + timedelta(days=1)).isoformat()  # dateTo exclusive হওয়ায় পরের দিন দিতে হয়
    params = {
        "dateFrom": date_iso,
        "dateTo": date_to,
        "competitions": ",".join(FREE_COMPETITIONS),
        "status": "SCHEDULED",
    }
    data = _get("/matches", params)
    return [_adapt_match(m) for m in data.get("matches", [])]


def get_team_recent_form(team_id: int, limit: int = 10, lookback_days: int = 270):
    """টিমের সাম্প্রতিক finished ম্যাচ ফিরিয়ে দেয়। CACHED by team_id (fetch_limit=10
    পর্যন্ত রাখা হয়, ছোট limit চাইলেও পুনরায় fetch হয় না)।"""
    cache_key = team_id
    if cache_key in _team_form_cache:
        return _team_form_cache[cache_key][:limit]

    today = date.today()
    from_date = (today - timedelta(days=lookback_days)).isoformat()
    to_date = today.isoformat()
    fetch_limit = max(limit, 10)
    params = {"dateFrom": from_date, "dateTo": to_date, "status": "FINISHED", "limit": 100}
    data = _get(f"/teams/{team_id}/matches", params)
    matches = [_adapt_match(m) for m in data.get("matches", [])]
    matches.sort(key=lambda m: m["utcDate"], reverse=True)
    matches = matches[:fetch_limit]
    if not matches:
        print(f"DEBUG: fdo get_team_recent_form empty for team {team_id}")

    _team_form_cache[cache_key] = matches
    return matches[:limit]


def get_head_to_head(match_id: int, limit: int = 10):
    """/matches/{id}/head2head — দুই টিমের আগের মুখোমুখি লড়াই। এই এন্ডপয়েন্ট
    match-ভিত্তিক (কোনো দুই স্বেচ্ছাচারী team_id দিয়ে নয়), তাই ROUTER-কে অবশ্যই
    ম্যাচের নিজস্ব match_id পাস করতে হবে — এটাই api-sports.io থেকে সবচেয়ে বড়
    পার্থক্য। CACHED by match_id."""
    cache_key = match_id
    if cache_key in _h2h_cache:
        return _h2h_cache[cache_key][:limit]

    data = _get(f"/matches/{match_id}/head2head", {"limit": max(limit, 10)})
    matches = [_adapt_match(m) for m in data.get("matches", [])]
    matches.sort(key=lambda m: m["utcDate"], reverse=True)
    if not matches:
        print(f"DEBUG: fdo get_head_to_head empty for match {match_id}")

    _h2h_cache[cache_key] = matches
    return matches[:limit]


def get_standings(competition_code: str, season: Optional[int] = None):
    """/competitions/{code}/standings — TOTAL টেবিল ফিরিয়ে দেয় (home/away আলাদা
    টেবিলও API দেয়, কিন্তু আমরা api-sports.io ফরম্যাটের সাথে মেলাতে TOTAL নিচ্ছি)।
    CACHED by (code, season)."""
    cache_key = (competition_code, season)
    if cache_key in _standings_cache:
        return _standings_cache[cache_key]

    params = {}
    if season:
        params["season"] = season
    data = _get(f"/competitions/{competition_code}/standings", params)
    table = []
    for block in data.get("standings", []):
        if block.get("type") == "TOTAL":
            table = block.get("table", [])
            break
    if not table:
        print(f"DEBUG: fdo get_standings empty for {competition_code} season {season}")

    # api-sports.io ফরম্যাটের কাছাকাছি করি যাতে predictor.py-এর
    # _standing_adjustment() দুই সোর্সেই একইভাবে কাজ করে (rank + team.id দরকার)।
    adapted = []
    for row in table:
        team = row.get("team", {}) or {}
        adapted.append({
            "rank": row.get("position"),
            "team": {"id": team.get("id"), "name": team.get("name")},
            "points": row.get("points"),
            "goalsDiff": row.get("goalDifference"),
        })

    _standings_cache[cache_key] = adapted
    return adapted


def get_team_statistics(team_id: int, competition_code: str, season: Optional[int] = None):
    """football-data.org-এর ফ্রি প্ল্যানে aggregate team-statistics endpoint নেই।
    predictor.py এই None-কে gracefully হ্যান্ডেল করে (কোনো adjustment প্রয়োগ হয় না)।"""
    return None


def get_injuries(match_id: int):
    """football-data.org-এর ফ্রি প্ল্যানে injuries endpoint নেই।"""
    return []


def get_lineups(match_id: int):
    """football-data.org-এর ফ্রি প্ল্যানে lineups শুধু paid tier-এ আনফোল্ড হয়।"""
    return []


def get_match_result(match_id: int):
    data = _get(f"/matches/{match_id}")
    if not data or "id" not in data:
        return {"status": None, "home_goals": None, "away_goals": None}
    m = _adapt_match(data)
    return {
        "status": m["status"],
        "home_goals": m["score"]["fullTime"]["home"],
        "away_goals": m["score"]["fullTime"]["away"],
    }
