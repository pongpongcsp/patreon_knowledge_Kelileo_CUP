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
    "市場筆記補完": "市場觀察筆記",
}
MAIN_SERIES = ["交易學習筆記", "市場觀察筆記", "市場資訊筆記", "市場數據"]


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


def series_of(title):
    m = re.match(r"\s*【([^】]+)】", title or "")
    if not m:
        return "其他"
    name = SERIES_ALIASES.get(m.group(1).strip(), m.group(1).strip())
    return name


def load_post(post_id):
    return json.loads((RAW_POSTS / f"{post_id}.json").read_text())


def all_post_ids():
    listing = json.loads((RAW / "posts_list.json").read_text())
    return [p["id"] for p in listing]


def safe_name(name, limit=80):
    name = re.sub(r'[\\/:*?"<>|\s]+', "-", name).strip("-.")
    return name[:limit] or "file"
