"""
elo.py
পার্সিস্টেন্ট Elo rating সিস্টেম। কোনো নতুন API কল লাগে না — এটা যে ম্যাচ
ডেটা এমনিতেই get_team_recent_form() দিয়ে ফেচ হয় (বা ব্যাকফিলের জন্য history
এন্ডপয়েন্ট), সেটা থেকেই রেটিং আপডেট করে এবং লোকাল ফাইলে (JSON) জমা রাখে।

power_rating() (advanced_stats.py) শুধু সাম্প্রতিক ৮-১০ ম্যাচ থেকে প্রতিবার
নতুন করে হিসাব করে — সিজনের শুরুতে বা কম ম্যাচ থাকলে অস্থির/অনির্ভরযোগ্য হয়ে
যায়। Elo এর বদলে প্রতি ম্যাচ শেষে ধীরে ধীরে আপডেট হয় এবং সিজন থেকে সিজন
"মেমরি" বহন করে (হালকা regression-to-mean সহ), তাই ছোট sample-এও বেশি স্থিতিশীল।

ব্যবহার:
    from elo import EloStore

    store = EloStore()  # ডিফল্ট পাথে লোড করে
    home_rating = store.get(home_team_id)
    away_rating = store.get(away_team_id)
    # ... xG হিসাবে home_rating/away_rating ব্যবহার করুন (elo_probability বা
    # সরাসরি multiplier হিসেবে) ...

    # ম্যাচ শেষ হলে ফলাফল দিয়ে আপডেট করুন এবং সেভ করুন:
    store.update(home_team_id, away_team_id, home_goals, away_goals)
    store.save()
"""
import json
import math
import os

# repo-এর data/ ফোল্ডারে রাখা হয় (predictions.json/history.json-এর মতোই) —
# এই রিপোর্তে GitHub Actions runner প্রতিবার ফ্রেশ VM-এ চলে, তাই .cache/ এর
# মতো কোনো টেম্প ফোল্ডারে রাখলে রেটিং প্রতি রানেই হারিয়ে যেত। data/ ফোল্ডার
# ওয়ার্কফ্লো-তে git commit হয় বলে এখানে রাখলেই রান-টু-রান পার্সিস্ট করবে।
DEFAULT_PATH = os.environ.get(
    "PREDICTOR_ELO_PATH",
    os.path.join(os.path.dirname(__file__), "..", "data", "elo_ratings.json"),
)
BASE_RATING = 1500.0
K_FACTOR = 20.0          # প্রতি ম্যাচে সর্বোচ্চ কতটা রেটিং সরে — ফুটবলে ২০ একটা প্রচলিত রক্ষণশীল মান
HOME_ELO_BONUS = 60.0    # হোম টিমের জন্য এক্সপেক্টেড-স্কোর হিসাবের সময় virtual বোনাস
GOAL_DIFF_DAMPING = 1.5  # বড় ব্যবধানে জেতাকে সামান্য বেশি ওজন দেওয়ার জন্য (কিন্তু ক্যাপড)
REGRESSION_TO_MEAN = 0.25  # নতুন সিজন শুরুতে রেটিং কতটা গড়ের দিকে টেনে আনা হবে (০-১)


