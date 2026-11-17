import requests

import config
import llm
from utils import tokens


def live_titles(limit=150):
    """Titles already live on the blog (public feed). Backup in case the saved state is ever missing."""
    if not config.BLOG_ID:
        return []
    try:
        r = requests.get(
            f"https://www.blogger.com/feeds/{config.BLOG_ID}/posts/default",
            params={"alt": "json", "max-results": limit},
            headers={"User-Agent": config.UA},
            timeout=25,
        )
        entries = r.json().get("feed", {}).get("entry", [])
        return [e.get("title", {}).get("$t", "") for e in entries if e.get("title", {}).get("$t")]
    except Exception:
        return []


def _cover(a, b):
    shared = a & b
    return len(shared), len(shared) / max(1, min(len(a), len(b)))


def is_duplicate(item, st, live):
    tt = tokens(item["title"])
    if len(tt) < 2:
        return False
    recent = list(dict.fromkeys(st.recent_titles() + live))
    scored = []
    for r in recent:
        n, c = _cover(tt, tokens(r))
        scored.append((c, n, r))
    scored.sort(reverse=True)
    if not scored:
        return False
    best_c, best_n, best_t = scored[0]
    if best_n >= 3 and best_c >= 0.6:
        print(f"  duplicate (very similar to: {best_t})", flush=True)
        return True
    maybe = [t for c, n, t in scored[:3] if n >= 2 and c >= 0.34]
    if not maybe:
        return False
    listing = "\n".join("- " + t for t in maybe)
    user = (
        f"Candidate headline: {item['title']}\nCandidate summary: {item['summary'][:300]}\n\n"
        f"Recent headlines already published:\n{listing}\n\n"
        "Is the candidate about the SAME news event or story as any recent headline (not merely the same general topic)? "
        'Return JSON: {"same_story": true or false}'
    )
    try:
        res = llm.chat_json(config.MODELS["style"], "You compare news headlines.", user, temperature=0.0, num_predict=40)
        same = bool(res.get("same_story"))
    except Exception:
        same = best_c >= 0.45
    if same:
        print(f"  duplicate (AI says same story as: {maybe[0]})", flush=True)
    return same
