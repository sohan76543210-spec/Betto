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

    which: "best_pick", "high_odds_pick", বা "correctscore_pick" — কোন
        পিক-টার accuracy দেখতে চান। "correctscore_pick" দিলে /correctscore
        মার্কেটের জন্য top_correct_scores()-এর সবচেয়ে সম্ভাব্য স্কোরের
        probability_pct অনুযায়ী bucket-accuracy বের হয় (check_results.py এই
        ফিল্ড history.json-এ সেভ করে, দেখুন correctscore_pick_status)। এই
        রিপোর্ট real, প্রোডাকশনে হওয়া প্রেডিকশনের ওপর ভিত্তি করে (ব্যাকটেস্ট না) —
        build_calibration_set.py-এর correctscore_calibration_report()
        (এই ফাইলে নিচে) একই মার্কেটকে ব্যাকটেস্ট ডেটায় RPS দিয়েও যাচাই করে,
        দুইটা মিলিয়ে দেখলে correctscore-এর calibration সবচেয়ে নির্ভরযোগ্যভাবে
        বোঝা যায়।
    min_reliability: শুধু এর উপরে reliability_score-এর পিকগুলো ধরে হিসাব করতে
        চাইলে দিন — এটা দিয়েই best_pick()/high_odds_pick()-এর min_reliability
        গেট ঠিক করার সিদ্ধান্ত নেওয়া উচিত (থ্রেশহোল্ড বাড়ালে হিট-রেট বাড়ে কিনা)।
        note: correctscore_pick-এর নিজের কোনো reliability_score নেই (সেটা
        সামগ্রিক ম্যাচ-লেভেল reliability, market-specific না) — তাই
        which="correctscore_pick" দিলে এই ফিল্টার কার্যকর হয় না।
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


def _score_probs_for_match(m, h2h_w_n, form_w_n, venue_w_n, half_life, rho):
    """predictor.predict_match()-এর মূল xG/স্কোরলাইন-probability লজিকের একটা
    হুবহু কপি, কিন্তু calibration sample ফরম্যাটের (matches_provider()) ওপর
    কাজ করে (কোনো API কল ছাড়া, আগে থেকে fetch করা h2h/recent/venue ডেটা
    থেকে)। _log_loss_for_params() এবং _correctscore_diagnostics_for_params()
    দুইটাতেই একই probs গ্রিড লাগে বলে এখানে একবার বের করে শেয়ার করা হলো।"""
    from advanced_stats import recency_weighted_scored_conceded, dixon_coles_adjustment

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
    return dixon_coles_adjustment(probs, home_xg, away_xg, rho=rho)


def _log_loss_for_params(matches, h2h_w_n, form_w_n, venue_w_n, half_life, rho):
    """একটা নির্দিষ্ট hyperparameter কম্বিনেশনের জন্য দেওয়া matches লিস্টের ওপর
    গড় log-loss হিসাব করে (exact scoreline probability-র ওপর ভিত্তি করে —
    তাই এটা এমনিতেই একটা score-sensitive objective)। train/test split-এ একই
    ফাংশন দুইবার (আলাদা matches সাবসেটে) কল হয় বলে আলাদা করে বের করা হলো।"""
    loss_sum = 0.0
    n = 0
    for m in matches:
        probs = _score_probs_for_match(m, h2h_w_n, form_w_n, venue_w_n, half_life, rho)
        actual_hg, actual_ag = m["home_goals"], m["away_goals"]
        p_actual = max(1e-6, probs.get((min(actual_hg, 6), min(actual_ag, 6)), 1e-6))
        loss_sum += -math.log(p_actual)
        n += 1

    return (loss_sum / n if n else float("inf")), n


