"""
generate_predictions.py
Runs daily from GitHub Actions. Uses football_api.py + predictor.py to build
predictions for today's/tomorrow's matches and writes them to
../data/predictions.json.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))

import football_api
from predictor import (
    predict_match,
    best_pick,
    top_correct_scores,
    high_odds_pick,
    apply_injury_adjustment,
)

MIN_ODDS = 1.40
HIGH_ODDS_THRESHOLD = 2.00
# Phase 1 স্ক্রিনিং-এ যত ম্যাচ ভালো লাগুক না কেন, screening_score এই থ্রেশহোল্ডের
# নিচে হলে top pick হিসেবে নেওয়া হবে না — অর্থাৎ কোনো দিন সব ম্যাচ অনিশ্চিত/কম-ডেটা
# হলে top_picks_count 5-এর কম (এমনকি 0) হতে পারে। "কম কিন্তু নিশ্চিত" ভালো,
# "৫টা পূরণ করার জন্য দুর্বল পিক ঢোকানো" খারাপ। মান নির্বাচন: confidence(0-100) ×
# strongest_outcome_pct(0-100)-এর প্রোডাক্ট, তাই maximum সম্ভাব্য মান 10000।
# 45%+ confidence ও 55%+ strongest outcome (মোটামুটি যুক্তিসঙ্গত ন্যূনতম) মিললে
# প্রায় 2475 হয় — তাই থ্রেশহোল্ড 2400-এ সেট করা হলো।
MIN_SCREENING_SCORE = 2400

# বাংলাদেশ সবসময় UTC+6 (কোনো DST নেই), তাই fixed offset যথেষ্ট।
BD_OFFSET = timedelta(hours=6)

# --------------------------------------------------------------------------
# api-football's free plan allows 100 requests per day total.
#
# TWO-PHASE PIPELINE (১০০ request-এর মধ্যে ম্যাচ কভার করার কৌশল):
#
#   Phase 1 — Cheap screening: বাংলাদেশ সময় "আজ সকাল ৬টা থেকে আগামীকাল সকাল
#   ৬টা" এই ২৪ ঘণ্টার উইন্ডোতে যত allowed-league ম্যাচ আছে, প্রতিটার জন্য শুধু
#   h2h + recent form fetch করে Poisson দিয়ে বেসিক prediction বানানো হয়।
#   football_api.py-তে team-level caching থাকায় একই টিম একাধিক ম্যাচে থাকলেও
#   দ্বিতীয়বার fetch হয় না।
#
#   Phase 2 — Deep analysis + FINAL SELECTION: Phase 1-এর confidence + edge
#   অনুযায়ী সবচেয়ে "sure" TOP_PICKS_COUNT-টা ম্যাচ বেছে, শুধু ওগুলোর জন্য
#   অতিরিক্ত costly কল করা হয় (team statistics, standings, injuries), এবং
#   চূড়ান্ত predictions.json-এ শুধু এই কয়টা ম্যাচই থাকে — বাকিগুলো বাদ যায়।
# --------------------------------------------------------------------------
MAX_MATCHES = 40          # Phase 1 (cheap) এ প্রসেস করা ম্যাচের সিলিং
TOP_PICKS_COUNT = 5       # ফাইনাল আউটপুটে থাকা সবচেয়ে sure ম্যাচের সংখ্যা
# If the quota drops below this number, remaining matches are skipped
# instead of processed, so a 429 error doesn't waste a batch of matches
# that would otherwise be lost — whatever was already done stays saved.
MIN_QUOTA_BUFFER = 5


def bd_prediction_window_utc():
    """বাংলাদেশ সময় 'আজ সকাল ৬টা থেকে আগামীকাল সকাল ৬টা' উইন্ডোটা UTC datetime-এ
    হিসাব করে ফেরত দেয়: (window_start_utc, window_end_utc, target_utc_date_iso)।

    যেহেতু বাংলাদেশ অফসেট ঠিক +৬ ঘণ্টা, তাই এই উইন্ডো UTC-তে গিয়ে ঠিক একটা
    পুরো UTC ক্যালেন্ডার দিন হয়ে যায় (00:00 UTC থেকে পরদিন 00:00 UTC) —
    তাই মাত্র ১টা UTC তারিখের ফিক্সচার fetch করলেই যথেষ্ট। (৬am BD - ৬h = 00:00
    UTC একই তারিখে।)

    স্ক্রিপ্টটা সাধারণত বাংলাদেশ সময় সকাল ৬টায় (cron) চলার কথা, তখন 'আজ' মানেই
    এই মুহূর্তের BD তারিখ। যদি কখনো সকাল ৬টার আগে manual/workflow_dispatch
    দিয়ে চালানো হয়, তাহলে সবচেয়ে সাম্প্রতিক অতিক্রান্ত ৬am (অর্থাৎ গতকাল সকাল
    ৬টা) থেকে উইন্ডো শুরু ধরা হয়, যাতে ফলাফল সবসময় সামঞ্জস্যপূর্ণ থাকে।
    """
    now_utc = datetime.now(timezone.utc)
    now_bd = now_utc + BD_OFFSET
    if now_bd.hour >= 6:
        window_start_date = now_bd.date()
    else:
        window_start_date = now_bd.date() - timedelta(days=1)

    window_start_bd = datetime(
        window_start_date.year, window_start_date.month, window_start_date.day, 6, 0, 0
    )
    window_end_bd = window_start_bd + timedelta(days=1)

    window_start_utc = window_start_bd - BD_OFFSET
    window_end_utc = window_end_bd - BD_OFFSET
    target_utc_date = window_start_utc.date().isoformat()

    return (
        window_start_utc.replace(tzinfo=timezone.utc),
        window_end_utc.replace(tzinfo=timezone.utc),
        target_utc_date,
    )

# --------------------------------------------------------------------------
# League whitelist: exactly these 20 leagues/cups the user supplied are
# kept. Matches are filtered by (country, league name) match — anything
# not in this list is skipped.
#
# IMPORTANT: these names were taken from a different app's screenshots.
# It isn't confirmed that api-sports.io uses the exact same spelling/format
# (e.g. "Türkiye" vs "Turkey", or "Persian Gulf" whose full name might be
# "Persian Gulf Pro League"). After the first run, check the stderr log for
# "league not in whitelist" messages — if a league was accidentally
# dropped, copy the correct spelling from there and fix it below.
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
    ("Spain", "LaLiga"),  # safety net: whichever variant matches
    ("Saudi Arabia", "Saudi Pro League"),
    ("Armenia", "Premier League"),
    ("Brazil", "Serie A"),
    ("International", "ASEAN Championship"),
    ("International", "Club Friendlies"),

    # --------------------------------------------------------------------
    # Europe's "top 5" leagues + major European cups. Names/countries
    # verified from api-football's (api-sports.io) leagues endpoint:
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
    ("Spain", "La Liga"),  # confirmed spelling; the ("Spain","LaLiga") entry above is a safety net
    ("Italy", "Serie A"),
    ("Germany", "Bundesliga"),
    ("France", "Ligue 1"),
    ("World", "UEFA Champions League"),
    ("World", "UEFA Europa League"),
    ("World", "UEFA Conference League"),
}

# A lowercase set is built once for name matching, so lower() doesn't need
# to be called on every loop iteration
ALLOWED_LEAGUES_LOWER = {(c.lower(), n.lower()) for c, n in ALLOWED_LEAGUES}


def is_allowed_league(match: dict) -> bool:
    """True if the league is in the whitelist; False for everything else (skipped)."""
    competition = match.get("competition", {})
    country = (competition.get("country") or "").lower()
    name = (competition.get("name") or "").lower()
    return (country, name) in ALLOWED_LEAGUES_LOWER


DISCLAIMER = (
    "This is a statistical estimate, not a guaranteed outcome. The odds shown are "
    "our own model's calculation, not a bookmaker's real odds. No prediction system "
    "can ever be 100% accurate. Please make decisions responsibly."
)


def humanize_pick(pick: dict | None, home_team: str, away_team: str) -> dict | None:
    """
    predictor.py's 'market' field holds generic labels like "Home Win"/
    "Away Win" (left unchanged here because check_pick_correctness() relies
    on those exact strings). This function adds a new 'market_label' field
    to the same dict, where "Home Win"/"Away Win" is replaced with the
    actual team name — so it's easier for users to read.
    """
    if pick is None:
        return None

    market = pick.get("market")
    label_map = {
        "Home Win": f"{home_team} Win",
        "Away Win": f"{away_team} Win",
        "Draw": "Draw",
        "Double Chance (Home/Draw)": f"{home_team} Win or Draw",
        "Double Chance (Draw/Away)": f"Draw or {away_team} Win",
        "Double Chance (Home/Away)": f"{home_team} Win or {away_team} Win",
        "Over 2.5 Goals": "Over 2.5 Goals",
        "Under 2.5 Goals": "Under 2.5 Goals",
        "Both Teams to Score - Yes": "Both Teams to Score - Yes",
        "Both Teams to Score - No": "Both Teams to Score - No",
    }
    market_label = label_map.get(market, market)

    return {**pick, "market_label": market_label}


def screening_score(pred: dict) -> float:
    """Phase 1 → Phase 2 বাছাইয়ের জন্য: confidence ও স্পষ্ট ফেভারিট থাকা দুটোকেই
    গুরুত্ব দেয় (শুধু বেশি সম্ভাবনার দিকে ঝুঁকে যাওয়া pick + যথেষ্ট ডেটা-সাপোর্ট থাকা
    ম্যাচগুলোই deep-analysis-এর যোগ্য বলে ধরা হয়)।"""
    outcome_pcts = [pred["home_win_pct"], pred["draw_pct"], pred["away_win_pct"]]
    strongest_signal = max(outcome_pcts)
    confidence = pred.get("confidence_score", 0)
    return confidence * strongest_signal


def deep_enrich(match_id: int, home_id: int, away_id: int, league_id: int, season: int) -> dict:
    """Phase 2: শুধু shortlisted ম্যাচের জন্য costly কল — team statistics
    (হোম+অ্যাওয়ে), league standings, injuries। প্রতিটা ফাংশনই football_api.py-তে
    cached, তাই একই লিগ/টিম একাধিক shortlisted ম্যাচে থাকলেও দ্বিতীয়বার fetch হয় না।
    কোনো একটা কল ব্যর্থ হলে বাকি deep_analysis ফিল্ডগুলো এখনো ব্যবহারযোগ্য থাকে
    (partial failure পুরো prediction-কে নষ্ট করে না)।"""
    result = {
        "home_team_stats": None,
        "away_team_stats": None,
        "standings": None,
        "injuries": [],
        "odds": None,
    }
    try:
        result["home_team_stats"] = football_api.get_team_statistics(home_id, league_id, season)
    except Exception as e:
        print(f"deep_enrich: team_statistics failed for home team {home_id}: {e}", file=sys.stderr)
    try:
        result["away_team_stats"] = football_api.get_team_statistics(away_id, league_id, season)
    except Exception as e:
        print(f"deep_enrich: team_statistics failed for away team {away_id}: {e}", file=sys.stderr)
    try:
        result["standings"] = football_api.get_standings(league_id, season)
    except Exception as e:
        print(f"deep_enrich: standings failed for league {league_id}: {e}", file=sys.stderr)
    try:
        result["injuries"] = football_api.get_injuries(match_id)
    except Exception as e:
        print(f"deep_enrich: injuries failed for fixture {match_id}: {e}", file=sys.stderr)
    try:
        # শুধু calibration/diagnostic-এর জন্য (দেখুন football_api.get_odds
        # docstring) — prediction-এর কোনো সংখ্যা এটা বদলায় না।
        result["odds"] = football_api.get_odds(match_id)
    except Exception as e:
        print(f"deep_enrich: odds failed for fixture {match_id}: {e}", file=sys.stderr)
    return result


def append_to_log(output_matches: list, log_path: str) -> int:
    """
    Saves each prediction with a 'pending' status into
    ../data/predictions_log.json, so that check_results.py can later match
    it against the real result once the match has finished (for the
    History tab). predictions.json gets overwritten every day, so this
    separate log file is what preserves history. Matches without a
    match_id can't be tracked (no way to look up the result later), so
    they're skipped.
    """
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            log = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        log = []

    existing_ids = {entry.get("match_id") for entry in log}

    added = 0
    for m in output_matches:
        match_id = m.get("match_id")
        if match_id is None or match_id in existing_ids:
            continue

        log.append({
            "match_id": match_id,
            "competition": m["competition"],
            "home_team": m["home_team"],
            "away_team": m["away_team"],
            "match_date": m["match_date"],
            "best_pick": m.get("best_pick"),
            "high_odds_pick": m.get("high_odds_pick"),
            # analyze_accuracy.py-তে confidence-bucket-ভিত্তিক accuracy চেক
            # করার জন্য প্রেডিকশনের সময়কার confidence_score সংরক্ষণ করা হয়
            "confidence_score_at_prediction": m.get("confidence_score"),
            "injury_adjusted": m.get("injury_adjusted", False),
            "status": "pending",       # pending -> correct / incorrect / no_pick
            "actual_score": None,
            "checked_at": None,
        })
        existing_ids.add(match_id)
        added += 1

    # Trim the oldest entries so the log file doesn't grow without bound
    LOG_MAX_ENTRIES = 1000
    if len(log) > LOG_MAX_ENTRIES:
        log = log[-LOG_MAX_ENTRIES:]

    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)

    return added


def build():
    window_start_utc, window_end_utc, target_utc_date = bd_prediction_window_utc()

    try:
        matches = football_api.get_matches_for_date(target_utc_date)
    except Exception as e:
        print(f"API error: {e}", file=sys.stderr)
        matches = []

    # Extra safety: get_matches_for_date already fetches exactly the UTC
    # calendar date that this BD 6am-to-6am window maps to, but we still
    # filter by exact timestamp in case a fixture's date is right at the
    # boundary.
    windowed_matches = []
    for m in matches:
        utc_date_str = m.get("utcDate")
        if not utc_date_str:
            continue
        try:
            kickoff = datetime.fromisoformat(utc_date_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        if window_start_utc <= kickoff < window_end_utc:
            windowed_matches.append(m)

    # First, allowed-league matches are collected and sorted by kickoff
    # time, so that when the MAX_MATCHES cap is applied, the earliest-
    # starting matches get priority (the API doesn't return them in
    # chronological order).
    allowed_matches = []
    for m in windowed_matches:
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

    # ---------------------------------------------------------------
    # PHASE 1 — cheap screening: h2h + recent form দিয়ে সব allowed ম্যাচের
    # বেসিক prediction বানানো হয়।
    # ---------------------------------------------------------------
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

        home_team_name = m["homeTeam"]["name"]
        away_team_name = m["awayTeam"]["name"]

        league_name = m["competition"]["name"]
        league_country = m["competition"].get("country")
        competition_display = (
            f"{league_name} ({league_country})" if league_country else league_name
        )

        output_matches.append({
            "match_id": m.get("id"),
            "competition": competition_display,
            "league_id": m["competition"].get("id"),
            "season": m["competition"].get("season"),
            "home_team": home_team_name,
            "away_team": away_team_name,
            "home_team_id": m["homeTeam"]["id"],
            "away_team_id": m["awayTeam"]["id"],
            "match_date": m.get("utcDate"),
            "home_expected_goals": pred["home_expected_goals"],
            "away_expected_goals": pred["away_expected_goals"],
            "most_likely_score": pred["most_likely_score"],
            "home_win_pct": pred["home_win_pct"],
            "draw_pct": pred["draw_pct"],
            "away_win_pct": pred["away_win_pct"],
            "over_2_5_pct": pred["over_2_5_pct"],
            "btts_yes_pct": pred["btts_yes_pct"],
            "home_power_rating": pred.get("home_power_rating"),
            "away_power_rating": pred.get("away_power_rating"),
            "confidence_score": pred.get("confidence_score"),
            "best_pick": humanize_pick(
                best_pick(pred, min_odds=MIN_ODDS), home_team_name, away_team_name
            ),
            "high_odds_pick": humanize_pick(
                high_odds_pick(pred, min_odds=HIGH_ODDS_THRESHOLD),
                home_team_name,
                away_team_name,
            ),
            "top_scores": top_correct_scores(pred),
            "top_pick": False,
            "deep_analysis": None,
            "_screening_score": screening_score(pred),
        })

    # ---------------------------------------------------------------
    # PHASE 2 — deep analysis + FINAL SELECTION: Phase 1-এর screening_score
    # অনুযায়ী সবচেয়ে "sure" TOP_PICKS_COUNT-টা ম্যাচ বেছে, শুধু ওগুলোর জন্য
    # injuries/team-statistics/standings আনা হয়। চূড়ান্ত predictions.json-এ
    # শুধু এই কয়টা ম্যাচই থাকে — বাকি Phase 1-এর ম্যাচ বাদ যায়।
    # ---------------------------------------------------------------
    ranked = sorted(output_matches, key=lambda x: x["_screening_score"], reverse=True)
    # MIN_SCREENING_SCORE-এর নিচের ম্যাচ বাদ — "৫টা পূরণ করার জন্য দুর্বল পিক
    # ঢোকানো"র বদলে সেদিন কম (এমনকি ০টা) sure pick দেওয়া হবে।
    qualified = [m for m in ranked if m["_screening_score"] >= MIN_SCREENING_SCORE]
    dropped_for_low_score = len(ranked[:TOP_PICKS_COUNT]) - len(qualified[:TOP_PICKS_COUNT])
    if dropped_for_low_score > 0:
        print(
            f"{dropped_for_low_score} candidate match(es) dropped from top picks: "
            f"screening_score below MIN_SCREENING_SCORE={MIN_SCREENING_SCORE}",
            file=sys.stderr,
        )
    top_matches = qualified[:TOP_PICKS_COUNT]

    for om in top_matches:
        remaining_quota = football_api.last_known_remaining_daily_quota
        if remaining_quota is not None and remaining_quota < MIN_QUOTA_BUFFER:
            print(
                f"stopping Phase 2 early: daily API quota nearly exhausted "
                f"(remaining={remaining_quota})",
                file=sys.stderr,
            )
            break
        if om.get("league_id") is not None and om.get("season") is not None:
            om["deep_analysis"] = deep_enrich(
                om["match_id"], om["home_team_id"], om["away_team_id"],
                om["league_id"], om["season"],
            )
        om["top_pick"] = True

        # ইনজুরি-অ্যাডজাস্টেড রিক্যালকুলেশন: Phase 1-এর বেসিক expected গোল থেকে
        # শুরু করে এইমাত্র deep_enrich-এ পাওয়া injuries প্রয়োগ করে outcome
        # (win%/draw%/score ইত্যাদি) পুনরায় হিসাব করা হয়। দেখুন
        # predictor.apply_injury_adjustment()-এর docstring-এ সীমাবদ্ধতার নোট।
        injuries = (om.get("deep_analysis") or {}).get("injuries") or []
        adjustment = apply_injury_adjustment(
            home_expected=om["home_expected_goals"],
            away_expected=om["away_expected_goals"],
            injuries=injuries,
            home_team_id=om["home_team_id"],
            away_team_id=om["away_team_id"],
        )
        if adjustment is not None:
            om.update({k: v for k, v in adjustment.items() if not k.startswith("_")})
            # probability বদলে গেছে, তাই pick/top-scores-ও ঐ নতুন সংখ্যা থেকেই
            # পুনরায় বের করতে হবে (adjustment dict-এ _score_probs/_raw_probs আছে)
            om["best_pick"] = humanize_pick(
                best_pick(adjustment, min_odds=MIN_ODDS), om["home_team"], om["away_team"]
            )
            om["high_odds_pick"] = humanize_pick(
                high_odds_pick(adjustment, min_odds=HIGH_ODDS_THRESHOLD),
                om["home_team"], om["away_team"],
            )
            om["top_scores"] = top_correct_scores(adjustment)
            om["injury_adjusted"] = True
        else:
            om["injury_adjusted"] = False

    # ফাইনাল আউটপুট = শুধু top picks (kickoff সময় অনুযায়ী সাজানো), internal
    # screening_score ফিল্ড বাদ দিয়ে
    final_matches = [om for om in top_matches if om["top_pick"]]
    for om in final_matches:
        om.pop("_screening_score", None)
    final_matches.sort(key=lambda x: x.get("match_date") or "")

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "prediction_window_bd": {
            "from": (window_start_utc + BD_OFFSET).strftime("%Y-%m-%dT%H:%M:%S+06:00"),
            "to": (window_end_utc + BD_OFFSET).strftime("%Y-%m-%dT%H:%M:%S+06:00"),
        },
        "disclaimer": DISCLAIMER,
        "top_picks_count": len(final_matches),
        "api_cache_stats": football_api.cache_stats(),
        "matches": final_matches,
    }

    out_path = os.path.join(os.path.dirname(__file__), "..", "data", "predictions.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    log_path = os.path.join(os.path.dirname(__file__), "..", "data", "predictions_log.json")
    added_to_log = append_to_log(final_matches, log_path)

    print(
        f"Wrote {len(final_matches)} top-pick matches to {out_path} "
        f"(fetched {len(matches)} NS fixtures for {target_utc_date} -> "
        f"{len(windowed_matches)} within BD 6am-6am window -> "
        f"{len(allowed_matches)} in whitelisted leagues -> "
        f"{no_data_skipped} dropped for lack of h2h/form data -> "
        f"{len(output_matches)} screened -> {len(final_matches)} final top picks); "
        f"{added_to_log} new match(es) added to {log_path} for result-tracking"
    )


if __name__ == "__main__":
    build()
