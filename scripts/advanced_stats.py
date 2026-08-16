"""
advanced_stats.py
এই মডিউলে কোনো API call নেই — শুধু football_api.py থেকে ইতিমধ্যে fetch করা raw
match data নিয়ে pure Python-এ হিসাব করা হয়।

গুরুত্বপূর্ণ সততার নোট (POWER RATING বনাম আসল ELO):
সত্যিকারের ELO rating একটা persistent, ক্রমাগত-আপডেট-হওয়া সংখ্যা যেটা সময়ের
সাথে প্রতিটা ম্যাচের ফলাফল অনুযায়ী বদলায় এবং database-এ জমা থাকে। এই প্রজেক্টে
কোনো persistent database নেই (শুধু predictions_log.json, যেটা predictions সংরক্ষণ
করে, rating না)। তাই এখানে "power_rating()" যা করে তা হলো: API থেকে পাওয়া
সাম্প্রতিক ম্যাচগুলো থেকে recency-weighted ফর্ম-ভিত্তিক একটা approximation —
এটা real ELO-এর বিকল্প, প্রতিস্থাপন না। ভবিষ্যতে persistent ELO চাইলে
SQLite/JSON-এ প্রতিটা team-এর rating সময়ের সাথে save+update করার আলাদা সিস্টেম লাগবে।

এখানে যা আছে:
  - recency_weighted_scored_conceded(): সাম্প্রতিক ম্যাচকে বেশি গুরুত্ব
  - power_rating(): ফর্ম-ভিত্তিক rating approximation (0 = গড়, + মানে ভালো ফর্ম)
  - dynamic_home_advantage(): টিম-স্পেসিফিক হোম বুস্ট (fixed 1.1 এর বদলে)
  - dixon_coles_adjustment(): কম-স্কোরিং ফলাফলে (0-0,1-0,0-1,1-1) Poisson-এর
    independence assumption-এর ভুল সংশোধন করে (Dixon & Coles, 1997)
  - confidence_score(): ডেটার পরিমাণ ও সিগন্যালগুলোর মধ্যে সামঞ্জস্য থেকে
    0-100 স্কেলে confidence
"""

import math


def _sorted_desc(matches):
    return sorted(matches, key=lambda m: m["utcDate"], reverse=True)


def recency_weighted_scored_conceded(matches, team_id, half_life: int = 4):
    """সবচেয়ে সাম্প্রতিক ম্যাচকে সবচেয়ে বেশি ওজন দিয়ে গড় scored/conceded বের করে।
    half_life=4 মানে ৪ ম্যাচ আগের রেজাল্টের ওজন এখনকার অর্ধেক।
    ডেটা না থাকলে None।"""
    matches = _sorted_desc(matches)
    total_w = 0.0
    scored_w = 0.0
    conceded_w = 0.0
    idx = 0
    for m in matches:
        full_time = m.get("score", {}).get("fullTime", {})
        hg, ag = full_time.get("home"), full_time.get("away")
        if hg is None or ag is None:
            continue
        home_id, away_id = m["homeTeam"]["id"], m["awayTeam"]["id"]
        if home_id == team_id:
            s, c = hg, ag
        elif away_id == team_id:
            s, c = ag, hg
        else:
            continue
        w = 0.5 ** (idx / half_life)
        scored_w += s * w
        conceded_w += c * w
        total_w += w
        idx += 1
    if total_w == 0:
        return None
    return scored_w / total_w, conceded_w / total_w


def power_rating(matches, team_id, half_life: int = 4):
    """ফর্ম-ভিত্তিক rating approximation (real persistent ELO না — উপরের মডিউল
    docstring দ্রষ্টব্য)। প্রতিটা ম্যাচকে পয়েন্ট (win=1, draw=0.5, loss=0) + গোল
    ডিফারেন্স দিয়ে স্কোর করে recency-weighted গড় নেওয়া হয়, তারপর একটা মানুষ-পড়ার-
    উপযোগী স্কেলে (~-100 থেকে +100, 0=গড়) ম্যাপ করা হয়। শুধু একই রানের মধ্যে
    দুই টিমকে তুলনা করার জন্য উপযোগী — absolute সংখ্যাটার নিজস্ব কোনো মানে নেই।"""
    matches = _sorted_desc(matches)
    total_w = 0.0
    score_w = 0.0
    idx = 0
    for m in matches:
        full_time = m.get("score", {}).get("fullTime", {})
        hg, ag = full_time.get("home"), full_time.get("away")
        if hg is None or ag is None:
            continue
        home_id, away_id = m["homeTeam"]["id"], m["awayTeam"]["id"]
        if home_id == team_id:
            gf, ga = hg, ag
        elif away_id == team_id:
            gf, ga = ag, hg
        else:
            continue
        if gf > ga:
            result_pts = 1.0
        elif gf == ga:
            result_pts = 0.5
        else:
            result_pts = 0.0
        gd_component = max(-3, min(3, gf - ga)) / 3.0  # -1..1, বড় ব্যবধান বেশি গুরুত্ব না পাক তাই ক্যাপ
        match_score = (result_pts - 0.5) * 2 + gd_component  # রেঞ্জ মোটামুটি -3..3
        w = 0.5 ** (idx / half_life)
        score_w += match_score * w
        total_w += w
        idx += 1
    if total_w == 0:
        return None
    avg = score_w / total_w  # রেঞ্জ মোটামুটি -3..3
    return round(avg * 33.3, 1)  # -100..100 স্কেলে


