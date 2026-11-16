import re

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
        else:
            print(f"    (unmapped issue skipped: {i.get('problem')})", flush=True)
    for sid, lst in grouped.items():
        if sid == "meta":
            writer.fix_meta(article, lst)
            continue
        sec = ids[sid]
        text = " ".join((x.get("problem", "") + " " + x.get("fix", "")).lower() for x in lst)
        if sec["has_image"] and re.search(r"\b(image|photo|picture)\b", text):
            images.replace_image(article, sid)
        writer.fix_section(article, sid, lst)


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
        res = llm.chat_json(model, "You are a meticulous news editor and fact-checker.", user, temperature=0.1, num_predict=600)
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


def run_all(article, sources):
    for n, name, model_key, focus in ROUNDS:
        if not run_round(n, name, model_key, focus, article, sources):
            print(f"  REJECTED at round {n} ({name})", flush=True)
            return False
    final = validators.validate(article, sources)
    if final:
        print(f"  REJECTED by final validation: {final[:2]}", flush=True)
        return False
    return True
