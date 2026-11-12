"""Transcripts and keyframes for video / audio posts -> raw/transcripts/<id>.json

- Uploaded videos (Mux HLS): use Mux's auto-generated Chinese subtitles; fall back to Whisper.
- Podcasts and audio attachments: faster-whisper "small" on CPU.
- Keyframes: scene detection on the 270p rendition, frames grabbed from the best rendition.
- YouTube embeds: transcripts saved in raw/youtube/<video_id>.json are attached.

Usage: python scripts/media_transcribe.py [--ids ...] [--series ...] [--no-keyframes] [--no-whisper]
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from common import ASSETS_DIR, DOCS, RAW, RAW_MEDIA, UA, all_post_ids, load_post, category_of, session

TR_DIR = RAW / "transcripts"
YT_DIR = RAW / "youtube"
HDRS = {"Referer": "https://www.patreon.com/", "Origin": "https://www.patreon.com", "User-Agent": UA}
FF_HDRS = "".join(f"{k}: {v}\r\n" for k, v in HDRS.items() if k != "User-Agent")
AUDIO_EXT = {".mp3", ".m4a", ".wav", ".aac", ".ogg"}
MAX_KEYFRAMES = 16
MIN_GAP = 25.0
_model = None


def whisper():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel("small", device="cpu", compute_type="int8", cpu_threads=4)
    return _model


def transcribe_source(src):
    net = ["-headers", FF_HDRS, "-user_agent", UA] if str(src).startswith("http") else []
    pcm = subprocess.run(
        ["ffmpeg", "-loglevel", "error", *net, "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
        capture_output=True, check=True).stdout
    audio = np.frombuffer(pcm, dtype=np.float32)
    segs, _ = whisper().transcribe(audio, language="zh", vad_filter=True,
                                   initial_prompt="以下是廣東話財經節目的繁體中文逐字稿。")
    return [{"start": round(s.start, 1), "text": s.text} for s in segs], len(audio) / 16000


def vtt_cues(text):
    cues = []
    for block in re.split(r"\n\s*\n", text):
        m = re.search(r"(\d+):(\d\d):(\d\d)\.(\d+)\s+-->", block)
        if not m:
            continue
        start = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) + int(m[4]) / 1000
        lines = block.splitlines()
        timing = next(i for i, ln in enumerate(lines) if "-->" in ln)
        body = " ".join(lines[timing + 1:]).strip()
        if body:
            cues.append({"start": round(start, 1), "text": body})
    return cues


def mux_variants(s, master_url):
    m3u8 = s.get(master_url, headers=HDRS, timeout=60).text
    subs = re.search(r'TYPE=SUBTITLES[^\n]*URI="([^"]+)"', m3u8)
    variants = []
    lines = m3u8.splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("#EXT-X-STREAM-INF"):
            res = re.search(r"RESOLUTION=(\d+)x(\d+)", ln)
            variants.append((int(res[2]) if res else 0, lines[i + 1].strip()))
    variants.sort()
    return (subs[1] if subs else None), variants


def mux_subtitles(s, subs_url):
    pl = s.get(subs_url, headers=HDRS, timeout=60).text
    cues, seen = [], set()
    for url in [ln for ln in pl.splitlines() if ln and not ln.startswith("#")]:
        for c in vtt_cues(s.get(url, headers=HDRS, timeout=60).content.decode("utf-8", "replace")):
            if (c["start"], c["text"]) not in seen:
                seen.add((c["start"], c["text"]))
                cues.append(c)
    cues.sort(key=lambda c: c["start"])
    return cues


def keyframes(post_id, low_url, high_url, duration):
    out = subprocess.run(
        ["ffmpeg", "-headers", FF_HDRS, "-user_agent", UA, "-i", low_url, "-an",
         "-vf", "select='gt(scene,0.25)',showinfo", "-vsync", "vfr", "-f", "null", "-"],
        capture_output=True, text=True).stderr
    times = [float(t) for t in re.findall(r"pts_time:([\d.]+)", out)]
    picked, last = [], -MIN_GAP
    for t in [3.0] + times:
        if t - last >= MIN_GAP:
            picked.append(t)
            last = t
    if len(picked) > MAX_KEYFRAMES:
        step = len(picked) / MAX_KEYFRAMES
        picked = [picked[int(i * step)] for i in range(MAX_KEYFRAMES)]
    folder = ASSETS_DIR / post_id
    folder.mkdir(parents=True, exist_ok=True)
    result = []
    for t in picked:
        dst = folder / f"kf-{int(t):05d}.webp"
        if not dst.exists():
            png = subprocess.run(
                ["ffmpeg", "-loglevel", "error", "-headers", FF_HDRS, "-user_agent", UA, "-ss", f"{t + 1.5:.1f}",
                 "-i", high_url, "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
                capture_output=True).stdout
            if not png:
                continue
            from io import BytesIO
            with Image.open(BytesIO(png)) as im:
                im.convert("RGB").save(dst, "WEBP", quality=80)
        result.append({"t": t, "path": dst.relative_to(DOCS).as_posix()})
    return result


def youtube_id(url):
    m = re.search(r"(?:youtu\.be/|v=|/shorts/|/live/)([\w-]{11})", url or "")
    return m[1] if m else None


def process(s, post_id, do_kf=True, do_whisper=True, force=False):
    out_path = TR_DIR / f"{post_id}.json"
    existing = json.loads(out_path.read_text()) if out_path.exists() and not force else []
    done = {it["key"] for it in existing
            if it.get("segments") or (it.get("keyframes") and not do_whisper)}
    items = [it for it in existing if it["key"] in done]
    old_kf = {it["key"]: it["keyframes"] for it in existing if it.get("keyframes")}
    d = load_post(post_id)
    a = d["data"]["attributes"]
    pf = a.get("post_file") or {}

    if a.get("post_type") == "video_external_file" and pf.get("url") and "video" not in done:
        item = {"key": "video", "label": "影片", "duration": pf.get("duration")}
        if old_kf.get("video"):
            item["keyframes"] = old_kf["video"]
        try:
            subs, variants = mux_variants(s, pf["url"])
            if subs:
                item["segments"] = mux_subtitles(s, subs)
                item["method"] = "mux-auto-subtitles"
            elif do_whisper:
                item["segments"], _ = transcribe_source(pf["url"])
                item["method"] = "whisper-small"
            if do_kf and variants and "keyframes" not in item:
                high = next((u for h, u in variants if h == 1080), variants[-1][1])
                item["keyframes"] = keyframes(post_id, variants[0][1], high, pf.get("duration"))
        except Exception as e:
            item["error"] = str(e)[:300]
        items.append(item)

    audio_files = []
    folder = RAW_MEDIA / post_id
    if folder.exists():
        audio_files = sorted(f for f in folder.iterdir() if f.suffix.lower() in AUDIO_EXT)
    for f in audio_files:
        key = f.name.split("__", 1)[0]
        if key in done or not do_whisper:
            continue
        item = {"key": key, "label": "音訊" if key == "post_file" else f"音訊附件：{f.name.split('__', 1)[-1]}", "method": "whisper-small"}
        try:
            item["segments"], item["duration"] = transcribe_source(f)
        except Exception as e:
            item["error"] = str(e)[:300]
        items.append(item)

    vid = youtube_id((a.get("embed") or {}).get("url"))
    if vid and f"yt:{vid}" not in done:
        yt = YT_DIR / f"{vid}.json"
        item = {"key": f"yt:{vid}", "label": "YouTube 影片" + (f"：{json.loads(yt.read_text()).get('title', '')}" if yt.exists() else ""),
                "source": f"https://www.youtube.com/watch?v={vid}"}
        if yt.exists():
            item["segments"] = json.loads(yt.read_text()).get("segments", [])
            item["method"] = "youtube-transcript"
        items.append(item)

    if items:
        TR_DIR.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(items, ensure_ascii=False, indent=1))
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", nargs="*")
    ap.add_argument("--series", nargs="*")
    ap.add_argument("--no-keyframes", action="store_true")
    ap.add_argument("--no-whisper", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    ids = [i for i in (args.ids or all_post_ids()) if (RAW / "posts" / f"{i}.json").exists()]
    if args.series:
        ids = [i for i in ids if category_of(load_post(i)["data"]["attributes"]) in args.series]
    s = session()
    for n, pid in enumerate(ids, 1):
        items = process(s, pid, not args.no_keyframes, not args.no_whisper, args.force)
        if items:
            summary = ", ".join(f"{it['key']}:{len(it.get('segments') or [])}seg/{len(it.get('keyframes') or [])}kf" + ("!ERR" if it.get("error") else "") for it in items)
            print(f"[{n}/{len(ids)}] {pid} {summary}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
