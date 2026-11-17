import re
from urllib.parse import urlparse

import requests

import config
from utils import strip_html

H = {"User-Agent": config.UA}
HB = {"User-Agent": config.BROWSER_UA}
_OK = {}


def ok_image(url):
    if url in _OK:
        return _OK[url]
    good = False
    try:
        r = requests.get(url, headers=HB, timeout=20, stream=True)
        size = int(r.headers.get("content-length") or 0)
        good = (
            r.status_code == 200
            and r.headers.get("content-type", "").lower().startswith("image/")
            and (size == 0 or size > 12000)
        )
        r.close()
    except Exception:
        good = False
    _OK[url] = good
    return good


def source_pool(sources):
    """Images that belong to the story itself: main article first, then other outlets covering the same story."""
    pool, seen = [], set()
    for s in sources:
        name = urlparse(s.get("link", "")).netloc.replace("www.", "") or "the original publisher"
        for im in s.get("images", []):
            if im["url"] in seen:
                continue
            seen.add(im["url"])
            pool.append({
                "url": im["url"], "title": im.get("alt", ""), "desc": "",
                "credit_line": f"Image: courtesy of {name}",
            })
    return pool


def commons(q, n=8):
    """Last-resort fallback only: free-license photo from Wikimedia Commons."""
    try:
        r = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query", "format": "json", "generator": "search",
                "gsrsearch": f"{q} filetype:bitmap", "gsrnamespace": 6, "gsrlimit": n,
                "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1000,
            },
            headers=H, timeout=20,
        )
        pages = ((r.json().get("query") or {}).get("pages") or {}).values()
    except Exception:
        return []
    out = []
    for p in pages:
        ii = (p.get("imageinfo") or [{}])[0]
        if ii.get("mime") not in ("image/jpeg", "image/png", "image/webp") or (ii.get("width") or 0) < 640:
            continue
        md = ii.get("extmetadata") or {}

        def g(k):
            return strip_html((md.get(k) or {}).get("value", ""))

        url = ii.get("thumburl") or ii.get("url")
        if url:
            out.append({
                "url": url,
                "title": re.sub(r"^File:|\.\w+$", "", p.get("title", "")),
                "desc": g("ImageDescription")[:300],
                "credit_line": f"Photo: {(g('Artist') or 'Wikimedia Commons contributor')[:60]} / Wikimedia Commons ({g('LicenseShortName') or 'CC'})",
                "illustrative": True,
            })
    return out


def attach_images(article, item, sources):
    """ONE image per post: the story's own original picture."""
    sec = next((s for s in article["sections"] if s["has_image"]), None)
    if not sec:
        return
    if config.USE_SOURCE_IMAGE:
        for c in source_pool(sources):
            if ok_image(c["url"]):
                sec["image"] = c
                print(f"  image (from the story itself): {c['url'][:100]}", flush=True)
                return
    kw = article["meta"]["focus_keyword"]
    for q in (kw, article["label"] or "news"):
        for c in commons(q):
            if ok_image(c["url"]):
                sec["image"] = c
                print(f"  image (free-license fallback): {c['url'][:100]}", flush=True)
                return
    print("  no image could be found", flush=True)
