import calendar
import json
import re
import time
from urllib.parse import urljoin

import feedparser
import requests
import trafilatura

import config
from utils import jaccard, strip_html, tokens

BAD_IMG = re.compile(
    r"(logo|icon|sprite|avatar|pixel|tracking|blank|placeholder|badge|/ads?/|\.svg|\.gif|1x1|spacer|emoji|button)", re.I
)


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
    for enc in e.get("enclosures", []) or []:
        if str(enc.get("type", "")).startswith("image"):
            return enc.get("href", "")
    m = re.search(r'<img[^>]+src=["\']([^"\']+)', e.get("summary", "") or "", re.I)
    return m.group(1) if m else ""


def fetch_feed(cat, url):
    try:
        r = requests.get(url, headers={"User-Agent": config.BROWSER_UA}, timeout=20)
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


def _attr(tag, name):
    m = re.search(r"(?<![\w-])" + name + r'=["\']([^"\']*)["\']', tag, re.I)
    return m.group(1) if m else ""


def _srcset_best(srcset):
    best, best_w = "", -1
    for part in srcset.split(","):
        bits = part.strip().split()
        if not bits:
            continue
        w = 0
        if len(bits) > 1 and bits[1].endswith("w"):
            try:
                w = int(bits[1][:-1])
            except ValueError:
                w = 0
        if w >= best_w:
            best, best_w = bits[0], w
    return best


def extract_images(page, base):
    found = []

    def add(url, alt=""):
        if not url or url.startswith("data:"):
            return
        u = urljoin(base, url.strip())
        if not u.startswith("http") or BAD_IMG.search(u):
            return
        if all(f["url"] != u for f in found):
            found.append({"url": u, "alt": strip_html(alt)[:200]})

    for pat in (
        r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)',
    ):
        for m in re.finditer(pat, page, re.I):
            add(m.group(1))
    m = re.search(r"<article.*?</article>", page, re.I | re.S)
    area = m.group(0) if m else page
    for tag in re.findall(r"<img\b[^>]*>", area, re.I)[:40]:
        w = _attr(tag, "width")
        if w.isdigit() and int(w) < 200:
            continue
        srcset = _attr(tag, "srcset") or _attr(tag, "data-srcset")
        url = _srcset_best(srcset) if srcset else ""
        url = url or _attr(tag, "data-src") or _attr(tag, "src")
        add(url, _attr(tag, "alt"))
    return found[:8]


def fetch_article(url):
    try:
        r = requests.get(url, headers={"User-Agent": config.BROWSER_UA}, timeout=25)
        if r.status_code != 200:
            return "", []
        page = r.text
        text = trafilatura.extract(page, include_comments=False, include_tables=False) or ""
        return text.strip(), extract_images(page, url)
    except Exception:
        return "", []


def gather_sources(item):
    out = []
    text, imgs = fetch_article(item["link"])
    if item.get("image"):
        imgs = imgs[:1] + [{"url": item["image"], "alt": item["title"]}] + imgs[1:]
    if len(text) < 500:
        text = (text + " " + item["summary"]).strip()
    if text:
        out.append({"title": item["title"], "text": text[:6000], "images": imgs, "link": item["link"]})
    for rel in item.get("related", [])[:2]:
        t, rimgs = fetch_article(rel["link"])
        if rel.get("image"):
            rimgs = rimgs[:1] + [{"url": rel["image"], "alt": rel["title"]}] + rimgs[1:]
        if len(t) < 500:
            t = (t + " " + rel["summary"]).strip()
        if len(t) >= 200:
            out.append({"title": rel["title"], "text": t[:4000], "images": rimgs, "link": rel["link"]})
    total = sum(len(s["text"]) for s in out)
    return out if total >= 500 else None
