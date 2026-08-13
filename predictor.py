"""
predictor.py
Poisson distribution ব্যবহার করে ম্যাচের সম্ভাব্য ফলাফলের probability হিসাব করে।

গুরুত্বপূর্ণ: এটা একটা statistical estimate, guarantee না।
কোনো মডেলই ফুটবল ম্যাচের ফলাফল নিশ্চিতভাবে বলতে পারে না -
ইনজুরি, রেফারি সিদ্ধান্ত, আবহাওয়া, লাক - এসব ফ্যাক্টর মডেলে ধরা পড়ে না।
"""

import math
from football_api import get_team_recent_form


def _avg_goals(matches, team_id):
    """একটা টিমের শেষ ম্যাচগুলোতে গড়ে কত গোল করেছে ও খেয়েছে তা বের করে।"""
    scored, conceded, count = 0, 0, 0
    for m in matches:
        home = m["homeTeam"]["id"]
        away = m["awayTeam"]["id"]
        full_time = m.get("score", {}).get("fullTime", {})
        home_goals = full_time.get("home")
        away_goals = full_time.get("away")
        if home_goals is None or away_goals is None:
            continue
        if home == team_id:
            scored += home_goals
            conceded += away_goals
        elif away == team_id:
            scored += away_goals
            conceded += home_goals
        else:
            continue
        count += 1
    if count == 0:
        return 1.2, 1.2  # ডিফল্ট লিগ গড়
    return scored / count, conceded / count


def _poisson_prob(k, lam):
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def predict_match(home_team_id: int, away_team_id: int, max_goals: int = 6):
    """
    হোম ও অ্যাওয়ে টিমের সাম্প্রতিক ফর্ম থেকে expected goals (লাম্বডা) হিসাব করে,
    তারপর Poisson distribution দিয়ে স্কোরলাইন probability matrix বানায়।
    """
    home_matches = get_team_recent_form(home_team_id, limit=6)
    away_matches = get_team_recent_form(away_team_id, limit=6)

    home_scored_avg, home_conceded_avg = _avg_goals(home_matches, home_team_id)
    away_scored_avg, away_conceded_avg = _avg_goals(away_matches, away_team_id)

    # প্রত্যাশিত গোল (হোম অ্যাডভান্টেজ হিসেবে সামান্য বুস্ট যোগ করা হলো)
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

    # ডাবল চান্স মার্কেট (দুইটা সম্ভাব্য ফলাফল একসাথে কভার করে, তাই probability বেশি)
    double_chance_1x = home_win + draw   # হোম জিতবে অথবা ড্র
    double_chance_x2 = draw + away_win   # ড্র অথবা অ্যাওয়ে জিতবে
    double_chance_12 = home_win + away_win  # যেকোনো একটা দল জিতবে (ড্র বাদে)

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
        "_score_probs": score_probs,  # top_correct_scores() ব্যবহার করার জন্য
        # raw probability (0-1) গুলো best_pick() এর জন্য আলাদা করে রাখা
        "_raw_probs": {
            "হোম জয়": home_win,
            "ড্র": draw,
            "অ্যাওয়ে জয়": away_win,
            "ডাবল চান্স (হোম/ড্র)": double_chance_1x,
            "ডাবল চান্স (ড্র/অ্যাওয়ে)": double_chance_x2,
            "ডাবল চান্স (হোম/অ্যাওয়ে)": double_chance_12,
            "Over 2.5 গোল": over_2_5,
            "Under 2.5 গোল": 1 - over_2_5,
            "উভয় দল গোল করবে (BTTS Yes)": btts_yes,
            "উভয় দল গোল করবে না (BTTS No)": 1 - btts_yes,
        },
    }


def fair_odds(probability: float) -> float:
    """
    আমাদের মডেলের probability থেকে 'fair odds' বের করে (বুকমেকারের margin ছাড়া)।
    সূত্র: odds = 1 / probability
    এটা কোনো bookmaker-এর real odds না — আমাদের নিজস্ব মডেলের হিসাব।
    """
    if probability <= 0:
        return float("inf")
    return round(1 / probability, 2)


