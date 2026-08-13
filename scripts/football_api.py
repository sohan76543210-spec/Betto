"""
football_api.py
API-Football (api-sports.io) থেকে আজকের/আসন্ন ম্যাচ ও টিমের সাম্প্রতিক ফলাফল টেনে আনে।
predictor.py ও generate_predictions.py football-data.org-এর পুরনো ফরম্যাট আশা করে,
তাই এখানে API-Football-এর রেসপন্স সেই একই shape-এ রূপান্তর করা হয়েছে।
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
        "competition": {"name": league["name"], "code": str(league["id"])},
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


def get_team_recent_form(team_id: int, limit: int = 5):
    url = f"{BASE_URL}/fixtures"
    params = {"team": team_id, "last": limit}
    resp = requests.get(url, headers=_headers(), params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return [_adapt_fixture(fx) for fx in data.get("response", [])[:limit]]


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
