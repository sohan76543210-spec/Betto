"""
football_api.py
API-Football (api-sports.io) থেকে আজকের/আসন্ন ম্যাচ ও টিমের সিজন-স্ট্যাটস টেনে আনে।
predictor.py ও generate_predictions.py-এর জন্য পুরনো football-data.org ফরম্যাটে
রেসপন্স রূপান্তর করা হয়েছে।

নোট: API-Football ফ্রি প্ল্যানে পুরনো ম্যাচ-হিস্ট্রি (last N fixtures) সবসময়
বর্তমান সিজনের জন্য কাজ করে না, তাই team form বের করতে /teams/statistics
endpoint ব্যবহার করা হচ্ছে, যা সরাসরি গড় গোল দেয়।
"""

import os
import requests
from datetime import date, timedelta

BASE_URL = "https://v3.football.api-sports.io"


def _headers():
    api_key = os.getenv("API_FOOTBALL_KEY")
    return {"x-apisports-key": api_key}


def _adapt_fixture(fx):
    fixture = fx["fixture"]
    league = fx["league"]
    teams = fx["teams"]
    goals = fx.get("goals", {})
    return {
        "id": fixture["id"],
        "utcDate": fixture["date"],
        "status": fixture["status"]["short"],
        "competition": {
            "name": league["name"],
            "code": str(league["id"]),
            "id": league["id"],
            "season": league.get("season"),
        },
        "homeTeam": {"id": teams["home"]["id"], "name": teams["home"]["name"]},
        "awayTeam": {"id": teams["away"]["id"], "name": teams["away"]["name"]},
        "score": {"fullTime": {"home": goals.get("home"), "away": goals.get("away")}},
    }


def get_upcoming_matches(days_ahead: int = 1):
    """আজ ও আগামী days_ahead দিনের সব লিগের নির্ধারিত ম্যাচ ফিরিয়ে দেয়।"""
    all_matches = []
    today = date.today()
    for offset in range(days_ahead + 1):
        d = (today + timedelta(days=offset)).isoformat()
        url = f"{BASE_URL}/fixtures"
        params = {"date": d, "status": "NS"}
        resp = requests.get(url, headers=_headers(), params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        for fx in data.get("response", []):
            all_matches.append(_adapt_fixture(fx))
    return all_matches


def get_team_season_stats(team_id: int, league_id: int, season: int):
    """
    টিমের চলতি সিজনের গড় গোল-স্কোর ও গোল-কনসিড ফিরিয়ে দেয়।
    ডেটা না পেলে None রিটার্ন করে (তখন predictor ডিফল্ট মান ব্যবহার করবে)।
    """
    if not league_id or not season:
        print(f"DEBUG: missing league_id/season for team {team_id} (league_id={league_id}, season={season})")
        return None
    url = f"{BASE_URL}/teams/statistics"
    params = {"team": team_id, "league": league_id, "season": season}
    resp = requests.get(url, headers=_headers(), params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    errors = data.get("errors")
    if errors:
        print(f"DEBUG: API error for team {team_id}, league {league_id}, season {season}: {errors}")

    stats = data.get("response") or {}
    goals = stats.get("goals", {})
    played = stats.get("fixtures", {}).get("played", {}).get("total", 0)

    if not played:
        print(f"DEBUG: no 'played' data for team {team_id}, league {league_id}, season {season}. Raw response: {data}")
        return None

    try:
        scored_avg = float(goals.get("for", {}).get("average", {}).get("total") or 0)
        conceded_avg = float(goals.get("against", {}).get("average", {}).get("total") or 0)
    except (TypeError, ValueError):
        return None

    if scored_avg == 0 and conceded_avg == 0:
        return None

    return scored_avg, conceded_avg


def get_head_to_head(home_team_id: int, away_team_id: int, limit: int = 5):
    url = f"{BASE_URL}/fixtures/headtohead"
    params = {"h2h": f"{home_team_id}-{away_team_id}", "last": limit}
    resp = requests.get(url, headers=_headers(), params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return [_adapt_fixture(fx) for fx in data.get("response", [])]


def get_match_result(fixture_id: int):
    url = f"{BASE_URL}/fixtures"
    params = {"id": fixture_id}
    resp = requests.get(url, headers=_headers(), params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    response = data.get("response", [])
    if not response:
        return {"status": None, "home_goals": None, "away_goals": None}
    fx = _adapt_fixture(response[0])
    return {
        "status": fx["status"],
        "home_goals": fx["score"]["fullTime"]["home"],
        "away_goals": fx["score"]["fullTime"]["away"],
    }
