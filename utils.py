import html
import re

STOP = set(
    "the a an and or but of to in on for with at by from is are was were be been this that these those it its as after "
    "before over into about new says say will would could has have had not more than they their you your our he she his "
    "her who what when where why how up out amid".split()
)


def tokens(text):
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 3 and w not in STOP}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def strip_html(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def words(text):
    return re.findall(r"[A-Za-z0-9'’]+", text or "")
