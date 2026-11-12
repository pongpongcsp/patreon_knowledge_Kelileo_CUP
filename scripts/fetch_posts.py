"""Download every post of the campaign plus its media into raw/ (resumable).

Usage: python scripts/fetch_posts.py [--no-media] [--refresh-list] [--ids ID ...]
"""
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor

from common import API, CAMPAIGN_ID, RAW, RAW_MEDIA, RAW_POSTS, get_json, safe_name, session

POST_INCLUDE = "attachments_media,images,media,audio,user_defined_tags"
SKIP_RELATIONSHIPS = {"audio", "audio_preview", "video_preview"}


def list_posts(s):
    url = f"{API}/posts"
    params = {
        "filter[campaign_id]": CAMPAIGN_ID,
        "filter[contains_exclusive_posts]": "true",
        "filter[is_draft]": "false",
        "sort": "-published_at",
        "page[count]": "100",
        "json-api-version": "1.0",
        "json-api-use-default-includes": "false",
        "fields[post]": "title,published_at,current_user_can_view,post_type,url",
    }
    posts = []
    while url:
        d = get_json(s, url, params)
        posts += d["data"]
        url, params = d.get("links", {}).get("next"), None
    return posts


def fetch_post(s, post_id, force=False):
    path = RAW_POSTS / f"{post_id}.json"
    if path.exists() and not force:
        return json.loads(path.read_text())
    d = get_json(s, f"{API}/posts/{post_id}", {"include": POST_INCLUDE, "json-api-version": "1.0"})
    path.write_text(json.dumps(d, ensure_ascii=False))
    return d


def media_items(d):
    """Yield (key, url, filename, kind) for every downloadable file in a post, except uploaded video."""
    attrs = d["data"]["attributes"]
    rel = d["data"].get("relationships", {})
    attach_ids = {x["id"] for x in (rel.get("attachments_media", {}).get("data") or [])}
    for inc in d.get("included", []):
        if inc["type"] != "media":
            continue
        a = inc["attributes"]
        mime = a.get("mimetype") or ""
        # "audio" duplicates post_file; previews and HLS video are not downloadable files
        if mime.startswith("video/") or "mpegurl" in mime.lower() or a.get("owner_relationship") in SKIP_RELATIONSHIPS:
            continue
        url = a.get("download_url") or (a.get("image_urls") or {}).get("original")
        if not url:
            continue
        kind = "attachment" if inc["id"] in attach_ids else ("image" if mime.startswith("image/") else "file")
        yield inc["id"], url, a.get("file_name") or inc["id"], kind
    pf = attrs.get("post_file") or {}
    if attrs.get("post_type") in ("podcast", "audio_file") and pf.get("url"):
        yield "post_file", pf["url"], pf.get("name") or "audio.mp3", "audio"


def download(s, post_id, key, url, filename):
    folder = RAW_MEDIA / post_id
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{key}__{safe_name(filename)}"
    if "." not in name.rsplit("__", 1)[-1]:
        name += ".bin"
    path = folder / name
    if path.exists() and path.stat().st_size > 0:
        return path, path.stat().st_size, False
    with s.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        tmp = path.with_suffix(path.suffix + ".part")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
        tmp.rename(path)
    return path, path.stat().st_size, True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-media", action="store_true")
    ap.add_argument("--refresh-list", action="store_true")
    ap.add_argument("--force", action="store_true", help="re-download post JSON (signed media URLs expire)")
    ap.add_argument("--ids", nargs="*")
    args = ap.parse_args()

    RAW_POSTS.mkdir(parents=True, exist_ok=True)
    s = session()
    list_path = RAW / "posts_list.json"
    if args.refresh_list or not list_path.exists():
        listing = list_posts(s)
        list_path.write_text(json.dumps(listing, ensure_ascii=False, indent=1))
    listing = json.loads(list_path.read_text())
    ids = args.ids or [p["id"] for p in listing if p["attributes"]["current_user_can_view"]]

    def work(pid):
        d = fetch_post(s, pid, force=args.force)
        rows = []
        if not args.no_media:
            for key, url, fn, kind in media_items(d):
                try:
                    path, size, _ = download(s, pid, key, url, fn)
                    rows.append({"post": pid, "key": key, "kind": kind, "file": str(path.relative_to(RAW)), "bytes": size})
                except Exception as e:  # expired signed URL etc.; rerun with --force
                    rows.append({"post": pid, "key": key, "kind": kind, "error": str(e)[:200]})
        return rows

    manifest = []
    with ThreadPoolExecutor(6) as ex:
        for i, rows in enumerate(ex.map(work, ids), 1):
            manifest += rows
            if i % 50 == 0:
                print(f"{i}/{len(ids)} posts", file=sys.stderr)

    old = RAW / "manifest.json"
    merged = {(r["post"], r["key"]): r for r in (json.loads(old.read_text()) if old.exists() else [])}
    merged.update({(r["post"], r["key"]): r for r in manifest})
    rows = list(merged.values())
    old.write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    by_kind = {}
    for r in rows:
        k = by_kind.setdefault(r["kind"], [0, 0, 0])
        k[0] += 1
        k[1] += r.get("bytes", 0)
        k[2] += "error" in r
    for k, (n, b, err) in sorted(by_kind.items()):
        print(f"{k:11s} files={n:5d} size={b / 1e6:8.1f} MB errors={err}")


if __name__ == "__main__":
    main()
