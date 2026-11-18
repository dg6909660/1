import html
import json
import re
from datetime import datetime, timezone

import config
import llm
from utils import STOP

M = config.MODELS
CATS = config.CATEGORIES

SYS = (
    "You are a senior English news editor at 'The News Detail'. You write original, accurate, engaging news articles. "
    "STRICT RULES: use ONLY facts found in the provided material; never invent names, numbers, quotes, dates or events; "
    "never copy sentences from the sources - always rewrite in your own words; you may mention an outlet name in plain "
    "text but never write links, URLs or bracketed references; write clear, natural English."
)

SECTION_SPECS = [
    dict(id="hook", image=True, words="90-130",
         brief="Open with a strong question, a shocking fact or a small mystery taken from the facts, then tease what the reader will learn. Two short paragraphs."),
    dict(id="context", image=False, words="130-180",
         brief="Explain the background and history of this topic in simple words so readers understand why this news matters. Two or three short paragraphs."),
    dict(id="core1", image=False, words="150-200",
         brief="Core facts, step by step: present the main details, data and evidence. Include one bullet list of 3-4 facts."),
    dict(id="core2", image=False, words="150-200",
         brief="Deeper analysis: arguments, reactions, causes and effects backed by evidence from the facts. Explain who is affected and why."),
    dict(id="story", image=False, words="120-170",
         brief="Conversational storytelling: talk directly to the reader (use 'you') like explaining to a friend, with a relatable scenario that stays true to the facts."),
    dict(id="keys", image=False, words="60-100",
         brief="Key takeaways: exactly 6 short bullet points, each starting with a **bold key phrase** followed by a short explanation."),
    dict(id="outro", image=False, words="70-110",
         brief="Conclusion: briefly summarize the discussion, then end with one open-ended, thought-provoking question for readers."),
]
SPEC = {s["id"]: s for s in SECTION_SPECS}

FALLBACK_HEADINGS = {
    "hook": "What Just Happened",
    "context": "The Story Behind the News",
    "core1": "The Key Facts Explained",
    "core2": "Looking Closer at the Details",
    "story": "What This Means for You",
    "keys": "Key Takeaways at a Glance",
    "outro": "Final Thoughts",
}

FORMAT_RULES = (
    "Formatting (lightweight markup): blank line between paragraphs; **bold** for key phrases (2-4 per section); "
    "==highlight== for the single most important phrase (max 2); __underline__ for one key term (max 1); "
    "lines starting with '- ' are bullets. No headings inside the body. No links. No emojis."
)


def _clean(s):
    return re.sub(r"\s+", " ", str(s or "")).strip().strip('"')


def _trim(t, n):
    if len(t) <= n:
        return t
    return t[:n].rsplit(" ", 1)[0].rstrip(",:;-– ")


def _list(v, n):
    if isinstance(v, str):
        v = re.split(r"[,;\n]", v)
    if not isinstance(v, list):
        return []
    out = []
    for x in v:
        x = _clean(x)
        if x and x.lower() not in [o.lower() for o in out]:
            out.append(x)
    return out[:n]


def sources_text(sources, limit=7000):
    per = max(1200, limit // max(1, len(sources)))
    return "\n\n".join(f"SOURCE {i + 1} - {s['title']}\n{s['text'][:per]}" for i, s in enumerate(sources))


def facts_block(article):
    return "\n".join(f"{f['id']}: {f['fact']}" for f in article["facts"])


def _sentences(text):
    parts = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text))
    return [p.strip() for p in parts if 40 <= len(p.strip()) <= 300]


def _build_meta(item, raw):
    title = _clean(raw.get("title")) or item["title"]
    summary = _clean(raw.get("short_summary")) or _trim(item["summary"] or item["title"], 300)
    kw = _clean(raw.get("focus_keyword")) or " ".join(title.split()[:3])
    low = lambda L: [x.lower() for x in L]
    sec = [k for k in _list(raw.get("secondary_keywords"), 6) if k.lower() != kw.lower()]
    for f in (f"{kw} news", f"{kw} explained", f"latest {kw} update", f"{kw} impact", f"what is {kw}"):
        if len(sec) >= 5:
            break
        if f.lower() not in low(sec):
            sec.append(f)
    lt = _list(raw.get("long_tail_keywords"), 5)
    for f in (f"what you need to know about {kw}", f"why {kw} matters", f"latest {kw} update explained", f"how {kw} affects you"):
        if len(lt) >= 4:
            break
        if f.lower() not in low(lt):
            lt.append(f)
    qs = [q if q.endswith("?") else q + "?" for q in _list(raw.get("questions"), 5)]
    for f in (f"What is {kw}?", f"Why does {kw} matter?", f"What happens next with {kw}?", f"Who is affected by {kw}?"):
        if len(qs) >= 4:
            break
        if f.lower() not in low(qs):
            qs.append(f)
    return {
        "title": title,
        "focus_keyword": kw,
        "keywords": list(dict.fromkeys([kw] + sec[:5] + lt[:4] + _list(raw.get("keywords"), 8)))[:10],
        "secondary": sec[:5],
        "longtail": lt[:4],
        "questions": qs[:4],
        "meta_description": _clean(raw.get("meta_description")) or _trim(summary, 155),
        "category": _clean(raw.get("category")),
        "short_summary": summary,
    }


