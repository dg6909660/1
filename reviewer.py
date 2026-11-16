import re
import time
import traceback

import config
import images
import llm
import validators
import writer

RUBRIC = {
    "facts_supported": "Every claim in the article is supported by the SOURCES.",
    "no_invented_details": "No invented names, numbers, quotes or dates.",
    "hook_strong": "The opening hooks the reader with a question, shocking fact or mystery.",
    "background_clear": "Background is explained simply and shows why the news matters.",
    "analysis_with_evidence": "Core analysis is step by step and backed by data or evidence.",
    "conversational_tone": "Tone is natural and conversational, not a dry textbook.",
    "key_points_marked": "Key points are marked (bold, highlight, bullets) and easy to scan.",
    "outro_open_question": "Conclusion summarizes briefly and ends with an open-ended question.",
    "images_match_evidence": "Each image caption matches the facts of its section; images are honest and relevant.",
    "seo_ready": "Title, description, keyword use, headings and structure are SEO-ready.",
}

CHECK_SECTION = {
    "hook_strong": "hook", "background_clear": "context", "analysis_with_evidence": "core2",
    "conversational_tone": "story", "key_points_marked": "keys", "outro_open_question": "outro", "seo_ready": "meta",
}

ROUNDS = [
    (1, "Fact-check", "fact", "Compare every sentence with the SOURCES. Flag anything unsupported, exaggerated or contradicted."),
    (2, "Style and language", "style", "Grammar, spelling, repetition, robotic or awkward phrasing, tone consistency, natural headings."),
    (3, "Structure and readability", "style",
     "Order: short summary, hook, background, core facts, storytelling, key takeaways, conclusion. Scannability, paragraph length, highlights."),
    (4, "Images and SEO", "seo",
     "Image captions vs the evidence in each section, image honesty, title and description, keyword use, headings."),
    (5, "Final approval", "final", "Act as chief editor and decide if the article is ready to publish. Be strict about accuracy."),
]


def article_text(article):
    m = article["meta"]
    lines = [
        f"TITLE: {m['title']}",
        f"META DESCRIPTION: {m['meta_description']}",
        f"FOCUS KEYWORD: {m['focus_keyword']}",
        f"CATEGORY LABEL: {article['label'] or 'none (Home only)'}",
        f"SHORT SUMMARY: {m['short_summary']}",
    ]
    for s in article["sections"]:
        lines.append(f"\n[SECTION {s['id']}] {s['heading']}\n{s['body']}")
        if s["image"]:
            im = s["image"]
            lines.append(
                f"[IMAGE for {s['id']}] caption: {s['caption']} | photo title: {im['title']} | photo details: {im['desc'][:160]}"
            )
    return "\n".join(lines)


def _normalize(article, issue):
    ids = [s["id"] for s in article["sections"]]
    sid = str(issue.get("section", "")).lower().strip()
    if sid in ids or sid == "meta":
        return sid
    for i in ids:
        if i in sid:
            return i
    if any(w in sid for w in ("meta", "title", "seo", "description")):
        return "meta"
    return None


def _apply(article, issues):
    ids = {s["id"]: s for s in article["sections"]}
    grouped = {}
    for i in issues:
        sid = _normalize(article, i)
        if sid:
            grouped.setdefault(sid, []).append(i)
    for sid, lst in grouped.items():
        try:
            if sid == "meta":
                writer.fix_meta(article, lst)
                continue
            sec = ids[sid]
            text = " ".join((x.get("problem", "") + " " + x.get("fix", "")).lower() for x in lst)
            if sec["has_image"] and re.search(r"\b(image|photo|picture)\b", text):
                images.replace_image(article, sid)
            writer.fix_section(article, sid, lst)
        except llm.OutOfTime:
            raise
        except Exception as e:
            print(f"    fix for {sid} failed: {e}", flush=True)


