"""On-page SEO booster: powerful title, meta description, keyword coverage, headings and FAQ.
The writing style of the article body is NOT changed."""
import re
from datetime import datetime, timezone

import config
import llm
import validators
from utils import STOP
from validators import numbers, source_norm

M = config.MODELS
YEAR = str(datetime.now(timezone.utc).year)

POWER = set(
    "explained revealed reveals why how what inside key facts latest breaking new exclusive major critical essential complete "
    "guide truth changes warning surprising biggest first top must need know means impact risks real behind".split()
)
CLICKBAIT = ("you won't believe", "you will not believe", "shocking", "jaw-dropping", "gone wrong", "doctors hate", "this one trick", "unbelievable")
CTA = ("learn", "find out", "discover", "see ", "read", "get ", "know", "understand", "here's", "here is", "explained")

SYS = (
    "You are a senior SEO editor for a news website. You write high-click-through, honest headlines and descriptions. "
    "Use ONLY the facts you are given. Never invent numbers, names or claims."
)


def _clean(s):
    return re.sub(r"\s+", " ", str(s or "")).strip().strip('"').strip("'")


def _trim(t, n):
    if len(t) <= n:
        return t
    return t[:n].rsplit(" ", 1)[0].rstrip(",:;-– ")


def _sentences(text):
    parts = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text))
    return [p.strip() for p in parts if 40 <= len(p.strip()) <= 300]


def score_title(t, kw, srcnorm=""):
    t = t.strip()
    low, k, n = t.lower(), kw.lower().strip(), len(t)
    s = 0.0
    if 50 <= n <= 60:
        s += 4
    elif 45 <= n <= 65:
        s += 2
    else:
        s -= 4
    if k and k in low:
        s += 3
        if low.index(k) <= 22:
            s += 2  # keyword at the front
    else:
        kws = [w for w in re.findall(r"[a-z0-9]+", k) if w not in STOP]
        have = sum(1 for w in kws if w in low)
        s += 3 * have / max(1, len(kws)) - 2
    toks = re.findall(r"[a-z']+", low)
    s += min(sum(1 for w in toks if w in POWER), 2) * 1.5
    if re.search(r"\d", t):
        s += 1.5
    if any(c in low for c in CLICKBAIT):
        s -= 5
    s -= 1.5 * len([w for w in t.split() if len(w) > 3 and w.isupper()])
    if t[-1:] in ".!":
        s -= 1
    if "?" in t:
        s += 0.5
    if ":" in t or "|" in t or " – " in t or " - " in t:
        s += 0.5
    if srcnorm and any(x != YEAR and x not in srcnorm for x in numbers(t)):
        s -= 10  # a number the sources do not contain
    return s


def score_desc(d, kw):
    n, low, k = len(d), d.lower(), kw.lower()
    s = 4 if 140 <= n <= 158 else (2 if 120 <= n <= 160 else -4)
    if k in low:
        s += 3
        if low.index(k) <= 60:
            s += 1.5
    if any(c in low for c in CTA):
        s += 1.5
    if re.search(r"\d", d):
        s += 1
    return s


def _fit_desc(d):
    d = _clean(d)
    if len(d) > 158:
        d = _trim(d, 156)
    if d and d[-1] not in ".!?":
        d += "."
    return d


def _best(cands, scorer, kw):
    pool = [c for c in cands if kw.lower() in c.lower()] or cands
    return max(pool, key=lambda c: (scorer(c), -abs(len(c) - 55)))


def _kw_hit(text, kw):
    ws = [w for w in re.findall(r"[a-z0-9]+", kw.lower()) if w not in STOP]
    if not ws:
        return False
    low = text.lower()
    have = sum(1 for w in ws if w in low)
    return have >= max(1, min(len(ws), round(len(ws) * 0.67 + 0.01)))


def _titles_and_descriptions(article, sources):
    m = article["meta"]
    kw = m["focus_keyword"]
    srcnorm = source_norm(sources)
    facts = "\n".join(f"- {f['fact']}" for f in article.get("facts", [])[:10]) or "\n".join("- " + x for x in _sentences(sources[0]["text"])[:8])
    user = (
        f"STORY SUMMARY: {m['short_summary']}\nFACTS:\n{facts}\n\nPRIMARY KEYWORD: {kw}\n"
        f"SECONDARY KEYWORDS: {', '.join(m['secondary'])}\nCURRENT YEAR: {YEAR}\nCURRENT HEADLINE: {m['title']}\n\n"
        "Write 6 powerful SEO headlines. Rules: 50 to 60 characters (never above 65); the primary keyword appears in the first 3-4 words; "
        "use one power word naturally (Explained, Revealed, Key Facts, What It Means, Why, Inside, Latest, Everything to Know, Major, Critical); "
        f"use a number or the year {YEAR} only when it truly fits; create curiosity or urgency but never promise anything the facts do not support; "
        "no clickbait phrases, no ALL CAPS words, no quotes, no emojis; Title Case. Vary the formula across the 6 headlines "
        "(keyword + colon + power phrase / how or why question / number + keyword / news + what it means / keyword + year + explained).\n"
        "Also write 3 meta descriptions: 150 to 158 characters, start with the primary keyword or a strong hook containing it, include one secondary keyword, "
        "promise a clear benefit or answer, end with a short call to action such as 'Here is what to know.'; only facts.\n"
        'Return JSON: {"titles": ["..."], "descriptions": ["..."]}'
    )
    raw = llm.chat_json(M["seo"], SYS, user, temperature=0.7, num_predict=550)
    titles = [_clean(x) for x in (raw.get("titles") or []) if isinstance(x, str)]
    titles = [t for t in dict.fromkeys(titles + [m["title"]]) if 20 <= len(t) <= 75]
    descs = [_fit_desc(x) for x in (raw.get("descriptions") or []) if isinstance(x, str)]
    descs = [d for d in dict.fromkeys(descs + [_fit_desc(m["meta_description"])]) if len(d) >= 60]
    seo = article.setdefault("seo", {})
    if titles:
        seo["titles"] = titles
        m["title"] = _best(titles, lambda t: score_title(t, kw, srcnorm), kw)
    if descs:
        seo["descs"] = descs
        m["meta_description"] = _best(descs, lambda d: score_desc(d, kw), kw)


