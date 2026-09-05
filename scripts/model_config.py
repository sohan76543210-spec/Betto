"""
model_config.py
predictor.py-এর হাইপারপ্যারামিটার (H2H/Form/Venue weight, recency half-life,
Dixon-Coles rho) আগে predictor.py-এর ভেতরে হার্ডকোড করা ছিল — তাই
build_calibration_set.py/tracking.run_calibration() যতই সেরা কম্বিনেশন বের
করুক না কেন, সেটা predictor.py-তে কার্যকর হতে ম্যানুয়ালি কোড এডিট + ডিপ্লয়
লাগতো, যেটা বাস্তবে কখনো হয়নি।

এই মডিউল সেই মানগুলো data/model_config.json থেকে লোড করে (না থাকলে/ভুল
ফরম্যাট হলে নিচের DEFAULT_CONFIG ফলব্যাক হিসেবে ব্যবহার হয়) — ফলে
build_calibration_set.py সরাসরি এই ফাইলে লিখে দিলেই পরের predict_match()
কল থেকে নতুন weight কাজ করা শুরু করে, কোনো predictor.py এডিট বা redeploy
ছাড়াই।

সেফটি: এখানে ইচ্ছাকৃতভাবে sanity-bound রাখা হয়েছে (weight negative না, rho
পরিসীমার মধ্যে ইত্যাদি) — যাতে একটা করাপ্টেড/ম্যানুয়ালি ভুল-এডিট করা
model_config.json পুরো predictor.py-কে ক্র্যাশ বা পাগলাটে prediction না
দেয়; সমস্যা হলে চুপচাপ DEFAULT_CONFIG-এ ফিরে যায়।
"""
import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "model_config.json")

DEFAULT_CONFIG = {
    "h2h_weight": 0.15,
    "form_weight": 0.40,
    "venue_weight": 0.45,
    "half_life": 4,
    "rho": -0.11,
    # মেটাডেটা — শুধু transparency/debugging-এর জন্য, predict_match() এগুলো
    # ব্যবহার করে না।
    "source": "default (কোনো calibration ফাইল থেকে না, কোডে হার্ডকোড করা)",
    "calibrated_at": None,
    "calibration_n_matches": None,
}

_BOUNDS = {
    "h2h_weight": (0.0, 1.0),
    "form_weight": (0.0, 1.0),
    "venue_weight": (0.0, 1.0),
    "half_life": (1, 20),
    "rho": (-0.35, 0.10),
}


def _sanitize(cfg):
    out = dict(DEFAULT_CONFIG)
    if not isinstance(cfg, dict):
        return out
    for key, (lo, hi) in _BOUNDS.items():
        val = cfg.get(key)
        if isinstance(val, (int, float)) and lo <= val <= hi:
            out[key] = val
    # weight তিনটা সবগুলো ০ হলে normalize করা যাবে না — সেক্ষেত্রেও ডিফল্টে ফিরে যাওয়া
    if out["h2h_weight"] + out["form_weight"] + out["venue_weight"] <= 0:
        out["h2h_weight"] = DEFAULT_CONFIG["h2h_weight"]
        out["form_weight"] = DEFAULT_CONFIG["form_weight"]
        out["venue_weight"] = DEFAULT_CONFIG["venue_weight"]
    for meta_key in ("source", "calibrated_at", "calibration_n_matches"):
        if meta_key in cfg:
            out[meta_key] = cfg[meta_key]
    return out


_cached = None


def load_config(force_reload=False):
    """মডিউল-লেভেল ক্যাশ — একবার প্রসেস চলাকালীন একবারই ফাইল পড়ে। GitHub
    Actions-এর প্রতিটা রান নতুন প্রসেস, তাই সবসময় সাম্প্রতিক ফাইল পড়া হয়;
    লোকাল লং-রানিং প্রসেসে নতুন calibration প্রয়োগ করতে force_reload=True দিন।
    """
    global _cached
    if _cached is not None and not force_reload:
        return _cached
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        raw = None
    _cached = _sanitize(raw)
    return _cached


def write_config(new_values, extra_meta=None):
    """calibration script থেকে কল করার জন্য — sanitize করেই লেখে, যাতে ভুল
    মান ফাইলে গিয়ে predictor.py-কে ক্র্যাশ না করায়।"""
    merged = dict(load_config())
    merged.update(new_values or {})
    if extra_meta:
        merged.update(extra_meta)
    clean = _sanitize(merged)
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)
    global _cached
    _cached = clean
    return clean
