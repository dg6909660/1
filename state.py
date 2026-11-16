import hashlib
import json
import os
import time

from utils import jaccard, tokens

PATH = "data/state.json"


def key(link):
    return hashlib.sha1(link.split("?")[0].encode()).hexdigest()[:16]


class State:
    def __init__(self):
        self.d = {"seen": [], "failed": {}, "posts": [], "last_cat": {}}
        if os.path.exists(PATH):
            try:
                with open(PATH, encoding="utf-8") as f:
                    self.d.update(json.load(f))
            except Exception:
                pass

    def is_seen(self, item):
        k = key(item["link"])
        if k in self.d["seen"] or self.d["failed"].get(k, 0) >= 2:
            return True
        t = tokens(item["title"])
        for p in self.d["posts"][-200:]:
            if jaccard(t, tokens(p.get("src_title", ""))) >= 0.7 or jaccard(t, tokens(p.get("title", ""))) >= 0.7:
                return True
        return False

    def mark_failed(self, item):
        k = key(item["link"])
        self.d["failed"][k] = self.d["failed"].get(k, 0) + 1

    def add_post(self, item, article, url):
        self.d["seen"].append(key(item["link"]))
        self.d["posts"].append(
            {
                "title": article["meta"]["title"],
                "src_title": item["title"],
                "url": url,
                "label": article["label"] or "Home",
                "ts": int(time.time()),
            }
        )
        self.d["last_cat"][item["category"]] = int(time.time())

    def last_post_ts(self, cat):
        return self.d["last_cat"].get(cat, 0)

    def save(self):
        os.makedirs("data", exist_ok=True)
        self.d["seen"] = self.d["seen"][-4000:]
        self.d["posts"] = self.d["posts"][-600:]
        if len(self.d["failed"]) > 3000:
            self.d["failed"] = dict(list(self.d["failed"].items())[-3000:])
        with open(PATH, "w", encoding="utf-8") as f:
            json.dump(self.d, f, indent=1, ensure_ascii=False)
