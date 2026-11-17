"""Google Indexing API + Search Console sitemap helper.

Uses client_email + private_key of a Google service account.
Secret: GOOGLE_SERVICE_ACCOUNT_JSON (the whole JSON text)  or  a local file service_account.json
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlparse

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

INDEXING_SCOPE = "https://www.googleapis.com/auth/indexing"
WEBMASTERS_SCOPE = "https://www.googleapis.com/auth/webmasters"
PUBLISH_URL = "https://indexing.googleapis.com/v3/urlNotifications:publish"
METADATA_URL = "https://indexing.googleapis.com/v3/urlNotifications/metadata"
LOG_PATH = "data/indexing.json"
STATE_PATH = "data/state.json"


def _int_env(name, default):
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


DAILY_LIMIT = _int_env("INDEXING_DAILY_LIMIT", 190)  # Google default quota: 200 publish requests/day
_sessions = {}


def _load_info():
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if raw:
        return json.loads(raw)
    path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


def _session(scope):
    if scope in _sessions:
        return _sessions[scope]
    info = _load_info()
    if not info:
        return None
    email, key = info.get("client_email"), info.get("private_key")
    if not email or not key:
        raise ValueError("service account JSON must contain client_email and private_key")
    creds = service_account.Credentials.from_service_account_info(
        {
            "type": "service_account",
            "client_email": email,
            "private_key": key.replace("\\n", "\n"),
            "token_uri": info.get("token_uri", "https://oauth2.googleapis.com/token"),
        },
        scopes=[scope],
    )
    _sessions[scope] = AuthorizedSession(creds)
    return _sessions[scope]


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_log():
    try:
        with open(LOG_PATH, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        d = {}
    d.setdefault("day", "")
    d.setdefault("count", 0)
    d.setdefault("items", {})
    return d


def _save_log(d):
    os.makedirs("data", exist_ok=True)
    if len(d["items"]) > 600:
        keep = sorted(d["items"].items(), key=lambda kv: kv[1].get("ts", 0))[-500:]
        d["items"] = dict(keep)
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1, ensure_ascii=False)


def _hint(err):
    e = err.lower()
    if "403" in e or "permission" in e:
        return ("HINT: add the service account email (client_email) as an OWNER of this site in "
                "Search Console -> Settings -> Users and permissions, and enable 'Web Search Indexing API' in Google Cloud.")
    if "has not been used" in e or "disabled" in e or "accessnotconfigured" in e:
        return "HINT: enable the API (Web Search Indexing API / Google Search Console API) in your Google Cloud project."
    if "429" in e or "quota" in e:
        return "HINT: daily quota reached; the bot will try again tomorrow."
    if "401" in e:
        return "HINT: the service account key is invalid or expired; create a new JSON key."
    return ""


def notify(url, kind="URL_UPDATED"):
    """Tell Google that a URL was added/updated (URL_UPDATED) or removed (URL_DELETED)."""
    s = _session(INDEXING_SCOPE)
    if s is None:
        print("  Indexing SKIPPED: GOOGLE_SERVICE_ACCOUNT_JSON secret is missing", flush=True)
        return False
    url = url.strip()
    if url.startswith("http://"):
        url = "https://" + url[7:]
    log = _load_log()
    if log["day"] != _today():
        log["day"], log["count"] = _today(), 0
    if log["count"] >= DAILY_LIMIT:
        print(f"  Indexing SKIPPED: daily limit {DAILY_LIMIT} reached", flush=True)
        return False
    last = ""
    for attempt in range(4):
        try:
            r = s.post(PUBLISH_URL, json={"url": url, "type": kind}, timeout=40)
        except Exception as e:
            last = str(e)
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code == 200:
            log["count"] += 1
            log["items"][url] = {"ts": int(time.time()), "type": kind, "ok": True}
            _save_log(log)
            print(f"  Indexing API OK ({kind}): {url}", flush=True)
            return True
        last = f"{r.status_code} {r.text[:300]}"
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(8 * (attempt + 1))
            continue
        break
    log["items"][url] = {"ts": int(time.time()), "type": kind, "ok": False, "err": last[:200]}
    _save_log(log)
    print(f"  Indexing API FAILED: {last}\n  {_hint(last)}", flush=True)
    return False


def status(url):
    s = _session(INDEXING_SCOPE)
    if s is None:
        print("no credentials")
        return
    r = s.get(METADATA_URL, params={"url": url}, timeout=30)
    print(r.status_code, r.text)


def submit_sitemap(any_url):
    """Official way: ask Google (Search Console API) to re-read the blog sitemap. Once per day."""
    if os.getenv("SUBMIT_SITEMAP", "1") != "1":
        return False
    p = urlparse(any_url)
    site = os.getenv("SEARCH_CONSOLE_SITE", "").strip() or f"{p.scheme}://{p.netloc}/"
    sitemap = f"{p.scheme}://{p.netloc}/sitemap.xml"
    log = _load_log()
    if log.get("sitemap_day") == _today():
        return True
    s = _session(WEBMASTERS_SCOPE)
    if s is None:
        return False
    try:
        r = s.put(
            f"https://www.googleapis.com/webmasters/v3/sites/{quote(site, safe='')}/sitemaps/{quote(sitemap, safe='')}",
            timeout=30,
        )
    except Exception as e:
        print(f"  Sitemap submit error: {e}", flush=True)
        return False
    if r.status_code in (200, 204):
        log["sitemap_day"] = _today()
        _save_log(log)
        print(f"  Sitemap submitted to Search Console: {sitemap}", flush=True)
        return True
    err = f"{r.status_code} {r.text[:200]}"
    print(f"  Sitemap submit FAILED: {err}\n  {_hint(err)}", flush=True)
    return False


def index_post(url, label=None):
    """Called right after a post is published."""
    if _load_info() is None:
        print("  Indexing SKIPPED: GOOGLE_SERVICE_ACCOUNT_JSON secret is missing", flush=True)
        return False
    time.sleep(8)  # let Blogger finish publishing
    ok = notify(url)
    if os.getenv("INDEX_EXTRA_PAGES", "0") == "1":  # optional: home page + category page
        p = urlparse(url)
        notify(f"{p.scheme}://{p.netloc}/")
        if label:
            notify(f"{p.scheme}://{p.netloc}/search/label/{quote(label)}")
    submit_sitemap(url)
    return ok


def retry_pending(limit=5):
    """Re-send recent posts whose notification failed earlier (quota, network, permission fixed later)."""
    if _load_info() is None:
        return
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            posts = json.load(f).get("posts", [])
    except Exception:
        return
    items = _load_log()["items"]
    now, sent = time.time(), 0
    for p in reversed(posts[-30:]):
        url = (p.get("url") or "").strip()
        if not url or now - p.get("ts", 0) > 3 * 86400:
            continue
        if url.startswith("http://"):
            url = "https://" + url[7:]
        if items.get(url, {}).get("ok"):
            continue
        if sent >= limit:
            break
        notify(url)
        sent += 1


def main():
    ap = argparse.ArgumentParser(description="Google Indexing API helper")
    ap.add_argument("url", nargs="?", help="page address to submit")
    ap.add_argument("--delete", action="store_true", help="tell Google the page was removed (URL_DELETED)")
    ap.add_argument("--status", action="store_true", help="show what Google knows about the URL")
    ap.add_argument("--sitemap", action="store_true", help="submit the blog sitemap to Search Console")
    ap.add_argument("--retry", action="store_true", help="re-send recent posts that failed before")
    a = ap.parse_args()
    if a.retry:
        retry_pending(20)
    elif a.url and a.status:
        status(a.url)
    elif a.url and a.sitemap:
        submit_sitemap(a.url)
    elif a.url:
        ok = notify(a.url, "URL_DELETED" if a.delete else "URL_UPDATED")
        sys.exit(0 if ok else 1)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
