"""
advanced_stats.py
Pure-Python statistical helpers. No API calls.
"""
import math

def _sorted_desc(matches):
    return sorted(matches or [], key=lambda m: m.get("utcDate", ""), reverse=True)

def recency_weighted_scored_conceded(matches, team_id, half_life=4):
    matches = _sorted_desc(matches)
    tw = sw = cw = 0.0
    idx = 0
    for m in matches:
        ft = m.get("score", {}).get("fullTime", {})
        hg, ag = ft.get("home"), ft.get("away")
        if hg is None or ag is None:
            continue
        hi = m.get("homeTeam", {}).get("id")
        ai = m.get("awayTeam", {}).get("id")
        if hi == team_id:
            s, c = hg, ag
        elif ai == team_id:
            s, c = ag, hg
        else:
            continue
        w = 0.5 ** (idx / half_life)
        sw += s * w
        cw += c * w
        tw += w
        idx += 1
    if tw == 0:
        return None
    return sw / tw, cw / tw

def power_rating(matches, team_id, half_life=4):
    matches = _sorted_desc(matches)
    tw = score = 0.0
    idx = 0
    for m in matches:
        ft = m.get("score", {}).get("fullTime", {})
        hg, ag = ft.get("home"), ft.get("away")
        if hg is None or ag is None:
            continue
        hi = m.get("homeTeam", {}).get("id")
        ai = m.get("awayTeam", {}).get("id")
        if hi == team_id:
            gf, ga = hg, ag
        elif ai == team_id:
            gf, ga = ag, hg
        else:
            continue
        result = 1.0 if gf > ga else 0.5 if gf == ga else 0.0
        gd = max(-3, min(3, gf - ga)) / 3.0
        match_score = (result - 0.5) * 2 + gd
        w = 0.5 ** (idx / half_life)
        score += match_score * w
        tw += w
        idx += 1
    return None if tw == 0 else round((score / tw) * 33.3, 1)

def dynamic_home_advantage(home_venue_matches, home_other_matches, team_id, default_boost=1.08):
    h = recency_weighted_scored_conceded(home_venue_matches, team_id)
    o = recency_weighted_scored_conceded(
        (home_venue_matches or []) + (home_other_matches or []), team_id
    )
    if h is None or o is None or len(home_venue_matches or []) < 3:
        return default_boost, 1.0
    hs, hc = h
    os, oc = o
    scored_mult = default_boost if os <= 0 else max(0.88, min(1.25, hs / os))
    conceded_mult = 1.0 if oc <= 0 else max(0.80, min(1.15, hc / oc))
    return round(scored_mult, 3), round(conceded_mult, 3)

def _tau(hg, ag, hxg, axg, rho):
    if hg == 0 and ag == 0:
        return 1 - hxg * axg * rho
    if hg == 0 and ag == 1:
        return 1 + hxg * rho
    if hg == 1 and ag == 0:
        return 1 + axg * rho
    if hg == 1 and ag == 1:
        return 1 - rho
    return 1.0

def dixon_coles_adjustment(score_probs, home_exp, away_exp, rho=-0.11):
    adjusted = {
        k: max(0.0, p * _tau(k[0], k[1], home_exp, away_exp, rho))
        for k, p in score_probs.items()
    }
    total = sum(adjusted.values())
    if total <= 0:
        return score_probs
    return {k: v / total for k, v in adjusted.items()}

def league_average_goals(table):
    """standings table (already fetched in deep_enrich, Phase 2 — no extra API
    call) থেকে লিগের প্রকৃত গড় গোল/ম্যাচ বের করে। এটা _team_stat_adjustment-এর
    hardcoded 1.35 বেসলাইনের বদলে ব্যবহার করলে হাই-স্কোরিং (Eredivisie) ও
    লো-স্কোরিং (Ligue 1, Serie A) লিগের attack/defense strength আলাদাভাবে
    ক্যালিব্রেটেড হয়। রিটার্ন করে (avg_goals_for, avg_goals_against) — টেবিলে
    played/goals ডেটা না থাকলে None।"""
    if not table:
        return None
    total_for = total_against = total_played = 0.0
    for row in table:
        all_stats = row.get("all") or {}
        played = all_stats.get("played")
        goals = all_stats.get("goals") or {}
        gf = goals.get("for")
        ga = goals.get("against")
        if not played or gf is None or ga is None:
            continue
        total_for += gf
        total_against += ga
        total_played += played
    if total_played == 0:
        return None
    return total_for / total_played, total_against / total_played

