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
    league_average_goals,
    rest_days_adjustment,
    referee_adjustment,
)
from elo import EloStore

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

def _cards_average(stats):
    """api-sports.io-এর /teams/statistics রেসপন্সে 'cards' ফিল্ডে yellow/red
    কার্ড সময়-বাকেট (0-15, 16-30, ...) অনুযায়ী ভাঙা থাকে; এখানে সেগুলো যোগ করে
    মোট ম্যাচ-সংখ্যা (fixtures.played.total) দিয়ে ভাগ করে গড় কার্ড/ম্যাচ বের
    করা হয়। কোনো নতুন API কল লাগে না — deep_enrich()-এ যে team_stats
    আগে থেকেই fetch হয়, এটা তার ভেতর থেকেই বের করে। ডেটা না থাকলে None।"""
    if not isinstance(stats, dict):
        return None
    fixtures_played = _stat_get(stats, "fixtures", "played", "total")
    if not fixtures_played:
        return None
    total_cards = 0.0
    found = False
    cards = stats.get("cards")
    if not isinstance(cards, dict):
        return None
    for card_type in ("yellow", "red"):
        intervals = cards.get(card_type)
        if not isinstance(intervals, dict):
            continue
        for bucket in intervals.values():
            if isinstance(bucket, dict):
                v = _safe_float(bucket.get("total"))
                if v:
                    total_cards += v
                    found = True
    if not found:
        return None
    return round(total_cards / fixtures_played, 2)

def _discipline_adjustment(stats):
    """Small, capped adjustment (max ±5%) based on average cards/match — a proxy
    for indiscipline/suspension-risk affecting a team's own attacking output.
    ~2.0 cards/match ধরা হয়েছে neutral বেসলাইন হিসেবে; এর বেশি হলে হালকা penalty।
    ডেটা না থাকলে (fdo সোর্স বা কভারেজ নেই) কোনো adjustment হয় না।"""
    avg = _cards_average(stats)
    if avg is None:
        return 1.0
    return max(0.95, min(1.0, 1.0 - (avg - 2.0) * 0.015))

def _team_stat_adjustment(stats, venue, baseline=1.35):
    """Small, capped adjustment. Missing API fields mean no adjustment.
    baseline: neutral গোল/ম্যাচ রেফারেন্স — ডিফল্ট 1.35, কিন্তু কলার যদি
    league_average_goals() থেকে আসল লিগ-গড় পাস করে, সেটা ব্যবহার হয় (নিচে দেখুন)।"""
    if not stats:
        return 1.0, 1.0

    goals_for = _stat_get(stats, "goals", "for", "average", venue)
    goals_against = _stat_get(stats, "goals", "against", "average", venue)

    attack = 1.0
    defence = 1.0
    if goals_for is not None:
        attack = max(0.90, min(1.12, 1.0 + (goals_for - baseline) * 0.08))
    if goals_against is not None:
        # Lower conceded is better -> stronger multiplier below 1.
        defence = max(0.90, min(1.12, 1.0 + (baseline - goals_against) * 0.08))
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

_elo_store = None


def _get_elo_store():
    # মডিউল-লেভেল singleton — একবার লোড হয়ে বারবার ব্যবহার হয়, প্রতি কলে
    # ডিস্ক থেকে re-read করতে হয় না। ম্যাচ ফলাফল আসার পর caller-কে
    # _get_elo_store().update(...) + .save() নিজে থেকে ডাকতে হবে (backtest.py
    # বা result-processing script থেকে) — predict_match() নিজে ডাকে না, কারণ
    # সেটার কাছে ফলাফল থাকে না।
    global _elo_store
    if _elo_store is None:
        _elo_store = EloStore()
    return _elo_store


