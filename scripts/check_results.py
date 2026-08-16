"""
check_results.py
Runs after generate_predictions.py (or separately, a few times a day) from
GitHub Actions. Checks whether the matches in ../data/predictions_log.json
that are still 'pending' have finished; if so, fetches the real score via
football_api.get_match_result() and uses predictor.check_pick_correctness()
to verify whether best_pick was right or wrong. Finally writes
../data/history.json (used by the frontend's History tab).
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))

import football_api
from predictor import check_pick_correctness

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
LOG_PATH = os.path.join(DATA_DIR, "predictions_log.json")
HISTORY_PATH = os.path.join(DATA_DIR, "history.json")

# There's no guarantee exactly when a match ends (stoppage time, extra
# time, penalties), so results are only checked this long after kickoff.
# Checking earlier risks catching a match still in progress, with a
# wrong/incomplete score.
RESULT_CHECK_DELAY = timedelta(minutes=115)

# How many recently-resolved matches to show in the History tab
HISTORY_LIMIT = 30

# Same as generate_predictions.py: if the daily quota drops below this
# number, remaining pending matches are skipped for this run (retried
# next run)
MIN_QUOTA_BUFFER = 5


def load_log() -> list:
    try:
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_log(log: list) -> None:
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def parse_match_date(match_date: str):
    if not match_date:
        return None
    try:
        return datetime.fromisoformat(match_date.replace("Z", "+00:00"))
    except ValueError:
        return None


def get_fixture_result(match_id):
    """
    football_api.get_match_result() returns
    {"status", "home_goals", "away_goals"} (all None if the match wasn't
    found). This just normalizes the "not found" case to None; everything
    else passes through directly.
    """
    result = football_api.get_match_result(match_id)
    if result.get("status") is None:
        return None
    return result


def update_pending(log: list) -> int:
    now = datetime.now(timezone.utc)
    updated = 0

    for entry in log:
        if entry.get("status") != "pending":
            continue

        match_dt = parse_match_date(entry.get("match_date"))
        if match_dt is None:
            continue
        if now < match_dt + RESULT_CHECK_DELAY:
            continue  # match shouldn't have finished yet; will retry next run

        remaining_quota = football_api.last_known_remaining_daily_quota
        if remaining_quota is not None and remaining_quota < MIN_QUOTA_BUFFER:
            print(
                f"stopping early: daily API quota nearly exhausted "
                f"(remaining={remaining_quota}); remaining pending matches "
                f"will be checked on the next run",
                file=sys.stderr,
            )
            break

        try:
            result = get_fixture_result(entry["match_id"])
        except Exception as e:
            print(f"result fetch failed for match {entry['match_id']}: {e}", file=sys.stderr)
            continue

        if not result or result.get("status") not in ("FT", "AET", "PEN"):
            continue  # not finished yet, or no data available

        home_goals = result.get("home_goals")
        away_goals = result.get("away_goals")
        if home_goals is None or away_goals is None:
            continue

        pick = entry.get("best_pick")
        if not pick or not pick.get("market"):
            entry["status"] = "no_pick"
            entry["actual_score"] = f"{home_goals}-{away_goals}"
            entry["checked_at"] = now.isoformat()
            continue

        is_correct = check_pick_correctness(pick["market"], home_goals, away_goals)
        entry["status"] = "correct" if is_correct else "incorrect"
        entry["actual_score"] = f"{home_goals}-{away_goals}"
        entry["checked_at"] = now.isoformat()
        updated += 1

        print(
            f"checked {entry['home_team']} vs {entry['away_team']}: "
            f"pick='{pick.get('market_label', pick['market'])}' "
            f"actual={home_goals}-{away_goals} -> {entry['status']}",
            file=sys.stderr,
        )

    return updated


def build_history(log: list, limit: int = HISTORY_LIMIT) -> list:
    done = [e for e in log if e.get("status") in ("correct", "incorrect")]
    done.sort(key=lambda e: e.get("match_date") or "", reverse=True)
    return done[:limit]


def main():
    log = load_log()
    updated = update_pending(log)
    save_log(log)

    history = build_history(log)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "matches": history,
    }
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    pending_count = sum(1 for e in log if e.get("status") == "pending")
    print(
        f"Checked results: {updated} match(es) newly resolved; "
        f"{pending_count} still pending; "
        f"wrote {len(history)} match(es) to {HISTORY_PATH}"
    )


if __name__ == "__main__":
    main()
