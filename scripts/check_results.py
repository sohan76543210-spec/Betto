"""
check_results.py
Runs daily from GitHub Actions (বাংলাদেশ সকাল ৫টা, cron "0 23 * * *" — আগের দিনের
top-pick ম্যাচগুলো ততক্ষণে শেষ হয়ে গেছে ধরে নেওয়া হয়)।

কাজ: ../data/predictions_log.json-এ status="pending" থাকা প্রতিটা এন্ট্রির জন্য
football_api.get_match_result() দিয়ে আসল ফলাফল আনে, best_pick/high_odds_pick
সঠিক হয়েছিল কিনা যাচাই করে (predictor.check_pick_correctness ব্যবহার করে),
এবং log এন্ট্রি + ../data/history.json দুটোই আপডেট করে।

match_date এখনো ভবিষ্যতে (এখনো খেলা শুরুই হয়নি) এমন pending এন্ট্রি স্কিপ করা হয়,
যাতে অকারণে API call না হয়।
"""

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

import football_api
from predictor import check_pick_correctness
from elo import EloStore

MIN_QUOTA_BUFFER = 5

LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "predictions_log.json")
HISTORY_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "history.json")
HISTORY_MAX_ENTRIES = 500


def _load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _pick_status(pick: dict | None, home_goals: int, away_goals: int) -> str:
    """pick None হলে 'no_pick', নাহলে market অনুযায়ী সঠিক/ভুল যাচাই করে।"""
    if pick is None or pick.get("market") is None:
        return "no_pick"
    correct = check_pick_correctness(pick["market"], home_goals, away_goals)
    return "correct" if correct else "incorrect"


def check_and_update():
    log = _load_json(LOG_PATH, [])
    history = _load_json(HISTORY_PATH, [])
    if not isinstance(history, list):
        # data/history.json ভুলবশত dict (বা অন্য কোনো non-list) আকারে সেভ হয়ে
        # থাকলে .append() ক্র্যাশ করে — সেফটিনেট হিসেবে খালি list দিয়ে রিসেট করা হলো
        print(
            f"check_results: history.json ছিল {type(history).__name__}, list আশা করা "
            "হয়েছিল — খালি list দিয়ে রিসেট করা হচ্ছে",
            file=sys.stderr,
        )
        history = []
    if not isinstance(log, list):
        print(
            f"check_results: predictions_log.json ছিল {type(log).__name__}, list আশা "
            "করা হয়েছিল — খালি list দিয়ে রিসেট করা হচ্ছে",
            file=sys.stderr,
        )
        log = []

    now_utc = datetime.now(timezone.utc)
    updated = 0
    skipped_not_started = 0
    skipped_no_result = 0
    elo_updated = 0
    # predictor.py-এর predict_match() একই ফাইল (data/elo_ratings.json) থেকে
    # রেটিং পড়ে — এই স্ক্রিপ্ট প্রতিদিন ফলাফল জানার পর সেই ফাইল আপডেট করে,
    # GitHub Actions ওয়ার্কফ্লো সেটা commit করে, পরের generate_predictions.py
    # রান তখন আপডেটেড রেটিং পায়। কোনো নতুন API কল লাগে না।
    elo_store = EloStore()

    for entry in log:
        if entry.get("status") != "pending":
            continue

        match_date_str = entry.get("match_date")
        if match_date_str:
            try:
                kickoff = datetime.fromisoformat(match_date_str.replace("Z", "+00:00"))
                # match_date প্রায়ই শুধু 'YYYY-MM-DD' (timezone-naive) হয়ে আসে,
                # কিন্তু now_utc সবসময় timezone-aware — তুলনার আগে naive হলে UTC
                # ধরে নিয়ে aware করে দেওয়া হচ্ছে (নাহলে TypeError হয়)।
                if kickoff.tzinfo is None:
                    kickoff = kickoff.replace(tzinfo=timezone.utc)
                if kickoff > now_utc:
                    skipped_not_started += 1
                    continue
            except ValueError:
                pass

        remaining_quota = football_api.last_known_remaining_daily_quota
        if remaining_quota is not None and remaining_quota < MIN_QUOTA_BUFFER:
            print(
                f"stopping early: daily API quota nearly exhausted (remaining={remaining_quota})",
                file=sys.stderr,
            )
            break

        match_id = entry.get("match_id")
        if match_id is None:
            continue

        try:
            result = football_api.get_match_result(match_id)
        except Exception as e:
            print(f"check_results: get_match_result failed for {match_id}: {e}", file=sys.stderr)
            continue

        if result.get("status") != "FT" or result.get("home_goals") is None:
            skipped_no_result += 1
            continue

        home_goals = result["home_goals"]
        away_goals = result["away_goals"]

        best_pick_status = _pick_status(entry.get("best_pick"), home_goals, away_goals)
        high_odds_status = _pick_status(entry.get("high_odds_pick"), home_goals, away_goals)

        # সামগ্রিক status: best_pick-কে primary ধরা হয় (predictions.json-এও সেটাই
        # হেডলাইন pick); best_pick না থাকলে high_odds_pick দিয়ে ফলব্যাক
        overall_status = best_pick_status if best_pick_status != "no_pick" else high_odds_status

        entry["status"] = overall_status
        entry["actual_score"] = f"{home_goals}-{away_goals}"
        entry["checked_at"] = now_utc.isoformat()
        entry["best_pick_status"] = best_pick_status
        entry["high_odds_pick_status"] = high_odds_status

        # Elo আপডেট — পুরনো লগ এন্ট্রিতে home_team_id/away_team_id না থাকলে
        # (এই পরিবর্তনের আগে তৈরি হওয়া এন্ট্রি) গায়েব থাকবে, সেক্ষেত্রে স্কিপ।
        home_team_id = entry.get("home_team_id")
        away_team_id = entry.get("away_team_id")
        if home_team_id and away_team_id:
            elo_store.update(home_team_id, away_team_id, home_goals, away_goals)
            elo_updated += 1

        history.append({
            "match_id": match_id,
            "competition": entry.get("competition"),
            "home_team": entry.get("home_team"),
            "away_team": entry.get("away_team"),
            "match_date": entry.get("match_date"),
            "best_pick": entry.get("best_pick"),
            "best_pick_status": best_pick_status,
            "high_odds_pick": entry.get("high_odds_pick"),
            "high_odds_pick_status": high_odds_status,
            "actual_score": entry["actual_score"],
            "checked_at": entry["checked_at"],
        })
        updated += 1

    if len(history) > HISTORY_MAX_ENTRIES:
        history = history[-HISTORY_MAX_ENTRIES:]

    with open(LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    if elo_updated:
        elo_store.save()

    print(
        f"Checked results: {updated} entries updated, "
        f"{skipped_not_started} skipped (not started yet), "
        f"{skipped_no_result} skipped (result not final yet), "
        f"{elo_updated} Elo ratings updated"
    )


if __name__ == "__main__":
    check_and_update()
