"""
predictor.py
High-selectivity football probability model designed for the API-Football
100 requests/day free tier.

Core:
- 15% H2H
- 40% recent form
- 45% venue form
- dynamic home advantage
- optional Phase-2 team statistics / standings / injuries
- Poisson + Dixon-Coles
- conservative reliability gate

This is a statistical estimator, not a guarantee.
"""
import math
from football_api import get_head_to_head, get_team_recent_form
from advanced_stats import (
    recency_weighted_scored_conceded,
    power_rating,
    dynamic_home_advantage,
    dixon_coles_adjustment,
    confidence_score,
)

H2H_WEIGHT = 0.15
FORM_WEIGHT = 0.40
VENUE_WEIGHT = 0.45

def _venue_split(matches, team_id):
    home = [m for m in matches if m.get("homeTeam", {}).get("id") == team_id]
    away = [m for m in matches if m.get("awayTeam", {}).get("id") == team_id]
    return home, away

def _weighted_avg(pairs):
    total_w = total_v = 0.0
    for value, weight in pairs:
        if value is None:
            continue
        total_v += value * weight
        total_w += weight
    return None if total_w == 0 else total_v / total_w

def _poisson_prob(k, lam):
    return (lam ** k) * math.exp(-lam) / math.factorial(k)

def _safe_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

def _stat_get(stats, *path):
    x = stats
    for key in path:
        if not isinstance(x, dict):
            return None
        x = x.get(key)
    return _safe_float(x)

def _team_stat_adjustment(stats, venue):
    """Small, capped adjustment. Missing API fields mean no adjustment."""
    if not stats:
        return 1.0, 1.0

    goals_for = _stat_get(stats, "goals", "for", "average", venue)
    goals_against = _stat_get(stats, "goals", "against", "average", venue)

    attack = 1.0
    defence = 1.0
    if goals_for is not None:
        # Around 1.35 goals is neutral; cap the influence.
        attack = max(0.90, min(1.12, 1.0 + (goals_for - 1.35) * 0.08))
    if goals_against is not None:
        # Lower conceded is better -> stronger multiplier below 1.
        defence = max(0.90, min(1.12, 1.0 + (1.35 - goals_against) * 0.08))
    return attack, defence

def _standing_adjustment(table, team_id):
    if not table:
        return 1.0
    row = next((r for r in table if r.get("team", {}).get("id") == team_id), None)
    if not row:
        return 1.0
    rank = _safe_float(row.get("rank"))
    if rank is None:
        return 1.0
    # Very small effect: standings should not overpower recent form.
    # 1st gets +2.5%, bottom gets roughly -2.5%.
    n = max(2, len(table))
    z = (n + 1 - 2 * rank) / max(1, n - 1)
    return max(0.975, min(1.025, 1.0 + 0.025 * z))

def _injury_adjustment(injuries, team_id):
    """Conservative count-only penalty because free API may not expose player quality."""
    count = sum(1 for x in (injuries or []) if x.get("team_id") == team_id)
    return max(0.92, 1.0 - 0.018 * min(count, 4))

def _reliability(pred):
    data = pred["confidence_score"]
    agreement = pred["signal_agreement"]
    edge = pred["best_market_probability"]
    # Reliability is deliberately stricter than confidence.
    return round(max(0, min(100, 0.45 * data + 0.30 * agreement + 0.25 * edge)))

