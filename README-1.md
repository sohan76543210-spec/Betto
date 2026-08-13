# Football Prediction Telegram Bot

Free + VIP subscription সিস্টেম সহ একটি টেলিগ্রাম বট, যা `football-data.org` API থেকে রিয়েল ম্যাচ ডেটা নিয়ে **Poisson distribution** পরিসংখ্যান মডেলের মাধ্যমে ম্যাচের সম্ভাব্য ফলাফল অনুমান করে।

## ⚠️ গুরুত্বপূর্ণ সত্য কথা

- এই বট **কোনো "guaranteed" বা ১০০% accurate prediction দেয় না** — কারণ কোনো সিস্টেমই তা দিতে পারে না। ফুটবল ম্যাচের ফলাফলে অনেক random factor (ইনজুরি, রেফারি সিদ্ধান্ত, আবহাওয়া, লাক) থাকে যা কোনো মডেলে ধরা যায় না।
- মূল স্ক্রিনশটে দেখানো অ্যাপটির মতো "guaranteed win" বা "accurate prediction" claim করা **misleading advertising** হিসেবে গণ্য হতে পারে অনেক দেশে, এবং এতে ইউজারদের আর্থিক ক্ষতি হতে পারে।
- এই বট শুধু **data-driven probability estimate** দেয় (যেমন: "৫৫% জয়ের সম্ভাবনা"), guarantee না — এবং প্রতিটি রেসপন্সে disclaimer যুক্ত আছে।
- বেটিং/জুয়া অনেক দেশে (বাংলাদেশ সহ) আইনত নিষিদ্ধ বা restricted। যদি টাকার বিনিময়ে VIP subscription বিক্রি করার পরিকল্পনা করেন, নিজ দেশের আইন যাচাই করে নিন — বিশেষ করে online gambling-সম্পর্কিত কনটেন্ট/সার্ভিস বিক্রির ক্ষেত্রে।

## ফিচার

- `/start` — পরিচিতি
- `/today` — Best Pick category, আজকের ম্যাচের সেরা পিক (Free: সীমিত তথ্য, VIP: পূর্ণ ব্রেকডাউন)
- `/correctscore` — (VIP-only) প্রতি ম্যাচের সম্ভাব্য top ৩টা স্কোরলাইন
- `/highodds` — (VIP-only) বেশি ঝুঁকি/বেশি রিটার্নের পিক (fair odds ≥ ২.০০)
- `/combo` — (VIP-only) একাধিক ম্যাচের সবচেয়ে confident পিক মিলিয়ে accumulator/combo বেট সাজেশন
- `/accuracy` — বটের এখন পর্যন্ত করা প্রেডিকশনের সত্যিকারের সাফল্যের হার (transparency)
- `/dailyon` / `/dailyoff` — প্রতিদিন সকালে অটোমেটিক পিক পাওয়া চালু/বন্ধ করা
- `/vip` — VIP সাবস্ক্রিপশনের তথ্য ও পেমেন্ট নির্দেশনা
- `/verify <TransactionID>` — পেমেন্ট যাচাইয়ের অনুরোধ (ম্যানুয়াল অ্যাডমিন অ্যাপ্রুভাল)
- `/myaccount` — নিজের VIP স্ট্যাটাস
- অ্যাডমিনদের জন্য: `/pending`, `/approve <user_id> <days>`, `/reject <user_id>`

### ব্যাকগ্রাউন্ড জব (স্বয়ংক্রিয়)

- **Daily auto-post**: প্রতিদিন সকাল ৯টায় (সার্ভার সময়, `bot.py`-তে `DAILY_POST_HOUR` দিয়ে বদলানো যায়) যারা `/dailyon` করেছে তাদের কাছে সেরা পিক পাঠানো হয়
- **Accuracy tracker**: প্রতি ৬ ঘণ্টায় আগের প্রেডিকশনগুলোর ম্যাচ শেষ হয়েছে কিনা চেক করে, শেষ হলে আসল স্কোরের সাথে মিলিয়ে prediction সঠিক ছিল কিনা রেকর্ড করে — এই তথ্যই `/accuracy` কমান্ডে দেখানো হয়

