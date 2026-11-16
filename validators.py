import re

import config
from utils import words

MIN_SECTION_WORDS = {"hook": 70, "context": 100, "core1": 110, "core2": 110, "story": 85, "keys": 45, "outro": 50}
GENERIC = {"hook", "background", "context", "conclusion", "introduction", "outro", "key takeaways", "core facts", "analysis", "storytelling"}
URL_RE = re.compile(r"https?://\S*|www\.\S*|\b\w+\.(?:com|org|net)\b", re.I)


def shingles(text, n=8):
    w = [x.lower() for x in words(text)]
    return {" ".join(w[i:i + n]) for i in range(max(0, len(w) - n + 1))}


def numbers(text):
    out = []
    for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        n = n.replace(",", "").rstrip(".")
        if len(re.sub(r"\D", "", n)) >= 2:
            out.append(n)
    return out


def source_norm(sources):
    return " ".join(s["text"] for s in sources).replace(",", "").lower()


def validate(article, sources):
    issues = []

    def add(section, problem, fix):
        issues.append({"section": section, "problem": problem, "fix": fix})

    m = article["meta"]
    secs = article["sections"]
    src_text = " ".join(s["text"] for s in sources)
    src_norm = source_norm(sources)
    src_sh = shingles(src_text)
    by = {s["id"]: s for s in secs}

    if not 30 <= len(m["title"]) <= 65:
        add("meta", f"Title length is {len(m['title'])} characters", "Make the title 45-60 characters")
    if not 110 <= len(m["meta_description"]) <= 160:
        add("meta", f"Meta description length is {len(m['meta_description'])} characters", "Make it 130-155 characters")
    if m["focus_keyword"].lower() not in m["title"].lower():
        add("meta", "Focus keyword is missing from the title", "Put the exact focus keyword in the title")
    if m["focus_keyword"].lower() not in m["meta_description"].lower():
        add("meta", "Focus keyword is missing from the meta description", "Use the focus keyword in the description")

    total = bold = hl = 0
    for s in secs:
        body, sid = s["body"], s["id"]
        wc = len(words(body))
        total += wc
        bold += body.count("**") // 2
        hl += body.count("==") // 2
        minw = MIN_SECTION_WORDS.get(sid, 0)
        if wc < minw:
            add(sid, f"Section is too short ({wc} words)", f"Expand to at least {minw + 20} words using the verified facts")
        if URL_RE.search(body) or URL_RE.search(s["heading"]):
            add(sid, "Contains a link or URL", "Remove every link and URL")
        if s["heading"].strip().lower() in GENERIC:
            add(sid, "Heading is a generic label", "Write a natural, specific news-style subheading")
        sh = shingles(body)
        if sh and len(sh & src_sh) / len(sh) > 0.08:
            add(sid, "Too many phrases copied from the sources", "Rewrite in completely your own words")
        bad = [n for n in numbers(body) if n not in src_norm]
        if bad:
            add(sid, f"Numbers not found in sources: {', '.join(bad[:4])}", "Remove or correct these numbers; use only numbers from the facts")

    if total < config.MIN_WORDS and "core2" in by:
        add("core2", f"Article is only {total} words", f"Add more evidence and detail so the article reaches {config.MIN_WORDS}+ words")
    if bold < 4 and "core1" in by:
        add("core1", "Too few bold key phrases in the article", "Mark more key phrases with **bold**")
    if hl < 2 and "core2" in by:
        add("core2", "Too few highlighted key points", "Mark the most important phrases with ==highlight==")
    if "keys" in by and sum(1 for l in by["keys"]["body"].split("\n") if re.match(r"^\s*[-*•]\s+", l)) < 5:
        add("keys", "Key takeaways need at least 5 bullet points", "Write 6 bullet lines starting with '- '")
    if "core1" in by and not any(re.match(r"^\s*[-*•]\s+", l) for l in by["core1"]["body"].split("\n")):
        add("core1", "Core facts section needs a bullet list", "Add a bullet list of 3-4 facts")
    if "outro" in by and "?" not in by["outro"]["body"]:
        add("outro", "Conclusion must end with an open-ended question", "End with one thought-provoking question")
    if "hook" in by and m["focus_keyword"].lower() not in (m["short_summary"] + " " + by["hook"]["body"][:600]).lower():
        add("hook", "Focus keyword missing near the start", "Use the focus keyword naturally in the first paragraph")
    return issues
