import requests

import config
import llm
from utils import strip_html

H = {"User-Agent": config.UA}
import re  # noqa: E402


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
    try:
        r = requests.get(url, headers=H, timeout=20, stream=True)
        good = r.status_code == 200 and r.headers.get("content-type", "").startswith("image/")
        size = int(r.headers.get("content-length") or 0)
        r.close()
        return good and (size == 0 or size > 15000)
    except Exception:
        return False


def score(cands, text):
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
            "You judge whether stock photos honestly illustrate a news section.",
            user, temperature=0.1, num_predict=250,
        )
        return {int(s["id"]): float(s["score"]) for s in res.get("scores", [])}
    except llm.OutOfTime:
        raise
    except Exception:
        return {}


def pick(queries, text, used):
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
        sc = score(cands, text)
        for i in sorted(range(len(cands)), key=lambda i: -sc.get(i, 0)):
            if sc.get(i, 0) < 6:
                break
            if ok_image(cands[i]["url"]):
                return cands[i]
    return None


def _queries(article, sec):
    kw = article["meta"]["focus_keyword"]
    qs = [sec["image_query"], kw, f"{article['label'] or ''} {kw.split()[0] if kw else ''}".strip()]
    return list(dict.fromkeys(q for q in qs if q))


def attach_images(article, item, sources):
    used = set()
    for s in article["sections"]:
        if not s["has_image"]:
            continue
        c = pick(_queries(article, s), s["heading"] + ". " + s["body"], used)
        if not c and s["id"] == "hook" and config.USE_SOURCE_IMAGE and sources[0].get("og"):
            c = {"url": sources[0]["og"], "title": "", "desc": "", "credit_line": "Image courtesy of the original publisher"}
        if c:
            used.add(c["url"])
            s["image"] = c
            print(f"  image for {s['id']}: {c['url'][:80]}", flush=True)


def replace_image(article, sid):
    used = {s["image"]["url"] for s in article["sections"] if s["image"]}
    sec = next(s for s in article["sections"] if s["id"] == sid)
    c = pick(_queries(article, sec), sec["heading"] + ". " + sec["body"], used)
    if c:
        sec["image"] = c
