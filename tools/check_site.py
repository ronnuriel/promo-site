#!/usr/bin/env python3
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "index.html"
EVENTS_DIR = ROOT / "events"
INDEX_JSON = EVENTS_DIR / "index.json"

REQUIRED_FIELDS = ["title", "date", "ticket_url", "image"]

def die(msg: str, code: int = 1):
    print(f"❌ {msg}")
    sys.exit(code)

def ok(msg: str):
    print(f"✅ {msg}")

def extract_inline_scripts(html: str) -> str:
    # grabs <script> ... </script> (without src=)
    scripts = []
    for m in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.DOTALL | re.IGNORECASE):
        scripts.append(m.group(1))
    return "\n\n".join(scripts)

def check_js_syntax():
    if not INDEX_HTML.exists():
        die("index.html לא קיים")

    html = INDEX_HTML.read_text(encoding="utf-8")
    js = extract_inline_scripts(html)
    if not js.strip():
        die("לא מצאתי inline <script> ב-index.html")

    tmp = ROOT / ".tmp_inline_bundle.js"
    tmp.write_text(js, encoding="utf-8")

    # Node is available on GH runners. We only check syntax, do not execute.
    try:
        res = subprocess.run(
            ["node", "--check", str(tmp)],
            text=True,
            capture_output=True,
            check=False,
        )
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass

    if res.returncode != 0:
        print(res.stdout)
        print(res.stderr)
        die("שגיאת תחביר ב-JS בתוך index.html (האתר ישבר בדפדפן)")

    ok("JS syntax OK (node --check)")

def check_events_index_and_meta():
    if not INDEX_JSON.exists():
        die("events/index.json לא קיים. תריץ build (tools/promogen.py build) או תוודא שה-Action מייצר אותו.")

    try:
        folders = json.loads(INDEX_JSON.read_text(encoding="utf-8"))
    except Exception as e:
        die(f"events/index.json לא JSON תקין: {e}")

    if not isinstance(folders, list):
        die("events/index.json חייב להיות מערך (list) של שמות תיקיות")

    ok(f"index.json contains {len(folders)} folders")

    bad = 0
    for folder in folders:
        fpath = EVENTS_DIR / folder
        meta_path = fpath / "meta.json"
        if not fpath.exists():
            print(f"❌ folder missing: {folder}")
            bad += 1
            continue
        if not meta_path.exists():
            print(f"❌ meta.json missing: {folder}/meta.json")
            bad += 1
            continue

        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"❌ meta.json invalid JSON: {folder}/meta.json -> {e}")
            bad += 1
            continue

        for k in REQUIRED_FIELDS:
            if not str(meta.get(k, "")).strip():
                print(f"❌ missing field '{k}' in {folder}/meta.json")
                bad += 1

        # basic date format check YYYY-MM-DD
        date = str(meta.get("date", ""))
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            print(f"❌ bad date format in {folder}/meta.json (expected YYYY-MM-DD): '{date}'")
            bad += 1

        # image file exists
        img = str(meta.get("image", "cover.jpg") or "cover.jpg")
        img_path = fpath / img
        if not img_path.exists():
            print(f"❌ image missing for {folder}: expected {img}")
            bad += 1

    if bad:
        die(f"Events validation failed: {bad} problems found")
    ok("Events validation OK")

def main():
    print("=== promo-site quick checks ===")
    check_js_syntax()
    check_events_index_and_meta()
    ok("ALL CHECKS PASSED")

if __name__ == "__main__":
    main()
