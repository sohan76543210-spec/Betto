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
MAX_MATCHES = 35
TOP_PICKS_COUNT = 10
MIN_QUOTA_BUFFER = 10
# NOTE: এই দুইটা আর best_pick() gate করতে ব্যবহার হয় না (ইউজারের অনুরোধে থ্রেশহোল্ড
# গেট সরিয়ে দেওয়া হয়েছে — প্রতিটা finalist ম্যাচের সেরা market সবসময় publish হয়)।
# reliability_score/probability এখনো প্রতিটা pick-এর সাথে দেখানো হয় info হিসেবে,
# যাতে ব্যবহারকারী নিজেই বুঝে নিতে পারে কোনটা কতটা নির্ভরযোগ্য।
MIN_FINAL_PROB = 0.70          # unused (kept for reference)
MIN_FINAL_RELIABILITY = 55     # unused (kept for reference)

# NOTE: ১২টা "core" লিগ (PL, Championship, La Liga, Serie A, Bundesliga, Ligue 1,
# Primeira Liga, Eredivisie, Brazil Serie A, UCL, World Cup, Euro) এখন
# football_api.py রাউটার football-data.org থেকে আনে এবং সেগুলোকে aps-এর রেজাল্ট
# থেকে dedup করেই বাদ দেয় (দেখুন football_api.py-এর CORE_LEAGUE_APS_IDS)।
# তাই এখানে আর সেগুলোর নাম আলাদা করে whitelist করার দরকার নেই — router থেকে আসা
# fdo ম্যাচ মানেই সেগুলো আমাদের চাওয়া ১২ লিগের একটা, তাই সবসময় allowed।
#
# এই লিস্টে শুধু SECONDARY/FALLBACK লিগগুলো থাকে (api-sports.io থেকে আসা,
# core ১২টার বাইরে) — নাম-ভিত্তিক ম্যাচিং এখানে ঠিক আছে কারণ এগুলো শুধু একটামাত্র
# সোর্স (aps) থেকে আসে, দুই সোর্সের নাম মেলানোর ঝামেলা নেই।
FALLBACK_APS_LEAGUES = {
    ("England","League One"), ("England","League Two"), ("England","National League"),
    ("Belgium","First Division A"), ("Austria","Bundesliga"),
    ("Scotland","League One"), ("Norway","Eliteserien"),
    ("Sweden","Allsvenskan"), ("Sweden","Superettan"), ("Japan","J. League"),
    ("Saudi Arabia","Saudi Pro League"),
    ("United Arab Emirates","Pro League"), ("Russia","Premier League"),
    ("Iran","Persian Gulf"), ("Türkiye","1. Lig"),
    ("World","UEFA Europa League"), ("World","UEFA Conference League"),
    # --- newly added leagues (more matches/coverage) ---
    ("Germany","2. Bundesliga"), ("Spain","Segunda División"),
    ("Italy","Serie B"), ("France","Ligue 2"),
    ("Netherlands","Eerste Divisie"), ("Portugal","Liga Portugal 2"),
    ("Scotland","Premiership"), ("Türkiye","Süper Lig"),
    ("Greece","Super League 1"), ("Switzerland","Super League"),
    ("Denmark","Superliga"), ("Poland","Ekstraklasa"),
    ("USA","MLS"), ("Mexico","Liga MX"), ("Argentina","Liga Profesional Argentina"),
    ("Brazil","Serie B"), ("South Korea","K League 1"),
    ("Australia","A-League"), ("China","Super League"),
    ("Qatar","Stars League"), ("World","UEFA Nations League"),
    ("World","Copa Libertadores"), ("World","Copa Sudamericana"),
}
ALLOWED = {(c.lower(), n.lower()) for c,n in FALLBACK_APS_LEAGUES}