### Category-ভিত্তিক odds থ্রেশহোল্ড

- Best Pick: fair odds ≥ ১.৪০ (নিরাপদ, বেশি সম্ভাবনার পিক)
- High-Odds Picks: fair odds ≥ ২.০০ (বেশি ঝুঁকি, কম সম্ভাবনা কিন্তু বেশি রিটার্ন)
- এই মানগুলো `bot.py`-এর উপরে `MIN_ODDS` ও `HIGH_ODDS_THRESHOLD` ভ্যারিয়েবল দিয়ে বদলানো যায়

VIP ইউজাররা VIP-only কমান্ডগুলো (`/correctscore`, `/highodds`, `/combo`) পুরোপুরি ব্যবহার করতে পারবে; ফ্রি ইউজাররা শুধু `/today`-তে সীমিত তথ্য পাবে।

## সেটআপ

### ১. প্রয়োজনীয় জিনিস
- Python 3.10+
- একটা Telegram bot token — [@BotFather](https://t.me/BotFather) থেকে `/newbot` দিয়ে বানাবেন
- একটা football-data.org API key — [ফ্রি রেজিস্ট্রেশন](https://www.football-data.org/client/register) (ফ্রি টিয়ারে দিনে ১০ রিকোয়েস্ট/মিনিট লিমিট আছে)

### ২. ইনস্টলেশন

```bash
cd football_bot
pip install -r requirements.txt
cp .env.example .env
```

তারপর `.env` ফাইল খুলে নিজের তথ্য বসান:

```
TELEGRAM_BOT_TOKEN=আপনার_বট_টোকেন
FOOTBALL_DATA_API_KEY=আপনার_api_key
ADMIN_USER_IDS=আপনার_টেলিগ্রাম_ইউজার_আইডি
```

আপনার টেলিগ্রাম ইউজার আইডি জানতে [@userinfobot](https://t.me/userinfobot) কে মেসেজ দিন।

### ৩. চালানো

```bash
python bot.py
```

## পেমেন্ট সিস্টেম কীভাবে কাজ করে

এখানে সরাসরি payment gateway (Stripe/bKash API) integrate করা নেই — বরং **manual verification flow**:

1. ইউজার `/vip` দিয়ে পেমেন্ট নির্দেশনা দেখে (যেমন bKash নাম্বার)
2. টাকা পাঠিয়ে `/verify <TransactionID>` কমান্ড দেয়
3. অ্যাডমিন নোটিফিকেশন পায়, bKash/Nagad অ্যাপে গিয়ে transaction যাচাই করে
4. `/approve <user_id> <days>` দিয়ে VIP অ্যাক্টিভেট করে

চাইলে পরে bKash Merchant API বা Stripe দিয়ে স্বয়ংক্রিয় পেমেন্ট যোগ করা যাবে — এটার জন্য bKash/Nagad-এর business/merchant account লাগবে।

## ডেপ্লয়মেন্ট

লোকাল টেস্টের পর ২৪/৭ চালাতে:
- **Railway.app** বা **Render.com** — ফ্রি টিয়ারে সহজে Python bot হোস্ট করা যায়
- VPS (DigitalOcean, Linode) + `systemd` বা `screen`/`tmux` দিয়ে persistent রান

## পরবর্তী উন্নতির আইডিয়া

- আরো লিগ যোগ (football-data.org এর paid tier-এ আরো competition পাওয়া যায়)
- আসল bookmaker odds ব্যবহার (Odds API ইন্টিগ্রেট করে fair odds-এর বদলে/পাশাপাশি)
- Inline button/menu দিয়ে category বাছাইয়ের UI (এখন সব text কমান্ড দিয়ে)
- নির্দিষ্ট লিগ ফিল্টার করার অপশন (যেমন শুধু Premier League দেখতে চাইলে)
