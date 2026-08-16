"""
generate_predictions.py
Free-tier optimized daily runner.

Budget design:
- 1 fixture-date call
- Phase 1: max 20 matches, using cached team form + H2H
- Phase 2: max 5 finalists, using team stats + one standings call per league
  + injuries
- Final predictions are recalculated AFTER deep data is available.
"""
import json, os, sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
import football_api
from predictor import predict_match, best_pick, high_odds_pick, top_correct_scores

BD_OFFSET = timedelta(hours=6)
MAX_MATCHES = 20
TOP_PICKS_COUNT = 5
MIN_QUOTA_BUFFER = 15
MIN_FINAL_PROB = 0.70
MIN_FINAL_RELIABILITY = 65

ALLOWED_LEAGUES = {
    ("England","Premier League"), ("England","Championship"),
    ("England","League One"), ("England","League Two"), ("England","National League"),
    ("Spain","La Liga"), ("Italy","Serie A"), ("Germany","Bundesliga"),
    ("France","Ligue 1"), ("Portugal","Liga Portugal"), ("Netherlands","Eredivisie"),
    ("Belgium","First Division A"), ("Austria","Bundesliga"),
    ("Scotland","League One"), ("Norway","Eliteserien"),
    ("Sweden","Allsvenskan"), ("Sweden","Superettan"), ("Japan","J. League"),
    ("Saudi Arabia","Saudi Pro League"), ("Brazil","Serie A"),
    ("United Arab Emirates","Pro League"), ("Russia","Premier League"),
    ("Iran","Persian Gulf"), ("Türkiye","1. Lig"),
    ("World","UEFA Champions League"), ("World","UEFA Europa League"),
    ("World","UEFA Conference League"),
}
ALLOWED = {(c.lower(), n.lower()) for c,n in ALLOWED_LEAGUES}

def prediction_window():
    now = datetime.now(timezone.utc)
    bd = now + BD_OFFSET
    d = bd.date() if bd.hour >= 6 else bd.date() - timedelta(days=1)
    start_bd = datetime(d.year,d.month,d.day,6)
    end_bd = start_bd + timedelta(days=1)
    start = (start_bd - BD_OFFSET).replace(tzinfo=timezone.utc)
    end = (end_bd - BD_OFFSET).replace(tzinfo=timezone.utc)
    return start, end, start.date().isoformat()

def allowed(m):
    c=m.get("competition",{})
    return ((c.get("country") or "").lower(), (c.get("name") or "").lower()) in ALLOWED

def humanize(pick, home, away):
    if not pick: return None
    mapping={
        "Home Win":f"{home} Win","Away Win":f"{away} Win","Draw":"Draw",
        "Double Chance (Home/Draw)":f"{home} Win or Draw",
        "Double Chance (Draw/Away)":f"Draw or {away} Win",
        "Double Chance (Home/Away)":f"{home} Win or {away} Win",
        "Over 2.5 Goals":"Over 2.5 Goals","Under 2.5 Goals":"Under 2.5 Goals",
        "Both Teams to Score - Yes":"BTTS - Yes","Both Teams to Score - No":"BTTS - No"
    }
    return {**pick, "market_label":mapping.get(pick["market"],pick["market"])}

def screen_score(pred):
    return (
        0.50 * pred["best_market_probability"] +
        0.30 * pred["reliability_score"] +
        0.20 * pred["confidence_score"]
    )

def deep_enrich(m):
    out={"home_team_stats":None,"away_team_stats":None,"standings":[],"injuries":[]}
    lid=m.get("league_id"); season=m.get("season")
    if lid is None or season is None: return out
    try: out["home_team_stats"]=football_api.get_team_statistics(m["home_team_id"],lid,season)
    except Exception as e: print("home stats:",e,file=sys.stderr)
    try: out["away_team_stats"]=football_api.get_team_statistics(m["away_team_id"],lid,season)
    except Exception as e: print("away stats:",e,file=sys.stderr)
    try: out["standings"]=football_api.get_standings(lid,season)
    except Exception as e: print("standings:",e,file=sys.stderr)
    try: out["injuries"]=football_api.get_injuries(m["match_id"])
    except Exception as e: print("injuries:",e,file=sys.stderr)
    return out

def append_log(items,path):
    try:
        with open(path,encoding="utf-8") as f: log=json.load(f)
    except (FileNotFoundError,json.JSONDecodeError): log=[]
    ids={x.get("match_id") for x in log}
    for m in items:
        if m["match_id"] in ids: continue
        log.append({
            "match_id":m["match_id"],"competition":m["competition"],
            "home_team":m["home_team"],"away_team":m["away_team"],
            "match_date":m["match_date"],"best_pick":m.get("best_pick"),
            "high_odds_pick":m.get("high_odds_pick"),"status":"pending",
            "actual_score":None,"checked_at":None
        })
    log=log[-1000:]
    with open(path,"w",encoding="utf-8") as f: json.dump(log,f,ensure_ascii=False,indent=2)

