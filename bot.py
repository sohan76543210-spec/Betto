"""
bot.py
মূল টেলিগ্রাম বট - Free ও VIP উভয় টিয়ারের ফুটবল প্রেডিকশন, একাধিক category,
daily auto-post, এবং prediction accuracy tracking সহ।

ইউজার কমান্ডসমূহ:
  /start          - বট শুরু ও পরিচিতি
  /today          - আজকের সেরা পিক (Free: সীমিত, VIP: পূর্ণ ব্রেকডাউন)
  /correctscore   - সবচেয়ে সম্ভাব্য correct-score prediction (VIP)
  /highodds       - বেশি odds-এর (>= 2.00) ঝুঁকিপূর্ণ কিন্তু বেশি রিটার্নের পিক (VIP)
  /combo          - একাধিক ম্যাচ মিলিয়ে accumulator/combo বেট সাজেশন (VIP)
  /accuracy       - বটের প্রেডিকশন history/accuracy দেখা (transparency)
  /dailyon        - প্রতিদিন সকালে অটো পিক পাওয়া চালু করা
  /dailyoff       - অটো পিক বন্ধ করা
  /vip            - VIP হওয়ার উপায় ও পেমেন্ট নির্দেশনা
  /verify <txid>  - পেমেন্ট ভেরিফিকেশন রিকোয়েস্ট পাঠানো
  /myaccount      - নিজের VIP স্ট্যাটাস দেখা

অ্যাডমিন কমান্ড:
  /pending        - পেন্ডিং পেমেন্ট রিকোয়েস্ট দেখা
  /approve <user_id> <days> - VIP অ্যাক্টিভেট করা
  /reject <user_id>         - রিকোয়েস্ট রিজেক্ট করা

ব্যাকগ্রাউন্ড জব:
  - প্রতিদিন সকাল ৯টায় (DAILY_POST_HOUR) সাবস্ক্রাইবারদের কাছে অটো পিক পাঠানো
  - প্রতি ৬ ঘণ্টায় আগের প্রেডিকশনগুলোর আসল ফলাফল চেক করে accuracy log আপডেট করা
"""

import os
import logging
from datetime import time as dtime
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

import database as db
import football_api
from predictor import (
    predict_match,
    best_pick,
    high_odds_pick,
    top_correct_scores,
    combo_pick,
    check_pick_correctness,
)

MIN_ODDS = 1.40          # সাধারণ "Best Pick" ক্যাটাগরির ন্যূনতম fair odds
HIGH_ODDS_THRESHOLD = 2.00  # "High-Odds Picks" ক্যাটাগরির ন্যূনতম fair odds
DAILY_POST_HOUR = 9      # সার্ভার সময় অনুযায়ী - প্রতিদিন সকাল ৯টায় অটো পোস্ট
RESULT_CHECK_INTERVAL_SECONDS = 6 * 60 * 60  # প্রতি ৬ ঘণ্টায় ফলাফল চেক

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ADMIN_IDS = set(
    int(x) for x in os.getenv("ADMIN_USER_IDS", "").split(",") if x.strip()
)

DISCLAIMER = (
    "\n\n⚠️ *মনে রাখবেন:* এটি পরিসংখ্যানভিত্তিক অনুমান, নিশ্চিত ফলাফল নয়। "
    "দেখানো odds আমাদের নিজস্ব মডেলের হিসাব (bookmaker-এর real odds না)। "
    "কোনো প্রেডিকশন সিস্টেম ১০০% accurate হতে পারে না। দায়িত্বশীলভাবে সিদ্ধান্ত নিন।"
)


# ---------- সাধারণ হেল্পার ----------