def _correctscore_diagnostics_for_params(matches, h2h_w_n, form_w_n, venue_w_n, half_life, rho,
                                          n_prob_buckets=5):
    """/correctscore মার্কেটকে নির্দিষ্টভাবে যাচাই করার জন্য দুইটা পরিপূরক
    মেট্রিক — run_calibration()-এর সাধারণ exact-scoreline log-loss-এর
    (যেটা দিয়ে h2h/form/venue/half_life/rho tune হয়) বাইরে অতিরিক্ত, শুধু
    এই মার্কেটের জন্যই প্রাসঙ্গিক দুইটা measurement:

    1. RPS (Ranked Probability Score) — মোট গোল-সংখ্যাকে (0,1,2,...,6+, ৭টা
       ক্রমবদ্ধ বাকেট) একটা ordinal ক্যাটেগরি হিসেবে ধরে predicted vs actual
       cumulative distribution-এর মধ্যে দূরত্ব মাপে। একদম সঠিক scoreline (২D,
       ক্রমহীন) grid-এ RPS প্রয়োগ করা যায় না (RPS-এর জন্য ordinal ক্যাটেগরি
       লাগে), কিন্তু মোট গোলের ওপর এটা প্রয়োগ করলে "কতটা কাছাকাছি" ভুল হয়েছে
       সেটা log-loss-এর চেয়ে ভালোভাবে ধরে — যেমন ২-১ প্রেডিক্ট করে আসলে ২-০
       হলে, log-loss শুধু "ভুল" বলে, RPS বলে "কাছাকাছি ভুল" (আসলে ৩-১ হলে RPS
       বেশি বড় পেনাল্টি দিত)। কম RPS = ভালো।
    2. top-score probability bucket-accuracy — most-likely-score-এর
       probability অনুযায়ী ডেসাইল বাকেটে ভাগ করে প্রতিটা বাকেটে আসল hit-rate
       (predicted top score-ই আসল স্কোর হওয়ার হার) বের করে — production-এর
       reliability_buckets-এর মতোই, কিন্তু ব্যাকটেস্ট ডেটায় সাথে সাথে পাওয়া
       যায় (history.json-এ correctscore_pick জমা হওয়ার জন্য অপেক্ষা করতে
       হয় না)।
    """
    TOTAL_GOAL_BUCKETS = 7  # 0,1,2,3,4,5,6+ গোল — RPS-এর ordinal ক্যাটেগরি

    rps_sum = 0.0
    n = 0
    prob_buckets = {}  # decile(0-100 এর মধ্যে int) -> [hits, total]

    for m in matches:
        probs = _score_probs_for_match(m, h2h_w_n, form_w_n, venue_w_n, half_life, rho)

        # --- RPS: মোট গোলের ওপর cumulative distribution ---
        total_goal_probs = [0.0] * TOTAL_GOAL_BUCKETS
        for (hg, ag), p in probs.items():
            bucket = min(hg + ag, TOTAL_GOAL_BUCKETS - 1)
            total_goal_probs[bucket] += p
        actual_total = min(m["home_goals"] + m["away_goals"], TOTAL_GOAL_BUCKETS - 1)
        actual_dist = [1.0 if i == actual_total else 0.0 for i in range(TOTAL_GOAL_BUCKETS)]

        cum_pred = cum_actual = 0.0
        rps = 0.0
        for i in range(TOTAL_GOAL_BUCKETS):
            cum_pred += total_goal_probs[i]
            cum_actual += actual_dist[i]
            rps += (cum_pred - cum_actual) ** 2
        rps /= (TOTAL_GOAL_BUCKETS - 1)  # ০-১ পরিসরে normalize
        rps_sum += rps

        # --- top-score bucket-accuracy: predicted argmax scoreline-ই কি আসল স্কোর? ---
        top_score, top_p = max(probs.items(), key=lambda kv: kv[1])
        actual_hg, actual_ag = min(m["home_goals"], 6), min(m["away_goals"], 6)
        hit = 1 if top_score == (actual_hg, actual_ag) else 0
        bucket = int((top_p * 100) // (100 / n_prob_buckets)) * (100 // n_prob_buckets)
        prob_buckets.setdefault(bucket, [0, 0])
        prob_buckets[bucket][1] += 1
        prob_buckets[bucket][0] += hit

        n += 1

    return {
        "n": n,
        "avg_rps": round(rps_sum / n, 4) if n else None,  # কম মানে ভালো, ০ = নিখুঁত
        "top_score_probability_buckets": {
            f"{b}-{b + (100 // n_prob_buckets) - 1}": {
                "hit_rate": round(h / t, 3), "n": t,
            }
            for b, (h, t) in sorted(prob_buckets.items())
        },
    }


def correctscore_calibration_report(matches, params=None):
    """build_calibration_set.py থেকে কল করার জন্য — /correctscore মার্কেটের
    জন্য RPS + probability-bucket accuracy রিপোর্ট বানায়, run_calibration()
    যে matches ইতিমধ্যে fetch করেছে সেটাই আবার ব্যবহার করে (নতুন কোনো API
    কল/matches_provider() re-call লাগে না, তাই কোটা খরচ হয় না)।

    params: None দিলে data/model_config.json-এর বর্তমান (production-এ থাকা)
        কম্বিনেশন ব্যবহার হয় — অর্থাৎ "এখন যা লাইভ আছে সেটার correctscore
        calibration কেমন" এই প্রশ্নের উত্তর দেয়। run_calibration()-এর সেরা
        কম্বিনেশন বা অন্য যেকোনো কাস্টম কম্বিনেশনও পাস করা যায়।

    ⚠️ এটা ব্যাকটেস্ট ডেটায় (matches_provider() থেকে) চলে, ফলাফল-জানা matches-এর
    ওপর একবারে (train/test split ছাড়া) — কারণ এটা নতুন hyperparameter খুঁজছে
    না, শুধু ইতিমধ্যে বাছাই করা কম্বিনেশনের correctscore-নির্দিষ্ট আচরণ
    diagnostic হিসেবে দেখাচ্ছে। তাই ছোট sample-এও ব্যবহারযোগ্য, কিন্তু
    n কম হলে bucket-গুলো noisy হতে পারে (নিচের n প্রতিটা বাকেটে দেখুন)।
    """
    if not matches:
        return {"error": "matches খালি — correctscore diagnostics চালানোর মতো ডেটা নেই।"}

    if params is None:
        from model_config import load_config
        params = load_config()

    return _correctscore_diagnostics_for_params(
        matches,
        params.get("h2h_weight", 0.15), params.get("form_weight", 0.40),
        params.get("venue_weight", 0.45), params.get("half_life", 4),
        params.get("rho", -0.11),
    )


def run_calibration(matches_provider, h2h_weights=(0.10, 0.15, 0.20),
                     form_weights=(0.30, 0.40, 0.50), venue_weights=(0.35, 0.45, 0.55),
                     half_lives=(3, 4, 5, 6), rhos=(-0.15, -0.11, -0.07, 0.0),
                     test_fraction=0.3, baseline=None, min_test_n=15):
    """সব কম্বিনেশন (weight normalize করা হয় যোগফল ১ করতে) এর জন্য গড় log-loss
    বের করে সেরাটা রিটার্ন করে।

    ⚠️ Overfitting-গার্ড: matches-কে train/test-এ ভাগ করা হয় (শেষ
    test_fraction অংশ, matches_provider() যেভাবে দেয় সেই ক্রম অনুযায়ী — টিমের
    ভিন্নতা থাকায় এটা মোটামুটি random split হিসেবেই কাজ করে)। সেরা কম্বিনেশন
    train সেটের ওপর খোঁজা হয়, কিন্তু চূড়ান্ত avg_log_loss রিপোর্ট হয়
    **held-out test সেটের ওপর** — কারণ যে ডেটার ওপর tune করা হয়েছে সেই একই
    ডেটার ওপর মূল্যায়ন করলে সবসময় ভালো দেখাবে (overfitting), কিন্তু বাস্তব
    ম্যাচে সেই সুবিধা থাকবে না। baseline দিলে (predictor.py-এর বর্তমান
    weight) সেটাও একই test সেটে মূল্যায়ন করে তুলনা করা হয় — নতুন কম্বিনেশন
    সত্যিই ভালো কিনা (নাকি শুধু noise) সেটা baseline_avg_log_loss vs
    avg_log_loss তুলনা করে caller বুঝতে পারবে।
    """
    matches = matches_provider()
    if not matches:
        return {"error": "matches_provider() খালি লিস্ট রিটার্ন করেছে — calibration চালানোর জন্য ঐতিহাসিক, ফলাফল-জানা ম্যাচ দরকার।"}

    n_test = max(0, round(len(matches) * test_fraction))
    if n_test < min_test_n or (len(matches) - n_test) < min_test_n:
        return {
            "error": (
                f"matches_provider() মাত্র {len(matches)}টা sample দিয়েছে — train/test "
                f"উভয় ভাগেই অন্তত {min_test_n}টা করে (মোট {2*min_test_n}+) দরকার, নাহলে "
                "ফলাফল অতিরিক্ত noisy/overfit হওয়ার ঝুঁকি থাকে। আরও কিছুদিন data জমতে দিন "
                "বা build_calibration_set.py-তে TEAM_IDS/MAX_AUTO_TEAMS বাড়ান।"
            ),
            "n_matches": len(matches),
        }
    train, test = matches[n_test:], matches[:n_test]

    best_train = None
    for h2h_w, form_w, venue_w, half_life, rho in product(h2h_weights, form_weights, venue_weights, half_lives, rhos):
        total_w = h2h_w + form_w + venue_w
        h2h_w_n, form_w_n, venue_w_n = h2h_w / total_w, form_w / total_w, venue_w / total_w

        train_loss, train_n = _log_loss_for_params(train, h2h_w_n, form_w_n, venue_w_n, half_life, rho)
        candidate = {
            "h2h_weight": round(h2h_w_n, 3), "form_weight": round(form_w_n, 3),
            "venue_weight": round(venue_w_n, 3), "half_life": half_life, "rho": rho,
            "train_avg_log_loss": round(train_loss, 4), "train_n": train_n,
        }
        if best_train is None or train_loss < best_train["train_avg_log_loss"]:
            best_train = candidate

    test_loss, test_n = _log_loss_for_params(
        test, best_train["h2h_weight"], best_train["form_weight"],
        best_train["venue_weight"], best_train["half_life"], best_train["rho"],
    )
    result = dict(best_train)
    result["avg_log_loss"] = round(test_loss, 4)  # held-out — এটাই আসল/বিশ্বাসযোগ্য নাম্বার
    result["n_matches"] = train_n + test_n
    result["test_n"] = test_n

    if baseline:
        base_loss, base_n = _log_loss_for_params(
            test, baseline.get("h2h_weight", 0.15), baseline.get("form_weight", 0.40),
            baseline.get("venue_weight", 0.45), baseline.get("half_life", 4), baseline.get("rho", -0.11),
        )
        result["baseline_avg_log_loss"] = round(base_loss, 4)
        result["improves_on_baseline"] = test_loss < base_loss

    return result


if __name__ == "__main__":
    import sys
    report = accuracy_report()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report.get("n", 0) == 0:
        sys.exit(0)
