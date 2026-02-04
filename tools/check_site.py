#!/usr/bin/env python3
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "index.html"
EVENTS_DIR = ROOT / "events"
INDEX_JSON = EVENTS_DIR / "index.json"

REQUIRED_FIELDS = ["title", "date", "ticket_url", "image"]

# -----------------------------
# Link-check tuning
# -----------------------------
LINK_TIMEOUT_SEC = float(os.environ.get("LINK_TIMEOUT_SEC", "6"))
MAX_LINKS = int(os.environ.get("MAX_LINKS", "60"))  # prevent very long builds
STRICT_LINKS = os.environ.get("STRICT_LINKS", "0").strip() == "1"

# Only check links for events >= today - GRACE_DAYS
GRACE_DAYS = int(os.environ.get("GRACE_DAYS", "2"))

# Treat these as "temporary" network-ish issues by default (warning, not fail)
TEMP_HTTP_CODES = {429, 500, 502, 503, 504}


def die(msg: str, code: int = 1):
    print(f"❌ {msg}")
    sys.exit(code)


def ok(msg: str):
    print(f"✅ {msg}")


def warn(msg: str):
    print(f"⚠️ {msg}")


# -----------------------------
# JS syntax check (inline scripts)
# -----------------------------
def extract_inline_scripts(html: str) -> str:
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


# -----------------------------
# Events index + meta validation
# -----------------------------
def check_events_index_and_meta() -> list[dict]:
    if not INDEX_JSON.exists():
        die("events/index.json לא קיים. תריץ build (tools/promogen.py build) או תוודא שה-Action מייצר אותו.")

    try:
        folders = json.loads(INDEX_JSON.read_text(encoding="utf-8"))
    except Exception as e:
        die(f"events/index.json לא JSON תקין: {e}")

    if not isinstance(folders, list):
        die("events/index.json חייב להיות מערך (list) של שמות תיקיות")

    ok(f"index.json contains {len(folders)} folders")

    all_items = []
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

        date = str(meta.get("date", ""))
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            print(f"❌ bad date format in {folder}/meta.json (expected YYYY-MM-DD): '{date}'")
            bad += 1

        img = str(meta.get("image", "cover.jpg") or "cover.jpg")
        img_path = fpath / img
        if not img_path.exists():
            print(f"❌ image missing for {folder}: expected {img}")
            bad += 1

        all_items.append({"folder": folder, "meta": meta})

    if bad:
        die(f"Events validation failed: {bad} problems found")

    ok("Events validation OK")
    return all_items


# -----------------------------
# Link checking
# -----------------------------
def _is_valid_http_url(u: str) -> bool:
    try:
        p = urlparse(u)
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


def _http_check(url: str) -> tuple[bool, str]:
    """
    Returns (ok, message).
    HEAD first, fallback to GET with Range if HEAD not allowed.
    """
    headers = {
        "User-Agent": "promo-site-link-check/1.0",
        "Accept": "*/*",
    }

    # HEAD attempt
    try:
        req = Request(url, method="HEAD", headers=headers)
        with urlopen(req, timeout=LINK_TIMEOUT_SEC) as resp:
            code = getattr(resp, "status", 200)
            if 200 <= code < 400:
                return True, f"{code}"
            return False, f"{code}"
    except HTTPError as e:
        # Some servers return 405 for HEAD -> try GET fallback
        if e.code == 405:
            pass
        else:
            return False, f"{e.code}"
    except URLError as e:
        return False, f"URLError: {e.reason}"
    except Exception as e:
        return False, f"Error: {e}"

    # GET fallback (lightweight)
    try:
        headers2 = dict(headers)
        headers2["Range"] = "bytes=0-0"
        req = Request(url, method="GET", headers=headers2)
        with urlopen(req, timeout=LINK_TIMEOUT_SEC) as resp:
            code = getattr(resp, "status", 200)
            if 200 <= code < 400:
                return True, f"{code}"
            return False, f"{code}"
    except HTTPError as e:
        return False, f"{e.code}"
    except URLError as e:
        return False, f"URLError: {e.reason}"
    except Exception as e:
        return False, f"Error: {e}"


def check_event_links(items: list[dict]):
    """
    Check links only for upcoming events:
    event_date >= (today - GRACE_DAYS)
    """
    today = datetime.now().date()
    cutoff = today - timedelta(days=GRACE_DAYS)

    urls = []
    skipped_past = 0

    for it in items:
        meta = it["meta"]
        folder = it["folder"]

        date_str = str(meta.get("date", "") or "").strip()
        try:
            event_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except Exception:
            # date format already validated earlier; if it still fails, treat as hard fail
            print(f"❌ [{folder}] bad date for link filter: '{date_str}'")
            continue

        if event_date < cutoff:
            skipped_past += 1
            continue

        for field in ("ticket_url", "promoter_url"):
            u = str(meta.get(field, "") or "").strip()
            if u:
                urls.append((folder, field, u))

    if skipped_past:
        ok(f"Skipped link checks for {skipped_past} past events (cutoff={cutoff})")

    if not urls:
        warn("No URLs found for upcoming events. Skipping link checks.")
        return

    if len(urls) > MAX_LINKS:
        warn(f"Too many links ({len(urls)}). Checking first {MAX_LINKS}. (set MAX_LINKS env to change)")
        urls = urls[:MAX_LINKS]

    ok(f"Checking {len(urls)} links for upcoming only (cutoff={cutoff}, timeout={LINK_TIMEOUT_SEC}s, strict={STRICT_LINKS})")

    hard_fail = 0
    soft_fail = 0

    for folder, field, u in urls:
        if not _is_valid_http_url(u):
            hard_fail += 1
            print(f"❌ [{folder}] {field}: invalid url '{u}'")
            continue

        ok_flag, msg = _http_check(u)

        if ok_flag:
            print(f"✅ [{folder}] {field}: {msg} {u}")
            continue

        code = None
        m = re.fullmatch(r"\d{3}", msg.strip())
        if m:
            code = int(msg.strip())

        if code in (404, 410) or msg.startswith("URLError"):
            hard_fail += 1
            print(f"❌ [{folder}] {field}: {msg} {u}")
        elif code in TEMP_HTTP_CODES and not STRICT_LINKS:
            soft_fail += 1
            print(f"⚠️ [{folder}] {field}: {msg} (temporary?) {u}")
        else:
            if STRICT_LINKS:
                hard_fail += 1
                print(f"❌ [{folder}] {field}: {msg} {u}")
            else:
                soft_fail += 1
                print(f"⚠️ [{folder}] {field}: {msg} {u}")

        time.sleep(0.1)

    if hard_fail:
        die(f"Link checks failed: {hard_fail} broken links (hard fails). Soft warnings={soft_fail}")
    if soft_fail:
        warn(f"Link checks warnings: {soft_fail} (non-fatal). Set STRICT_LINKS=1 to fail on these.")
    ok("Links check OK (no hard failures)")


def main():
    print("=== promo-site quick checks ===")
    check_js_syntax()
    items = check_events_index_and_meta()
    check_event_links(items)
    ok("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
