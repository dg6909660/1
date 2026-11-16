import re
from urllib.parse import urlparse

import requests

import config
import llm
from utils import strip_html

H = {"User-Agent": config.UA}
HB = {"User-Agent": config.BROWSER_UA}
_OK = {}


def commons(q, n=8):
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
        if ii.get("mime") not in ("image/jpeg", "image/png", "image/webp"):
            continue
        if (ii.get("width") or 0) < 640:
            continue
        md = ii.get("extmetadata") or {}

        def g(k):
            return strip_html((md.get(k) or {}).get("value", ""))

        url = ii.get("thumburl") or ii.get("url")
        if not url:
            continue
        out.append({
            "url": url,
            "title": re.sub(r"^File:|\.\w+$", "", p.get("title", "")),
            "desc": g("ImageDescription")[:300],
            "credit_line": f"Photo: {(g('Artist') or 'Wikimedia Commons contributor')[:60]} / Wikimedia Commons ({g('LicenseShortName') or 'CC'})",
        })
    return out


def openverse(q, n=8):
    try:
        r = requests.get(
            "https://api.openverse.org/v1/images/",
            params={"q": q, "page_size": n, "license_type": "commercial", "mature": "false", "extension": "jpg"},
            headers=H, timeout=20,
        )
        results = r.json().get("results", [])
    except Exception:
        return []
    out = []
    for x in results:
        url = x.get("thumbnail") or x.get("url")
        if not url:
            continue
        lic = f"CC {(x.get('license') or '').upper()} {x.get('license_version') or ''}".strip()
        tags = ", ".join(t.get("name", "") for t in (x.get("tags") or [])[:8] if t.get("name"))
        out.append({
            "url": url,
            "title": x.get("title") or "",
            "desc": tags,
            "credit_line": f"Photo: {(x.get('creator') or 'Unknown author')[:60]} / {(x.get('source') or 'Openverse').title()} ({lic})",
        })
    return out


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


def score(cands, text):
    """Return {index: score} or None if the AI could not score (e.g. out of time)."""
    lines = "\n".join(f"{i}. title: {c['title']} | details: {c['desc']}" for i, c in enumerate(cands))
    user = (
        f"SECTION TEXT:\n{text[:700]}\n\nPHOTO CANDIDATES:\n{lines}\n\n"
        "Rate each photo 0-10 for how well it visually illustrates this section's subject "
        "(10 = clearly relevant; 0 = unrelated, misleading, offensive, or about private individuals). "
        'Return JSON: {"scores":[{"id":0,"score":7}]}'
    )
    try:
        res = llm.chat_json(
            config.MODELS["seo"],
            "You judge whether photos honestly illustrate a news section.",
            user, temperature=0.1, num_predict=250,
        )
        return {int(s["id"]): float(s["score"]) for s in res.get("scores", [])}
    except Exception:
        return None


def pick(queries, text, used, min_score=6):
    seen = set()
    for q in queries:
        cands = []
        for c in commons(q) + openverse(q):
            if c["url"] and c["url"] not in used and c["url"] not in seen:
                seen.add(c["url"])
                cands.append(c)
        cands = cands[:12]
        if not cands:
            continue
        sc = score(cands, text) if min_score > 0 else None
        order = range(len(cands)) if sc is None else sorted(range(len(cands)), key=lambda i: -sc.get(i, 0))
        for i in order:
            if sc is not None and sc.get(i, 0) < min_score:
                break
            if ok_image(cands[i]["url"]):
                return cands[i]
    return None


def source_pool(sources):
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


def take_source(pool, text, used, first=False):
    good = []
    for c in pool:
        if c["url"] in used:
            continue
        if ok_image(c["url"]):
            good.append(c)
        if len(good) >= 5:
            break
    if not good:
        return None
    if first or len(good) == 1:
        return good[0]
    sc = score(good, text)
    if sc is None:
        return good[0]
    best = max(range(len(good)), key=lambda i: sc.get(i, 0))
    return good[best] if sc.get(best, 0) >= 4 else None


def _queries(article, sec):
    kw = article["meta"]["focus_keyword"]
    qs = [sec["image_query"], kw, f"{article['label'] or ''} {kw.split()[0] if kw else ''}".strip()]
    return list(dict.fromkeys(q for q in qs if q))


def attach_images(article, item, sources):
    used = set()
    pool = source_pool(sources) if config.USE_SOURCE_IMAGE else []
    for s in article["sections"]:
        if not s["has_image"]:
            continue
        text = s["heading"] + ". " + s["body"]
        c = None
        if s["id"] in ("hook", "main") and pool:
            c = take_source(pool, text, used, first=True)  # the story's own picture makes the best hero image
        if not c:
            c = pick(_queries(article, s), text, used)
        if not c and pool:
            c = take_source(pool, text, used)
        if c:
            used.add(c["url"])
            s["image"] = c
            print(f"  image for {s['id']}: {c['url'][:90]}", flush=True)
    # last resort: make sure at least one image exists
    if not any(s["image"] for s in article["sections"]):
        first = next((s for s in article["sections"] if s["has_image"]), None)
        if first:
            kw = article["meta"]["focus_keyword"]
            c = pick([kw, article["label"] or "news"], "", used, min_score=0) or take_source(pool, "", used, first=True)
            if c:
                first["image"] = c
                print(f"  fallback image: {c['url'][:90]}", flush=True)


def replace_image(article, sid):
    used = {s["image"]["url"] for s in article["sections"] if s["image"]}
    sec = next(s for s in article["sections"] if s["id"] == sid)
    c = pick(_queries(article, sec), sec["heading"] + ". " + sec["body"], used)
    if c:
        sec["image"] = c
