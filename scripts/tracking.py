"""
tracking.py
আগের ভার্সনে এটা আলাদা SQLite ডাটাবেসে প্রেডিকশন/ফলাফল লগ করতো। কিন্তু এই
রিপোতে check_results.py ইতিমধ্যেই data/history.json-এ প্রতিটা resolved
প্রেডিকশনের জন্য best_pick, best_pick_status ("correct"/"incorrect"/"no_pick"),
reliability_score, actual_score ইত্যাদি জমা রাখে (এবং GitHub Actions ওয়ার্কফ্লো
সেটা git commit করে, তাই রান-টু-রান পার্সিস্ট করে)। তাই আলাদা SQLite ডাটাবেস
রাখলে (ক) ডেটা ডুপ্লিকেট হতো, (খ) বাইনারি .db ফাইল প্রতি রানে git history-তে
জমা হতে থাকতো। এই ভার্সন সরাসরি data/history.json থেকে পড়ে কাজ করে — কোনো
নতুন ফাইল, কোনো নতুন API কল লাগে না।

ব্যবহার:
    python scripts/tracking.py            # data/history.json থেকে accuracy রিপোর্ট প্রিন্ট করে

    from tracking import accuracy_report
    report = accuracy_report()
"""
import json
import math
import os
from itertools import product

DEFAULT_HISTORY_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "history.json")


