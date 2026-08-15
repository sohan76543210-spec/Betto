"""
generate_predictions.py
GitHub Actions থেকে প্রতিদিন চলে। football_api.py + predictor.py ব্যবহার করে
আজ/আগামীকালের ম্যাচের প্রেডিকশন বানিয়ে ../data/predictions.json ফাইলে লেখে।
"""

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

import football_api
from predictor import predict_match, best_pick, top_correct_scores, high_odds_pick, combo_pick

MIN_ODDS = 1.40
HIGH_ODDS_THRESHOLD = 2.00

# --------------------------------------------------------------------------
# ক্যাটাগরি থ্রেশহোল্ড: কোন ম্যাচ কোন ক্যাটাগরিতে পড়বে তা এই মানগুলো ঠিক করে।
# প্রয়োজনমতো টিউন করা যাবে — এগুলো শুরুর জন্য যুক্তিসঙ্গত ডিফল্ট।
# --------------------------------------------------------------------------
SAFE_TIP_MIN_PROB = 65.0          # "Safe & Sure Tips"-এ ঢুকতে best_pick probability কমপক্ষে এই %
OVER_UNDER_MIN_CONFIDENCE = 60.0  # Over/Under ক্যাটাগরিতে ঢুকতে over_2_5/under_2_5 কমপক্ষে এই %
CORRECT_SCORE_MIN_PROB = 12.0     # "Correct Score" ক্যাটাগরিতে ঢুকতে টপ স্কোরের probability কমপক্ষে এই %

# একাধিক ম্যাচ মিলিয়ে accumulator/ticket বানানোর টার্গেট combined odds
FOOTBALL_3ODDS_TARGET = 3.0
ODDS_10PLUS_TARGET = 10.0
ODDS_20PLUS_TARGET = 20.0
# --------------------------------------------------------------------------
# api-football ফ্রি প্ল্যানে দিনে মোট ১০০টা রিকোয়েস্ট পাওয়া যায়। প্রতিটা ম্যাচের
# প্রেডিকশনে গড়ে ~৩-৪টা কল লাগে (h2h + home form + away form, কখনো কখনো form
# কলে সিজন-ফলব্যাকের কারণে extra কল)। শুরুর fixtures fetch-এ ২টা কল যায়।
# তাই MAX_MATCHES এমনভাবে রাখা হয়েছে যাতে পুরো রান নিরাপদে দৈনিক কোটার মধ্যে
# শেষ হয় এবং কিছুটা বাফারও থাকে।
# --------------------------------------------------------------------------
MAX_MATCHES = 20
# কোটা এই সংখ্যার নিচে নেমে গেলে বাকি ম্যাচ প্রসেস না করে নিরাপদে থেমে যাওয়া হয়,
# যাতে ৪২৯ এরর দিয়ে একগাদা ম্যাচ নষ্ট হওয়ার বদলে যা হয়েছে তা সেভ থাকে।
MIN_QUOTA_BUFFER = 5

# --------------------------------------------------------------------------
# লিগ হোয়াইটলিস্ট: ব্যবহারকারীর সরবরাহ করা ঠিক এই ২০টা লিগ/কাপ-ই রাখা হয়েছে।
# ম্যাচ ফিল্টার হয় (country, league name) মিলিয়ে — যেটা এখানে নেই সেটা স্কিপ হবে।
#
# গুরুত্বপূর্ণ: এই নামগুলো একটা ভিন্ন অ্যাপের স্ক্রিনশট থেকে নেওয়া। api-sports.io
# হুবহু একই বানান/ফরম্যাট ব্যবহার করে কিনা তা নিশ্চিত নয় (যেমন: "Türkiye" বনাম
# "Turkey", বা "Persian Gulf" পুরো নাম হয়তো "Persian Gulf Pro League")। প্রথমবার
# চালিয়ে stderr লগে "league not in whitelist" মেসেজ চেক করুন — কোনো লিগ ভুলবশত
# বাদ পড়লে সেখান থেকে সঠিক বানান কপি করে নিচে ঠিক করে দিন।
# --------------------------------------------------------------------------

