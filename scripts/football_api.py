"""
football_api.py
ROUTER — predictor.py ও generate_predictions.py এই একটাই মডিউল ইম্পোর্ট করে;
এর ভেতরে দুটো সোর্স আছে:

  - football_data_org.py (fdo)  -> PRIMARY, ১২টা বড় ফ্রি লিগ, current season
  - api_sports.py         (aps) -> FALLBACK, বাকি সব লিগ, season capped 2024

দুই সোর্সের team_id / match_id সম্পূর্ণ আলাদা নেমস্পেস (একই সংখ্যা দুই সোর্সে
সম্পূর্ণ ভিন্ন টিম/ম্যাচ বোঝাতে পারে) — তাই এই রাউটার সবসময় "COMPOSITE ID"
ব্যবহার করে: "fdo:123" বা "aps:456" স্ট্রিং হিসেবে। predictor.py বা
generate_predictions.py কখনো raw int id নিয়ে কাজ করে না, শুধু এই composite
স্ট্রিং পাস করে যায় — রাউটার ভেতরে ভেতরে ডিকোড করে সঠিক সোর্সে কল পাঠায়।

লিগ রাউটিং সিদ্ধান্ত নাম দিয়ে না, ID/CODE দিয়ে হয় (এটা আগের নাম-ম্যাচিং
পদ্ধতির (football-data.org vs api-sports.io নামের mismatch, যেমন "La Liga"
vs "Primera Division") চেয়ে অনেক বেশি নির্ভরযোগ্য):

  - fdo সবসময় তার নিজের ১২টা কম্পিটিশন কোড (PL, SA, ...) ব্যবহার করে ফেরত দেয়।
  - aps-এর ফিরিয়ে দেওয়া ম্যাচগুলো থেকে CORE_LEAGUE_APS_IDS-এ থাকা লিগগুলো
    বাদ দেওয়া হয়, যাতে একই ম্যাচ দুইবার (fdo + aps) না আসে।
"""

import football_data_org as fdo
import api_sports as aps

# api-football (api-sports.io)-এর well-known/স্থিতিশীল league id — এই ১২টা লিগ
# football-data.org থেকেই আসবে, তাই aps-এর রেজাল্ট থেকে এগুলো বাদ দেওয়া হয়
# (dedup)। PL, Championship, La Liga, Serie A, Bundesliga, Ligue 1, UCL,
# Eredivisie, Primeira Liga, Brazil Serie A, World Cup, Euro Championship।
CORE_LEAGUE_APS_IDS = {39, 40, 140, 135, 78, 61, 2, 88, 94, 71, 1, 4}

SOURCE_FDO = "fdo"
SOURCE_APS = "aps"


def _enc(source, raw_id):
    if raw_id is None:
        return None
    return f"{source}:{raw_id}"


def _dec(composite_id):
    """'fdo:123' -> ('fdo', '123' as-is, রুট module থেকে int/str যেমন লাগে)।"""
    if composite_id is None:
        return None, None
    source, _, raw = str(composite_id).partition(":")
    return source, raw


def _reencode_match(m, source):
    """একটা adapted match dict-এর সব id/competition-code কে composite-এ বদলায়।"""
    m = dict(m)
    m["id"] = _enc(source, m["id"])
    comp = dict(m.get("competition") or {})
    comp["code"] = _enc(source, comp.get("code") if source == SOURCE_FDO else comp.get("id"))
    m["competition"] = comp
    m["homeTeam"] = {**m["homeTeam"], "id": _enc(source, m["homeTeam"]["id"])}
    m["awayTeam"] = {**m["awayTeam"], "id": _enc(source, m["awayTeam"]["id"])}
    return m


def cache_stats() -> dict:
    return {"fdo": fdo.cache_stats(), "aps": aps.cache_stats()}


def __getattr__(name):
    # generate_predictions.py এই মডিউলকে `football_api.last_known_remaining_daily_quota`
    # হিসেবে অ্যাট্রিবিউট আকারে অ্যাক্সেস করে (ফাংশন কল না)। aps-এই দৈনিক কোটা
    # আছে (fdo-তে শুধু per-minute), তাই সেটাই আসল "বাজেট গার্ড" হিসেবে থাকে।
    if name == "last_known_remaining_daily_quota":
        return aps.last_known_remaining_daily_quota
    raise AttributeError(f"module 'football_api' has no attribute {name!r}")