def dynamic_home_advantage(home_venue_matches, away_venue_matches, team_id, default_boost: float = 1.10):
    """ফিক্সড ১.১x-এর বদলে টিম-নির্দিষ্ট হোম-বুস্ট বের করে: এই টিম হোমে খেললে ওভারঅল
    গড়ের তুলনায় কতটা বেশি গোল করে/কম খায়। যথেষ্ট sample (কমপক্ষে ৩ হোম ম্যাচ) না
    থাকলে default_boost-এ fallback করে।
    রিটার্ন করে (scored_multiplier, conceded_multiplier)।"""
    home_avg = recency_weighted_scored_conceded(home_venue_matches, team_id)
    overall_avg = recency_weighted_scored_conceded(
        home_venue_matches + away_venue_matches, team_id
    )
    if home_avg is None or overall_avg is None or len(home_venue_matches) < 3:
        return default_boost, 1.0

    home_scored, home_conceded = home_avg
    overall_scored, overall_conceded = overall_avg
    if overall_scored <= 0:
        scored_mult = default_boost
    else:
        # চরম মান এড়াতে ০.৮–১.৪ রেঞ্জে ক্ল্যাম্প করা হয়
        scored_mult = max(0.8, min(1.4, home_scored / overall_scored))
    if overall_conceded <= 0:
        conceded_mult = 1.0
    else:
        conceded_mult = max(0.7, min(1.2, home_conceded / overall_conceded))
    return round(scored_mult, 3), round(conceded_mult, 3)


def _dixon_coles_tau(hg: int, ag: int, home_exp: float, away_exp: float, rho: float) -> float:
    """Dixon & Coles (1997)-এর low-score correlation term। Poisson মডেল ধরে নেয়
    হোম ও অ্যাওয়ে গোল independent, কিন্তু বাস্তবে 0-0, 1-0, 0-1, 1-1-এর মতো
    কম-স্কোরিং ফলাফল সামান্য বেশি/কম ঘটে। rho (সাধারণত -0.05 থেকে -0.15) দিয়ে
    এই ৪টা স্কোরলাইনের probability সামান্য adjust করা হয়; বাকি সব স্কোরলাইনে tau=1।"""
    if hg == 0 and ag == 0:
        return 1 - (home_exp * away_exp * rho)
    if hg == 0 and ag == 1:
        return 1 + (home_exp * rho)
    if hg == 1 and ag == 0:
        return 1 + (away_exp * rho)
    if hg == 1 and ag == 1:
        return 1 - rho
    return 1.0


def dixon_coles_adjustment(score_probs: dict, home_exp: float, away_exp: float, rho: float = -0.11) -> dict:
    """score_probs (key=(hg,ag), value=poisson probability) নিয়ে 0-0/1-0/0-1/1-1
    এর জন্য tau-adjustment প্রয়োগ করে, তারপর সবগুলো probability আবার sum=1 হওয়ার
    জন্য renormalize করে। max_goals ছোট রাখা (৬) থাকলে normalization-এর প্রভাব
    নগণ্য।"""
    adjusted = {}
    for (hg, ag), p in score_probs.items():
        tau = _dixon_coles_tau(hg, ag, home_exp, away_exp, rho)
        adjusted[(hg, ag)] = max(0.0, p * tau)

    total = sum(adjusted.values())
    if total <= 0:
        return score_probs
    return {k: v / total for k, v in adjusted.items()}


def confidence_score(h2h_count: int, home_recent_count: int, away_recent_count: int,
                      venue_home_count: int, venue_away_count: int) -> int:
    """0-100 স্কেলে বলে দেয় প্রেডিকশনটা কতটা ডেটা-সমর্থিত। এটা probability না,
    বরং "এই প্রেডিকশনের পেছনে কতটা sample আছে" তার একটা measure — কম confidence
    মানে predictor.py-এর ফলাফল কম বিশ্বাসযোগ্য, ভুল না অগত্যা।
    প্রতিটা সিগন্যালের জন্য একটা saturating (diminishing-returns) স্কোর যোগ হয়:
    h2h ৫+ ম্যাচে full মার্ক, overall form ৬+ ম্যাচে, venue-specific form ৩+ ম্যাচে।"""
    def sat(count, target, weight):
        return weight * min(1.0, count / target)

    score = (
        sat(h2h_count, 5, 20)
        + sat(home_recent_count, 6, 20)
        + sat(away_recent_count, 6, 20)
        + sat(venue_home_count, 3, 20)
        + sat(venue_away_count, 3, 20)
    )
    return round(score)