class EloStore:
    def __init__(self, path=DEFAULT_PATH):
        self.path = path
        self._ratings = {}   # team_id(str) -> rating(float)
        self._season_tag = None  # কোন সিজনের জন্য রেটিং লোড হয়েছে, regression চেক করতে
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                self._ratings = {str(k): float(v) for k, v in payload.get("ratings", {}).items()}
                self._season_tag = payload.get("season_tag")
            except (json.JSONDecodeError, OSError):
                self._ratings = {}

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump({"ratings": self._ratings, "season_tag": self._season_tag}, f)
        os.replace(tmp_path, self.path)

    def get(self, team_id):
        return self._ratings.get(str(team_id), BASE_RATING)

    def expected_score(self, home_team_id, away_team_id):
        """ক্লাসিক Elo win-expectancy ফর্মুলা, হোম বোনাস সহ। রিটার্ন করে
        (home_expected, away_expected) যেখানে home_expected + away_expected = 1
        (ড্র বিবেচনায় নেয় না — সেটা Poisson মডেলেই হয়, এটা শুধু আপেক্ষিক শক্তির
        একটা স্কেলার হিসেবে ব্যবহারের জন্য)।"""
        rh = self.get(home_team_id) + HOME_ELO_BONUS
        ra = self.get(away_team_id)
        exp_home = 1.0 / (1.0 + 10 ** ((ra - rh) / 400.0))
        return exp_home, 1.0 - exp_home

    def elo_xg_multiplier(self, home_team_id, away_team_id, cap=(0.85, 1.18)):
        """predict_match()-এ ব্যবহারের জন্য: Elo-ভিত্তিক প্রত্যাশার সাথে ৫০-৫০
        (কোনো তথ্য নেই অবস্থা) তুলনা করে হোম/অ্যাওয়ে xG-তে ছোট, ক্যাপড
        multiplier রিটার্ন করে। যেমন predictor.py-তে:

            h_elo, a_elo = elo_store.elo_xg_multiplier(home_id, away_id)
            home_xg *= h_elo
            away_xg *= a_elo
        """
        exp_home, exp_away = self.expected_score(home_team_id, away_team_id)
        # ০.৫ থেকে বিচ্যুতি অনুযায়ী multiplier — যত বেশি Elo gap, তত বেশি (কিন্তু ক্যাপড) adjust
        lo, hi = cap
        h_mult = max(lo, min(hi, 1.0 + (exp_home - 0.5) * 0.6))
        a_mult = max(lo, min(hi, 1.0 + (exp_away - 0.5) * 0.6))
        return round(h_mult, 3), round(a_mult, 3)

    def update(self, home_team_id, away_team_id, home_goals, away_goals):
        """একটা সম্পন্ন ম্যাচের ফলাফল দিয়ে উভয় টিমের Elo আপডেট করে। এটা
        মেমরিতে করে — শেষে save() ডাকতে হবে ডিস্কে লিখতে।"""
        rh = self.get(home_team_id) + HOME_ELO_BONUS
        ra = self.get(away_team_id)
        exp_home = 1.0 / (1.0 + 10 ** ((ra - rh) / 400.0))
        exp_away = 1.0 - exp_home

        if home_goals > away_goals:
            actual_home, actual_away = 1.0, 0.0
        elif home_goals < away_goals:
            actual_home, actual_away = 0.0, 1.0
        else:
            actual_home = actual_away = 0.5

        gd = abs(home_goals - away_goals)
        margin_mult = math.log(gd + 1) / math.log(GOAL_DIFF_DAMPING + 1) if gd > 0 else 1.0
        margin_mult = max(1.0, min(1.75, margin_mult))  # বড় জয়কে সামান্য বেশি গুরুত্ব, কিন্তু ক্যাপড

        delta_home = K_FACTOR * margin_mult * (actual_home - exp_home)
        delta_away = K_FACTOR * margin_mult * (actual_away - exp_away)

        self._ratings[str(home_team_id)] = self.get(home_team_id) + delta_home
        self._ratings[str(away_team_id)] = self.get(away_team_id) + delta_away

    def regress_to_mean(self, season_tag, factor=REGRESSION_TO_MEAN):
        """নতুন সিজন শুরু হলে একবার ডাকুন (season_tag বদলালে স্বয়ংক্রিয়ভাবে
        প্রযোজ্য করতে চাইলে predictor-এর caller থেকে চেক করে কল করুন)। প্রতিটা
        রেটিংকে BASE_RATING-এর দিকে factor অনুযায়ী টেনে আনে যাতে গত সিজনের
        চরম রেটিং নতুন সিজনে ওভার-কনফিডেন্ট প্রেডিকশন না দেয়, কিন্তু পুরনো
        ফর্মের তথ্য পুরোপুরি হারিয়েও না যায়।"""
        if self._season_tag == season_tag:
            return  # ইতিমধ্যে এই সিজনের জন্য regress করা হয়েছে
        for team_id in list(self._ratings.keys()):
            r = self._ratings[team_id]
            self._ratings[team_id] = r + (BASE_RATING - r) * factor
        self._season_tag = season_tag

    def backfill_from_matches(self, matches):
        """ঐতিহাসিক ম্যাচের লিস্ট (get_team_recent_form()-এর ফরম্যাটে, প্রতিটায়
        homeTeam/awayTeam/score.fullTime) দিয়ে ব্যাচে Elo বিল্ড করতে। ম্যাচগুলো
        তারিখ অনুযায়ী পুরনো থেকে নতুন ক্রমে সাজানো থাকা জরুরি — অন্যথায় রেটিং
        ভুল ক্রমে আপডেট হবে।"""
        matches = sorted(matches or [], key=lambda m: m.get("utcDate", ""))
        for m in matches:
            ft = m.get("score", {}).get("fullTime", {})
            hg, ag = ft.get("home"), ft.get("away")
            if hg is None or ag is None:
                continue
            hi = m.get("homeTeam", {}).get("id")
            ai = m.get("awayTeam", {}).get("id")
            if hi is None or ai is None:
                continue
            self.update(hi, ai, hg, ag)
