import calendar
import json
import re
import time

import feedparser
import requests
import trafilatura

import config
from utils import jaccard, strip_html, tokens


def load_feeds(path="feeds.json"):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return [(cat, url) for cat, urls in data.items() for url in urls]


def _rss_image(e):
    for k in ("media_content", "media_thumbnail"):
        for m in e.get(k, []) or []:
            if m.get("url"):
                return m["url"]
    for l in e.get("links", []) or []:
        if str(l.get("type", "")).startswith("image"):
            return l.get("href", "")
    return ""


def fetch_feed(cat, url):
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; TheNewsDetailBot/1.0)"}, timeout=20)
        d = feedparser.parse(r.content)
    except Exception:
        return []
    items = []
    for e in d.entries[:25]:
        link = e.get("link")
        title = strip_html(e.get("title", ""))
        if not link or not title:
            continue
        ts = None
        for k in ("published_parsed", "updated_parsed"):
            if e.get(k):
                ts = calendar.timegm(e[k])
                break
        items.append(
            {
                "category": cat,
                "feed": url,
                "title": title,
                "link": link,
                "summary": strip_html(e.get("summary", "")),
                "ts": ts or time.time(),
                "image": _rss_image(e),
            }
        )
    return items


def rank(items, st):
    now = time.time()
    fresh = [i for i in items if -3600 <= now - i["ts"] <= config.MAX_AGE_HOURS * 3600 and not st.is_seen(i)]
    uniq = {}
    for i in fresh:
        uniq.setdefault(i["link"], i)
    fresh = list(uniq.values())
    toks = [tokens(i["title"]) for i in fresh]
    for a, i in enumerate(fresh):
        rel = [
            fresh[b]
            for b in range(len(fresh))
            if b != a and fresh[b]["feed"] != i["feed"] and jaccard(toks[a], toks[b]) >= 0.3
        ]
        i["related"] = rel[:3]
        feeds_cnt = len({r["feed"] for r in rel})  # how many outlets cover it = trending
        age = (now - i["ts"]) / 3600
        cat_gap = min((now - st.last_post_ts(i["category"])) / 3600, 24) / 6  # rotate categories
        i["score"] = feeds_cnt * 3 + max(0, config.MAX_AGE_HOURS - age) / 12 + cat_gap
    return sorted(fresh, key=lambda x: -x["score"])


def fetch_article(url):
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; TheNewsDetailBot/1.0)"}, timeout=25)
        if r.status_code != 200:
            return "", ""
        page = r.text
        text = trafilatura.extract(page, include_comments=False, include_tables=False) or ""
        m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', page, re.I)
        return text.strip(), (m.group(1) if m else "")
    except Exception:
        return "", ""


def gather_sources(item):
    out = []
    text, og = fetch_article(item["link"])
    if len(text) < 500:
        text = (text + " " + item["summary"]).strip()
    if text:
        out.append({"title": item["title"], "text": text[:6000], "og": og or item.get("image", "")})
    for rel in item.get("related", [])[:2]:
        t, _ = fetch_article(rel["link"])
        if len(t) < 500:
            t = (t + " " + rel["summary"]).strip()
        if len(t) >= 200:
            out.append({"title": rel["title"], "text": t[:4000], "og": ""})
    total = sum(len(s["text"]) for s in out)
    return out if total >= 900 else None