def _build_match_predictions(matches, limit=5):
    """
    ম্যাচ লিস্ট থেকে প্রতিটার জন্য prediction ও বিভিন্ন category-র pick বানিয়ে
    একটা লিস্ট রিটার্ন করে। এটা /today, /correctscore, /highodds, /combo -
    সবগুলো কমান্ড এই একই হেল্পার ব্যবহার করে।
    """
    results = []
    for m in matches:
        if len(results) >= limit:
            break
        try:
            pred = predict_match(m["homeTeam"]["id"], m["awayTeam"]["id"])
        except Exception as e:
            logger.warning(f"prediction failed for match {m.get('id')}: {e}")
            continue

        results.append({
            "match_external_id": m["id"],
            "match_date": m.get("utcDate"),
            "competition": m["competition"]["name"],
            "home_team": m["homeTeam"]["name"],
            "away_team": m["awayTeam"]["name"],
            "prediction": pred,
            "best": best_pick(pred, min_odds=MIN_ODDS),
            "high": high_odds_pick(pred, min_odds=HIGH_ODDS_THRESHOLD),
            "top_scores": top_correct_scores(pred, n=3),
        })
    return results


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.add_user(user.id, user.username or user.first_name)
    text = (
        f"👋 স্বাগতম, {user.first_name}!\n\n"
        "⚽ এই বট ফুটবল ম্যাচের পরিসংখ্যানভিত্তিক প্রেডিকশন দেয়, "
        "রিয়েল-টাইম টিম ফর্ম ও Poisson মডেল ব্যবহার করে।\n\n"
        "📋 ক্যাটাগরিসমূহ:\n"
        "/today - আজকের সেরা পিক\n"
        "/correctscore - সম্ভাব্য correct score (VIP)\n"
        "/highodds - বেশি odds-এর ঝুঁকিপূর্ণ পিক (VIP)\n"
        "/combo - একাধিক ম্যাচের accumulator (VIP)\n"
        "/accuracy - বটের track record\n"
        "/dailyon - প্রতিদিন সকালে অটো পিক চালু করুন\n"
        "/vip - VIP আনলক করার উপায়\n"
        "/myaccount - আপনার স্ট্যাটাস\n"
        + DISCLAIMER
    )
    await update.message.reply_markdown(text)


# ---------- Free/VIP category কমান্ড ----------

async def today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Best Pick category - ফ্রি ইউজার সীমিত তথ্য, VIP পূর্ণ ব্রেকডাউন।"""
    await update.message.reply_text("🔍 আজকের ম্যাচ খুঁজছি...")
    try:
        matches = football_api.get_upcoming_matches(days_ahead=1)
    except Exception as e:
        logger.error(f"API error: {e}")
        await update.message.reply_text("দুঃখিত, এখন ডেটা আনতে সমস্যা হচ্ছে। পরে চেষ্টা করুন।")
        return

    if not matches:
        await update.message.reply_text("আজ কোনো নির্ধারিত ম্যাচ পাওয়া যায়নি।")
        return

    user_vip = db.is_vip(update.effective_user.id)
    match_data = _build_match_predictions(matches, limit=8)

    lines = ["📅 *আজকের সাজেস্টেড পিক (Best Pick)*\n"]
    shown = 0
    for md in match_data:
        pick = md["best"]
        if pick is None:
            continue
        if shown >= 5:
            break

        pred = md["prediction"]
        lines.append(f"🏆 *{md['competition']}*")
        lines.append(f"{md['home_team']} vs {md['away_team']}")
        lines.append(f"✅ সাজেস্টেড পিক: *{pick['market']}*")
        lines.append(f"📈 সম্ভাবনা: {pick['probability_pct']}% | Fair Odds: {pick['fair_odds']}")

        if user_vip:
            lines.append(
                f"📊 প্রত্যাশিত গোল: {pred['home_expected_goals']} - {pred['away_expected_goals']}"
            )
            lines.append(f"🎯 সম্ভাব্য স্কোর: {pred['most_likely_score']}")
            lines.append(
                f"🔎 অন্যান্য মার্কেট: হোম {pred['home_win_pct']}% | ড্র {pred['draw_pct']}% | "
                f"অ্যাওয়ে {pred['away_win_pct']}% | O2.5 {pred['over_2_5_pct']}% | BTTS {pred['btts_yes_pct']}%"
            )
        else:
            lines.append("🔒 পূর্ণ ব্রেকডাউন দেখতে /vip দেখুন")
        lines.append("")
        shown += 1

    if shown == 0:
        lines.append(f"আজ {MIN_ODDS}+ odds-এর যোগ্য কোনো পিক পাওয়া যায়নি।")

    lines.append(DISCLAIMER)
    await update.message.reply_markdown("\n".join(lines))


async def correct_score_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Correct Score category - VIP-only, প্রতি ম্যাচের top ৩টা সম্ভাব্য স্কোরলাইন।"""
    if not db.is_vip(update.effective_user.id):
        await update.message.reply_text(
            "🔒 এই ফিচারটা VIP-only। /vip দেখে সাবস্ক্রাইব করুন।"
        )
        return

    await update.message.reply_text("🔍 Correct score prediction তৈরি হচ্ছে...")
    try:
        matches = football_api.get_upcoming_matches(days_ahead=1)
    except Exception as e:
        logger.error(f"API error: {e}")
        await update.message.reply_text("দুঃখিত, এখন ডেটা আনতে সমস্যা হচ্ছে।")
        return

    if not matches:
        await update.message.reply_text("আজ কোনো নির্ধারিত ম্যাচ পাওয়া যায়নি।")
        return

    match_data = _build_match_predictions(matches, limit=5)
    lines = ["🎯 *Correct Score প্রেডিকশন*\n"]
    for md in match_data:
        lines.append(f"🏆 *{md['competition']}* - {md['home_team']} vs {md['away_team']}")
        for s in md["top_scores"]:
            lines.append(f"   • {s['score']} — {s['probability_pct']}% (odds {s['fair_odds']})")
        lines.append("")

    lines.append(DISCLAIMER)
    await update.message.reply_markdown("\n".join(lines))


