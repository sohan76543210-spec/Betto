"""
build_calibration_set.py
স্ট্যান্ডঅ্যালোন, ম্যানুয়াল-চালানোর টুল — GitHub Actions/CI-এর অংশ না।

tracking.run_calibration() একটা matches_provider() ফাংশন চায় যেটা ঐতিহাসিক,
ফলাফল-জানা ম্যাচের একটা লিস্ট রিটার্ন করে, প্রতিটায় থাকা দরকার:
    {"home_id", "away_id", "h2h", "home_recent", "away_recent",
     "home_home", "away_away", "home_goals", "away_goals"}

এই স্ক্রিপ্ট সেই ডেটা বানায় — একদম predict_match()-এ যেভাবে H2H/recent-form/
venue-split ডেটা জোগাড় করা হয়, ঠিক সেভাবেই, প্লাস প্রতিটা ম্যাচের আসল ফলাফল।

⚠️ সীমাবদ্ধতা (approximate calibration, exact না):
- get_team_recent_form() সবসময় "এখন থেকে সবচেয়ে সাম্প্রতিক" ম্যাচ ফেরত দেয়,
  "সেই ম্যাচের আগে পর্যন্ত" না — তাই সামান্য ডেটা-লিকেজ থাকতে পারে (ভবিষ্যতের
  কিছু ফর্ম-ডেটাও prediction-এর ইনপুটে ঢুকে যেতে পারে)। ছোট/মাঝারি sample-এ
  এটা calibration-এর দিক নির্দেশনার জন্য যথেষ্ট, কিন্তু ল্যাব-গ্রেড নিখুঁত না।
- প্রতিটা টিমের জন্য কয়েকটা API কল লাগে (recent_form + h2h + venue) —
  ফ্রি কোটার মধ্যে থাকতে TEAM_IDS ছোট রাখুন (৮-১৫টা টিম যথেষ্ট শুরুর জন্য)।

ব্যবহার:
    python scripts/build_calibration_set.py

    বা কোড থেকে:
        from build_calibration_set import matches_provider
        from tracking import run_calibration
        best = run_calibration(matches_provider)
"""
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))

import football_api

# --------------------------------------------------------------------------
# এখানে যে টিমগুলোর ওপর calibration চালাতে চান তাদের composite id বসান
# (যেমন "fdo:57" football-data.org-এর জন্য, "aps:33" api-sports.io-এর জন্য)।
# football_api.py-এর composite-id স্কিম অনুযায়ী "source:raw_id" ফরম্যাটে।
# আপনার data/predictions_log.json বা data/history.json-এর পুরনো এন্ট্রি থেকে
# home_team_id/away_team_id কপি করে এখানে বসাতে পারেন — যেসব টিম নিয়মিত
# predict করা হচ্ছে সেগুলোই এখানে দেওয়া ভালো (calibration সবচেয়ে relevant হবে)।
# --------------------------------------------------------------------------
TEAM_IDS = [
    # "fdo:57",   # উদাহরণ — এখানে আসল id বসান
    # "fdo:61",
    # "aps:33",
]

MATCHES_PER_TEAM = 10  # প্রতি টিমের সাম্প্রতিক কতগুলো finished ম্যাচ ব্যবহার হবে

# generate_predictions.py-এর মতোই একই buffer/guard-প্যাটার্ন — যাতে একই দিনে
# generate_predictions.py/check_results.py আগে থেকে কোটা খরচ করে ফেললে এই
# স্ট্যান্ডঅ্যালোন স্ক্রিপ্ট বাকি কোটা শেষ করে না দেয় (মাঝপথে থেমে যাবে)।
MIN_QUOTA_BUFFER = 10


def _quota_exhausted():
    remaining = football_api.last_known_remaining_daily_quota
    return remaining is not None and remaining < MIN_QUOTA_BUFFER


def _venue_split(matches, team_id):
    home = [m for m in matches if m.get("homeTeam", {}).get("id") == team_id]
    away = [m for m in matches if m.get("awayTeam", {}).get("id") == team_id]
    return home, away


