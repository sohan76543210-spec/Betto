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
from predictor import predict_match, best_pick, top_correct_scores, high_odds_pick

MIN_ODDS = 1.40
HIGH_ODDS_THRESHOLD = 2.00
MAX_MATCHES = 10

DISCLAIMER = (
    "This is a statistical estimate, not a guaranteed outcome. The odds shown are "
    "our own model's calculation, not a bookmaker's real odds. No prediction system "
    "can ever be 100% accurate. Please make decisions responsibly."
)


def build():
    try:
        matches = football_api.get_upcoming_matches(days_ahead=1)
    except Exception as e:
        print(f"API error: {e}", file=sys.stderr)
        matches = []

    output_matches = []
    for m in matches:
        if len(output_matches) >= MAX_MATCHES:
            break
        try:
            pred = predict_match(m["homeTeam"]["id"], m["awayTeam"]["id"])
        except Exception as e:
            print(f"prediction failed for match {m.get('id')}: {e}", file=sys.stderr)
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

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": DISCLAIMER,
        "matches": output_matches,
    }

    out_path = os.path.join(os.path.dirname(__file__), "..", "data", "predictions.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"Wrote {len(output_matches)} matches to {out_path}")


if __name__ == "__main__":
    build()