ALLOWED_LEAGUES = {
    ("United States", "MLS Next Pro"),
    ("Russia", "Premier League"),
    ("United Arab Emirates", "Pro League"),
    ("Scotland", "League One"),
    ("Iran", "Persian Gulf"),
    ("Norway", "Eliteserien"),
    ("Sweden", "Superettan"),
    ("Japan", "J. League"),
    ("Sweden", "Allsvenskan"),
    ("Belgium", "First Division A"),
    ("Türkiye", "1. Lig"),
    ("England", "National League"),
    ("Austria", "Bundesliga"),
    ("England", "Championship"),
    ("Italy", "Coppa Italia"),
    ("Portugal", "Liga Portugal"),
    ("Netherlands", "Eredivisie"),
    ("England", "League Two"),
    ("England", "League One"),
    ("Spain", "La Liga"),
    ("Spain", "LaLiga"),  # সেফটি-নেট: কোনো একটা ভ্যারিয়েন্ট মিলে যাবে
    ("Saudi Arabia", "Saudi Pro League"),
    ("Armenia", "Premier League"),
    ("Brazil", "Serie A"),
    ("International", "ASEAN Championship"),
    ("International", "Club Friendlies"),

    # --------------------------------------------------------------------
    # ইউরোপের "টপ ৫" লিগ + মেজর ইউরোপিয়ান কাপ। api-football (api-sports.io)-এর
    # leagues এন্ডপয়েন্ট থেকে ভেরিফাই করা নাম/দেশ ব্যবহার করা হয়েছে:
    #   /leagues?id=39  -> England / Premier League
    #   /leagues?id=140 -> Spain   / La Liga
    #   /leagues?id=135 -> Italy   / Serie A
    #   /leagues?id=78  -> Germany / Bundesliga
    #   /leagues?id=61  -> France  / Ligue 1
    #   /leagues?id=2   -> World   / UEFA Champions League
    #   /leagues?id=3   -> World   / UEFA Europa League
    #   /leagues?id=848 -> World   / UEFA Conference League
    # --------------------------------------------------------------------
    ("England", "Premier League"),
    ("Spain", "La Liga"),  # কনফার্মড বানান; উপরের ("Spain","LaLiga") সেফটি-নেট হিসেবে থাকল
    ("Italy", "Serie A"),
    ("Germany", "Bundesliga"),
    ("France", "Ligue 1"),
    ("World", "UEFA Champions League"),
    ("World", "UEFA Europa League"),
    ("World", "UEFA Conference League"),
}

# নাম-মিলের জন্য lowercase সেট বানিয়ে রাখা হচ্ছে যাতে প্রতিবার লুপে lower() কল করতে না হয়
ALLOWED_LEAGUES_LOWER = {(c.lower(), n.lower()) for c, n in ALLOWED_LEAGUES}


def is_allowed_league(match: dict) -> bool:
    """হোয়াইটলিস্টের লিগ হলে True; বাকি সব লিগ False (স্কিপ হবে)।"""
    competition = match.get("competition", {})
    country = (competition.get("country") or "").lower()
    name = (competition.get("name") or "").lower()
    return (country, name) in ALLOWED_LEAGUES_LOWER


DISCLAIMER = (
    "This is a statistical estimate, not a guaranteed outcome. The odds shown are "
    "our own model's calculation, not a bookmaker's real odds. No prediction system "
    "can ever be 100% accurate. Please make decisions responsibly."
)


def _match_label(m: dict) -> str:
    return f"{m['home_team']} vs {m['away_team']}"


def _build_ticket(match_picks: list, target_odds: float, min_legs: int = 2, max_legs: int = 8):
    """match_picks-এর সবচেয়ে বেশি probability-ওয়ালা পিকগুলো একে একে যোগ করে
    combined odds টার্গেটে পৌঁছানোর চেষ্টা করে। টার্গেটে পৌঁছালে সেটাই রিটার্ন করে;
    না পৌঁছালে সর্বোচ্চ যতটুকু বানানো গেছে সেটা রিটার্ন করে (না বানা গেলে None)।
    """
    best = None
    upper = min(max_legs, len(match_picks))
    for legs in range(min_legs, upper + 1):
        combo = combo_pick(match_picks, legs=legs)
        if combo is None:
            continue
        best = combo
        if combo["combined_odds"] >= target_odds:
            return combo
    return best


