"""
football_api.py
API-Football (api-sports.io) থেকে আজকের/আসন্ন ম্যাচ, দুই টিমের head-to-head
এবং প্রতিটা টিমের সাম্প্রতিক ফর্ম (recent fixtures) টেনে আনে।

নোট: ফ্রি প্ল্যানে "last" প্যারামিটার ব্যবহার নিষিদ্ধ, তাই এখানে "from"/"to"
তারিখ-রেঞ্জ ব্যবহার করে ম্যাচ টেনে এনে Python-এ sort করে সর্বশেষ N-টা নেওয়া হয়।
"""

import os
import time
import requests
from datetime import date, timedelta
from typing import Optional

BASE_URL = "https://v3.football.api-sports.io"

# --------------------------------------------------------------------------
# RATE LIMITING: api-football-এর ফ্রি প্ল্যানে প্রতি মিনিটে সর্বোচ্চ ১০টা
# রিকোয়েস্ট (আর দিনে ১০০টা) নেওয়া যায়। MIN_REQUEST_INTERVAL সেকেন্ড হলো
# পরপর দুইটা রিকোয়েস্টের মধ্যে বাধ্যতামূলক ন্যূনতম বিরতি (৭ সেকেন্ড => প্রতি
# মিনিটে ~৮.৫টা কল, নিরাপদ মার্জিনসহ ১০/মিনিট লিমিটের নিচে থাকবে)।
# --------------------------------------------------------------------------
MIN_REQUEST_INTERVAL = 7.0
_last_request_time = 0.0

# শেষ রেসপন্স থেকে জানা দৈনিক কোটার অবশিষ্ট সংখ্যা (api-football header
# x-ratelimit-requests-remaining থেকে)। None মানে এখনো জানা যায়নি।
last_known_remaining_daily_quota = None


def _throttle():
    """পরের রিকোয়েস্টের আগে দরকার হলে অপেক্ষা করে, যাতে per-minute rate limit
    (429 Too Many Requests) না ভাঙে।"""
    global _last_request_time
    now = time.monotonic()
    elapsed = now - _last_request_time
    if elapsed < MIN_REQUEST_INTERVAL:
        time.sleep(MIN_REQUEST_INTERVAL - elapsed)
    _last_request_time = time.monotonic()


def _get(url, params, timeout=15):
    """throttled GET request; api-football-এর সব কল এই ফাংশন দিয়ে যাওয়া উচিত।"""
    global last_known_remaining_daily_quota
    _throttle()
    resp = requests.get(url, headers=_headers(), params=params, timeout=timeout)
    remaining = resp.headers.get("x-ratelimit-requests-remaining")
    if remaining is not None:
        try:
            last_known_remaining_daily_quota = int(remaining)
        except ValueError:
            pass
    resp.raise_for_status()
    return resp.json()


def _current_season_year(ref_date: Optional[date] = None) -> int:
    """বেশিরভাগ ইউরোপিয়ান-স্টাইল লিগে সিজন আগস্টে শুরু হয় এবং api-football-এ
    সিজন নম্বর হিসেবে সেই শুরুর বছরটাই ব্যবহার হয় (যেমন 2026/27 সিজন = season 2026)।
    এটা একটা approximation—ক্যালেন্ডার-ইয়ার সিজনের লিগ (যেমন MLS, ব্রাজিল)-এ পুরোপুরি
    নাও মিলতে পারে, কিন্তু আমাদের whitelist-এর বেশিরভাগ বড় ইউরোপিয়ান লিগের জন্য সঠিক।"""
    d = ref_date or date.today()
    return d.year if d.month >= 7 else d.year - 1


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
            "country": league.get("country"),
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
        data = _get(url, params)
        errors = data.get("errors")
        if errors:
            print(f"DEBUG: get_upcoming_matches error for {d}: {errors}")
        for fx in data.get("response", []):
            all_matches.append(_adapt_fixture(fx))
    return all_matches


def get_team_recent_form(team_id: int, limit: int = 6, lookback_days: int = 270):
    """টিমের শেষ কয়েকটি (যেকোনো ভেন্যুর) ফিনিশড ম্যাচ ফিরিয়ে দেয় (from/to রেঞ্জ দিয়ে)।

    নোট: api-football-এ শুধু "team" + "from"/"to" দিয়ে কল করলে
    "The Season field is required." এরর আসে — "team" প্যারামিটারের সাথে "season"
    বাধ্যতামূলক। তাই বর্তমান সিজন (এবং সিজন-বাউন্ডারির কাছাকাছি সময়ে গত সিজনও,
    যাতে জুলাই-আগস্টের মতো ট্রানজিশন পিরিয়ডে ডেটা মিস না হয়) দুটোই ট্রাই করা হয়।
    """
    today = date.today()
    from_date = (today - timedelta(days=lookback_days)).isoformat()
    to_date = today.isoformat()
    url = f"{BASE_URL}/fixtures"

    current_season = _current_season_year(today)

    def _fetch(season):
        params = {
            "team": team_id,
            "season": season,
            "from": from_date,
            "to": to_date,
            "status": "FT",
        }
        data = _get(url, params)
        errors = data.get("errors")
        if errors:
            print(f"DEBUG: get_team_recent_form error for team {team_id} season {season}: {errors}")
            return []
        return [_adapt_fixture(fx) for fx in data.get("response", [])]

    matches = _fetch(current_season)
    # সিজনের শুরুর দিকে (জুলাই-আগস্ট) নতুন সিজনে এখনো তেমন ম্যাচ খেলা হয়নি বলে
    # খালি আসতে পারে — সেক্ষেত্রে একটা extra কল দিয়ে আগের সিজন ট্রাই করা হয়।
    if not matches:
        matches = _fetch(current_season - 1)

    matches.sort(key=lambda m: m["utcDate"], reverse=True)
    matches = matches[:limit]
    if not matches:
        print(f"DEBUG: get_team_recent_form empty for team {team_id} (tried seasons {current_season} & {current_season - 1})")
    return matches


def get_head_to_head(home_team_id: int, away_team_id: int, limit: int = 10, lookback_days: int = 1095):
    """দুই টিমের আগের মুখোমুখি লড়াইয়ের (finished) ফলাফল ফিরিয়ে দেয় (from/to রেঞ্জ দিয়ে)।"""
    today = date.today()
    from_date = (today - timedelta(days=lookback_days)).isoformat()
    to_date = today.isoformat()
    url = f"{BASE_URL}/fixtures/headtohead"
    params = {
        "h2h": f"{home_team_id}-{away_team_id}",
        "from": from_date,
        "to": to_date,
        "status": "FT",
    }
    data = _get(url, params)
    errors = data.get("errors")
    if errors:
        print(f"DEBUG: get_head_to_head error for {home_team_id} vs {away_team_id}: {errors}")
    matches = [_adapt_fixture(fx) for fx in data.get("response", [])]
    matches.sort(key=lambda m: m["utcDate"], reverse=True)
    matches = matches[:limit]
    if not matches:
        print(f"DEBUG: get_head_to_head empty for {home_team_id} vs {away_team_id}. Raw: {data}")
    return matches


def get_match_result(fixture_id: int):
    url = f"{BASE_URL}/fixtures"
    params = {"id": fixture_id}
    data = _get(url, params)
    response = data.get("response", [])
    if not response:
        return {"status": None, "home_goals": None, "away_goals": None}
    fx = _adapt_fixture(response[0])
    return {
        "status": fx["status"],
        "home_goals": fx["score"]["fullTime"]["home"],
        "away_goals": fx["score"]["fullTime"]["away"],
    }
