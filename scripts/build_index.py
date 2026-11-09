"""Build docs/index/* (topic / ticker / timeline / series indexes), per-category landing pages and
docs/index/posts.json from kb/classification.json and kb/paths.json. Run after to_markdown.py.
"""
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from common import CATEGORIES, DOCS, INDEX_DIR, POSTS_DIR, ROOT, all_post_ids, load_post, prefix_of, safe_name

KB = ROOT / "kb"
HKT = timezone(timedelta(hours=8))
VALUE_LABEL = {"core": "⭐ 核心", "reference": "參考", "time-sensitive": "時效"}
CATEGORY_INTRO = {
    "交易學習筆記": "作者的核心教學：交易系統、技術分析、資金管理、交易心理與大師心法。",
    "專題教學": "一次性專題系列：CUP Beginners、風險管理／技術指標教學、統計學筆記、讀書筆記、年度回顧等。",
    "頻道影片": "作者 YouTube 頻道的完整影片（大市研判、主題投資、選股邏輯）。",
    "市場觀察筆記": "每日更新與每週總結：作者如何解讀當下市況。",
    "市場資訊筆記": "精選外部資訊：Podcast、新聞摘要、公司深度分析、財經日曆。",
    "市場數據": "定期數據報告：COT 報告、Market Monitor、美元相關係數、Weekly Expected Move、經濟日曆。",
    "公告": "頻道公告與會員須知。",
    "其他": "未能歸類的貼文。",
}


def rel_link(from_dir: Path, target: str):
    return Path(*[".."] * len(from_dir.relative_to(DOCS).parts), Path(target).relative_to("docs")).as_posix()


def load():
    cls = json.loads((KB / "classification.json").read_text())
    paths = json.loads((KB / "paths.json").read_text())
    rows = []
    for pid in all_post_ids():
        a = load_post(pid)["data"]["attributes"]
        c = cls.get(pid, {})
        dt = datetime.fromisoformat(a["published_at"]).astimezone(HKT)
        url = a.get("url") or ""
        rows.append({
            "id": pid, "title": a["title"], "date": dt.strftime("%Y-%m-%d"), "category": c.get("category"),
            "subseries": prefix_of(a["title"]), "report_type": c.get("report_type", ""), "post_type": a["post_type"],
            "topics": c.get("topics", []), "tickers": c.get("tickers", []), "value": c.get("value", ""),
            "path": paths.get(pid), "source": "https://www.patreon.com" + url if url.startswith("/") else url,
            "has_note": (KB / "notes" / f"{pid}.md").exists(),
        })
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows


def esc(title):
    return title.strip().replace("[", "\\[").replace("]", "\\]").replace("|", "\\|")


def line(r, from_dir):
    tag = " 📝" if r["has_note"] else ""
    star = "⭐ " if r["value"] == "core" else ""
    return f"| {r['date']} | {star}[{esc(r['title'])}]({rel_link(from_dir, r['path'])}){tag} | {r['category']} |"


def table(rows, from_dir):
    return "\n".join(["| 日期 | 標題 | 分類 |", "|---|---|---|"] + [line(r, from_dir) for r in rows])