def build_categories(output_matches: list) -> dict:
    """Free/VIP অ্যাপের মতো ক্যাটাগরিতে ম্যাচগুলো ভাগ করে।

    নোট: এই মুহূর্তে শুধু ফুটবল ডেটা (api-football) আছে, তাই Basketball/Tennis/
    All Sport এবং HT/FT-এর মতো ক্যাটাগরি বানানো সম্ভব না — সেগুলোর জন্য আলাদা
    ডেটা সোর্স/মডেল দরকার। Free বনাম VIP-এর মধ্যে কোনো access-lock এখানে নেই,
    শুধু কনটেন্ট আলাদা করে দেওয়া হয়েছে।
    """

    matches_with_pick = [m for m in output_matches if m.get("best_pick")]

    # ---- Game Of The Day: সবচেয়ে বেশি confidence-এর একটা ম্যাচ ----
    game_of_the_day = None
    if matches_with_pick:
        game_of_the_day = max(
            matches_with_pick, key=lambda m: m["best_pick"]["probability_pct"]
        )

    # ---- Safe & Sure Tips ----
    safe_sure_tips = [
        m for m in matches_with_pick
        if m["best_pick"]["probability_pct"] >= SAFE_TIP_MIN_PROB
    ]
    safe_sure_tips.sort(key=lambda m: m["best_pick"]["probability_pct"], reverse=True)

    # ---- Over/Under Goal: over_2_5 বা under_2_5 যেটা confident সেটা পিক করা ----
    over_under_goal = []
    for m in output_matches:
        over_p = m["over_2_5_pct"]
        under_p = 100.0 - over_p  # over_2_5_pct থেকেই ডেরাইভড, predictor-এর under_2_5_pct এর সমান
        if over_p >= OVER_UNDER_MIN_CONFIDENCE:
            over_under_goal.append({
                "match": _match_label(m),
                "competition": m["competition"],
                "match_date": m["match_date"],
                "market": "Over 2.5 Goals",
                "probability_pct": round(over_p, 1),
            })
        elif under_p >= OVER_UNDER_MIN_CONFIDENCE:
            over_under_goal.append({
                "match": _match_label(m),
                "competition": m["competition"],
                "match_date": m["match_date"],
                "market": "Under 2.5 Goals",
                "probability_pct": round(under_p, 1),
            })
    over_under_goal.sort(key=lambda x: x["probability_pct"], reverse=True)

    # ---- Correct Score: টপ স্কোরের probability যথেষ্ট বেশি এমন ম্যাচ ----
    correct_score = []
    for m in output_matches:
        if not m["top_scores"]:
            continue
        top = m["top_scores"][0]
        if top["probability_pct"] >= CORRECT_SCORE_MIN_PROB:
            correct_score.append({
                "match": _match_label(m),
                "competition": m["competition"],
                "match_date": m["match_date"],
                "score": top["score"],
                "probability_pct": top["probability_pct"],
                "fair_odds": top["fair_odds"],
            })
    correct_score.sort(key=lambda x: x["probability_pct"], reverse=True)

    # ---- Odds accumulators/tickets: একাধিক ম্যাচ মিলিয়ে ----
    match_picks = [
        {"match": _match_label(m), "pick": m["best_pick"]}
        for m in matches_with_pick
    ]
    football_3odds = _build_ticket(match_picks, FOOTBALL_3ODDS_TARGET, min_legs=2)
    odds_10plus = _build_ticket(match_picks, ODDS_10PLUS_TARGET, min_legs=3)
    odds_20plus = _build_ticket(match_picks, ODDS_20PLUS_TARGET, min_legs=4)

    return {
        "game_of_the_day": game_of_the_day,
        "safe_sure_tips": safe_sure_tips,
        "over_under_goal": over_under_goal,
        "correct_score": correct_score,
        "football_3odds_ticket": football_3odds,
        "odds_10plus_ticket": odds_10plus,
        "odds_20plus_ticket": odds_20plus,
    }