def run_round(n, name, model_key, focus, article, sources):
    model = config.MODELS[model_key]
    for attempt in range(1, config.MAX_FIX + 1):
        prog = validators.validate(article, sources)
        rubric = "\n".join(f"{i + 1}. {k}: {v}" for i, (k, v) in enumerate(RUBRIC.items()))
        user = (
            f"You are reviewer #{n} ({name}). Focus: {focus}\n\n"
            f"SOURCES:\n{writer.sources_text(sources, 6000)}\n\nARTICLE:\n{article_text(article)}\n\n"
            f"Evaluate the article against this 10-point checklist, one by one. Be strict but fair; flag only real problems.\n{rubric}\n\n"
            'Return JSON: {"checks": {"facts_supported": true, "...": true}, "issues": '
            '[{"section": "hook|context|core1|core2|story|keys|outro|meta", "problem": "...", "fix": "specific instruction"}]}. '
            "Include all 10 check names. List issues only for checks that are false; otherwise issues must be []."
        )
        try:
            res = llm.chat_json(model, "You are a meticulous news editor and fact-checker.", user, temperature=0.1, num_predict=600)
        except llm.OutOfTime:
            raise
        except Exception as e:
            print(f"  round {n} {name}: reviewer unavailable ({e}), moving on", flush=True)
            return False
        checks = res.get("checks") if isinstance(res.get("checks"), dict) else {}
        raw_issues = [i for i in (res.get("issues") or []) if isinstance(i, dict) and i.get("problem")]
        false_checks = [k for k, v in checks.items() if v is False]
        problems = prog + raw_issues
        if not problems and len(false_checks) > config.CHECK_TOLERANCE:
            for k in false_checks:
                sid = CHECK_SECTION.get(k)
                if sid:
                    problems.append({"section": sid, "problem": f"Reviewer judged '{k}' as failing", "fix": "Strengthen this section for that criterion"})
        print(f"  round {n} {name} attempt {attempt}: {len(problems)} problems, false checks: {false_checks}", flush=True)
        if not problems:
            return True
        _apply(article, problems)
    return False


def _trim(t, n):
    if len(t) <= n:
        return t
    return t[:n].rsplit(" ", 1)[0].rstrip(",:;-– ")


def finalize(article, sources):
    """Deterministic clean-up so the post is always safe and well-formed."""
    src_norm = validators.source_norm(sources)
    m = article["meta"]
    for s in article["sections"]:
        s["heading"] = validators.URL_RE.sub("", s["heading"]).strip() or "More Details"
        body = validators.URL_RE.sub("", s["body"])
        kept = []
        for line in body.split("\n"):
            if not line.strip():
                kept.append(line)
                continue
            is_bullet = bool(re.match(r"^\s*[-*•]\s+", line))
            sentences = [line] if is_bullet else re.split(r"(?<=[.!?])\s+", line)
            good = [x for x in sentences if all(n in src_norm for n in validators.numbers(x))]
            if good:
                kept.append(" ".join(good))
        cleaned = "\n".join(kept).strip()
        s["body"] = cleaned if len(cleaned.split()) >= 20 else body.strip()
    if len(m["title"]) > 65:
        m["title"] = _trim(m["title"], 62)
    if len(m["meta_description"]) > 160:
        m["meta_description"] = _trim(m["meta_description"], 155)
    if len(m["meta_description"]) < 60:
        m["meta_description"] = _trim(m["short_summary"], 155)
    outro = next((s for s in article["sections"] if s["id"] == "outro"), None)
    if outro and "?" not in outro["body"]:
        outro["body"] += f"\n\nWhat do you think about {m['focus_keyword']}?"


def run_all(article, sources):
    """Runs the 5 review rounds. Never raises; returns (all_passed, remaining_issues)."""
    all_ok = True
    for n, name, model_key, focus in ROUNDS:
        if time.time() > config.DEADLINE - config.REVIEW_RESERVE_SEC:
            print("  time is almost up, skipping remaining review rounds", flush=True)
            all_ok = False
            break
        try:
            ok = run_round(n, name, model_key, focus, article, sources)
        except llm.OutOfTime:
            all_ok = False
            break
        except Exception:
            traceback.print_exc()
            ok = False
        if not ok:
            print(f"  round {n} ({name}) was not fully satisfied", flush=True)
            all_ok = False
    finalize(article, sources)
    remaining = validators.validate(article, sources)
    return all_ok and not remaining, remaining