def plan_meta(item, sources):
    user = (
        sources_text(sources, 7000)
        + "\n\nReturn JSON with these keys: "
        "title (45-60 characters, accurate, contains the focus keyword, no clickbait), "
        "focus_keyword (2-4 words, the phrase people would really type into Google for this story), "
        "secondary_keywords (array of 5 related search phrases, 2-4 words each), "
        "long_tail_keywords (array of 4 longer search phrases, 4-7 words each), "
        "questions (array of 4 questions readers search for about this story), "
        "keywords (array of 6 short phrases), "
        "meta_description (130-155 characters, contains the focus keyword), "
        f"category (one of {CATS} or \"None\" if none clearly fits), "
        "short_summary (2-3 plain sentences, 45-70 words: what happened and why it matters)."
    )
    try:
        raw = llm.chat_json(M["writer"], SYS, user, temperature=0.3, num_predict=700)
    except llm.OutOfTime:
        raise
    except Exception as e:
        print(f"  meta planning failed ({e}), using fallback", flush=True)
        raw = {}
    return _build_meta(item, raw)


def extract_facts(sources):
    src = " ".join(s["text"] for s in sources).replace(",", "").lower()
    facts = []
    try:
        user = (
            sources_text(sources, 7000)
            + "\n\nExtract 10 to 12 key facts. Each fact is ONE self-contained sentence with concrete details "
            "(who, what, numbers, dates, places) stated accurately as in the sources. "
            'Return JSON: {"facts":[{"id":"F1","fact":"..."}]}'
        )
        raw = llm.chat_json(M["writer"], SYS, user, temperature=0.2, num_predict=800)
        for f in raw.get("facts", []):
            t = _clean(f.get("fact") if isinstance(f, dict) else f)
            if len(t) < 25:
                continue
            nums = [n.replace(",", "").rstrip(".") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", t) if len(re.sub(r"\D", "", n)) >= 2]
            if any(n not in src for n in nums):
                continue
            facts.append({"fact": t})
    except llm.OutOfTime:
        raise
    except Exception as e:
        print(f"  fact extraction failed ({e}), using source sentences", flush=True)
    if len(facts) < 6:
        have = {f["fact"] for f in facts}
        for s in _sentences(" ".join(x["text"] for x in sources)):
            if len(facts) >= 10:
                break
            if s not in have:
                facts.append({"fact": s})
                have.add(s)
    if not facts:
        raise ValueError("no usable facts in sources")
    facts = facts[:12]
    for i, f in enumerate(facts):
        f["id"] = f"F{i + 1}"
    return facts


def _clean_body(b):
    if isinstance(b, list):
        b = "\n\n".join(str(x) for x in b)
    b = str(b or "").replace("\r", "")
    b = re.sub(r"https?://\S+|www\.\S+", "", b)
    b = re.sub(r"\[\d+\]", "", b)
    b = re.sub(r"^#+\s*", "", b, flags=re.M)
    return b.strip()


def sec_keywords(m, sid):
    kw, sec, lt = m["focus_keyword"], m["secondary"], m["longtail"]
    g = lambda L, i: L[i:i + 1]
    plan = {
        "hook": [kw] + g(sec, 0), "context": g(sec, 1) + g(lt, 0), "core1": g(sec, 2) + g(lt, 1),
        "core2": g(sec, 3) + g(lt, 2), "story": g(sec, 4) + g(lt, 3), "keys": [kw] + g(sec, 0), "outro": [kw],
    }
    return plan.get(sid, [kw])


def write_section(article, spec, done_headings):
    m = article["meta"]
    cap = (
        ', "caption": "one sentence, max 22 words, describing what the story\'s main photo shows in relation to the key fact; do not invent details"'
        if spec["image"] else ""
    )
    kws = ", ".join(sec_keywords(m, spec["id"]))
    user = (
        f"STORY SUMMARY: {m['short_summary']}\nFOCUS KEYWORD: {m['focus_keyword']}\n\nVERIFIED FACTS:\n{facts_block(article)}\n\n"
        f"Headings already used (do not repeat ideas): {done_headings}\n\n"
        f"WRITE SECTION '{spec['id']}'.\nTask: {spec['brief']}\nLength: {spec['words']} words.\n{FORMAT_RULES}\n"
        f"Search keywords to weave in naturally where they fit (keep the same natural, conversational style; never force or repeat them awkwardly): {kws}\n"
        "Use only the verified facts.\n"
        'Return JSON: {"heading": "natural news-style subheading, 4-9 words, never a generic label like Hook or Background", '
        '"body": "the section text"' + cap + "}"
    )
    last = None
    for _ in range(2):
        try:
            raw = llm.chat_json(M["writer"], SYS, user, temperature=0.5, num_predict=700)
            body = _clean_body(raw.get("body"))
            if len(body.split()) < 25:
                raise ValueError("section too short")
            return {
                "id": spec["id"],
                "heading": _clean(raw.get("heading")) or FALLBACK_HEADINGS[spec["id"]],
                "body": body,
                "caption": _clean(raw.get("caption")),
                "has_image": spec["image"],
                "image": None,
            }
        except llm.OutOfTime:
            raise
        except Exception as e:
            last = e
    raise RuntimeError(f"section {spec['id']} failed: {last}")


def fallback_section(article, spec):
    facts = [f["fact"] for f in article["facts"]]
    idx = [s["id"] for s in SECTION_SPECS].index(spec["id"])
    kw = article["meta"]["focus_keyword"]
    chunk = facts[idx * 2: idx * 2 + 3] or facts[:3]
    if spec["id"] == "keys":
        body = "\n".join("- " + f for f in facts[:6])
    elif spec["id"] == "outro":
        body = f"To sum up, {article['meta']['short_summary']}\n\nWhat do you think about {kw}?"
    elif spec["id"] == "core1":
        body = "Here are the main points, step by step:\n\n" + "\n".join("- **" + f.split(",")[0][:60] + "**: " + f for f in chunk)
    else:
        body = "\n\n".join(chunk)
    return {
        "id": spec["id"], "heading": FALLBACK_HEADINGS[spec["id"]], "body": body,
        "caption": article["meta"]["title"], "has_image": spec["image"], "image": None,
    }


def build_article(item, sources):
    meta = plan_meta(item, sources)
    facts = extract_facts(sources)
    if item["category"] in CATS:
        label = item["category"]
    elif meta["category"] in CATS:
        label = meta["category"]
    else:
        label = None  # no label -> shows only on Home
    article = {"meta": meta, "facts": facts, "label": label, "sections": []}
    done = []
    for spec in SECTION_SPECS:
        try:
            sec = write_section(article, spec, done)
        except Exception as e:
            print(f"  section {spec['id']}: using fallback ({type(e).__name__})", flush=True)
            sec = fallback_section(article, spec)
        article["sections"].append(sec)
        done.append(sec["heading"])
        print(f"  section {spec['id']}: {len(sec['body'].split())} words", flush=True)
    return article


def quick_article(item, sources):
    user = (
        sources_text(sources, 6000)
        + "\n\nWrite a complete news article of 450-600 words in your own words using only the sources. "
        "Structure: a hook, background, core facts with evidence, a conversational part, a short summary, and end with an open-ended question. "
        + FORMAT_RULES
        + "\nReturn JSON with keys: title (45-60 chars), meta_description (130-155 chars), focus_keyword, secondary_keywords (array of 5), "
        "long_tail_keywords (array of 4), questions (array of 4), keywords (array), "
        f"category (one of {CATS} or \"None\"), short_summary (2-3 sentences), body."
    )
    raw = llm.chat_json(M["writer"], SYS, user, temperature=0.4, num_predict=1200)
    body = _clean_body(raw.get("body"))
    if len(body.split()) < 120:
        raise ValueError("quick article too short")
    meta = _build_meta(item, raw)
    label = item["category"] if item["category"] in CATS else (meta["category"] if meta["category"] in CATS else None)
    sec = {"id": "main", "heading": "The Full Story", "body": body, "caption": meta["title"], "has_image": True, "image": None}
    return {"meta": meta, "facts": [], "label": label, "sections": [sec], "quick": True}


def make_permalink(article):
    """Local AI writes the permalink phrase. Blogger builds the post URL from the title at first publish."""
    m = article["meta"]
    words = []
    try:
        raw = llm.chat_json(
            M["seo"],
            "You create short SEO-friendly URL slugs for news articles.",
            f"Headline: {m['title']}\nFocus keyword: {m['focus_keyword']}\n\n"
            "Create a permalink phrase: 3 to 6 lowercase English words, at most 36 characters in total, starting with the main words "
            "of the focus keyword, without filler words (the, a, of, in, to), without numbers unless essential, without punctuation.\n"
            'Return JSON: {"slug": "..."}',
            temperature=0.2, num_predict=60,
        )
        words = re.findall(r"[a-z0-9]+", str(raw.get("slug", "")).lower())
    except Exception:
        words = []
    if len(words) < 2:
        words = re.findall(r"[a-z0-9]+", (m["focus_keyword"] + " " + m["title"]).lower())
    picked, total = [], 0
    for w in words:
        if w in STOP or w in picked:
            continue
        add = len(w) + (1 if picked else 0)
        if total + add > 36:
            break
        picked.append(w)
        total += add
        if len(picked) == 6:
            break
    if len(picked) < 2:
        picked = ["latest", "news", "update"]
    return " ".join(picked).title()


def fix_section(article, sid, issues):
    sec = next(s for s in article["sections"] if s["id"] == sid)
    spec = SPEC[sid]
    probs = "\n".join(f"- {i['problem']} -> {i.get('fix', '')}" for i in issues)
    user = (
        f"VERIFIED FACTS:\n{facts_block(article)}\n\nREWRITE this section and fix the problems. Keep what is good.\n"
        f"Section task: {spec['brief']}\nLength: {spec['words']} words.\n{FORMAT_RULES}\n"
        f"Current heading: {sec['heading']}\nCurrent body:\n{sec['body']}\n\nProblems to fix:\n{probs}\n\n"
        'Return JSON: {"heading": "...", "body": "...", "caption": "one sentence max 22 words, or empty string if this section has no picture"}'
    )
    raw = llm.chat_json(M["writer"], SYS, user, temperature=0.3, num_predict=700)
    if _clean(raw.get("heading")):
        sec["heading"] = _clean(raw["heading"])
    body = _clean_body(raw.get("body"))
    if len(body.split()) >= 30:
        sec["body"] = body
    if sec["has_image"] and _clean(raw.get("caption")):
        sec["caption"] = _clean(raw["caption"])


def fix_meta(article, issues):
    m = article["meta"]
    probs = "\n".join(f"- {i['problem']} -> {i.get('fix', '')}" for i in issues)
    user = (
        f"VERIFIED FACTS:\n{facts_block(article)}\n\nCurrent title: {m['title']}\nCurrent meta description: {m['meta_description']}\n"
        f"Current focus keyword: {m['focus_keyword']}\nCurrent summary: {m['short_summary']}\n\nProblems to fix:\n{probs}\n\n"
        "Rules: title 45-60 characters and contains the focus keyword; meta_description 130-155 characters and contains the focus keyword; "
        "summary 2-3 sentences using only verified facts.\n"
        'Return JSON: {"title":"...","meta_description":"...","focus_keyword":"...","short_summary":"..."}'
    )
    raw = llm.chat_json(M["writer"], SYS, user, temperature=0.3, num_predict=400)
    for k in ("title", "meta_description", "focus_keyword", "short_summary"):
        if _clean(raw.get(k)):
            m[k] = _clean(raw[k])


# ---------------------------------------------------------------- HTML
def _inline(s):
    s = html.escape(s, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"==(.+?)==", r'<mark style="background:#fde047;color:#111;padding:0 4px;border-radius:4px">\1</mark>', s)
    s = re.sub(r"__(.+?)__", r"<u>\1</u>", s)
    return s


def md_to_html(body):
    out, para, bullets = [], [], []

    def fp():
        if para:
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
            para.clear()

    def fb():
        if bullets:
            out.append("<ul>" + "".join("<li>" + _inline(b) + "</li>" for b in bullets) + "</ul>")
            bullets.clear()

    for raw in body.replace("\r", "").split("\n"):
        line = raw.strip()
        if not line:
            fp()
            fb()
            continue
        m = re.match(r"^[-*•]\s+(.*)$", line)
        if m:
            fp()
            bullets.append(m.group(1))
        else:
            fb()
            para.append(line)
    fp()
    fb()
    return "\n".join(out)


def figure(sec, kw):
    im = sec["image"]
    cap = sec["caption"] or sec["heading"]
    alt = cap if kw.lower() in cap.lower() else f"{kw} - {cap}"
    alt = html.escape(alt[:125], quote=True)
    credit = im["credit_line"]
    if im.get("illustrative"):
        credit = "Illustrative image. " + credit
    return (
        '<figure style="margin:0 0 22px;text-align:center">'
        f'<img src="{html.escape(im["url"], quote=True)}" alt="{alt}" title="{alt}" referrerpolicy="no-referrer" fetchpriority="high" '
        'style="width:100%;max-width:100%;height:auto;border-radius:12px"/>'
        f'<figcaption style="font-size:13px;opacity:.85;margin-top:8px"><strong>{html.escape(cap, quote=False)}</strong><br/>'
        f'<span style="opacity:.8">{html.escape(credit, quote=False)}</span></figcaption></figure>'
    )


def _ld(obj):
    return '<script type="application/ld+json">' + json.dumps(obj, ensure_ascii=False).replace("</", "<\\/") + "</script>"


def build_html(article, url=None):
    m = article["meta"]
    kw = m["focus_keyword"]
    lead = m["meta_description"].strip()
    summ = m["short_summary"].strip()
    rest = "" if (not summ or summ.lower()[:50] == lead.lower()[:50]) else " " + summ
    parts = [
        '<div class="tnd-article">',
        # the first text of the post becomes the search-result snippet, so it starts with the meta description
        '<div style="border-left:5px solid #e11d48;background:rgba(225,29,72,.08);padding:14px 18px;border-radius:10px;margin:0 0 22px">'
        f'<p style="margin:0"><strong>{html.escape(lead, quote=False)}</strong>{html.escape(rest, quote=False)}</p></div>',
    ]
    hero = next((s for s in article["sections"] if s["image"]), None)
    if hero:
        parts.append(figure(hero, kw))
    images = [hero["image"]["url"]] if hero else []
    faq = article.get("faq") or []
    total_words = 0
    for s in article["sections"]:
        total_words += len(re.findall(r"\w+", s["body"]))
        h2 = f"<h2>{html.escape(s['heading'], quote=False)}</h2>"
        body = md_to_html(s["body"])
        if s["id"] == "keys":
            parts.append('<div style="border:2px solid #e11d48;border-radius:14px;padding:6px 20px 10px;margin:26px 0">' + h2 + body + "</div>")
        elif s["id"] == "outro":
            if faq:
                parts.append(
                    f"<h2>{html.escape(kw.title(), quote=False)}: Frequently Asked Questions</h2>"
                    + "".join(f"<h3>{html.escape(f['q'], quote=False)}</h3><p>{html.escape(f['a'], quote=False)}</p>" for f in faq)
                )
            parts.append('<div style="border-top:3px solid #e11d48;margin-top:28px;padding-top:6px">' + h2 + body + "</div>")
        else:
            parts.append(h2 + body)
    if m["keywords"]:
        parts.append('<p style="font-size:13px;opacity:.8"><em>Topics covered: ' + html.escape(", ".join(m["keywords"]), quote=False) + "</em></p>")
    parts.append("</div>")
    if config.ADD_JSONLD:
        now = datetime.now(timezone.utc).isoformat()
        art = {
            "@context": "https://schema.org",
            "@type": "NewsArticle",
            "headline": m["title"][:110],
            "description": m["meta_description"],
            "image": images,
            "datePublished": now,
            "dateModified": now,
            "keywords": ", ".join(m["keywords"]),
            "articleSection": article.get("label") or "News",
            "wordCount": total_words,
            "inLanguage": "en",
            "isAccessibleForFree": True,
            "author": {"@type": "Organization", "name": config.SITE_NAME},
            "publisher": {"@type": "Organization", "name": config.SITE_NAME},
        }
        if url:
            art["mainEntityOfPage"] = {"@type": "WebPage", "@id": url}
            art["url"] = url
        parts.append(_ld(art))
        if faq:
            parts.append(_ld({
                "@context": "https://schema.org",
                "@type": "FAQPage",
                "mainEntity": [{"@type": "Question", "name": f["q"], "acceptedAnswer": {"@type": "Answer", "text": f["a"]}} for f in faq],
            }))
    return "\n".join(parts)