def build():
    try:
        matches = football_api.get_upcoming_matches(days_ahead=1)
    except Exception as e:
        print(f"API error: {e}", file=sys.stderr)
        matches = []

    # আগে allowed-league ম্যাচগুলো বাছাই করে kickoff সময় অনুযায়ী সাজানো হচ্ছে,
    # যাতে MAX_MATCHES ক্যাপ প্রয়োগ হওয়ার সময় সবচেয়ে তাড়াতাড়ি শুরু হওয়া ম্যাচগুলো
    # অগ্রাধিকার পায় (API যে ক্রমে ফেরত দেয় সেটা সময়ানুক্রমিক নয়)।
    allowed_matches = []
    for m in matches:
        if is_allowed_league(m):
            allowed_matches.append(m)
        else:
            print(
                f"skipping {m['homeTeam']['name']} vs {m['awayTeam']['name']} "
                f"({m['competition'].get('country')} - {m['competition']['name']}): "
                f"league not in big/medium whitelist",
                file=sys.stderr,
            )

    allowed_matches.sort(key=lambda m: m.get("utcDate") or "")

    output_matches = []
    no_data_skipped = 0
    for m in allowed_matches:
        if len(output_matches) >= MAX_MATCHES:
            print(
                f"reached MAX_MATCHES={MAX_MATCHES} cap; "
                f"{len(allowed_matches) - len(output_matches)} more allowed-league "
                f"match(es) left unprocessed",
                file=sys.stderr,
            )
            break

        remaining_quota = football_api.last_known_remaining_daily_quota
        if remaining_quota is not None and remaining_quota < MIN_QUOTA_BUFFER:
            print(
                f"stopping early: daily API quota nearly exhausted "
                f"(remaining={remaining_quota}); "
                f"{len(allowed_matches) - len(output_matches)} more allowed-league "
                f"match(es) left unprocessed",
                file=sys.stderr,
            )
            break

        try:
            pred = predict_match(m["homeTeam"]["id"], m["awayTeam"]["id"])
        except Exception as e:
            print(f"prediction failed for match {m.get('id')}: {e}", file=sys.stderr)
            continue

        if not pred.get("has_real_data"):
            no_data_skipped += 1
            print(
                f"skipping {m['homeTeam']['name']} vs {m['awayTeam']['name']} "
                f"({m['competition']['name']}): no h2h/form/venue data available "
                f"from API-Football, would be pure fallback (1.2/1.2)",
                file=sys.stderr,
            )
            continue

        output_matches.append({
            "competition": m["competition"]["name"],
            "home_team": m["homeTeam"]["name"],
            "away_team": m["awayTeam"]["name"],
            "match_date": m.get("utcDate"),
            "home_expected_goals": pred["home_expected_goals"],
            "away_expected_goals": pred["away_expected_goals"],
            "most_likely_score": pred["most_likely_score"],
            "home_win_pct": pred["home_win_pct"],
            "draw_pct": pred["draw_pct"],
            "away_win_pct": pred["away_win_pct"],
            "over_2_5_pct": pred["over_2_5_pct"],
            "btts_yes_pct": pred["btts_yes_pct"],
            "best_pick": best_pick(pred, min_odds=MIN_ODDS),
            "high_odds_pick": high_odds_pick(pred, min_odds=HIGH_ODDS_THRESHOLD),
            "top_scores": top_correct_scores(pred, n=3),
        })

    categories = build_categories(output_matches)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": DISCLAIMER,
        "categories": categories,
        "matches": output_matches,
    }

    out_path = os.path.join(os.path.dirname(__file__), "..", "data", "predictions.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(
        f"Wrote {len(output_matches)} matches to {out_path} "
        f"(fetched {len(matches)} total NS fixtures today+tomorrow -> "
        f"{len(allowed_matches)} in whitelisted leagues -> "
        f"{no_data_skipped} dropped for lack of h2h/form data -> "
        f"{len(output_matches)} final)"
    )


if __name__ == "__main__":
    build()