def predict_match(home_team_id, away_team_id, max_goals=7, deep=None):
    h2h = get_head_to_head(home_team_id, away_team_id, limit=8)
    home_recent = get_team_recent_form(home_team_id, limit=10)
    away_recent = get_team_recent_form(away_team_id, limit=10)

    home_home, home_away = _venue_split(home_recent, home_team_id)
    away_home, away_away = _venue_split(away_recent, away_team_id)

    h2h_h = recency_weighted_scored_conceded(h2h, home_team_id)
    h2h_a = recency_weighted_scored_conceded(h2h, away_team_id)
    form_h = recency_weighted_scored_conceded(home_recent, home_team_id)
    form_a = recency_weighted_scored_conceded(away_recent, away_team_id)
    venue_h = recency_weighted_scored_conceded(home_home, home_team_id)
    venue_a = recency_weighted_scored_conceded(away_away, away_team_id)

    hs = _weighted_avg([
        (h2h_h[0] if h2h_h else None, H2H_WEIGHT),
        (form_h[0] if form_h else None, FORM_WEIGHT),
        (venue_h[0] if venue_h else None, VENUE_WEIGHT),
    ]) or 1.2
    hc = _weighted_avg([
        (h2h_h[1] if h2h_h else None, H2H_WEIGHT),
        (form_h[1] if form_h else None, FORM_WEIGHT),
        (venue_h[1] if venue_h else None, VENUE_WEIGHT),
    ]) or 1.2
    a_s = _weighted_avg([
        (h2h_a[0] if h2h_a else None, H2H_WEIGHT),
        (form_a[0] if form_a else None, FORM_WEIGHT),
        (venue_a[0] if venue_a else None, VENUE_WEIGHT),
    ]) or 1.2
    a_c = _weighted_avg([
        (h2h_a[1] if h2h_a else None, H2H_WEIGHT),
        (form_a[1] if form_a else None, FORM_WEIGHT),
        (venue_a[1] if venue_a else None, VENUE_WEIGHT),
    ]) or 1.2

    h_mult, h_con_mult = dynamic_home_advantage(home_home, home_away, home_team_id)
    home_xg = ((hs * h_mult) + a_c) / 2.0
    away_xg = (a_s + (hc * h_con_mult)) / 2.0

    # Phase-2 enrichments: only small, capped adjustments.
    deep = deep or {}
    hstats = deep.get("home_team_stats")
    astats = deep.get("away_team_stats")
    table = deep.get("standings")
    injuries = deep.get("injuries") or []

    h_attack, h_def = _team_stat_adjustment(hstats, "home")
    a_attack, a_def = _team_stat_adjustment(astats, "away")
    home_xg *= h_attack * a_def
    away_xg *= a_attack * h_def
    home_xg *= _standing_adjustment(table, home_team_id)
    away_xg *= _standing_adjustment(table, away_team_id)
    home_xg *= _injury_adjustment(injuries, home_team_id)
    away_xg *= _injury_adjustment(injuries, away_team_id)

    # Guard against pathological API values.
    home_xg = max(0.15, min(4.50, home_xg))
    away_xg = max(0.15, min(4.50, away_xg))

    probs = {}
    for hg in range(max_goals):
        for ag in range(max_goals):
            probs[(hg, ag)] = _poisson_prob(hg, home_xg) * _poisson_prob(ag, away_xg)
    probs = dixon_coles_adjustment(probs, home_xg, away_xg)

    hw = dr = aw = btts = over = 0.0
    for (hg, ag), p in probs.items():
        if hg > ag: hw += p
        elif hg == ag: dr += p
        else: aw += p
        if hg > 0 and ag > 0: btts += p
        if hg + ag >= 3: over += p

    dc1x = hw + dr
    dcx2 = dr + aw
    dc12 = hw + aw

    raw = {
        "Home Win": hw, "Draw": dr, "Away Win": aw,
        "Double Chance (Home/Draw)": dc1x,
        "Double Chance (Draw/Away)": dcx2,
        "Double Chance (Home/Away)": dc12,
        "Over 2.5 Goals": over, "Under 2.5 Goals": 1 - over,
        "Both Teams to Score - Yes": btts,
        "Both Teams to Score - No": 1 - btts,
    }

    ordered = sorted(raw.values(), reverse=True)
    best_p = ordered[0] if ordered else 0.0
    second_p = ordered[1] if len(ordered) > 1 else 0.0
    agreement = max(0.0, min(100.0, 50.0 + (best_p - second_p) * 250.0))
    conf = confidence_score(len(h2h), len(home_recent), len(away_recent),
                            len(home_home), len(away_away))
    signal_agreement = agreement
    temp = {
        "confidence_score": conf,
        "signal_agreement": signal_agreement,
        "best_market_probability": best_p * 100,
    }
    reliability = _reliability(temp)

    best_market = max(raw.items(), key=lambda kv: kv[1])[0]
    most_score = max(probs, key=probs.get)

    return {
        "home_expected_goals": round(home_xg, 2),
        "away_expected_goals": round(away_xg, 2),
        "home_win_pct": round(hw * 100, 1),
        "draw_pct": round(dr * 100, 1),
        "away_win_pct": round(aw * 100, 1),
        "btts_yes_pct": round(btts * 100, 1),
        "btts_no_pct": round((1 - btts) * 100, 1),
        "over_2_5_pct": round(over * 100, 1),
        "under_2_5_pct": round((1 - over) * 100, 1),
        "double_chance_1x_pct": round(dc1x * 100, 1),
        "double_chance_x2_pct": round(dcx2 * 100, 1),
        "double_chance_12_pct": round(dc12 * 100, 1),
        "most_likely_score": f"{most_score[0]}-{most_score[1]}",
        "has_real_data": any([h2h_h, h2h_a, form_h, form_a, venue_h, venue_a]),
        "home_power_rating": power_rating(home_recent, home_team_id),
        "away_power_rating": power_rating(away_recent, away_team_id),
        "confidence_score": conf,
        "signal_agreement": round(signal_agreement),
        "reliability_score": reliability,
        "best_market": best_market,
        "best_market_probability": round(best_p * 100, 1),
        "_score_probs": probs,
        "_raw_probs": raw,
    }

