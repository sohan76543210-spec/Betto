"""
database.py
SQLite ডেটাবেজ - ইউজার, VIP সাবস্ক্রিপশন, এবং পেমেন্ট ভেরিফিকেশন রিকোয়েস্ট সংরক্ষণ করে।
"""

import sqlite3
from datetime import datetime, timedelta
from contextlib import contextmanager

DB_PATH = "bot_data.db"


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                joined_at TEXT,
                is_vip INTEGER DEFAULT 0,
                vip_expires_at TEXT,
                daily_alerts INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS payment_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                transaction_id TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS predictions_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_external_id INTEGER,
                competition TEXT,
                home_team TEXT,
                away_team TEXT,
                match_date TEXT,
                category TEXT,
                market TEXT,
                probability_pct REAL,
                fair_odds REAL,
                actual_home_goals INTEGER,
                actual_away_goals INTEGER,
                result_checked INTEGER DEFAULT 0,
                was_correct INTEGER,
                created_at TEXT
            )
        """)
        # পুরনো ডেটাবেজে daily_alerts কলাম না থাকলে যোগ করার জন্য (backward compatibility)
        try:
            conn.execute("ALTER TABLE users ADD COLUMN daily_alerts INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # কলাম আগে থেকেই আছে


def add_user(user_id: int, username: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, username, joined_at) VALUES (?, ?, ?)",
            (user_id, username, datetime.utcnow().isoformat()),
        )


def is_vip(user_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT is_vip, vip_expires_at FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row or not row["is_vip"]:
            return False
        if row["vip_expires_at"]:
            expires = datetime.fromisoformat(row["vip_expires_at"])
            if expires < datetime.utcnow():
                # মেয়াদ শেষ - অটো ডিঅ্যাক্টিভেট
                conn.execute(
                    "UPDATE users SET is_vip = 0 WHERE user_id = ?", (user_id,)
                )
                conn.commit()
                return False
        return True


def grant_vip(user_id: int, days: int = 30):
    expires = (datetime.utcnow() + timedelta(days=days)).isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET is_vip = 1, vip_expires_at = ? WHERE user_id = ?",
            (expires, user_id),
        )


def revoke_vip(user_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE users SET is_vip = 0 WHERE user_id = ?", (user_id,))


def create_payment_request(user_id: int, transaction_id: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO payment_requests (user_id, transaction_id, created_at) VALUES (?, ?, ?)",
            (user_id, transaction_id, datetime.utcnow().isoformat()),
        )
        return cur.lastrowid


def get_pending_requests():
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM payment_requests WHERE status = 'pending' ORDER BY created_at"
        ).fetchall()


def update_request_status(request_id: int, status: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE payment_requests SET status = ? WHERE id = ?", (status, request_id)
        )


def all_user_ids():
    with get_conn() as conn:
        rows = conn.execute("SELECT user_id FROM users").fetchall()
        return [r["user_id"] for r in rows]


# ---------- Daily alerts subscription ----------

def set_daily_alerts(user_id: int, enabled: bool):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET daily_alerts = ? WHERE user_id = ?",
            (1 if enabled else 0, user_id),
        )


def get_daily_alert_subscribers():
    """যেসব ইউজার daily auto-post চালু করেছে তাদের (user_id, is_vip) লিস্ট।"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT user_id FROM users WHERE daily_alerts = 1"
        ).fetchall()
        return [r["user_id"] for r in rows]


# ---------- Prediction history / accuracy tracking ----------

def log_prediction(match_external_id, competition, home_team, away_team,
                    match_date, category, market, probability_pct, fair_odds):
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO predictions_log
               (match_external_id, competition, home_team, away_team, match_date,
                category, market, probability_pct, fair_odds, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (match_external_id, competition, home_team, away_team, match_date,
             category, market, probability_pct, fair_odds, datetime.utcnow().isoformat()),
        )
        return cur.lastrowid


def get_unverified_predictions():
    """যেসব prediction-এর ফলাফল এখনো চেক করা হয়নি।"""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM predictions_log WHERE result_checked = 0"
        ).fetchall()


def mark_prediction_result(pred_id: int, home_goals: int, away_goals: int, was_correct: bool):
    with get_conn() as conn:
        conn.execute(
            """UPDATE predictions_log
               SET actual_home_goals = ?, actual_away_goals = ?,
                   result_checked = 1, was_correct = ?
               WHERE id = ?""",
            (home_goals, away_goals, 1 if was_correct else 0, pred_id),
        )


def get_accuracy_stats():
    """মোট verified prediction, সঠিক সংখ্যা, এবং market অনুযায়ী breakdown।"""
    with get_conn() as conn:
        overall = conn.execute(
            """SELECT COUNT(*) as total, SUM(was_correct) as correct
               FROM predictions_log WHERE result_checked = 1"""
        ).fetchone()

        by_category = conn.execute(
            """SELECT category, COUNT(*) as total, SUM(was_correct) as correct
               FROM predictions_log WHERE result_checked = 1
               GROUP BY category"""
        ).fetchall()

        return {
            "total": overall["total"] or 0,
            "correct": overall["correct"] or 0,
            "by_category": [
                {
                    "category": row["category"],
                    "total": row["total"],
                    "correct": row["correct"] or 0,
                }
                for row in by_category
            ],
        }