def _headings(article):
    m = article["meta"]
    secs = article["sections"]
    kw = m["focus_keyword"]
    listing = "\n".join(f"{s['id']}: {s['heading']} | content starts: {s['body'][:140]}" for s in secs)
    user = (
        f"PRIMARY KEYWORD: {kw}\nSECONDARY KEYWORDS: {', '.join(m['secondary'])}\nLONG-TAIL KEYWORDS: {', '.join(m['longtail'])}\n\n"
        f"SECTIONS:\n{listing}\n\n"
        "Rewrite ONLY the section headings so each one is search-friendly and clickable. Rules: 4 to 10 words, natural news style, "
        "accurate to the section content, the primary keyword in exactly two headings (the hook and core1 sections), a different secondary or "
        "long-tail keyword in each of the other headings, no generic labels like Hook, Background or Conclusion, no quotes.\n"
        'Return JSON: {"headings": {"<section id>": "<new heading>"}} using exactly these ids: ' + ", ".join(s["id"] for s in secs)
    )
    raw = llm.chat_json(M["seo"], SYS, user, temperature=0.5, num_predict=400)
    hs = raw.get("headings") if isinstance(raw.get("headings"), dict) else {}
    kws = [kw] + m["secondary"] + m["longtail"]
    for s in article["sections"]:
        h = _clean(hs.get(s["id"]))
        if not 12 <= len(h) <= 75 or h.lower() in validators.GENERIC:
            continue
        if any(_kw_hit(h, k) for k in kws):
            s["heading"] = h


def _faq(article, sources):
    m = article["meta"]
    kw = m["focus_keyword"]
    srcnorm = source_norm(sources)
    facts = "\n".join(f"{f['id']}: {f['fact']}" for f in article.get("facts", [])) or "\n".join(
        "- " + x for x in _sentences(" ".join(s["text"] for s in sources))[:10]
    )
    user = (
        f"FACTS (the only allowed source):\n{facts}\n\nPRIMARY KEYWORD: {kw}\nREADER QUESTIONS TO ANSWER: {'; '.join(m['questions'])}\n\n"
        "Write 4 FAQ items. Each question is a natural search query ending with '?'. Each answer is 35 to 60 words, conversational and clear, "
        "uses only the facts above (never invent numbers, names or claims), has no links, and two answers include the primary keyword.\n"
        'Return JSON: {"faq": [{"q": "...", "a": "..."}]}'
    )
    raw = llm.chat_json(M["writer"], SYS, user, temperature=0.4, num_predict=700)
    out = []
    for it in raw.get("faq") or []:
        if not isinstance(it, dict):
            continue
        q, a = _clean(it.get("q")), _clean(it.get("a"))
        if not q.endswith("?") or not 15 <= len(q) <= 120 or not 25 <= len(a.split()) <= 90:
            continue
        if validators.URL_RE.search(a) or any(n not in srcnorm for n in numbers(a)):
            continue
        if q.lower() not in [x["q"].lower() for x in out]:
            out.append({"q": q, "a": a})
    article["faq"] = out[:4] if len(out) >= 2 else []


def optimize(article, sources):
    """Run once after the article is written. Every step is optional: a failure never blocks publishing."""
    for name, fn in (("titles/descriptions", _titles_and_descriptions), ("headings", _headings), ("FAQ", _faq)):
        try:
            fn(article, sources) if name != "headings" else fn(article)
            print(f"  SEO {name}: done", flush=True)
        except Exception as e:
            print(f"  SEO {name} skipped ({type(e).__name__}: {e})", flush=True)


def finalize(article, sources):
    """Run after the reviewers (they may touch the title): keep the strongest title/description and the full keyword list."""
    m = article["meta"]
    kw = m["focus_keyword"]
    srcnorm = source_norm(sources)
    seo = article.setdefault("seo", {})
    titles = list(dict.fromkeys(seo.get("titles", []) + [m["title"]]))
    titles = [t for t in titles if 20 <= len(t) <= 75]
    if titles:
        best = _best(titles, lambda t: score_title(t, kw, srcnorm), kw)
        m["title"] = best if len(best) <= 65 else _trim(best, 62)
    descs = list(dict.fromkeys(seo.get("descs", []) + [_fit_desc(m["meta_description"])]))
    descs = [d for d in descs if len(d) >= 60]
    if descs:
        m["meta_description"] = _best(descs, lambda d: score_desc(d, kw), kw)
    if kw.lower() not in m["meta_description"].lower():
        m["meta_description"] = _fit_desc(f"{kw.capitalize()}: {m['short_summary']}")
    m["keywords"] = list(dict.fromkeys([kw] + m.get("secondary", []) + m.get("longtail", []) + m.get("keywords", [])))[:10]
    print(f"  SEO title: {m['title']} ({len(m['title'])} chars) | score {score_title(m['title'], kw, srcnorm):.1f}", flush=True)
