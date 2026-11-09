"""Store TubeAlfred youtube_video_transcript responses as raw/youtube/<video_id>.json.

YouTube blocks direct transcript downloads from cloud machines, so transcripts are fetched with the
TubeAlfred MCP tool and the saved responses are imported here.

Usage:
  python scripts/import_youtube.py --list            # print video ids still missing, by category
  python scripts/import_youtube.py RESPONSE.json ...  # import saved tool responses
"""
import json
import sys

from common import RAW, all_post_ids, category_of, load_post
from media_transcribe import YT_DIR, youtube_id


def wanted():
    out = {}
    for pid in all_post_ids():
        a = load_post(pid)["data"]["attributes"]
        vid = youtube_id((a.get("embed") or {}).get("url"))
        if vid:
            out.setdefault(vid, (category_of(a), (a.get("embed") or {}).get("subject") or a["title"]))
    return out


def main():
    YT_DIR.mkdir(parents=True, exist_ok=True)
    if sys.argv[1:] == ["--list"]:
        for vid, (cat, title) in sorted(wanted().items(), key=lambda kv: kv[1][0]):
            if not (YT_DIR / f"{vid}.json").exists():
                print(cat, vid, title[:60], sep="\t")
        return
    titles = wanted()
    for path in sys.argv[1:]:
        resp = json.loads(open(path).read())
        d = resp.get("data") or {}
        vid = d.get("video_id")
        if not vid:
            print(f"skip {path}: {resp.get('outcome')}")
            continue
        segs = [{"start": round(int(float(x["start_ms"])) / 1000, 1), "text": x["text"]} for x in d.get("transcript") or []]
        rec = {"video_id": vid, "title": titles.get(vid, ("", ""))[1], "language": d.get("language"), "segments": segs}
        (YT_DIR / f"{vid}.json").write_text(json.dumps(rec, ensure_ascii=False))
        print(f"{vid}: {len(segs)} segments")


if __name__ == "__main__":
    main()
