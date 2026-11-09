import json
import os
import re
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw"
RAW_POSTS = RAW / "posts"
RAW_MEDIA = RAW / "media"
DOCS = ROOT / "docs"
POSTS_DIR = DOCS / "posts"
ASSETS_DIR = DOCS / "assets"
INDEX_DIR = DOCS / "index"
CAMPAIGN_ID = "5017503"
API = "https://www.patreon.com/api"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

SERIES_ALIASES = {
    "交交易學習筆記": "交易學習筆記",
    "市場資訊觀察": "市場資訊筆記",
    "財經日曆": "市場資訊筆記",
    "市場筆記補完": "市場觀察筆記",
    "通告": "公告",
    "內部公告": "公告",
    "重要⚠️": "公告",
}
TEACHING_ONE_OFFS = {
    "CUP Beginners", "風險管理教學", "技術指標教學", "交易統計學筆記", "歷史統計筆記", "讀書筆記",
    "Deep Dive", "Average Joe Can Win", "AI 學習筆記", "SI 時代求存筆記", "2025年回顧",
}
MAIN_SERIES = ["交易學習筆記", "市場觀察筆記", "市場資訊筆記", "市場數據"]
CATEGORIES = ["交易學習筆記", "專題教學", "頻道影片", "市場觀察筆記", "市場資訊筆記", "市場數據", "公告", "其他"]


def session():
    sid = os.environ.get("PATREON_SESSION_ID")
    if not sid:
        raise SystemExit("PATREON_SESSION_ID is not set")
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    s.cookies.set("session_id", sid, domain=".patreon.com")
    return s


def get_json(s, url, params=None, tries=5):
    for attempt in range(tries):
        r = s.get(url, params=params, timeout=60)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 ** attempt * 3)
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()


def prefix_of(title):
    """Original 【...】 prefix, tolerating a missing opening or closing bracket."""
    t = title or ""
    m = re.match(r"\s*【([^】]+)】", t) or re.match(r"\s*([^【】\s]{2,12})】", t) or re.match(r"\s*【(\S+筆記)\s", t)
    return m.group(1).strip() if m else ""


def series_of(title, post_type=None):
    """Category used for folders and navigation (one of CATEGORIES)."""
    prefix = prefix_of(title)
    if not prefix:
        return "頻道影片" if post_type in ("video_external_file", "video_embed") else "其他"
    if prefix in TEACHING_ONE_OFFS:
        return "專題教學"
    name = SERIES_ALIASES.get(prefix, prefix)
    return name if name in CATEGORIES else "其他"


def load_post(post_id):
    return json.loads((RAW_POSTS / f"{post_id}.json").read_text())


def all_post_ids():
    listing = json.loads((RAW / "posts_list.json").read_text())
    return [p["id"] for p in listing if (RAW_POSTS / f"{p['id']}.json").exists()]


def safe_name(name, limit=80):
    name = re.sub(r'[\\/:*?"<>|\s]+', "-", name).strip("-.")
    return name[:limit] or "file"


def category_of(attrs):
    return series_of(attrs.get("title"), attrs.get("post_type"))
