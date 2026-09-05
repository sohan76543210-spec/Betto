"""
api_sports.py  (football_api.py থেকে রিনেম করা)
API-Football (api-sports.io) থেকে আজকের/আসন্ন ম্যাচ, দুই টিমের head-to-head
এবং প্রতিটা টিমের সাম্প্রতিক ফর্ম (recent fixtures) টেনে আনে।

এখন এটা SECONDARY/FALLBACK সোর্স — football-data.org (football_data_org.py)
যে ১২টা বড় লিগ কভার করে না, শুধু সেগুলোর জন্য ব্যবহার হয় (রাউটার football_api.py
এই সিদ্ধান্ত নেয়)।

নোট: ফ্রি প্ল্যানে "last" প্যারামিটার ব্যবহার নিষিদ্ধ, তাই এখানে "from"/"to"
তারিখ-রেঞ্জ ব্যবহার করে ম্যাচ টেনে এনে Python-এ sort করে সর্বশেষ N-টা নেওয়া হয়।

SEASON CAP: api-sports.io-এর ফ্রি প্ল্যানে সাম্প্রতিক সিজনের ডেটা কখনো কখনো
অসম্পূর্ণ/কম নির্ভরযোগ্য পাওয়া গেছে, তাই _current_season_year() ইচ্ছাকৃতভাবে
2024-এ cap করা — এর বেশি বছর চাওয়া হলেও 2024 সিজনের ডেটা রিকোয়েস্ট করা হবে।
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

# --------------------------------------------------------------------------
# IN-MEMORY CACHE (per run): এইটাই ১০০ request বাঁচানোর মূল কৌশল।
# একই team/league-এর data একবার API থেকে আনলে, ওই একই process-run-এর মধ্যে
# আবার দরকার হলে cache থেকে ফেরত দেওয়া হয় — নতুন কোনো HTTP call হয় না।
# generate_predictions.py-তে ৩০টা team, ১৫টা ম্যাচে থাকলেও প্রতিটা team-এর
# recent form মাত্র একবার fetch হবে (home হিসেবে হোক বা away হিসেবে হোক,
# একই team দুই জায়গায় থাকলেও)।
#
# এটা স্ক্রিপ্ট শেষ হলে হারিয়ে যায় (process-lifetime cache) — পরের দিনের রান
# আবার fresh শুরু হয়, যেটা ঠিক আছে কারণ ফর্ম/লাইনআপ ইত্যাদি প্রতিদিন বদলায়।
# --------------------------------------------------------------------------
_team_form_cache: dict = {}
_h2h_cache: dict = {}
_team_stats_cache: dict = {}
_standings_cache: dict = {}
_injuries_cache: dict = {}
_lineups_cache: dict = {}
_results_by_date_cache: dict = {}


def cache_stats() -> dict:
    """ডিবাগ/লগের জন্য: এই রানে কতগুলো ইউনিক জিনিস cache-এ জমা হয়েছে।"""
    return {
        "teams_cached": len(_team_form_cache),
        "h2h_pairs_cached": len(_h2h_cache),
        "team_stats_cached": len(_team_stats_cache),
        "standings_cached": len(_standings_cache),
        "injuries_cached": len(_injuries_cache),
        "lineups_cached": len(_lineups_cache),
    }


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


MAX_SEASON_YEAR = 2024  # ফ্রি প্ল্যানে নির্ভরযোগ্যতার জন্য ইচ্ছাকৃত cap (দেখুন মডিউল docstring)


def _current_season_year(ref_date: Optional[date] = None) -> int:
    """বেশিরভাগ ইউরোপিয়ান-স্টাইল লিগে সিজন আগস্টে শুরু হয় এবং api-football-এ
    সিজন নম্বর হিসেবে সেই শুরুর বছরটাই ব্যবহার হয় (যেমন 2026/27 সিজন = season 2026)।
    এটা একটা approximation—ক্যালেন্ডার-ইয়ার সিজনের লিগ (যেমন MLS, ব্রাজিল)-এ পুরোপুরি
    নাও মিলতে পারে, কিন্তু আমাদের whitelist-এর বেশিরভাগ বড় ইউরোপিয়ান লিগের জন্য সঠিক।

    MAX_SEASON_YEAR-এ cap করা থাকে (দেখুন মডিউল docstring)।"""
    d = ref_date or date.today()
    year = d.year if d.month >= 7 else d.year - 1
    return min(year, MAX_SEASON_YEAR)


def _season_window(season: int, lookback_days: int, today: date):
    """একটা সিজনের জন্য from/to রেঞ্জ বানায় — সবসময় আজকের তারিখ থেকে না।

    কেন লাগে: api-football-এ team+season+from/to সবগুলো একসাথে (AND) ফিল্টার
    করে। "season" ইউরোপিয়ান-স্টাইল সিজন নম্বর (শুরুর বছর) — তার আসল ম্যাচ
    মোটামুটি ঐ বছরের জুলাই থেকে পরের বছরের জুন পর্যন্ত থাকে। season CAP করা
    (MAX_SEASON_YEAR) থাকলে, "আজকের তারিখ থেকে lookback_days" রেঞ্জ সেই cap-করা
    সিজনের আসল ম্যাচ-তারিখের সাথে আর মিলে না (আজ যদি সিজনের ক্যালেন্ডার-সীমার
    অনেক পরে হয়, রেঞ্জ ও সিজন একে অপরকে ওভারল্যাপ করে না) — ফলে সবসময় খালি
    রেজাল্ট আসে, টিম-নির্বিশেষে। তাই "today" এর বদলে সেই সিজনের নিজের শেষ
    তারিখ (বা আজ, যেটা আগে) থেকে পেছনের দিকে lookback করা হয়।
    """
    season_start = date(season, 7, 1)
    season_end = date(season + 1, 6, 30)
    anchor = min(today, season_end)
    to_date = anchor
    from_date = max(season_start, anchor - timedelta(days=lookback_days))
    return from_date.isoformat(), to_date.isoformat()


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


def get_matches_for_date(date_iso: str):
    """একটা নির্দিষ্ট (UTC) ক্যালেন্ডার তারিখের সব লিগের নির্ধারিত (not-started) ম্যাচ
    ফিরিয়ে দেয়। এক তারিখে ১টা মাত্র API call।"""
    all_matches = []
    url = f"{BASE_URL}/fixtures"
    params = {"date": date_iso, "status": "NS"}
    data = _get(url, params)
    errors = data.get("errors")
    if errors:
        print(f"DEBUG: get_matches_for_date error for {date_iso}: {errors}")
    for fx in data.get("response", []):
        all_matches.append(_adapt_fixture(fx))
    return all_matches


def get_upcoming_matches(days_ahead: int = 1):
    """আজ ও আগামী days_ahead দিনের সব লিগের নির্ধারিত ম্যাচ ফিরিয়ে দেয়।"""
    all_matches = []
    today = date.today()
    for offset in range(days_ahead + 1):
        d = (today + timedelta(days=offset)).isoformat()
        all_matches.extend(get_matches_for_date(d))
    return all_matches


def get_team_recent_form(team_id: int, limit: int = 10, lookback_days: int = 270):
    """টিমের শেষ কয়েকটি (যেকোনো ভেন্যুর) ফিনিশড ম্যাচ ফিরিয়ে দেয় (from/to রেঞ্জ দিয়ে)।

    CACHED by team_id: এই ফাংশন সবসময় limit=10 পর্যন্ত raw data fetch/cache করে
    রাখে (module-level _team_form_cache-এ), তারপর caller-এর চাওয়া limit অনুযায়ী
    python-এ slice করে দেয়। ফলে একই team-এর জন্য ছোট limit দিয়ে বারবার call
    করলেও (যেমন quick screening-এ limit=6, deep analysis-এ limit=10) দ্বিতীয়বার
    API call হয় না।

    নোট: api-football-এ শুধু "team" + "from"/"to" দিয়ে কল করলে
    "The Season field is required." এরর আসে — "team" প্যারামিটারের সাথে "season"
    বাধ্যতামূলক। তাই বর্তমান সিজন (এবং সিজন-বাউন্ডারির কাছাকাছি সময়ে গত সিজনও,
    যাতে জুলাই-আগস্টের মতো ট্রানজিশন পিরিয়ডে ডেটা মিস না হয়) দুটোই ট্রাই করা হয়।
    """
    cache_key = team_id
    if cache_key in _team_form_cache:
        return _team_form_cache[cache_key][:limit]

    today = date.today()
    url = f"{BASE_URL}/fixtures"

    current_season = _current_season_year(today)
    fetch_limit = max(limit, 10)  # সবসময় অন্তত ১০টা cache-এ রাখি, ভবিষ্যতের বড় limit call-এর জন্য

    def _fetch(season):
        from_date, to_date = _season_window(season, lookback_days, today)
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
    matches = matches[:fetch_limit]
    if not matches:
        print(f"DEBUG: get_team_recent_form empty for team {team_id} (tried seasons {current_season} & {current_season - 1})")

    _team_form_cache[cache_key] = matches
    return matches[:limit]


def get_head_to_head(home_team_id: int, away_team_id: int, limit: int = 10, lookback_days: int = 1095):
    """দুই টিমের আগের মুখোমুখি লড়াইয়ের (finished) ফলাফল ফিরিয়ে দেয় (from/to রেঞ্জ দিয়ে)।

    CACHED by unordered team-id pair, তাই home/away যেকোনো দিক থেকে একই দুই
    টিমের জন্য দ্বিতীয়বার call হয় না।
    """
    cache_key = tuple(sorted((home_team_id, away_team_id)))
    if cache_key in _h2h_cache:
        return _h2h_cache[cache_key][:limit]

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
    matches = matches[:max(limit, 10)]
    if not matches:
        print(f"DEBUG: get_head_to_head empty for {home_team_id} vs {away_team_id}. Raw: {data}")

    _h2h_cache[cache_key] = matches
    return matches[:limit]


def get_team_statistics(team_id: int, league_id: int, season: int):
    """/teams/statistics — সিজন-ব্যাপী aggregate stats: form string, goals for/against
    (home ও away আলাদা করে), clean sheets, failed-to-score ইত্যাদি। এক call-এই
    home ও away আলাদা ব্রেকডাউন থাকে বলে এটা get_team_recent_form-এর চেয়ে বেশি
    নির্ভরযোগ্য venue-specific data দেয়, বিশেষত যেসব টিমের সাম্প্রতিক ফর্মে হোম/অ্যাওয়ে
    ম্যাচ কম পড়েছে। শুধু Phase 2 (shortlisted ম্যাচ)-এ ব্যবহারের জন্য উদ্দিষ্ট, কারণ
    এটা প্রতি টিমে একটা করে আলাদা call।
    CACHED by (team_id, league_id, season)."""
    season = min(season, MAX_SEASON_YEAR) if season else season
    cache_key = (team_id, league_id, season)
    if cache_key in _team_stats_cache:
        return _team_stats_cache[cache_key]

    url = f"{BASE_URL}/teams/statistics"
    params = {"team": team_id, "league": league_id, "season": season}
    data = _get(url, params)
    errors = data.get("errors")
    stats = data.get("response") or None
    if errors or stats is None:
        print(f"DEBUG: get_team_statistics empty/error for team {team_id} league {league_id} season {season}: {errors}")

    _team_stats_cache[cache_key] = stats
    return stats


def get_standings(league_id: int, season: int):
    """/standings — লিগ টেবিল (position, points, goal diff)। CACHED by (league_id, season),
    তাই একই লিগের একাধিক ম্যাচ Phase 2-তে থাকলেও একবারই fetch হয়।"""
    season = min(season, MAX_SEASON_YEAR) if season else season
    cache_key = (league_id, season)
    if cache_key in _standings_cache:
        return _standings_cache[cache_key]

    url = f"{BASE_URL}/standings"
    params = {"league": league_id, "season": season}
    data = _get(url, params)
    errors = data.get("errors")
    response = data.get("response") or []
    table = []
    if response:
        try:
            table = response[0]["league"]["standings"][0]
        except (KeyError, IndexError, TypeError):
            table = []
    if errors or not table:
        print(f"DEBUG: get_standings empty/error for league {league_id} season {season}: {errors}")

    _standings_cache[cache_key] = table
    return table


def get_injuries(fixture_id: int):
    """/injuries?fixture=... — নির্দিষ্ট ম্যাচের জন্য জানা ইনজুরি/সাসপেনশন লিস্ট।
    ফ্রি প্ল্যানে সব লিগে এই ডেটা নাও থাকতে পারে — খালি লিস্ট মানে হয়তো ইনজুরি নেই,
    নয়তো এই লিগের জন্য ডেটা কভারেজ নেই, দুটো আলাদা করা যায় না। CACHED by fixture_id.
    শুধু Phase 2 (ম্যাচের কাছাকাছি সময়ে, shortlisted ম্যাচ)-এ কল করার জন্য।"""
    cache_key = fixture_id
    if cache_key in _injuries_cache:
        return _injuries_cache[cache_key]

    url = f"{BASE_URL}/injuries"
    params = {"fixture": fixture_id}
    data = _get(url, params)
    errors = data.get("errors")
    injuries = []
    for item in data.get("response", []):
        player = item.get("player", {})
        team = item.get("team", {})
        injuries.append({
            "team_id": team.get("id"),
            "team_name": team.get("name"),
            "player_name": player.get("name"),
            "type": player.get("type"),   # e.g. "Missing Fixture"
            "reason": player.get("reason"),
        })
    if errors:
        print(f"DEBUG: get_injuries error for fixture {fixture_id}: {errors}")

    _injuries_cache[cache_key] = injuries
    return injuries


def get_lineups(fixture_id: int):
    """/fixtures/lineups?fixture=... — কনফার্মড (বা এখনো available না হলে খালি) লাইনআপ।
    api-football সাধারণত কিকঅফের ~৪৫-৬০ মিনিট আগে এটা পাবলিশ করে, তার আগে খালি
    আসবে — এটা normal, error না। CACHED by fixture_id। শুধু Phase 2-তে, এবং
    আদর্শভাবে ম্যাচের কাছাকাছি সময়ে আলাদা রান থেকে কল করা উচিত।"""
    cache_key = fixture_id
    if cache_key in _lineups_cache:
        return _lineups_cache[cache_key]

    url = f"{BASE_URL}/fixtures/lineups"
    params = {"fixture": fixture_id}
    data = _get(url, params)
    errors = data.get("errors")
    lineups = []
    for item in data.get("response", []):
        team = item.get("team", {})
        lineups.append({
            "team_id": team.get("id"),
            "team_name": team.get("name"),
            "formation": item.get("formation"),
            "starting_xi": [p["player"]["name"] for p in item.get("startXI", []) if p.get("player")],
        })
    if errors:
        print(f"DEBUG: get_lineups error for fixture {fixture_id}: {errors}")
    if not lineups:
        print(f"DEBUG: get_lineups not yet published for fixture {fixture_id} (normal if far from kickoff)")

    _lineups_cache[cache_key] = lineups
    return lineups


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


def get_results_by_date(date_iso: str):
    """একই তারিখে শেষ হওয়া সব ম্যাচের ফলাফল **এক কলে** ফেরত দেয় —
    {fixture_id: {status, home_goals, away_goals}}।

    কেন লাগে: check_results.py-এর আগের ভার্সন প্রতিটা pending prediction-এর
    জন্য আলাদা get_match_result(fixture_id) কল করতো, অর্থাৎ একই দিনে ১০টা
    ম্যাচ resolve করতে ১০টা কোটা খরচ হতো। free tier-এ দৈনিক মাত্র ১০০ কল
    থাকায় (আর generate_predictions.py/build_calibration_set.py-ও একই কোটা
    শেয়ার করে), pending list বড় হলে এটা দ্রুত কোটা শেষ করে দিতে পারতো। এই
    ফাংশন দিয়ে একই তারিখের সব ম্যাচ (status=FT) একবারে টেনে আনা যায় — N-টা
    ম্যাচ resolve করতে ১টা মাত্র কল লাগবে, N যতই বড় হোক (check_results.py-এর
    caller সেই তারিখের সব pending entry-কে এই একটা ডিকশনারি দিয়েই resolve
    করতে পারবে)। CACHED by date, তাই একই রানে একই তারিখ দ্বিতীয়বার চাইলে
    আবার কল হবে না।
    """
    if date_iso in _results_by_date_cache:
        return _results_by_date_cache[date_iso]

    url = f"{BASE_URL}/fixtures"
    params = {"date": date_iso, "status": "FT"}
    data = _get(url, params)
    errors = data.get("errors")
    if errors:
        print(f"DEBUG: get_results_by_date error for {date_iso}: {errors}")

    out = {}
    for fx in data.get("response", []):
        adapted = _adapt_fixture(fx)
        out[adapted["id"]] = {
            "status": adapted["status"],
            "home_goals": adapted["score"]["fullTime"]["home"],
            "away_goals": adapted["score"]["fullTime"]["away"],
        }
    _results_by_date_cache[date_iso] = out
    return out