def fair_odds(probability):
    return float("inf") if probability <= 0 else round(1 / probability, 2)

def best_pick(prediction, min_probability=0.70, min_reliability=65):
    candidates = [
        (m, p) for m, p in prediction["_raw_probs"].items()
        if p >= min_probability
    ]
    if prediction.get("reliability_score", 0) < min_reliability or not candidates:
        return None
    market, p = max(candidates, key=lambda x: x[1])
    return {
        "market": market,
        "probability_pct": round(p * 100, 1),
        "fair_odds": fair_odds(p),
        "reliability_score": prediction["reliability_score"],
    }

def high_odds_pick(prediction, min_probability=0.60, min_reliability=70):
    # Higher-risk market is allowed only with stronger reliability.
    if prediction.get("reliability_score", 0) < min_reliability:
        return None
    candidates = [
        (m, p) for m, p in prediction["_raw_probs"].items()
        if p >= min_probability and fair_odds(p) >= 2.0
    ]
    if not candidates:
        return None
    market, p = max(candidates, key=lambda x: x[1])
    return {
        "market": market,
        "probability_pct": round(p * 100, 1),
        "fair_odds": fair_odds(p),
        "reliability_score": prediction["reliability_score"],
    }

def top_correct_scores(prediction, relative_threshold=0.55, max_scores=10):
    items = sorted(prediction["_score_probs"].items(), key=lambda x: x[1], reverse=True)
    if not items:
        return []
    cutoff = items[0][1] * relative_threshold
    out = []
    for (hg, ag), p in items:
        if out and p < cutoff:
            break
        if len(out) >= max_scores:
            break
        out.append({"score": f"{hg}-{ag}", "probability_pct": round(p*100,1),
                    "fair_odds": fair_odds(p)})
    return out

def check_pick_correctness(market, home_goals, away_goals):
    total = home_goals + away_goals
    both = home_goals > 0 and away_goals > 0
    if market == "Home Win": return home_goals > away_goals
    if market == "Draw": return home_goals == away_goals
    if market == "Away Win": return away_goals > home_goals
    if market == "Double Chance (Home/Draw)": return home_goals >= away_goals
    if market == "Double Chance (Draw/Away)": return away_goals >= home_goals
    if market == "Double Chance (Home/Away)": return home_goals != away_goals
    if market == "Over 2.5 Goals": return total >= 3
    if market == "Under 2.5 Goals": return total <= 2
    if market == "Both Teams to Score - Yes": return both
    if market == "Both Teams to Score - No": return not both
    return False