async def high_odds_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """High-Odds Picks category - VIP-only, বেশি ঝুঁকি/বেশি রিটার্নের পিক।"""
    if not db.is_vip(update.effective_user.id):
        await update.message.reply_text(
            "🔒 এই ফিচারটা VIP-only। /vip দেখে সাবস্ক্রাইব করুন।"
        )
        return

    await update.message.reply_text("🔍 High-odds পিক খোঁজা হচ্ছে...")
    try:
        matches = football_api.get_upcoming_matches(days_ahead=1)
    except Exception as e:
        logger.error(f"API error: {e}")
        await update.message.reply_text("দুঃখিত, এখন ডেটা আনতে সমস্যা হচ্ছে।")
        return

    match_data = _build_match_predictions(matches, limit=8)
    lines = [f"🔥 *High-Odds Picks (odds ≥ {HIGH_ODDS_THRESHOLD})*\n"]
    shown = 0
    for md in match_data:
        pick = md["high"]
        if pick is None:
            continue
        lines.append(f"🏆 *{md['competition']}* - {md['home_team']} vs {md['away_team']}")
        lines.append(f"⚡ পিক: *{pick['market']}* — {pick['probability_pct']}% (odds {pick['fair_odds']})")
        lines.append("")
        shown += 1
        if shown >= 5:
            break

    if shown == 0:
        lines.append(f"আজ {HIGH_ODDS_THRESHOLD}+ odds-এর যোগ্য কোনো পিক পাওয়া যায়নি।")

    lines.append(
        "\n⚠️ High-odds মানে কম সম্ভাবনা, বেশি ঝুঁকি — এগুলো সাধারণ পিকের চেয়ে বেশিবার ভুল হবে।"
    )
    lines.append(DISCLAIMER)
    await update.message.reply_markdown("\n".join(lines))


