"""
football_api.py
API-Football (api-sports.io) থেকে আজকের/আসন্ন ম্যাচ, দুই টিমের head-to-head
এবং প্রতিটা টিমের সাম্প্রতিক ফর্ম (recent fixtures) টেনে আনে।

নোট: ফ্রি প্ল্যানে /teams/statistics endpoint বর্তমান সিজনের জন্য কাজ করে না
(শুধু 2022-2024 সিজন সাপোর্ট করে), তাই এই ফাইলে সেটা ব্যবহার করা হয়নি।
এর বদলে /fixtures endpoint (team + last N) এবং /fixtures/headtohead
ব্যবহার করা হয়েছে, যেগুলো সাধারণত ফ্রি প্ল্যানেও সিজন-নিরপেক্ষভাবে কাজ করে।
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
        errors = data.get("errors")
        if errors:
            print(f"DEBUG: get_upcoming_matches error for {d}: {errors}")
        for fx in data.get("response", []):
            all_matches.append(_adapt_fixture(fx))
    return all_matches


def get_team_recent_form(team_id: int, limit: int = 6):
    """টিমের শেষ কয়েকটি (যেকোনো ভেন্যুর) ফিনিশড ম্যাচ ফিরিয়ে দেয়।"""
    url = f"{BASE_URL}/fixtures"
    params = {"team": team_id, "last": limit}
    resp = requests.get(url, headers=_headers(), params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    errors = data.get("errors")
    if errors:
        print(f"DEBUG: get_team_recent_form error for team {team_id}: {errors}")
    matches = [_adapt_fixture(fx) for fx in data.get("response", [])]
    if not matches:
        print(f"DEBUG: get_team_recent_form empty for team {team_id}. Raw: {data}")
    return matches


def get_head_to_head(home_team_id: int, away_team_id: int, limit: int = 10):
    """দুই টিমের আগের মুখোমুখি লড়াইয়ের (finished) ফলাফল ফিরিয়ে দেয়।"""
    url = f"{BASE_URL}/fixtures/headtohead"
    params = {"h2h": f"{home_team_id}-{away_team_id}", "last": limit}
    resp = requests.get(url, headers=_headers(), params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    errors = data.get("errors")
    if errors:
        print(f"DEBUG: get_head_to_head error for {home_team_id} vs {away_team_id}: {errors}")
    matches = [_adapt_fixture(fx) for fx in data.get("response", [])]
    if not matches:
        print(f"DEBUG: get_head_to_head empty for {home_team_id} vs {away_team_id}. Raw: {data}")
    return matches


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