def matches_provider():
    if not TEAM_IDS:
        print(
            "DEBUG: TEAM_IDS খালি — এই ফাইলের উপরের দিকে TEAM_IDS লিস্টে "
            "কয়েকটা composite team_id (যেমন 'fdo:57') বসান।",
            file=sys.stderr,
        )
        return []

    samples = []
    seen_match_ids = set()

    for team_id in TEAM_IDS:
        if _quota_exhausted():
            print(
                f"DEBUG: দৈনিক কোটা বাফারের ({MIN_QUOTA_BUFFER}) নিচে নেমে গেছে — "
                f"বাকি টিমগুলো (এখনো বাকি {len(TEAM_IDS) - TEAM_IDS.index(team_id)}টা) "
                "স্কিপ করে থেমে যাচ্ছি, যাতে সেদিনের bot-প্রেডিকশনের জন্য কোটা থাকে।",
                file=sys.stderr,
            )
            break
        try:
            team_matches = football_api.get_team_recent_form(team_id, limit=MATCHES_PER_TEAM)
        except Exception as e:
            print(f"DEBUG: get_team_recent_form failed for {team_id}: {e}", file=sys.stderr)
            continue

        for m in team_matches:
            if _quota_exhausted():
                print(
                    f"DEBUG: দৈনিক কোটা বাফারের ({MIN_QUOTA_BUFFER}) নিচে নেমে গেছে — "
                    "এই টিমের বাকি ম্যাচ enrichment স্কিপ করে থেমে যাচ্ছি।",
                    file=sys.stderr,
                )
                break
            match_id = m.get("id")
            if match_id in seen_match_ids:
                continue  # একই ম্যাচ দুই টিমের ফর্ম-লিস্টেই আসতে পারে (দুই টিমই TEAM_IDS-এ থাকলে)
            seen_match_ids.add(match_id)

            home_id = m.get("homeTeam", {}).get("id")
            away_id = m.get("awayTeam", {}).get("id")
            full_time = (m.get("score") or {}).get("fullTime") or {}
            home_goals = full_time.get("home")
            away_goals = full_time.get("away")

            if home_id is None or away_id is None or home_goals is None or away_goals is None:
                continue  # অসম্পূর্ণ এন্ট্রি — বাদ

            try:
                h2h = football_api.get_head_to_head(home_id, away_id, match_id=match_id, limit=8)
                home_recent = football_api.get_team_recent_form(home_id, limit=10)
                away_recent = football_api.get_team_recent_form(away_id, limit=10)
            except Exception as e:
                print(f"DEBUG: enrichment failed for match {match_id}: {e}", file=sys.stderr)
                continue

            home_home, _ = _venue_split(home_recent, home_id)
            _, away_away = _venue_split(away_recent, away_id)

            samples.append({
                "home_id": home_id,
                "away_id": away_id,
                "h2h": h2h,
                "home_recent": home_recent,
                "away_recent": away_recent,
                "home_home": home_home,
                "away_away": away_away,
                "home_goals": home_goals,
                "away_goals": away_goals,
            })

    print(f"DEBUG: matches_provider বানালো {len(samples)}টা calibration sample "
          f"({len(TEAM_IDS)}টা টিম থেকে)", file=sys.stderr)
    return samples


if __name__ == "__main__":
    import json
    from datetime import datetime, timezone
    from tracking import run_calibration

    result = run_calibration(matches_provider)
    print(result)

    # কম্পিউটার ছাড়া, GitHub Actions (workflow_dispatch) দিয়ে ফোন থেকে চালানো
    # হলে টার্মিনাল আউটপুট সরাসরি দেখা যায় না বলে ফলাফলটা repo-তে একটা ফাইলেও
    # লিখে রাখা হচ্ছে (../data/calibration_result.json) — ওয়ার্কফ্লো সেটা commit
    # করলে GitHub অ্যাপ/ওয়েবসাইট থেকেই ফাইলটা খুলে দেখা যাবে।
    out_path = os.path.join(os.path.dirname(__file__), "..", "data", "calibration_result.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "team_ids_used": TEAM_IDS,
            "result": result,
        }, f, ensure_ascii=False, indent=2)
    print(f"Calibration result written to {out_path}")