async def combo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Multi-bet Combo category - VIP-only, একাধিক ম্যাচের সবচেয়ে confident পিক মিলিয়ে accumulator।"""
    if not db.is_vip(update.effective_user.id):
        await update.message.reply_text(
            "🔒 এই ফিচারটা VIP-only। /vip দেখে সাবস্ক্রাইব করুন।"
        )
        return

    await update.message.reply_text("🔍 Combo বেট তৈরি হচ্ছে...")
    try:
        matches = football_api.get_upcoming_matches(days_ahead=1)
    except Exception as e:
        logger.error(f"API error: {e}")
        await update.message.reply_text("দুঃখিত, এখন ডেটা আনতে সমস্যা হচ্ছে।")
        return

    match_data = _build_match_predictions(matches, limit=10)
    match_picks = [
        {"match": f"{md['home_team']} vs {md['away_team']}", "pick": md["best"]}
        for md in match_data
    ]

    combo = combo_pick(match_picks, legs=2)
    if combo is None:
        await update.message.reply_text(
            "আজ যথেষ্ট যোগ্য ম্যাচ নেই একটা combo বানানোর জন্য (কমপক্ষে ২টা দরকার)।"
        )
        return

    lines = ["🎰 *Multi-bet Combo সাজেশন*\n"]
    for leg in combo["legs"]:
        lines.append(f"• {leg['match']} — {leg['market']} ({leg['probability_pct']}%, odds {leg['fair_odds']})")
    lines.append("")
    lines.append(f"📊 সম্মিলিত সম্ভাবনা: {combo['combined_probability_pct']}%")
    lines.append(f"💰 সম্মিলিত Odds: {combo['combined_odds']}")
    lines.append(
        "\n⚠️ Combo বেটে সবগুলো লেগ সঠিক হতে হবে জেতার জন্য - "
        "একটা ভুল হলেই পুরো combo হেরে যাবে। ঝুঁকি একক পিকের চেয়ে অনেক বেশি।"
    )
    lines.append(DISCLAIMER)
    await update.message.reply_markdown("\n".join(lines))


async def accuracy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """বটের এখন পর্যন্ত করা প্রেডিকশনের accuracy track record দেখায় (transparency)।"""
    stats = db.get_accuracy_stats()
    if stats["total"] == 0:
        await update.message.reply_text(
            "এখনো কোনো verified prediction নেই। daily auto-post চালু হলে ধীরে ধীরে history জমা হবে।"
        )
        return

    win_rate = round((stats["correct"] / stats["total"]) * 100, 1)
    lines = [
        "📊 *বটের Track Record (Transparency)*\n",
        f"মোট verified prediction: {stats['total']}",
        f"সঠিক হয়েছে: {stats['correct']} ({win_rate}%)",
        "",
    ]
    if stats["by_category"]:
        lines.append("ক্যাটাগরি অনুযায়ী:")
        for c in stats["by_category"]:
            cat_rate = round((c["correct"] / c["total"]) * 100, 1) if c["total"] else 0
            lines.append(f"  • {c['category']}: {c['correct']}/{c['total']} ({cat_rate}%)")

    lines.append(
        "\n⚠️ অতীতের accuracy ভবিষ্যতের ফলাফলের গ্যারান্টি না — শুধু transparency-র জন্য দেখানো হলো।"
    )
    await update.message.reply_markdown("\n".join(lines))


async def daily_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.set_daily_alerts(update.effective_user.id, True)
    await update.message.reply_text(
        f"✅ প্রতিদিন সকাল {DAILY_POST_HOUR}টায় (সার্ভার সময়) অটো পিক পাবেন। বন্ধ করতে /dailyoff দিন।"
    )


async def daily_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.set_daily_alerts(update.effective_user.id, False)
    await update.message.reply_text("❌ অটো পিক বন্ধ করা হয়েছে।")


async def vip_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    price = os.getenv("VIP_PRICE_DISPLAY", "যোগাযোগ করুন")
    payment_info = os.getenv("PAYMENT_INSTRUCTIONS", "অ্যাডমিনের সাথে যোগাযোগ করুন")
    text = (
        f"💎 *VIP সাবস্ক্রিপশন* - {price}\n\n"
        "VIP মেম্বাররা পাবেন:\n"
        "✅ Best Pick-এর পূর্ণ ব্রেকডাউন (expected goals, সব মার্কেট)\n"
        "✅ Correct Score prediction\n"
        "✅ High-Odds Picks\n"
        "✅ Multi-bet Combo সাজেশন\n\n"
        f"💳 পেমেন্ট পদ্ধতি:\n{payment_info}\n\n"
        "পেমেন্ট করার পর /verify <TransactionID> কমান্ড দিয়ে জানান।\n"
        + DISCLAIMER
    )
    await update.message.reply_markdown(text)


async def verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("ব্যবহার: /verify <Transaction ID>")
        return
    txid = context.args[0]
    user = update.effective_user
    req_id = db.create_payment_request(user.id, txid)
    await update.message.reply_text(
        "✅ আপনার রিকোয়েস্ট জমা হয়েছে। যাচাই করার পর VIP অ্যাক্টিভেট করা হবে।"
    )
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                admin_id,
                f"🔔 নতুন পেমেন্ট রিকোয়েস্ট #{req_id}\n"
                f"ইউজার: @{user.username or user.first_name} (ID: {user.id})\n"
                f"Transaction ID: {txid}\n\n"
                f"অ্যাপ্রুভ করতে: /approve {user.id} 30",
            )
        except Exception as e:
            logger.warning(f"Could not notify admin {admin_id}: {e}")


async def myaccount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    vip = db.is_vip(user_id)
    status = "💎 VIP (সক্রিয়)" if vip else "🆓 Free ইউজার"
    await update.message.reply_text(f"আপনার স্ট্যাটাস: {status}")


# ---------- Admin commands ----------

def _is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return
    reqs = db.get_pending_requests()
    if not reqs:
        await update.message.reply_text("কোনো পেন্ডিং রিকোয়েস্ট নেই।")
        return
    lines = ["📋 পেন্ডিং পেমেন্ট রিকোয়েস্ট:\n"]
    for r in reqs:
        lines.append(f"#{r['id']} | User: {r['user_id']} | TxID: {r['transaction_id']}")
    await update.message.reply_text("\n".join(lines))


async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return
    if len(context.args) < 2:
        await update.message.reply_text("ব্যবহার: /approve <user_id> <days>")
        return
    target_id, days = int(context.args[0]), int(context.args[1])
    db.grant_vip(target_id, days=days)
    await update.message.reply_text(f"✅ ইউজার {target_id} কে {days} দিনের VIP দেওয়া হয়েছে।")
    try:
        await context.bot.send_message(
            target_id, f"🎉 আপনার VIP সাবস্ক্রিপশন অ্যাক্টিভেট হয়েছে! মেয়াদ: {days} দিন।"
        )
    except Exception:
        pass


async def reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("ব্যবহার: /reject <user_id>")
        return
    target_id = int(context.args[0])
    await update.message.reply_text(f"ইউজার {target_id} এর রিকোয়েস্ট রিজেক্ট করা হয়েছে।")
    try:
        await context.bot.send_message(
            target_id, "❌ আপনার পেমেন্ট ভেরিফাই করা যায়নি। সঠিক Transaction ID দিয়ে আবার চেষ্টা করুন।"
        )
    except Exception:
        pass


# ---------- ব্যাকগ্রাউন্ড জব ----------

async def job_daily_post(context: ContextTypes.DEFAULT_TYPE):
    """
    প্রতিদিন নির্দিষ্ট সময়ে চলে:
    ১) আজকের ম্যাচের prediction বানায়
    ২) accuracy-history-র জন্য ডেটাবেজে লগ করে
    ৩) daily_alerts চালু থাকা সব সাবস্ক্রাইবারকে পাঠায় (VIP status অনুযায়ী তথ্যের পরিমাণ ভিন্ন)
    """
    logger.info("Daily post job শুরু হলো...")
    try:
        matches = football_api.get_upcoming_matches(days_ahead=1)
    except Exception as e:
        logger.error(f"Daily job - API error: {e}")
        return

    if not matches:
        logger.info("Daily job - আজ কোনো ম্যাচ নেই।")
        return

    match_data = _build_match_predictions(matches, limit=8)

    # accuracy tracking-এর জন্য প্রতিটা "Best Pick" ডেটাবেজে লগ করা
    for md in match_data:
        if md["best"] is not None:
            db.log_prediction(
                match_external_id=md["match_external_id"],
                competition=md["competition"],
                home_team=md["home_team"],
                away_team=md["away_team"],
                match_date=md["match_date"],
                category="Best Pick",
                market=md["best"]["market"],
                probability_pct=md["best"]["probability_pct"],
                fair_odds=md["best"]["fair_odds"],
            )

    subscribers = db.get_daily_alert_subscribers()
    if not subscribers:
        logger.info("Daily job - কোনো সাবস্ক্রাইবার নেই।")
        return

    lines = ["🌅 *আজকের সকালের পিক*\n"]
    shown = 0
    for md in match_data:
        pick = md["best"]
        if pick is None:
            continue
        lines.append(f"🏆 {md['competition']}: {md['home_team']} vs {md['away_team']}")
        lines.append(f"✅ {pick['market']} — {pick['probability_pct']}% (odds {pick['fair_odds']})")
        lines.append("")
        shown += 1
        if shown >= 5:
            break

    if shown == 0:
        return

    lines.append(DISCLAIMER)
    text = "\n".join(lines)

    for user_id in subscribers:
        try:
            await context.bot.send_message(user_id, text, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Could not send daily post to {user_id}: {e}")


async def job_check_results(context: ContextTypes.DEFAULT_TYPE):
    """
    প্রতি কয়েক ঘণ্টায় চলে: আগের যেসব prediction-এর ফলাফল এখনো verify করা হয়নি,
    সেগুলোর ম্যাচ শেষ হয়েছে কিনা চেক করে এবং হয়ে থাকলে accuracy log আপডেট করে।
    """
    unverified = db.get_unverified_predictions()
    if not unverified:
        return

    logger.info(f"{len(unverified)} টা prediction-এর ফলাফল চেক করা হচ্ছে...")
    for pred_row in unverified:
        try:
            result = football_api.get_match_result(pred_row["match_external_id"])
        except Exception as e:
            logger.warning(f"Result check failed for match {pred_row['match_external_id']}: {e}")
            continue

        if result["status"] != "FINISHED":
            continue  # এখনো ম্যাচ শেষ হয়নি

        home_goals = result["home_goals"]
        away_goals = result["away_goals"]
        if home_goals is None or away_goals is None:
            continue

        was_correct = check_pick_correctness(pred_row["market"], home_goals, away_goals)
        db.mark_prediction_result(pred_row["id"], home_goals, away_goals, was_correct)
        logger.info(
            f"Prediction #{pred_row['id']} ({pred_row['market']}) verified: "
            f"{'✅ সঠিক' if was_correct else '❌ ভুল'}"
        )


def main():
    db.init_db()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN .env ফাইলে সেট করা নেই!")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("today", today))
    app.add_handler(CommandHandler("correctscore", correct_score_cmd))
    app.add_handler(CommandHandler("highodds", high_odds_cmd))
    app.add_handler(CommandHandler("combo", combo_cmd))
    app.add_handler(CommandHandler("accuracy", accuracy_cmd))
    app.add_handler(CommandHandler("dailyon", daily_on))
    app.add_handler(CommandHandler("dailyoff", daily_off))
    app.add_handler(CommandHandler("vip", vip_info))
    app.add_handler(CommandHandler("verify", verify))
    app.add_handler(CommandHandler("myaccount", myaccount))
    app.add_handler(CommandHandler("pending", pending))
    app.add_handler(CommandHandler("approve", approve))
    app.add_handler(CommandHandler("reject", reject))

    # ব্যাকগ্রাউন্ড জব সেটআপ (python-telegram-bot এর বিল্ট-ইন JobQueue ব্যবহার করে,
    # এটার জন্য 'pip install python-telegram-bot[job-queue]' লাগবে)
    if app.job_queue is not None:
        app.job_queue.run_daily(
            job_daily_post, time=dtime(hour=DAILY_POST_HOUR, minute=0)
        )
        app.job_queue.run_repeating(
            job_check_results, interval=RESULT_CHECK_INTERVAL_SECONDS, first=60
        )
    else:
        logger.warning(
            "JobQueue পাওয়া যায়নি - 'pip install python-telegram-bot[job-queue]' ইনস্টল করুন, "
            "নাহলে daily auto-post ও accuracy tracking কাজ করবে না।"
        )

    logger.info("বট চালু হচ্ছে...")
    app.run_polling()


if __name__ == "__main__":
    main()