def write(path: Path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def main():
    rows = [r for r in load() if r["path"]]
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    (INDEX_DIR / "posts.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))

    by_topic = defaultdict(list)
    for r in rows:
        for t in r["topics"]:
            by_topic[t].append(r)
    parts = ["# 按主題索引", "", "⭐ = 核心教學內容　📝 = 附整理者重點摘要", ""]
    for t, rs in sorted(by_topic.items(), key=lambda kv: -len(kv[1])):
        rs = sorted(rs, key=lambda r: (r["value"] != "core", r["date"]), reverse=False)
        rs = sorted(rs, key=lambda r: r["value"] != "core")
        parts += [f"## {t}（{len(rs)} 篇） {{#{t}}}", "", table(rs, INDEX_DIR), ""]
    write(INDEX_DIR / "by-topic.md", "\n".join(parts))

    by_ticker = defaultdict(list)
    for r in rows:
        for t in r["tickers"]:
            by_ticker[t].append(r)
    parts = ["# 按股票代號索引", "", "只列出在貼文正文中出現至少數次的代號（數據報告除外）。", ""]
    for t, rs in sorted(by_ticker.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        links = "、".join(f"[{r['date']} {esc(r['title'][:40])}]({rel_link(INDEX_DIR, r['path'])})" for r in rs)
        parts.append(f"- **{t}**（{len(rs)}）：{links}")
    write(INDEX_DIR / "by-ticker.md", "\n".join(parts) + "\n")

    by_month = defaultdict(list)
    for r in rows:
        by_month[r["date"][:7]].append(r)
    parts = ["# 時間線", ""]
    for year in sorted({m[:4] for m in by_month}, reverse=True):
        parts += [f"## {year}", ""]
        for m in sorted((m for m in by_month if m.startswith(year)), reverse=True):
            rs = by_month[m]
            parts += [f'??? note "{m}（{len(rs)} 篇）"', ""] + ["    " + ln for ln in table(rs, INDEX_DIR).splitlines()] + [""]
    write(INDEX_DIR / "timeline.md", "\n".join(parts))

    parts = ["# 分類總覽", "", "| 分類 | 篇數 | 說明 |", "|---|---|---|"]
    by_cat = defaultdict(list)
    for r in rows:
        by_cat[r["category"]].append(r)
    for cat in CATEGORIES:
        if by_cat.get(cat):
            parts.append(f"| [{cat}](../posts/{safe_name(cat)}/index.md) | {len(by_cat[cat])} | {CATEGORY_INTRO.get(cat, '')} |")
    parts += ["", "## 子系列 / 報告類型", ""]
    for cat in CATEGORIES:
        sub = defaultdict(int)
        for r in by_cat.get(cat, []):
            sub[r["report_type"] or r["subseries"] or "（無前綴）"] += 1
        if sub:
            parts.append(f"- **{cat}**：" + "、".join(f"{k} ({v})" for k, v in sorted(sub.items(), key=lambda kv: -kv[1])))
    write(INDEX_DIR / "by-series.md", "\n".join(parts) + "\n")

    for cat, rs in by_cat.items():
        folder = POSTS_DIR / safe_name(cat)
        parts = [f"# {cat}", "", CATEGORY_INTRO.get(cat, ""), "", f"共 {len(rs)} 篇。", ""]
        groups = defaultdict(list)
        for r in rs:
            groups[r["report_type"]].append(r)
        if cat in ("市場數據", "市場觀察筆記", "市場資訊筆記") and len(groups) > 1:
            for g, grs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
                parts += [f"## {g or '其他'}（{len(grs)}）", ""] + table(grs, folder).splitlines() + [""]
        else:
            for year in sorted({r["date"][:4] for r in rs}, reverse=True):
                yrs = [r for r in rs if r["date"].startswith(year)]
                parts += [f"## {year}（{len(yrs)}）", "", table(yrs, folder), ""]
        write(folder / "index.md", "\n".join(parts))
    write_home(rows, by_cat, by_topic)
    print(f"indexed {len(rows)} posts, {len(by_topic)} topics, {len(by_ticker)} tickers")


def write_home(rows, by_cat, by_topic):
    notes = sum(r["has_note"] for r in rows)
    first, last = min(r["date"] for r in rows), max(r["date"] for r in rows)
    recent_core = [r for r in rows if r["value"] == "core"][:8]
    parts = [
        "# 茄利略・CUP 知識庫", "",
        f"整理自 [茄利略・CUP Patreon](https://www.patreon.com/KelileoCUP) 的 **{len(rows)} 篇貼文**（{first} 至 {last}），"
        f"其中 {notes} 篇附有重點摘要。只供私人學習，不構成投資建議。", "",
        "## 從課程開始", "",
        "| 單元 | 你會學到 |", "|---|---|",
        "| [01 看市場](course/01-看市場.md) | 作者的大市研判框架：宏觀與聯儲局、流動性、市寬與情緒、每週例行檢查 |",
        "| [02 投資原則](course/02-投資原則.md) | 投資與交易的分別、期望值與統計、止損與風險管理、交易心理 |",
        "| [03 強勢股](course/03-強勢股.md) | 如何定義、篩選、買入及賣出強勢股，附圖表例子 |",
        "| [04 個人化投資組合](course/04-個人化投資組合.md) | 倉位與 R、核心／衛星配置、對沖、再平衡，附個人工作紙 |", "",
        "## 按分類瀏覽", "",
        "| 分類 | 篇數 |", "|---|---|",
    ]
    for cat in CATEGORIES:
        if by_cat.get(cat):
            parts.append(f"| [{cat}](posts/{safe_name(cat)}/index.md) | {len(by_cat[cat])} |")
    parts += ["", "## 按主題瀏覽", ""]
    parts.append("、".join(f"[{t}](index/by-topic.md#{t}) ({len(rs)})" for t, rs in sorted(by_topic.items(), key=lambda kv: -len(kv[1]))))
    parts += ["", "## 最新核心內容", ""]
    parts += [f"- {r['date']} [{esc(r['title'])}]({Path(r['path']).relative_to('docs').as_posix()})" for r in recent_core]
    parts += ["", "---", "", "[分類總覽](index/by-series.md) · [按股票代號](index/by-ticker.md) · [時間線](index/timeline.md) · [標籤](tags.md) · [關於](about.md)", ""]
    write(DOCS / "index.md", "\n".join(parts))


if __name__ == "__main__":
    main()
