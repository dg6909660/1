import hashlib
import json
import os
import time

PATH = "data/state.json"


def key(link):
    return hashlib.sha1(link.split("?")[0].encode()).hexdigest()[:16]


class State:
    def __init__(self):
        self.d = {"seen": [], "failed": {}, "posts": [], "next_idx": 0}
        if os.path.exists(PATH):
            try:
                with open(PATH, encoding="utf-8") as f:
                    self.d.update(json.load(f))
            except Exception:
                pass

    def is_seen(self, item):
        k = key(item["link"])
        return k in self.d["seen"] or self.d["failed"].get(k, 0) >= 2

    def mark_seen(self, item):
        self.d["seen"].append(key(item["link"]))

    def mark_failed(self, item):
        k = key(item["link"])
        self.d["failed"][k] = self.d["failed"].get(k, 0) + 1

    def add_post(self, item, article, url):
        related = item.get("related", [])
        for link in [item["link"]] + [r["link"] for r in related]:
            self.d["seen"].append(key(link))  # every outlet covering this story counts as done
        self.d["posts"].append(
            {
                "title": article["meta"]["title"],
                "titles": [item["title"]] + [r["title"] for r in related],
                "focus": article["meta"]["focus_keyword"],
                "url": url,
                "label": article["label"] or "Home",
                "ts": int(time.time()),
            }
        )

    def recent_titles(self, n=300):
        out = []
        for p in self.d["posts"][-n:]:
            out.append(p.get("title", ""))
            out.extend(p.get("titles") or [p.get("src_title", "")])
        return [t for t in out if t]

    def set_next(self, idx):
        self.d["next_idx"] = idx + 1

    def save(self):
        os.makedirs("data", exist_ok=True)
        self.d["seen"] = list(dict.fromkeys(self.d["seen"]))[-6000:]
        self.d["posts"] = self.d["posts"][-600:]
        if len(self.d["failed"]) > 3000:
            self.d["failed"] = dict(list(self.d["failed"].items())[-3000:])
        with open(PATH, "w", encoding="utf-8") as f:
            json.dump(self.d, f, indent=1, ensure_ascii=False)
