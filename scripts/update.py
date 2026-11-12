"""Incremental sync: fetch posts published since the last run and rebuild everything that depends on them.

  1. refresh the post list and download new posts + media      (fetch_posts.py)
  2. transcripts / keyframes for the new posts                  (media_transcribe.py)
  3. re-classify all posts                                      (classify.py)
  4. render the new posts to Markdown                           (to_markdown.py)
  5. rebuild indexes and category pages                         (build_index.py)
  6. optionally back up raw/ to the persistent archive          (--archive DIR)

Posts edited after they were fetched are not re-downloaded; pass their ids to
fetch_posts.py --force --ids ... and to_markdown.py --ids ... to refresh them.

Usage: python scripts/update.py [--no-media] [--archive /cursor/stores/self/kelileo-originals]
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from common import RAW, RAW_POSTS

HERE = Path(__file__).resolve().parent


def run(*args):
    print("+", " ".join(args), file=sys.stderr, flush=True)
    subprocess.run([sys.executable, *args], cwd=HERE, check=True)


def archive(dest: Path):
    """Copy raw/ into dest, skipping files that already exist with the same size."""
    copied = 0
    for src in RAW.rglob("*"):
        if src.is_dir() or src.suffix == ".part":
            continue
        dst = dest / src.relative_to(RAW)
        if dst.exists() and dst.stat().st_size == src.stat().st_size:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
    print(f"archived {copied} files to {dest}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-media", action="store_true", help="skip transcription and keyframes")
    ap.add_argument("--archive", help="rsync raw/ into this directory afterwards")
    args = ap.parse_args()

    before = {p.stem for p in RAW_POSTS.glob("*.json")}
    run("fetch_posts.py", "--refresh-list")
    after = {p.stem for p in RAW_POSTS.glob("*.json")}
    new = sorted(after - before)
    print(f"{len(new)} new posts: {' '.join(new)}", file=sys.stderr)

    if new and not args.no_media:
        run("media_transcribe.py", "--ids", *new)
    run("classify.py")
    if new:
        run("to_markdown.py", "--ids", *new)
    run("build_index.py")

    if args.archive:
        archive(Path(args.archive))
    (RAW / "last_update.json").write_text(json.dumps({"new_posts": new}, ensure_ascii=False))


if __name__ == "__main__":
    main()