def predict_match(home_team_id, away_team_id, max_goals=7, deep=None, match_id=None, match_date=None):
    # match_id ঐচ্ছিক: football-data.org (fdo) সোর্সের H2H এন্ডপয়েন্ট match-ভিত্তিক,
    # তাই router-কে এটা পাস করা দরকার। api-sports.io (aps) সোর্সে এটা ব্যবহার হয় না
    # (router নিজেই সেটা হ্যান্ডেল করে) — না দিলে fdo-এর জন্য শুধু H2H সিগন্যাল বাদ পড়বে।
    h2h = get_head_to_head(home_team_id, away_team_id, match_id=match_id, limit=8)
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

    # standings already fetched in deep_enrich (Phase 2, no extra API call) —
    # সেখান থেকে আসল লিগ-গড় গোল/ম্যাচ বের করে হার্ডকোড করা 1.35-এর বদলে
    # প্রতিটা লিগের নিজস্ব বেসলাইন ব্যবহার করা হচ্ছে (হাই-স্কোরিং vs
    # লো-স্কোরিং লিগের attack/defense strength আলাদাভাবে ক্যালিব্রেটেড হবে)।
    league_avg = league_average_goals(table)
    baseline = league_avg[0] if league_avg else 1.35
    h_attack, h_def = _team_stat_adjustment(hstats, "home", baseline)
    a_attack, a_def = _team_stat_adjustment(astats, "away", baseline)
    home_xg *= h_attack * a_def
    away_xg *= a_attack * h_def
    home_xg *= _discipline_adjustment(hstats)
    away_xg *= _discipline_adjustment(astats)
    home_xg *= _standing_adjustment(table, home_team_id)
    away_xg *= _standing_adjustment(table, away_team_id)
    home_xg *= _injury_adjustment(injuries, home_team_id)
    away_xg *= _injury_adjustment(injuries, away_team_id)

    # Elo: persistent শক্তির রেটিং, কোনো নতুন API কল লাগে না (elo.py দ্রষ্টব্য)।
    # সিজনের শুরুতে বা কম রিসেন্ট-ম্যাচ থাকা অবস্থায় power_rating()-এর চেয়ে
    # বেশি স্থিতিশীল সিগন্যাল দেয়।
    elo_store = _get_elo_store()
    h_elo, a_elo = elo_store.elo_xg_multiplier(home_team_id, away_team_id)
    home_xg *= h_elo
    away_xg *= a_elo

    # Rest-days / fixture congestion: home_recent ও away_recent-এ utcDate
    # এমনিতেই আছে, তাই নতুন কল ছাড়াই বের করা যায়।
    if match_date:
        home_xg *= rest_days_adjustment(home_recent, home_team_id, match_date)
        away_xg *= rest_days_adjustment(away_recent, away_team_id, match_date)

    # Referee card-tendency: deep.get("referee_name")/deep.get("referee_history")
    # দিলে প্রযোজ্য হয়; ফ্রি টিয়ারেও fixture রেসপন্সে referee নাম আসে, কিন্তু
    # per-referee history নিজে থেকে সময়ের সাথে জমাতে হবে (নতুন কল নয়, শুধু
    # ইতিমধ্যে দেখা fixture-গুলো থেকে aggregate করে রাখা)।
    ref_name = deep.get("referee_name")
    ref_history = deep.get("referee_history")
    if ref_name and ref_history:
        ref_mult = referee_adjustment(ref_name, ref_history)
        home_xg *= ref_mult
        away_xg *= ref_mult

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
        "home_elo_rating": round(elo_store.get(home_team_id), 1),
        "away_elo_rating": round(elo_store.get(away_team_id), 1),
        "home_cards_avg": _cards_average(hstats),
        "away_cards_avg": _cards_average(astats),
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

def best_pick(prediction, min_probability=0.0, min_reliability=0, min_odds=1.40):
    """min_odds: fair_odds = 1/probability, তাই min_odds=1.40 মানে probability
    সর্বোচ্চ ~71.4% পর্যন্ত market-ই বিবেচনা করা হয় (এর বেশি probability মানে
    odds 1.40-এর নিচে নেমে যায়, যেটা বেটিং ভ্যালুর দিক থেকে প্রায় অর্থহীন —
    তাই বাদ)। এই আপার-বাউন্ডের মধ্যে যে market-এর probability সবচেয়ে বেশি,
    সেটাই pick হয় (এখনো "best"/সবচেয়ে সম্ভাব্য, কিন্তু odds ≥ min_odds শর্তসাপেক্ষে)।
    কোনো market এই দুই শর্ত (min_probability, max_probability-from-min_odds) একসাথে
    পূরণ না করলে ম্যাচটার জন্য কোনো pick প্রকাশিত হবে না।"""
    max_probability = (1.0 / min_odds) if min_odds and min_odds > 0 else 1.0
    candidates = [
        (m, p) for m, p in prediction["_raw_probs"].items()
        if p >= min_probability and p <= max_probability
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