def top_correct_scores(prediction: dict, n: int = 3):
    """
    সবচেয়ে বেশি সম্ভাবনার n টা correct-score prediction, probability ও fair odds সহ।
    prediction dict-এর ভিতরের _score_probs ব্যবহার করে।
    """
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
    """
    High-Odds category: সাধারণ best_pick-এর মতোই, কিন্তু বেশি ঝুঁকি/বেশি রিটার্নের
    জন্য ন্যূনতম odds থ্রেশহোল্ড বেশি রাখা হয় (ডিফল্ট ২.০০)।
    """
    return best_pick(prediction, min_odds=min_odds)


def combo_pick(match_picks: list, legs: int = 2):
    """
    একাধিক ম্যাচের individual best_pick থেকে সবচেয়ে বেশি confident (সম্ভাবনার) কয়েকটা
    বেছে নিয়ে একটা accumulator/combo বেট তৈরি করে।

    match_picks: [{"match": "টিম A vs টিম B", "pick": best_pick()-এর রেজাল্ট}, ...]
    রিটার্ন করে সম্মিলিত probability ও combo odds (ধরে নেওয়া হচ্ছে প্রতিটা ম্যাচ independent)।
    """
    # সম্ভাবনা অনুযায়ী sort করে সবচেয়ে বেশি confident গুলো বাছাই
    sorted_picks = sorted(
        [mp for mp in match_picks if mp["pick"] is not None],
        key=lambda mp: mp["pick"]["probability_pct"],
        reverse=True,
    )[:legs]

    if len(sorted_picks) < legs:
        return None  # যথেষ্ট যোগ্য ম্যাচ নেই

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
    """
    ম্যাচ শেষ হওয়ার পর আসল স্কোর দিয়ে যাচাই করে যে সাজেস্ট করা market/pick সঠিক ছিল কিনা।
    accuracy tracking-এ ব্যবহৃত হয়।
    """
    total_goals = home_goals + away_goals
    both_scored = home_goals > 0 and away_goals > 0

    if market == "হোম জয়":
        return home_goals > away_goals
    if market == "ড্র":
        return home_goals == away_goals
    if market == "অ্যাওয়ে জয়":
        return away_goals > home_goals
    if market == "ডাবল চান্স (হোম/ড্র)":
        return home_goals >= away_goals
    if market == "ডাবল চান্স (ড্র/অ্যাওয়ে)":
        return away_goals >= home_goals
    if market == "ডাবল চান্স (হোম/অ্যাওয়ে)":
        return home_goals != away_goals
    if market == "Over 2.5 গোল":
        return total_goals > 2.5
    if market == "Under 2.5 গোল":
        return total_goals < 2.5
    if market == "উভয় দল গোল করবে (BTTS Yes)":
        return both_scored
    if market == "উভয় দল গোল করবে না (BTTS No)":
        return not both_scored
    # Correct score market হলে market স্ট্রিং হবে "hg-ag" ফরম্যাটে
    if "-" in market:
        try:
            pred_h, pred_a = map(int, market.split("-"))
            return pred_h == home_goals and pred_a == away_goals
        except ValueError:
            return False
    return False


def best_pick(prediction: dict, min_odds: float = 1.40):
    """
    সবগুলো মার্কেট থেকে যেগুলোর fair odds >= min_odds, তাদের মধ্যে
    সবচেয়ে বেশি probability-র মার্কেটটা বেছে নেয় (অর্থাৎ থ্রেশহোল্ডের ঠিক উপরে
    সবচেয়ে 'নিরাপদ' পছন্দ)।

    রিটার্ন করে: {"market": ..., "probability_pct": ..., "fair_odds": ...} অথবা None
    (যদি কোনো মার্কেটই থ্রেশহোল্ড পার না করে)
    """
    raw_probs = prediction["_raw_probs"]
    candidates = []
    for market, p in raw_probs.items():
        odds = fair_odds(p)
        if odds >= min_odds:
            candidates.append((market, p, odds))

    if not candidates:
        return None

    # সবচেয়ে বেশি probability (= থ্রেশহোল্ডের সবচেয়ে কাছের/নিরাপদ পছন্দ)
    best = max(candidates, key=lambda x: x[1])
    return {
        "market": best[0],
        "probability_pct": round(best[1] * 100, 1),
        "fair_odds": best[2],
    }
