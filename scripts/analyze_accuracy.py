"""
analyze_accuracy.py
স্ট্যান্ডঅ্যালোন স্ক্রিপ্ট — GitHub Actions-এর অংশ না, ম্যানুয়ালি চালানোর জন্য
(যেমন: `python scripts/analyze_accuracy.py` কিছুদিন check_results.py চলার পর)।

../data/predictions_log.json পড়ে (যেসব এন্ট্রি আর pending নেই, অর্থাৎ
check_results.py আগে চেক করে ফেলেছে) দুইটা জিনিস বের করে:

  ১. Overall + confidence-bucket-ভিত্তিক accuracy: confidence_score যত বেশি,
     আসলেই কি hit-rate তত বেশি? যদি না হয়, confidence formula/সিগন্যাল ঠিক
     করা দরকার (advanced_stats.confidence_score)।

  ২. Calibration: predicted probability_pct যত বেশি (৭০%, ৮০%, ৯০%...), আসলেই
     কি সেই bucket-এর pick-গুলো ওই হারে সঠিক হচ্ছে? Model যদি নিয়মিত বলে
     "৮০% probability" কিন্তু আসলে ৬০% সময় সঠিক হয়, তাহলে model
     over-confident — সেটা এখানে ধরা পড়বে।

কম ডেটা (৩০-৫০ চেকড ম্যাচের কম) নিয়ে সিদ্ধান্তে আসা বিভ্রান্তিকর — অন্তত
কয়েক সপ্তাহ চালিয়ে যথেষ্ট sample জমা হওয়ার পর চালানো ভালো।
"""

import json
import os
import sys

LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "predictions_log.json")

CONFIDENCE_BUCKETS = [(0, 40), (40, 60), (60, 80), (80, 101)]
PROBABILITY_BUCKETS = [(0, 50), (50, 60), (60, 70), (70, 80), (80, 90), (90, 101)]


def _load_checked_entries():
    try:
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            log = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        print(f"লগ ফাইল পাওয়া যায়নি বা খালি: {LOG_PATH}", file=sys.stderr)
        return []
    return [e for e in log if e.get("status") not in (None, "pending")]


def _bucket_for(value, buckets):
    for lo, hi in buckets:
        if lo <= value < hi:
            return f"{lo}-{hi - 1}"
    return None


def analyze():
    entries = _load_checked_entries()
    if not entries:
        print("এখনো চেক-করা কোনো প্রেডিকশন নেই। check_results.py কিছুদিন চলার পর আবার চেষ্টা করুন।")
        return

    total = len(entries)
    correct = sum(1 for e in entries if e.get("best_pick_status") == "correct")
    incorrect = sum(1 for e in entries if e.get("best_pick_status") == "incorrect")
    no_pick = sum(1 for e in entries if e.get("best_pick_status") == "no_pick")
    decided = correct + incorrect

    print(f"=== সামগ্রিক ফলাফল ({total}টা checked ম্যাচ) ===")
    if decided > 0:
        print(f"best_pick accuracy: {correct}/{decided} = {round(100 * correct / decided, 1)}%")
    print(f"(no_pick: {no_pick}টা — কোনো market ৩ নম্বর ফ্যাক্টর MIN_ODDS পূরণ করেনি)")
    print()

    # ---- confidence bucket অনুযায়ী accuracy ----
    print("=== Confidence bucket অনুযায়ী accuracy ===")
    print("(যদি bucket যত বেশি তার accuracy তত বেশি না হয়, confidence formula ঠিক করা দরকার)")
    for lo, hi in CONFIDENCE_BUCKETS:
        bucket_entries = [
            e for e in entries
            if e.get("confidence_score_at_prediction") is not None
            and lo <= e["confidence_score_at_prediction"] < hi
        ]
        # নোট: শুধু generate_predictions.py-এর সাম্প্রতিক আপডেটের পর তৈরি হওয়া
        # log এন্ট্রিতেই confidence_score_at_prediction থাকবে — আপডেটের আগের
        # পুরনো এন্ট্রি এই bucket-এ আসবে না (None থাকায় ফিল্টার হয়ে যাবে)।
        if not bucket_entries:
            continue
        b_correct = sum(1 for e in bucket_entries if e.get("best_pick_status") == "correct")
        b_decided = sum(1 for e in bucket_entries if e.get("best_pick_status") in ("correct", "incorrect"))
        if b_decided:
            print(f"  confidence {lo}-{hi - 1}: {b_correct}/{b_decided} = {round(100 * b_correct / b_decided, 1)}% (n={len(bucket_entries)})")
    print()

    # ---- predicted probability bucket অনুযায়ী calibration ----
    print("=== Calibration: predicted probability vs আসল hit-rate ===")
    print("(ভালো calibration মানে '৮০% bucket'-এর pick-গুলো আসলেই ~৮০% সময় সঠিক হয়)")
    for lo, hi in PROBABILITY_BUCKETS:
        bucket_entries = []
        for e in entries:
            pick = e.get("best_pick")
            if not pick or pick.get("probability_pct") is None:
                continue
            if lo <= pick["probability_pct"] < hi:
                bucket_entries.append(e)
        if not bucket_entries:
            continue
        b_correct = sum(1 for e in bucket_entries if e.get("best_pick_status") == "correct")
        b_decided = sum(1 for e in bucket_entries if e.get("best_pick_status") in ("correct", "incorrect"))
        if b_decided:
            actual_rate = round(100 * b_correct / b_decided, 1)
            print(f"  predicted {lo}-{hi - 1}%: n={b_decided}, আসল hit-rate={actual_rate}%")

    print()
    print("Tip: সবচেয়ে informative হবে যখন প্রতিটা bucket-এ অন্তত ~২০-৩০টা করে sample জমবে।")


if __name__ == "__main__":
    analyze()
