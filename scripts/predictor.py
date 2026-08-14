"""
predictor.py
Poisson distribution ব্যবহার করে ম্যাচের সম্ভাব্য ফলাফলের probability হিসাব করে।

তিনটা সিগন্যাল মিলিয়ে প্রতিটা টিমের expected গোল বের করা হয়:
  1. Head-to-Head (দুই টিমের আগের মুখোমুখি লড়াই)         - weight 0.25
  2. Overall recent form (শেষ কয়েক ম্যাচ, যেকোনো ভেন্যু)   - weight 0.35
  3. Venue-specific form (হোম টিমের হোম-ফর্ম / অ্যাওয়ে টিমের অ্যাওয়ে-ফর্ম) - weight 0.40

যেকোনো একটা সিগন্যাল ডেটা না পেলে বাকিগুলো দিয়েই (re-normalize করে) হিসাব হয়।
সবগুলোই না পেলে ডিফল্ট মান (1.2, 1.2) ব্যবহার হয়।

গুরুত্বপূর্ণ: এটা একটা statistical estimate, guarantee না।
"""

import math
from football_api import get_head_to_head, get_team_recent_form

H2H_WEIGHT = 0.25
FORM_WEIGHT = 0.35
VENUE_WEIGHT = 0.40


def _venue_split(matches, team_id):
    """টিমের ম্যাচগুলোকে হোম-এ খেলা ও অ্যাওয়ে-তে খেলা ম্যাচে ভাগ করে।"""
    home_matches = [m for m in matches if m["homeTeam"]["id"] == team_id]
    away_matches = [m for m in matches if m["awayTeam"]["id"] == team_id]
    return home_matches, away_matches


def _avg_scored_conceded(matches, team_id):
    """একটা ম্যাচ-লিস্ট থেকে টিমের গড় স্কোর করা ও গোল খাওয়ার হিসাব। ডেটা না থাকলে None।"""
    scored, conceded, count = 0, 0, 0
    for m in matches:
        full_time = m.get("score", {}).get("fullTime", {})
        home_goals = full_time.get("home")
        away_goals = full_time.get("away")
        if home_goals is None or away_goals is None:
            continue
        home_id = m["homeTeam"]["id"]
        away_id = m["awayTeam"]["id"]
        if home_id == team_id:
            scored += home_goals
            conceded += away_goals
        elif away_id == team_id:
            scored += away_goals
            conceded += home_goals
        else:
            continue
        count += 1
    if count == 0:
        return None
    return scored / count, conceded / count


def _weighted_avg(pairs):
    """[(value_or_None, weight), ...] থেকে উপলব্ধ ভ্যালুগুলোর ওজনসহ গড় বের করে।"""
    total_w = 0.0
    total_v = 0.0
    for v, w in pairs:
        if v is None:
            continue
        total_v += v * w
        total_w += w
    if total_w == 0:
        return None
    return total_v / total_w


def _poisson_prob(k, lam):
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def predict_match(home_team_id: int, away_team_id: int, max_goals: int = 6):
    h2h = get_head_to_head(home_team_id, away_team_id, limit=10)
    home_recent = get_team_recent_form(home_team_id, limit=6)
    away_recent = get_team_recent_form(away_team_id, limit=6)

    home_home_matches, _ = _venue_split(home_recent, home_team_id)
    _, away_away_matches = _venue_split(away_recent, away_team_id)

    h2h_home = _avg_scored_conceded(h2h, home_team_id)
    h2h_away = _avg_scored_conceded(h2h, away_team_id)
    overall_home = _avg_scored_conceded(home_recent, home_team_id)
    overall_away = _avg_scored_conceded(away_recent, away_team_id)
    venue_home = _avg_scored_conceded(home_home_matches, home_team_id)
    venue_away = _avg_scored_conceded(away_away_matches, away_team_id)

    home_scored_avg = _weighted_avg([
        (h2h_home[0] if h2h_home else None, H2H_WEIGHT),
        (overall_home[0] if overall_home else None, FORM_WEIGHT),
        (venue_home[0] if venue_home else None, VENUE_WEIGHT),
    ])
    home_conceded_avg = _weighted_avg([
        (h2h_home[1] if h2h_home else None, H2H_WEIGHT),
        (overall_home[1] if overall_home else None, FORM_WEIGHT),
        (venue_home[1] if venue_home else None, VENUE_WEIGHT),
    ])
    away_scored_avg = _weighted_avg([
        (h2h_away[0] if h2h_away else None, H2H_WEIGHT),
        (overall_away[0] if overall_away else None, FORM_WEIGHT),
        (venue_away[0] if venue_away else None, VENUE_WEIGHT),
    ])
    away_conceded_avg = _weighted_avg([
        (h2h_away[1] if h2h_away else None, H2H_WEIGHT),
        (overall_away[1] if overall_away else None, FORM_WEIGHT),
        (venue_away[1] if venue_away else None, VENUE_WEIGHT),
    ])

    if home_scored_avg is None:
        home_scored_avg = 1.2
    if home_conceded_avg is None:
        home_conceded_avg = 1.2
    if away_scored_avg is None:
        away_scored_avg = 1.2
    if away_conceded_avg is None:
        away_conceded_avg = 1.2

    home_expected = ((home_scored_avg + away_conceded_avg) / 2) * 1.1
    away_expected = (away_scored_avg + home_conceded_avg) / 2

    home_win, draw, away_win = 0.0, 0.0, 0.0
    btts_yes = 0.0
    over_2_5 = 0.0
    score_probs = {}

    for hg in range(max_goals):
        for ag in range(max_goals):
            p = _poisson_prob(hg, home_expected) * _poisson_prob(ag, away_expected)
            score_probs[(hg, ag)] = p
            if hg > ag:
                home_win += p
            elif hg == ag:
                draw += p
            else:
                away_win += p
            if hg > 0 and ag > 0:
                btts_yes += p
            if hg + ag > 2.5:
                over_2_5 += p

    most_likely_score = max(score_probs, key=score_probs.get)

    double_chance_1x = home_win + draw
    double_chance_x2 = draw + away_win
    double_chance_12 = home_win + away_win

    return {
        "home_expected_goals": round(home_expected, 2),
        "away_expected_goals": round(away_expected, 2),
        "home_win_pct": round(home_win * 100, 1),
        "draw_pct": round(draw * 100, 1),
        "away_win_pct": round(away_win * 100, 1),
        "btts_yes_pct": round(btts_yes * 100, 1),
        "btts_no_pct": round((1 - btts_yes) * 100, 1),
        "over_2_5_pct": round(over_2_5 * 100, 1),
        "under_2_5_pct": round((1 - over_2_5) * 100, 1),
        "double_chance_1x_pct": round(double_chance_1x * 100, 1),
        "double_chance_x2_pct": round(double_chance_x2 * 100, 1),
        "double_chance_12_pct": round(double_chance_12 * 100, 1),
        "most_likely_score": f"{most_likely_score[0]}-{most_likely_score[1]}",
        "_score_probs": score_probs,
        "_raw_probs": {
            "Home Win": home_win,
            "Draw": draw,
            "Away Win": away_win,
            "Double Chance (Home/Draw)": double_chance_1x,
            "Double Chance (Draw/Away)": double_chance_x2,
            "Double Chance (Home/Away)": double_chance_12,
            "Over 2.5 Goals": over_2_5,
            "Under 2.5 Goals": 1 - over_2_5,
            "Both Teams to Score - Yes": btts_yes,
            "Both Teams to Score - No": 1 - btts_yes,
        },
    }


