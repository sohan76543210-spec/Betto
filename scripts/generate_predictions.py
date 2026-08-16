"""
generate_predictions.py
Runs daily from GitHub Actions. Uses football_api.py + predictor.py to build
predictions for today's/tomorrow's matches and writes them to
../data/predictions.json.
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
# --------------------------------------------------------------------------
# api-football's free plan allows 100 requests per day total. Each match
# prediction costs ~3-4 calls on average (h2h + home form + away form,
# sometimes an extra call when the form call falls back to a previous
# season). The initial fixtures fetch costs 2 calls. MAX_MATCHES is set so
# a full run safely finishes within the daily quota, with some buffer left.
# --------------------------------------------------------------------------
MAX_MATCHES = 20
# If the quota drops below this number, remaining matches are skipped
# instead of processed, so a 429 error doesn't waste a batch of matches
# that would otherwise be lost — whatever was already done stays saved.
MIN_QUOTA_BUFFER = 5

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
    try:
        matches = football_api.get_upcoming_matches(days_ahead=1)
    except Exception as e:
        print(f"API error: {e}", file=sys.stderr)
        matches = []

    # First, allowed-league matches are collected and sorted by kickoff
    # time, so that when the MAX_MATCHES cap is applied, the earliest-
    # starting matches get priority (the API doesn't return them in
    # chronological order).
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
            "home_team": home_team_name,
            "away_team": away_team_name,
            "match_date": m.get("utcDate"),
            "home_expected_goals": pred["home_expected_goals"],
            "away_expected_goals": pred["away_expected_goals"],
            "most_likely_score": pred["most_likely_score"],
            "home_win_pct": pred["home_win_pct"],
            "draw_pct": pred["draw_pct"],
            "away_win_pct": pred["away_win_pct"],
            "over_2_5_pct": pred["over_2_5_pct"],
            "btts_yes_pct": pred["btts_yes_pct"],
            "best_pick": humanize_pick(
                best_pick(pred, min_odds=MIN_ODDS), home_team_name, away_team_name
            ),
            "high_odds_pick": humanize_pick(
                high_odds_pick(pred, min_odds=HIGH_ODDS_THRESHOLD),
                home_team_name,
                away_team_name,
            ),
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

    log_path = os.path.join(os.path.dirname(__file__), "..", "data", "predictions_log.json")
    added_to_log = append_to_log(output_matches, log_path)

    print(
        f"Wrote {len(output_matches)} matches to {out_path} "
        f"(fetched {len(matches)} total NS fixtures today+tomorrow -> "
        f"{len(allowed_matches)} in whitelisted leagues -> "
        f"{no_data_skipped} dropped for lack of h2h/form data -> "
        f"{len(output_matches)} final); "
        f"{added_to_log} new match(es) added to {log_path} for result-tracking"
    )


if __name__ == "__main__":
    build()
