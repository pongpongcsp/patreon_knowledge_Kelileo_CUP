"""Convert raw/ posts into docs/posts/**.md with WebP images and extracted PDF text.

Curated per-post material is merged in when present:
  kb/classification.json        topics / tickers / value per post id
  kb/notes/<id>.md              summary and chart descriptions written by hand
  raw/transcripts/<id>.json     Whisper / YouTube transcripts (see media_transcribe.py)
  kb/drive_links.json           optional external links (e.g. Google Drive) per media key

Usage: python scripts/to_markdown.py [--ids ID ...] [--series NAME ...]
"""
import argparse
import json
import re
import shutil
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pymupdf
import yaml
from PIL import Image

from common import (ASSETS_DIR, DOCS, POSTS_DIR, RAW, RAW_MEDIA, ROOT, all_post_ids, load_post, safe_name,
                    category_of, prefix_of)

Image.MAX_IMAGE_PIXELS = None
HKT = timezone(timedelta(hours=8))
KB = ROOT / "kb"
MAX_W = 2000
PDF_PAGE_IMAGE_LIMIT = 12


def load_json(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def published_hkt(attrs):
    return datetime.fromisoformat(attrs["published_at"]).astimezone(HKT)


def post_path(post_id, attrs):
    title = attrs["title"] or post_id
    slug = safe_name(re.sub(r"^\s*【[^】]*】\s*", "", title), 40)
    dt = published_hkt(attrs)
    return POSTS_DIR / safe_name(category_of(attrs)) / str(dt.year) / f"{dt:%Y-%m-%d}-{slug}-{post_id}.md"


def media_files(post_id):
    """media key -> raw file path"""
    folder = RAW_MEDIA / post_id
    out = {}
    if folder.exists():
        for f in folder.iterdir():
            if not f.name.endswith(".part"):
                out[f.name.split("__", 1)[0]] = f
    return out


def to_webp(src: Path, dst: Path):
    if dst.exists():
        return dst
    dst.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        im.seek(0)
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGBA" if "transparency" in im.info else "RGB")
        if im.width > MAX_W:
            im = im.resize((MAX_W, round(im.height * MAX_W / im.width)), Image.LANCZOS)
        im.save(dst, "WEBP", quality=85, method=4)
    return dst


class Renderer:
    def __init__(self, post_id, md_path, files):
        self.post_id = post_id
        self.md_dir = md_path.parent
        self.files = files
        self.used_media = set()
        self.img_n = 0

    def rel(self, p: Path):
        return Path(*[".."] * len(self.md_dir.relative_to(POSTS_DIR.parent).parts), p.relative_to(POSTS_DIR.parent)).as_posix()

    def image(self, media_id, alt=""):
        src = self.files.get(str(media_id))
        if not src:
            return ""
        self.used_media.add(str(media_id))
        self.img_n += 1
        try:
            dst = to_webp(src, ASSETS_DIR / self.post_id / f"{self.img_n:02d}.webp")
        except Exception:
            return ""
        return f"![{alt or f'圖 {self.img_n}'}]({self.rel(dst)})"

    def inline(self, nodes):
        out = []
        for n in nodes or []:
            t = n.get("type")
            if t == "hardBreak":
                out.append("<br>\n")
            elif t == "text":
                s = n.get("text", "")
                lead, core, trail = re.match(r"^(\s*)(.*?)(\s*)$", s, re.S).groups()
                if core:
                    for m in n.get("marks") or []:
                        mt = m["type"]
                        if mt == "bold":
                            core = f"**{core}**"
                        elif mt == "italic":
                            core = f"*{core}*"
                        elif mt == "underline":
                            core = f"<u>{core}</u>"
                        elif mt == "link":
                            core = f"[{core}]({(m.get('attrs') or {}).get('href', '')})"
                out.append(lead + core + trail)
            elif t == "image":
                out.append(self.image((n.get("attrs") or {}).get("media_id"), (n.get("attrs") or {}).get("alt", "")))
            else:
                out.append(self.inline(n.get("content")))
        return "".join(out)

    def block(self, n, indent=""):
        t = n.get("type")
        c = n.get("content") or []
        if t == "doc":
            return "\n\n".join(filter(None, (self.block(x, indent) for x in c)))
        if t == "paragraph":
            return indent + self.inline(c).replace("\n", "\n" + indent)
        if t == "heading":
            lvl = min(max(int((n.get("attrs") or {}).get("level", 3)), 2), 6)
            return f"{indent}{'#' * lvl} {self.inline(c).strip()}"
        if t in ("bulletList", "orderedList"):
            items = []
            for i, li in enumerate(c, 1):
                marker = f"{i}. " if t == "orderedList" else "- "
                body = "\n\n".join(filter(None, (self.block(x, indent + "    ") for x in li.get("content") or [])))
                body = body[len(indent) + 4:] if body.startswith(indent + "    ") else body.lstrip()
                items.append(indent + marker + body)
            return "\n".join(items)
        if t == "blockquote":
            inner = "\n\n".join(filter(None, (self.block(x) for x in c)))
            return "\n".join(indent + "> " + line for line in inner.splitlines())
        if t == "horizontalRule":
            return indent + "---"
        if t == "image":
            return indent + self.image((n.get("attrs") or {}).get("media_id"), (n.get("attrs") or {}).get("alt", ""))
        if t == "video":
            return indent + "> 🎬 影片（見下方「影片 / 音訊」部分）"
        return self.inline(c)


def pdf_section(r: Renderer, path: Path, title):
    lines = [f"### 📄 {title}", ""]
    try:
        doc = pymupdf.open(path)
    except Exception as e:
        return "\n".join(lines + [f"(無法讀取 PDF: {e})"])
    rendered = 0
    for i, page in enumerate(doc, 1):
        text = page.get_text().strip()
        visual = len(text) < 400 or len(page.get_images()) > 0
        lines.append(f"??? note \"第 {i} 頁\"" if len(doc) > 3 else f"**第 {i} 頁**")
        pad = "    " if len(doc) > 3 else ""
        if visual and rendered < PDF_PAGE_IMAGE_LIMIT:
            dst = ASSETS_DIR / r.post_id / f"{safe_name(path.stem, 30)}-p{i:02d}.webp"
            if not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                pix = page.get_pixmap(dpi=110)
                Image.frombytes("RGB", (pix.width, pix.height), pix.samples).save(dst, "WEBP", quality=80)
            rendered += 1
            lines += ["", pad + f"![{title} 第 {i} 頁]({r.rel(dst)})"]
        if text:
            lines += [""] + [pad + ln for ln in text.splitlines()]
        lines.append("")
    return "\n".join(lines)


def text_file_section(path: Path, title):
    try:
        text = path.read_text(errors="replace")[:20000]
    except Exception:
        return ""
    return f"### 📄 {title}\n\n```text\n{text}\n```"


def transcript_section(r: Renderer, tr, drive):
    out = ["## 🎬 影片 / 音訊"]
    for item in tr:
        out.append(f"### {item.get('label', '媒體')}")
        info = []
        if item.get("duration"):
            info.append(f"長度：{int(item['duration'] // 60)} 分 {int(item['duration'] % 60)} 秒")
        if item.get("source"):
            info.append(f"來源：<{item['source']}>")
        if drive.get(item["key"]):
            info.append(f"[Google Drive 原檔]({drive[item['key']]})")
        if info:
            out.append(" · ".join(info))
        kfs = item.get("keyframes") or []
        if kfs:
            grid = [f"![{int(k['t']) // 60:d}:{int(k['t']) % 60:02d}]({r.rel(DOCS / k['path'])}){{ loading=lazy width=360 }}" for k in kfs]
            out.append('??? example "影片關鍵畫面（自動擷取）"\n\n' + "\n".join("    " + g for g in grid))
        segs = item.get("segments") or []
        if segs:
            out.append('??? quote "逐字稿（自動轉錄，可能有錯字）"')
            for sg in segs:
                ts = int(sg["start"])
                out.append(f"    **[{ts // 3600:d}:{ts % 3600 // 60:02d}:{ts % 60:02d}]** {sg['text'].strip()}  ")
        elif item.get("error"):
            out.append(f"(未能轉錄：{item['error']})")
    return "\n\n".join(out)


def render_post(post_id):
    d = load_post(post_id)
    attrs = d["data"]["attributes"]
    path = post_path(post_id, attrs)
    files = media_files(post_id)
    r = Renderer(post_id, path, files)
    cls = load_json(KB / "classification.json", {}).get(post_id, {})
    drive = load_json(KB / "drive_links.json", {}).get(post_id, {})
    note = KB / "notes" / f"{post_id}.md"
    tr_path = RAW / "transcripts" / f"{post_id}.json"

    dt = published_hkt(attrs)
    rel = d["data"].get("relationships", {})
    tags = [t["id"].split(";", 1)[-1].strip() for t in (rel.get("user_defined_tags", {}).get("data") or [])]
    meta = {
        "id": post_id,
        "title": attrs["title"],
        "series": category_of(attrs),
        "subseries": prefix_of(attrs["title"]),
        "date": dt.strftime("%Y-%m-%d %H:%M"),
        "post_type": attrs.get("post_type"),
        "source": "https://www.patreon.com" + attrs["url"] if attrs.get("url", "").startswith("/") else attrs.get("url"),
        "patreon_tags": tags,
        "topics": cls.get("topics", []),
        "tickers": cls.get("tickers", []),
        "value": cls.get("value", ""),
        "tags": [category_of(attrs)] + cls.get("topics", []),
    }

    parts = [f"# {attrs['title']}", f"*{dt:%Y-%m-%d %H:%M} HKT* · [Patreon 原文]({meta['source']})"]
    if note.exists():
        parts.append('!!! abstract "重點摘要（整理者筆記）"\n' + "\n".join("    " + ln for ln in note.read_text().strip().splitlines()))

    body = ""
    if attrs.get("content_json_string"):
        body = r.block(json.loads(attrs["content_json_string"]))
    elif attrs.get("content"):
        body = re.sub(r"<[^>]+>", "", re.sub(r"</p>|<br\s*/?>", "\n\n", attrs["content"]))
    parts.append(body.strip())

    embed = attrs.get("embed") or {}
    if embed.get("url"):
        parts.append(f"**嵌入連結：** [{embed.get('subject') or embed['url']}]({embed['url']})")

    attach_ids = {x["id"] for x in (rel.get("attachments_media", {}).get("data") or [])}
    gallery = [x["id"] for x in (rel.get("images", {}).get("data") or [])]
    extra_imgs = [r.image(mid) for mid in gallery if mid not in r.used_media]
    extra_imgs = [x for x in extra_imgs if x]
    if extra_imgs:
        parts.append("## 🖼️ 圖片\n\n" + "\n\n".join(extra_imgs))

    names = {inc["id"]: inc["attributes"].get("file_name") or inc["id"] for inc in d.get("included", []) if inc["type"] == "media"}
    attach_md = []
    for mid in sorted(attach_ids):
        f = files.get(mid)
        name = names.get(mid, mid)
        link = drive.get(mid)
        head = f"- {name}" + (f" — [Google Drive 原檔]({link})" if link else "")
        if not f:
            attach_md.append(head + "（未下載）")
            continue
        ext = f.suffix.lower()
        if ext == ".pdf":
            attach_md.append(pdf_section(r, f, name))
        elif ext in (".txt", ".csv", ".md"):
            attach_md.append(text_file_section(f, name))
        elif ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            attach_md.append(r.image(mid, name))
        elif ext in (".xlsx", ".xls", ".docx", ".csv") and f.stat().st_size < 5_000_000:
            dst = ASSETS_DIR / post_id / safe_name(name)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f, dst)
            attach_md.append(f"- 📥 [{name}]({r.rel(dst)})")
        else:
            attach_md.append(head)
    if attach_md:
        parts.append("## 📎 附件\n\n" + "\n\n".join(attach_md))

    if tr_path.exists():
        parts.append(transcript_section(r, json.loads(tr_path.read_text()), drive))
    elif attrs.get("post_type") in ("video_external_file", "podcast", "audio_file"):
        link = drive.get("post_file")
        parts.append("## 🎬 影片 / 音訊\n\n(尚未轉錄)" + (f" — [Google Drive 原檔]({link})" if link else ""))

    path.parent.mkdir(parents=True, exist_ok=True)
    fm = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False)
    path.write_text(f"---\n{fm}---\n\n" + "\n\n".join(p for p in parts if p) + "\n")
    return post_id, str(path.relative_to(ROOT))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", nargs="*")
    ap.add_argument("--series", nargs="*")
    ap.add_argument("--clean", action="store_true", help="remove docs/posts before rendering everything")
    args = ap.parse_args()
    ids = args.ids or all_post_ids()
    ids = [i for i in ids if (RAW / "posts" / f"{i}.json").exists()]
    if args.series:
        ids = [i for i in ids if category_of(load_post(i)["data"]["attributes"]) in args.series]
    if args.clean and not args.ids and not args.series and POSTS_DIR.exists():
        shutil.rmtree(POSTS_DIR)
    paths = {}
    with ProcessPoolExecutor(4) as ex:
        for pid, p in ex.map(render_post, ids):
            paths[pid] = p
    old = load_json(KB / "paths.json", {})
    old.update(paths)
    KB.mkdir(exist_ok=True)
    (KB / "paths.json").write_text(json.dumps(old, ensure_ascii=False, indent=1))
    print(f"rendered {len(paths)} posts")


if __name__ == "__main__":
    main()