def fair_odds(probability: float) -> float:
    if probability <= 0:
        return float("inf")
    return round(1 / probability, 2)


def top_correct_scores(prediction: dict, n: int = 3):
    score_probs_raw = prediction["_score_probs"]
    sorted_scores = sorted(score_probs_raw.items(), key=lambda x: x[1], reverse=True)[:n]
    results = []
    for (hg, ag), p in sorted_scores:
        results.append({
            "score": f"{hg}-{ag}",
            "probability_pct": round(p * 100, 1),
            "fair_odds": fair_odds(p),
        })
    return results


def high_odds_pick(prediction: dict, min_odds: float = 2.00):
    return best_pick(prediction, min_odds=min_odds)


def combo_pick(match_picks: list, legs: int = 2):
    sorted_picks = sorted(
        [mp for mp in match_picks if mp["pick"] is not None],
        key=lambda mp: mp["pick"]["probability_pct"],
        reverse=True,
    )[:legs]

    if len(sorted_picks) < legs:
        return None

    combined_prob = 1.0
    combined_odds = 1.0
    legs_info = []
    for mp in sorted_picks:
        p = mp["pick"]["probability_pct"] / 100
        combined_prob *= p
        combined_odds *= mp["pick"]["fair_odds"]
        legs_info.append({
            "match": mp["match"],
            "market": mp["pick"]["market"],
            "probability_pct": mp["pick"]["probability_pct"],
            "fair_odds": mp["pick"]["fair_odds"],
        })

    return {
        "legs": legs_info,
        "combined_probability_pct": round(combined_prob * 100, 1),
        "combined_odds": round(combined_odds, 2),
    }


def check_pick_correctness(market: str, home_goals: int, away_goals: int) -> bool:
    total_goals = home_goals + away_goals
    both_scored = home_goals > 0 and away_goals > 0

    if market == "Home Win":
        return home_goals > away_goals
    if market == "Draw":
        return home_goals == away_goals
    if market == "Away Win":
        return away_goals > home_goals
    if market == "Double Chance (Home/Draw)":
        return home_goals >= away_goals
    if market == "Double Chance (Draw/Away)":
        return away_goals >= home_goals
    if market == "Double Chance (Home/Away)":
        return home_goals != away_goals
    if market == "Over 2.5 Goals":
        return total_goals > 2.5
    if market == "Under 2.5 Goals":
        return total_goals < 2.5
    if market == "Both Teams to Score - Yes":
        return both_scored
    if market == "Both Teams to Score - No":
        return not both_scored
    if "-" in market:
        try:
            pred_h, pred_a = map(int, market.split("-"))
            return pred_h == home_goals and pred_a == away_goals
        except ValueError:
            return False
    return False


def best_pick(prediction: dict, min_odds: float = 1.40):
    raw_probs = prediction["_raw_probs"]
    candidates = []
    for market, p in raw_probs.items():
        odds = fair_odds(p)
        if odds >= min_odds:
            candidates.append((market, p, odds))

    if not candidates:
        return None

    best = max(candidates, key=lambda x: x[1])
    return {
        "market": best[0],
        "probability_pct": round(best[1] * 100, 1),
        "fair_odds": best[2],
    }