def rest_days_adjustment(matches, team_id, match_date, cap=(0.93, 1.05)):
    """Fixture-congestion সিগন্যাল — কোনো নতুন API কল লাগে না, কারণ
    get_team_recent_form()-এ যে ম্যাচগুলো এমনিতেই আসে তাতে utcDate থাকে।
    সবচেয়ে সাম্প্রতিক আগের ম্যাচ থেকে বর্তমান ম্যাচ পর্যন্ত কত দিন বিরতি
    সেটা বের করে ছোট, ক্যাপড multiplier দেয়:
    - ৩ দিনের কম বিরতি (মিডউইক টার্নঅ্যারাউন্ড): হালকা penalty
    - ৬+ দিন বিরতি (পূর্ণ বিশ্রাম): হালকা বোনাস
    matches: টিমের রিসেন্ট ম্যাচের লিস্ট (predict_match()-এ যা এমনিতেই আছে)।
    match_date: 'YYYY-MM-DD' বা ISO স্ট্রিং — যে ম্যাচের জন্য প্রেডিক্ট করা হচ্ছে।
    """
    from datetime import datetime, timezone

    def _parse(d):
        # match_date সাধারণত শুধু 'YYYY-MM-DD' (naive), কিন্তু utcDate API থেকে
        # সবসময় timezone-aware ('...Z' বা '+00:00' সহ) আসে। দুটো তুলনা করতে গেলে
        # Python একটা naive vs aware TypeError দেয় — তাই naive হলে UTC ধরে নিয়ে
        # aware করে দেওয়া হচ্ছে, যাতে (target - last_match) সবসময় নিরাপদে চলে।
        try:
            dt = datetime.fromisoformat(str(d).replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    target = _parse(match_date)
    if target is None:
        return 1.0

    team_matches = []
    for m in matches or []:
        hi = m.get("homeTeam", {}).get("id")
        ai = m.get("awayTeam", {}).get("id")
        if team_id not in (hi, ai):
            continue
        dt = _parse(m.get("utcDate", ""))
        if dt and dt < target:
            team_matches.append(dt)
    if not team_matches:
        return 1.0

    last_match = max(team_matches)
    rest_days = (target - last_match).days
    lo, hi = cap
    if rest_days <= 3:
        return max(lo, 1.0 - (3 - rest_days) * 0.02)
    if rest_days >= 6:
        return min(hi, 1.0 + min(rest_days - 6, 4) * 0.0125)
    return 1.0


def referee_adjustment(referee_name, referee_history, baseline_cards=3.8):
    """referee_history: {referee_name: avg_cards_per_match} — API-Football-এর
    ফ্রি টিয়ারেও /fixtures রেসপন্সে referee-র নাম থাকে (নতুন কল লাগে না, শুধু
    ইতিমধ্যে ফেচ করা fixture ডেটা থেকে referee নাম বের করে সময়ের সাথে সাথে
    নিজে একটা লোকাল হিস্টরি বানাতে হবে — recent_form/h2h কলে যেসব পুরনো
    ম্যাচের fixture ডেটা এমনিতেই আসে, সেখান থেকে card-count agregat করে রাখলেই
    হয়)। শুধু কার্ড-প্রবণতার ওপর ভিত্তি করে ছোট, ক্যাপড multiplier — কড়া রেফারি
    থাকলে ম্যাচ কিছুটা বেশি stop-start/র‌্যাগেড হতে পারে, যা সামান্য কম গোল-প্রবণতার
    সাথে সম্পর্কিত ধরা হয়।
    """
    if not referee_name or not referee_history:
        return 1.0
    avg = referee_history.get(referee_name)
    if avg is None:
        return 1.0
    # baseline_cards-এর চেয়ে বেশি কার্ড-গড় রেফারি হলে হালকা down-adjust
    return max(0.97, min(1.02, 1.0 - (avg - baseline_cards) * 0.008))


def confidence_score(h2h_count, home_recent_count, away_recent_count,
                     venue_home_count, venue_away_count):
    def sat(count, target, weight):
        return weight * min(1.0, count / target)
    return round(
        sat(h2h_count, 5, 20) +
        sat(home_recent_count, 8, 20) +
        sat(away_recent_count, 8, 20) +
        sat(venue_home_count, 4, 20) +
        sat(venue_away_count, 4, 20)
    )