def build():
    start,end,target=prediction_window()
    matches=football_api.get_matches_for_date(target)
    candidates=[]
    for m in matches:
        if not allowed(m): continue
        ts=m.get("utcDate")
        if not ts: continue
        try: kick=datetime.fromisoformat(ts.replace("Z","+00:00"))
        except ValueError: continue
        if not(start<=kick<end): continue
        candidates.append(m)
    candidates.sort(key=lambda x:x.get("utcDate") or "")
    candidates=candidates[:MAX_MATCHES]

    screened=[]
    for m in candidates:
        remaining=football_api.last_known_remaining_daily_quota
        if remaining is not None and remaining < MIN_QUOTA_BUFFER:
            break
        try:
            p=predict_match(m["homeTeam"]["id"],m["awayTeam"]["id"])
        except Exception as e:
            print("screen failed:",e,file=sys.stderr); continue
        if not p["has_real_data"]: continue
        screened.append((screen_score(p),m,p))

    screened.sort(key=lambda x:x[0],reverse=True)
    finalists=screened[:TOP_PICKS_COUNT]

    final=[]
    for _,m,base_pred in finalists:
        if football_api.last_known_remaining_daily_quota is not None and football_api.last_known_remaining_daily_quota < MIN_QUOTA_BUFFER:
            break
        deep=deep_enrich({
            "match_id":m["id"],"home_team_id":m["homeTeam"]["id"],
            "away_team_id":m["awayTeam"]["id"],
            "league_id":m["competition"].get("id"),
            "season":m["competition"].get("season")
        })
        try:
            p=predict_match(m["homeTeam"]["id"],m["awayTeam"]["id"],deep=deep)
        except Exception:
            p=base_pred

        best=best_pick(p,MIN_FINAL_PROB,MIN_FINAL_RELIABILITY)
        # If no robust market survives, do NOT publish this match as a "top pick".
        if best is None: continue

        high=high_odds_pick(p)
        item={
            "match_id":m["id"],
            "competition":f'{m["competition"].get("name")} ({m["competition"].get("country")})',
            "league_id":m["competition"].get("id"),
            "season":m["competition"].get("season"),
            "home_team":m["homeTeam"]["name"],"away_team":m["awayTeam"]["name"],
            "home_team_id":m["homeTeam"]["id"],"away_team_id":m["awayTeam"]["id"],
            "match_date":m.get("utcDate"),
            "home_expected_goals":p["home_expected_goals"],
            "away_expected_goals":p["away_expected_goals"],
            "most_likely_score":p["most_likely_score"],
            "home_win_pct":p["home_win_pct"],"draw_pct":p["draw_pct"],
            "away_win_pct":p["away_win_pct"],
            "over_2_5_pct":p["over_2_5_pct"],
            "btts_yes_pct":p["btts_yes_pct"],
            "double_chance_1x_pct":p["double_chance_1x_pct"],
            "double_chance_x2_pct":p["double_chance_x2_pct"],
            "home_power_rating":p["home_power_rating"],
            "away_power_rating":p["away_power_rating"],
            "confidence_score":p["confidence_score"],
            "signal_agreement":p["signal_agreement"],
            "reliability_score":p["reliability_score"],
            "best_market_probability":p["best_market_probability"],
            "best_pick":humanize(best,m["homeTeam"]["name"],m["awayTeam"]["name"]),
            "high_odds_pick":humanize(high,m["homeTeam"]["name"],m["awayTeam"]["name"]),
            "top_scores":top_correct_scores(p),
            "deep_analysis":deep,
        }
        final.append(item)

    final.sort(key=lambda x:x["match_date"] or "")
    payload={
        "generated_at":datetime.now(timezone.utc).isoformat(),
        "prediction_window_bd":{
            "from":(start+BD_OFFSET).isoformat(),"to":(end+BD_OFFSET).isoformat()
        },
        "model_version":"v2.0-high-selectivity",
        "disclaimer":"Statistical estimate only; no prediction is guaranteed.",
        "top_picks_count":len(final),
        "api_cache_stats":football_api.cache_stats(),
        "remaining_quota":football_api.last_known_remaining_daily_quota,
        "matches":final,
    }
    root=os.path.dirname(__file__)
    data_dir=os.path.join(root,"..","data")
    os.makedirs(data_dir,exist_ok=True)
    out=os.path.join(data_dir,"predictions.json")
    with open(out,"w",encoding="utf-8") as f: json.dump(payload,f,ensure_ascii=False,indent=2)
    append_log(final,os.path.join(data_dir,"predictions_log.json"))
    print(f"Generated {len(final)} high-confidence picks; remaining quota={football_api.last_known_remaining_daily_quota}")

if __name__=="__main__":
    build()
