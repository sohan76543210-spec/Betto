"""
advanced_stats.py
Pure-Python statistical helpers. No API calls.
"""
import math

def _sorted_desc(matches):
    return sorted(matches or [], key=lambda m: m.get("utcDate", ""), reverse=True)

def recency_weighted_scored_conceded(matches, team_id, half_life=4):
    matches = _sorted_desc(matches)
    tw = sw = cw = 0.0
    idx = 0
    for m in matches:
        ft = m.get("score", {}).get("fullTime", {})
        hg, ag = ft.get("home"), ft.get("away")
        if hg is None or ag is None:
            continue
        hi = m.get("homeTeam", {}).get("id")
        ai = m.get("awayTeam", {}).get("id")
        if hi == team_id:
            s, c = hg, ag
        elif ai == team_id:
            s, c = ag, hg
        else:
            continue
        w = 0.5 ** (idx / half_life)
        sw += s * w
        cw += c * w
        tw += w
        idx += 1
    if tw == 0:
        return None
    return sw / tw, cw / tw

def power_rating(matches, team_id, half_life=4):
    matches = _sorted_desc(matches)
    tw = score = 0.0
    idx = 0
    for m in matches:
        ft = m.get("score", {}).get("fullTime", {})
        hg, ag = ft.get("home"), ft.get("away")
        if hg is None or ag is None:
            continue
        hi = m.get("homeTeam", {}).get("id")
        ai = m.get("awayTeam", {}).get("id")
        if hi == team_id:
            gf, ga = hg, ag
        elif ai == team_id:
            gf, ga = ag, hg
        else:
            continue
        result = 1.0 if gf > ga else 0.5 if gf == ga else 0.0
        gd = max(-3, min(3, gf - ga)) / 3.0
        match_score = (result - 0.5) * 2 + gd
        w = 0.5 ** (idx / half_life)
        score += match_score * w
        tw += w
        idx += 1
    return None if tw == 0 else round((score / tw) * 33.3, 1)

def dynamic_home_advantage(home_venue_matches, home_other_matches, team_id, default_boost=1.08):
    h = recency_weighted_scored_conceded(home_venue_matches, team_id)
    o = recency_weighted_scored_conceded(
        (home_venue_matches or []) + (home_other_matches or []), team_id
    )
    if h is None or o is None or len(home_venue_matches or []) < 3:
        return default_boost, 1.0
    hs, hc = h
    os, oc = o
    scored_mult = default_boost if os <= 0 else max(0.88, min(1.25, hs / os))
    conceded_mult = 1.0 if oc <= 0 else max(0.80, min(1.15, hc / oc))
    return round(scored_mult, 3), round(conceded_mult, 3)

def _tau(hg, ag, hxg, axg, rho):
    if hg == 0 and ag == 0:
        return 1 - hxg * axg * rho
    if hg == 0 and ag == 1:
        return 1 + hxg * rho
    if hg == 1 and ag == 0:
        return 1 + axg * rho
    if hg == 1 and ag == 1:
        return 1 - rho
    return 1.0

def dixon_coles_adjustment(score_probs, home_exp, away_exp, rho=-0.11):
    adjusted = {
        k: max(0.0, p * _tau(k[0], k[1], home_exp, away_exp, rho))
        for k, p in score_probs.items()
    }
    total = sum(adjusted.values())
    if total <= 0:
        return score_probs
    return {k: v / total for k, v in adjusted.items()}

def league_average_goals(table):
    """standings table (already fetched in deep_enrich, Phase 2 — no extra API
    call) থেকে লিগের প্রকৃত গড় গোল/ম্যাচ বের করে। এটা _team_stat_adjustment-এর
    hardcoded 1.35 বেসলাইনের বদলে ব্যবহার করলে হাই-স্কোরিং (Eredivisie) ও
    লো-স্কোরিং (Ligue 1, Serie A) লিগের attack/defense strength আলাদাভাবে
    ক্যালিব্রেটেড হয়। রিটার্ন করে (avg_goals_for, avg_goals_against) — টেবিলে
    played/goals ডেটা না থাকলে None।"""
    if not table:
        return None
    total_for = total_against = total_played = 0.0
    for row in table:
        all_stats = row.get("all") or {}
        played = all_stats.get("played")
        goals = all_stats.get("goals") or {}
        gf = goals.get("for")
        ga = goals.get("against")
        if not played or gf is None or ga is None:
            continue
        total_for += gf
        total_against += ga
        total_played += played
    if total_played == 0:
        return None
    return total_for / total_played, total_against / total_played

def confidence_score(h2h_count, home_recent_count, away_recent_count,
                     venue_home_count, venue_away_count):
    def sat(count, target, weight):
        return weight * min(1.0, count / target)
    return round(
        sat(h2h_count, 5, 20) +
        sat(home_recent_count, 8, 20) +
        sat(away_recent_count, 8, 20) +
        sat(venue_home_count, 4, 20) +
        sat(venue_away_count, 4, 20)
    )
