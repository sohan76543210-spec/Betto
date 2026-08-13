"""
football_api.py
football-data.org API থেকে আজকের/আসন্ন ম্যাচ এবং টিমের সাম্প্রতিক ফলাফল (form) টেনে আনে।
ফ্রি API কী নিতে: https://www.football-data.org/client/register
"""

import os
import requests
from datetime import date, timedelta

BASE_URL = "https://api.football-data.org/v4"

# সাপোর্টেড কম্পিটিশন কোড (ফ্রি টিয়ারে যা পাওয়া যায়)
COMPETITIONS = {
    "PL": "প্রিমিয়ার লিগ",
    "PD": "লা লিগা",
    "SA": "সিরি আ",
    "BL1": "বুন্দেসলিগা",
    "FL1": "লিগ ১",
    "CL": "চ্যাম্পিয়ন্স লিগ",
}


def _headers():
    api_key = os.getenv("FOOTBALL_DATA_API_KEY")
    return {"X-Auth-Token": api_key}


def get_upcoming_matches(days_ahead: int = 3):
    """আগামী কয়েকদিনের ম্যাচ ফিরিয়ে দেয়।"""
    today = date.today()
    date_to = today + timedelta(days=days_ahead)
    url = f"{BASE_URL}/matches"
    params = {
        "dateFrom": today.isoformat(),
        "dateTo": date_to.isoformat(),
        "status": "SCHEDULED",
    }
    resp = requests.get(url, headers=_headers(), params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    matches = [
        m for m in data.get("matches", [])
        if m.get("competition", {}).get("code") in COMPETITIONS
    ]
    return matches


def get_team_recent_form(team_id: int, limit: int = 5):
    """একটি টিমের শেষ কয়েকটি ম্যাচের ফলাফল ফিরিয়ে দেয় (গোল সহ)।"""
    url = f"{BASE_URL}/teams/{team_id}/matches"
    params = {"status": "FINISHED", "limit": limit}
    resp = requests.get(url, headers=_headers(), params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data.get("matches", [])[:limit]


def get_head_to_head(match_id: int, limit: int = 5):
    """দুই টিমের আগের মুখোমুখি লড়াইয়ের ফলাফল।"""
    url = f"{BASE_URL}/matches/{match_id}/head2head"
    params = {"limit": limit}
    resp = requests.get(url, headers=_headers(), params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_match_result(match_id: int):
    """
    একটা নির্দিষ্ট ম্যাচের বর্তমান স্ট্যাটাস ও ফলাফল ফেরত দেয়।
    accuracy tracking-এর জন্য ব্যবহৃত হয় - ম্যাচ শেষ হয়েছে কিনা ও স্কোর কী তা চেক করতে।
    """
    url = f"{BASE_URL}/matches/{match_id}"
    resp = requests.get(url, headers=_headers(), timeout=15)
    resp.raise_for_status()
    data = resp.json()
    match = data.get("match", data)  # কিছু response সরাসরি match object দেয়
    status = match.get("status")
    full_time = match.get("score", {}).get("fullTime", {})
    return {
        "status": status,  # SCHEDULED, IN_PLAY, FINISHED ইত্যাদি
        "home_goals": full_time.get("home"),
        "away_goals": full_time.get("away"),
    }