def _load_history(history_path=None):
    path = history_path or DEFAULT_HISTORY_PATH
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def accuracy_report(history_path=None, which="best_pick", min_reliability=0):
    """data/history.json থেকে log-loss, Brier score এবং reliability-bucket
    অনুযায়ী hit-rate বের করে। check_results.py প্রতিদিন এই ফাইল আপডেট করে বলে
    এটা চালাতে কোনো নতুন কল লাগে না।

    which: "best_pick" বা "high_odds_pick" — কোন পিক-টার accuracy দেখতে চান।
    min_reliability: শুধু এর উপরে reliability_score-এর পিকগুলো ধরে হিসাব করতে
        চাইলে দিন — এটা দিয়েই best_pick()/high_odds_pick()-এর min_reliability
        গেট ঠিক করার সিদ্ধান্ত নেওয়া উচিত (থ্রেশহোল্ড বাড়ালে হিট-রেট বাড়ে কিনা)।
    """
    history = _load_history(history_path)
    status_key = f"{which}_status"

    n = 0
    log_loss_sum = 0.0
    brier_sum = 0.0
    buckets = {}  # reliability decile -> [hits, total]

    for entry in history:
        pick = entry.get(which)
        status = entry.get(status_key)
        if not pick or status not in ("correct", "incorrect"):
            continue
        reliability = pick.get("reliability_score")
        if reliability is not None and reliability < min_reliability:
            continue
        prob_pct = pick.get("probability_pct")
        if prob_pct is None:
            continue
        p = max(1e-6, min(1 - 1e-6, prob_pct / 100.0))
        y = 1.0 if status == "correct" else 0.0

        log_loss_sum += -(y * math.log(p) + (1 - y) * math.log(1 - p))
        brier_sum += (p - y) ** 2
        n += 1
        if reliability is not None:
            bucket = int(reliability // 10) * 10
            buckets.setdefault(bucket, [0, 0])
            buckets[bucket][1] += 1
            if y:
                buckets[bucket][0] += 1

    if n == 0:
        return {
            "n": 0,
            "note": "data/history.json-এ এখনো কোনো resolved প্রেডিকশন নেই — "
                    "check_results.py কয়েকদিন চলার পর আবার চেষ্টা করুন।",
        }

    return {
        "n": n,
        "which": which,
        "avg_log_loss": round(log_loss_sum / n, 4),   # কম মানে ভালো
        "avg_brier_score": round(brier_sum / n, 4),   # কম মানে ভালো, ~০.২৫-এর নিচে ভালো ধরা হয়
        "reliability_buckets": {
            f"{b}-{b+9}": {"hit_rate": round(h / t, 3), "n": t}
            for b, (h, t) in sorted(buckets.items())
        },
    }


def run_calibration(matches_provider, h2h_weights=(0.10, 0.15, 0.20),
                     form_weights=(0.30, 0.40, 0.50), venue_weights=(0.35, 0.45, 0.55),
                     half_lives=(3, 4, 5, 6), rhos=(-0.15, -0.11, -0.07, 0.0)):
    """সব কম্বিনেশন (weight normalize করা হয় যোগফল ১ করতে) এর জন্য গড় log-loss
    বের করে সেরাটা রিটার্ন করে।

    ⚠️ এটা এখনও data/history.json ব্যবহার করে না — history.json-এ শুধু "পিক করা"
    মার্কেটের probability সেভ থাকে, কিন্তু calibration-এর জন্য প্রতিটা ম্যাচের
    কাঁচা H2H/recent-form/venue ম্যাচ-লিস্ট লাগে (যেগুলো এখন কোথাও সেভ হয় না)।
    তাই এটা এখনো একটা standalone/offline টুল — matches_provider() নিজে লিখে
    ঐতিহাসিক ম্যাচ-ডেটা (ফ্রি API থেকে আগেই ফেচ করে লোকালি জমানো) সাপ্লাই
    করতে হবে। এটা CI/GitHub Actions-এ চালানো হয় না, ম্যানুয়ালি লোকালি চালিয়ে
    ফলাফল অনুযায়ী predictor.py-এর H2H_WEIGHT/FORM_WEIGHT/VENUE_WEIGHT/rho
    হাতে বসাতে হবে।

    matches_provider: একটা ফাংশন যা () নিয়ে ঐতিহাসিক, ফলাফল-জানা ম্যাচের একটা
    লিস্ট রিটার্ন করে — প্রতিটা dict-এ থাকা দরকার:
        {"home_id", "away_id", "h2h", "home_recent", "away_recent",
         "home_home", "away_away", "home_goals", "away_goals"}
    """
    from advanced_stats import recency_weighted_scored_conceded, dixon_coles_adjustment

    def _weighted_avg(pairs):
        total_w = total_v = 0.0
        for value, weight in pairs:
            if value is None:
                continue
            total_v += value * weight
            total_w += weight
        return None if total_w == 0 else total_v / total_w

    def _poisson_prob(k, lam):
        return (lam ** k) * math.exp(-lam) / math.factorial(k)

    matches = matches_provider()
    if not matches:
        return {"error": "matches_provider() খালি লিস্ট রিটার্ন করেছে — calibration চালানোর জন্য ঐতিহাসিক, ফলাফল-জানা ম্যাচ দরকার।"}

    best = None
    for h2h_w, form_w, venue_w, half_life, rho in product(h2h_weights, form_weights, venue_weights, half_lives, rhos):
        total_w = h2h_w + form_w + venue_w
        h2h_w_n, form_w_n, venue_w_n = h2h_w / total_w, form_w / total_w, venue_w / total_w

        loss_sum = 0.0
        n = 0
        for m in matches:
            h2h_h = recency_weighted_scored_conceded(m.get("h2h"), m["home_id"], half_life=half_life)
            h2h_a = recency_weighted_scored_conceded(m.get("h2h"), m["away_id"], half_life=half_life)
            form_h = recency_weighted_scored_conceded(m.get("home_recent"), m["home_id"], half_life=half_life)
            form_a = recency_weighted_scored_conceded(m.get("away_recent"), m["away_id"], half_life=half_life)
            venue_h = recency_weighted_scored_conceded(m.get("home_home"), m["home_id"], half_life=half_life)
            venue_a = recency_weighted_scored_conceded(m.get("away_away"), m["away_id"], half_life=half_life)

            hs = _weighted_avg([(h2h_h[0] if h2h_h else None, h2h_w_n),
                                 (form_h[0] if form_h else None, form_w_n),
                                 (venue_h[0] if venue_h else None, venue_w_n)]) or 1.2
            hc = _weighted_avg([(h2h_h[1] if h2h_h else None, h2h_w_n),
                                 (form_h[1] if form_h else None, form_w_n),
                                 (venue_h[1] if venue_h else None, venue_w_n)]) or 1.2
            a_s = _weighted_avg([(h2h_a[0] if h2h_a else None, h2h_w_n),
                                  (form_a[0] if form_a else None, form_w_n),
                                  (venue_a[0] if venue_a else None, venue_w_n)]) or 1.2
            a_c = _weighted_avg([(h2h_a[1] if h2h_a else None, h2h_w_n),
                                  (form_a[1] if form_a else None, form_w_n),
                                  (venue_a[1] if venue_a else None, venue_w_n)]) or 1.2

            home_xg = max(0.15, min(4.5, (hs + a_c) / 2.0))
            away_xg = max(0.15, min(4.5, (a_s + hc) / 2.0))

            probs = {}
            for hg in range(7):
                for ag in range(7):
                    probs[(hg, ag)] = _poisson_prob(hg, home_xg) * _poisson_prob(ag, away_xg)
            probs = dixon_coles_adjustment(probs, home_xg, away_xg, rho=rho)

            actual_hg, actual_ag = m["home_goals"], m["away_goals"]
            p_actual = max(1e-6, probs.get((min(actual_hg, 6), min(actual_ag, 6)), 1e-6))
            loss_sum += -math.log(p_actual)
            n += 1

        avg_loss = loss_sum / n if n else float("inf")
        candidate = {
            "h2h_weight": round(h2h_w_n, 3), "form_weight": round(form_w_n, 3),
            "venue_weight": round(venue_w_n, 3), "half_life": half_life, "rho": rho,
            "avg_log_loss": round(avg_loss, 4), "n_matches": n,
        }
        if best is None or avg_loss < best["avg_log_loss"]:
            best = candidate

    return best


if __name__ == "__main__":
    import sys
    report = accuracy_report()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report.get("n", 0) == 0:
        sys.exit(0)