def get_matches_for_date(date_iso: str):
    """fdo-এর ১২ লিগ + aps-এর বাকি লিগ (dedup করে) — এক তারিখের সব নির্ধারিত ম্যাচ।"""
    matches = []
    try:
        for m in fdo.get_matches_for_date(date_iso):
            matches.append(_reencode_match(m, SOURCE_FDO))
    except Exception as e:
        print(f"DEBUG: router fdo.get_matches_for_date failed: {e}")

    try:
        for m in aps.get_matches_for_date(date_iso):
            league_id = (m.get("competition") or {}).get("id")
            if league_id in CORE_LEAGUE_APS_IDS:
                continue  # fdo থেকে ইতিমধ্যে পাওয়া গেছে
            matches.append(_reencode_match(m, SOURCE_APS))
    except Exception as e:
        print(f"DEBUG: router aps.get_matches_for_date failed: {e}")

    return matches


def get_team_recent_form(team_id, limit: int = 10):
    source, raw = _dec(team_id)
    if source == SOURCE_FDO:
        matches = fdo.get_team_recent_form(int(raw), limit=limit)
    elif source == SOURCE_APS:
        matches = aps.get_team_recent_form(int(raw), limit=limit)
    else:
        print(f"DEBUG: router get_team_recent_form unknown source for {team_id!r}")
        return []
    return [_reencode_match(m, source) for m in matches]


def get_head_to_head(home_team_id, away_team_id, match_id=None, limit: int = 10):
    """fdo-এর H2H match-ভিত্তিক (match_id লাগে); aps-এর H2H দুই team_id দিয়েই হয়।
    match_id না থাকলে (বা fdo না হলে) fdo-এর জন্য খালি লিস্ট ফেরত যাবে —
    predictor.py সেটা gracefully হ্যান্ডেল করে (H2H সিগন্যাল শুধু বাদ পড়ে)।"""
    source, raw_home = _dec(home_team_id)
    _, raw_away = _dec(away_team_id)

    if source == SOURCE_FDO:
        m_source, raw_match = _dec(match_id) if match_id else (None, None)
        if m_source != SOURCE_FDO or raw_match is None:
            return []
        matches = fdo.get_head_to_head(int(raw_match), limit=limit)
    elif source == SOURCE_APS:
        matches = aps.get_head_to_head(int(raw_home), int(raw_away), limit=limit)
    else:
        print(f"DEBUG: router get_head_to_head unknown source for {home_team_id!r}")
        return []
    return [_reencode_match(m, source) for m in matches]


def get_team_statistics(team_id, competition_code, season):
    source, raw_team = _dec(team_id)
    _, raw_comp = _dec(competition_code)
    if source == SOURCE_FDO:
        return fdo.get_team_statistics(int(raw_team), raw_comp, season)
    if source == SOURCE_APS:
        return aps.get_team_statistics(int(raw_team), int(raw_comp), season)
    print(f"DEBUG: router get_team_statistics unknown source for {team_id!r}")
    return None


def get_standings(competition_code, season):
    source, raw_comp = _dec(competition_code)
    if source == SOURCE_FDO:
        table = fdo.get_standings(raw_comp, season)
        source_for_reencode = SOURCE_FDO
    elif source == SOURCE_APS:
        table = aps.get_standings(int(raw_comp), season)
        source_for_reencode = SOURCE_APS
    else:
        print(f"DEBUG: router get_standings unknown source for {competition_code!r}")
        return []

    out = []
    for row in table:
        row = dict(row)
        team = dict(row.get("team") or {})
        team["id"] = _enc(source_for_reencode, team.get("id"))
        row["team"] = team
        out.append(row)
    return out


def get_injuries(match_id):
    source, raw = _dec(match_id)
    if source == SOURCE_FDO:
        injuries = fdo.get_injuries(int(raw))
    elif source == SOURCE_APS:
        injuries = aps.get_injuries(int(raw))
    else:
        print(f"DEBUG: router get_injuries unknown source for {match_id!r}")
        return []
    for inj in injuries:
        inj["team_id"] = _enc(source, inj.get("team_id"))
    return injuries


def get_lineups(match_id):
    source, raw = _dec(match_id)
    if source == SOURCE_FDO:
        lineups = fdo.get_lineups(int(raw))
    elif source == SOURCE_APS:
        lineups = aps.get_lineups(int(raw))
    else:
        print(f"DEBUG: router get_lineups unknown source for {match_id!r}")
        return []
    for lu in lineups:
        lu["team_id"] = _enc(source, lu.get("team_id"))
    return lineups


def get_match_result(match_id):
    source, raw = _dec(match_id)
    if source == SOURCE_FDO:
        return fdo.get_match_result(int(raw))
    if source == SOURCE_APS:
        return aps.get_match_result(int(raw))
    print(f"DEBUG: router get_match_result unknown source for {match_id!r}")
    return {"status": None, "home_goals": None, "away_goals": None}