# --------------------------------------------------------------------------
# NAME-ম্যাচিং ভঙ্গুর (api-sports.io-এর league.name/country ঠিক আক্ষরিক না
# মিললে বাদ পড়ে)। api_sports.py-এর _adapt_fixture() দেখে জানা যায়,
# competition.code আসলে aps-এ league["id"]-ই (নাম না) — router-এর নিজের
# কমেন্টেও (football_api.py) বলা আছে ID-ভিত্তিক routing নাম-ম্যাচিং এর চেয়ে
# বেশি নির্ভরযোগ্য। তাই এখানে নাম + ID দুইটাই — যেকোনো একটা মিললেই allow
# (OR লজিক), existing নাম-ভিত্তিক এন্ট্রি না ভেঙেই।
#
# ⚠️ এই ID গুলো api-football-এর সুপরিচিত/স্থিতিশীল লিগ ID (একই patterns যা
# CORE_LEAGUE_APS_IDS-এ (football_api.py) কনফার্ম করা ১২টা লিগের সাথে মেলে),
# কিন্তু এই সিস্টেমে নেটওয়ার্ক অ্যাক্সেস নেই বলে লাইভ API কল করে ভেরিফাই করা
# যায়নি। প্রথম রান দেওয়ার পর stderr-এর DEBUG লগে "dropped_not_allowed" সংখ্যা
# ও sample দেখে ভুল থাকলে ঠিক করে নেওয়া ভালো।
# --------------------------------------------------------------------------
FALLBACK_APS_LEAGUE_IDS = {
    41, 42, 43,      # England: League One, League Two, National League
    144,             # Belgium First Division A
    218,             # Austria Bundesliga
    103,             # Norway Eliteserien
    113, 114,        # Sweden Allsvenskan, Superettan
    98,              # Japan J1 League
    307,             # Saudi Pro League
    301,             # UAE Pro League
    235,             # Russia Premier League
    290,             # Iran Persian Gulf Pro League
    204,             # Türkiye 1. Lig
    3, 848,          # UEFA Europa League, UEFA Conference League
    # --- newly added ---
    79,              # Germany 2. Bundesliga
    141,             # Spain Segunda División
    136,             # Italy Serie B
    62,              # France Ligue 2
    89,              # Netherlands Eerste Divisie
    95,              # Portugal Liga Portugal 2
    179,             # Scotland Premiership
    203,             # Türkiye Süper Lig
    197,             # Greece Super League
    207,             # Switzerland Super League
    119,             # Denmark Superliga
    106,             # Poland Ekstraklasa
    253,             # USA MLS
    262,             # Mexico Liga MX
    128,             # Argentina Liga Profesional
    72,              # Brazil Serie B
    292,             # South Korea K League 1
    188,             # Australia A-League
    169,             # China Super League
    305,             # Qatar Stars League
    5,               # UEFA Nations League
    13,              # Copa Libertadores
    11,              # Copa Sudamericana
}

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
    c = m.get("competition", {})
    code = str(c.get("code") or "")
    if code.startswith("fdo:"):
        return True  # router-এর ১২ core লিগ থেকে আসা মানেই already whitelisted
    if ((c.get("country") or "").lower(), (c.get("name") or "").lower()) in ALLOWED:
        return True
    if code.startswith("aps:"):
        try:
            league_id = int(code.split(":", 1)[1])
        except (ValueError, IndexError):
            return False
        return league_id in FALLBACK_APS_LEAGUE_IDS
    return False

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
    # competition_code এখন composite ("fdo:PL" বা "aps:39") — router নিজেই ভেতরে
    # ডিকোড করে সঠিক সোর্সে পাঠায়। fdo সোর্সে team_stats/injuries সবসময় খালি/None
    # আসবে (ফ্রি প্ল্যানে ঐ endpoint নেই) — predictor.py সেটা gracefully হ্যান্ডেল করে।
    out={"home_team_stats":None,"away_team_stats":None,"standings":[],"injuries":[]}
    code=m.get("competition_code")
    if code is None: return out
    season=m.get("season")
    try: out["home_team_stats"]=football_api.get_team_statistics(m["home_team_id"],code,season)
    except Exception as e: print("home stats:",e,file=sys.stderr)
    try: out["away_team_stats"]=football_api.get_team_statistics(m["away_team_id"],code,season)
    except Exception as e: print("away stats:",e,file=sys.stderr)
    try: out["standings"]=football_api.get_standings(code,season)
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
    print(f"DEBUG: fetched {len(matches)} raw matches for date={target} "
          f"(window {start.isoformat()} .. {end.isoformat()})", file=sys.stderr)

    dropped_not_allowed=0
    dropped_no_ts=0
    dropped_out_of_window=0
    candidates=[]
    for m in matches:
        if not allowed(m):
            dropped_not_allowed+=1
            continue
        ts=m.get("utcDate")
        if not ts:
            dropped_no_ts+=1
            continue
        try: kick=datetime.fromisoformat(ts.replace("Z","+00:00"))
        except ValueError:
            dropped_no_ts+=1
            continue
        if not(start<=kick<end):
            dropped_out_of_window+=1
            continue
        candidates.append(m)
    print(f"DEBUG: after filters -> {len(candidates)} candidates "
          f"(dropped: not_allowed_league={dropped_not_allowed}, "
          f"no_timestamp={dropped_no_ts}, outside_window={dropped_out_of_window})",
          file=sys.stderr)
    if matches and not candidates:
        sample=[(m.get("competition",{}).get("name"),m.get("competition",{}).get("country"),
                 m.get("competition",{}).get("code"),m.get("utcDate")) for m in matches[:8]]
        print(f"DEBUG: sample of raw matches that got dropped (name,country,code,date): {sample}", file=sys.stderr)

    candidates.sort(key=lambda x:x.get("utcDate") or "")
    candidates=candidates[:MAX_MATCHES]

    screened=[]
    dropped_quota=0
    dropped_predict_error=0
    dropped_no_real_data=0
    for m in candidates:
        remaining=football_api.last_known_remaining_daily_quota
        if remaining is not None and remaining < MIN_QUOTA_BUFFER:
            dropped_quota+=1
            break
        try:
            p=predict_match(m["homeTeam"]["id"],m["awayTeam"]["id"],match_id=m["id"])
        except Exception as e:
            print("screen failed:",e,file=sys.stderr)
            dropped_predict_error+=1
            continue
        if not p["has_real_data"]:
            dropped_no_real_data+=1
            continue
        screened.append((screen_score(p),m,p))
    print(f"DEBUG: screening -> {len(screened)} passed has_real_data "
          f"(dropped: quota_exhausted={dropped_quota}, predict_error={dropped_predict_error}, "
          f"no_real_data={dropped_no_real_data})", file=sys.stderr)

    screened.sort(key=lambda x:x[0],reverse=True)
    finalists=screened[:TOP_PICKS_COUNT]

    final=[]
    dropped_no_best_pick=0
    for _,m,base_pred in finalists:
        if football_api.last_known_remaining_daily_quota is not None and football_api.last_known_remaining_daily_quota < MIN_QUOTA_BUFFER:
            break
        deep=deep_enrich({
            "match_id":m["id"],"home_team_id":m["homeTeam"]["id"],
            "away_team_id":m["awayTeam"]["id"],
            "competition_code":m["competition"].get("code"),
            "season":m["competition"].get("season")
        })
        try:
            p=predict_match(m["homeTeam"]["id"],m["awayTeam"]["id"],deep=deep,match_id=m["id"])
        except Exception:
            p=base_pred

        best=best_pick(p,min_probability=0.0,min_reliability=0,min_odds=1.30)
        # No reliability/probability gate — but a pick's odds must be >= 1.30
        # (i.e. not an overly 'safe'/low-value market). If every market for this
        # match is either below min_odds or has no signal, no pick is published.
        if best is None:
            dropped_no_best_pick+=1
            print(f"DEBUG: finalist has no market with odds >= 1.30: "
                  f"{m['homeTeam']['name']} vs {m['awayTeam']['name']} "
                  f"reliability={p.get('reliability_score')} "
                  f"best_prob={p.get('best_market_probability')}", file=sys.stderr)
            continue

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
            "home_cards_avg":p.get("home_cards_avg"),
            "away_cards_avg":p.get("away_cards_avg"),
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

    print(f"DEBUG: finalists -> {len(final)} published "
          f"(dropped_no_best_pick={dropped_no_best_pick} out of {len(finalists)} finalists checked)",
          file=sys.stderr)

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
