import requests

import config
import llm


def access_token():
    r = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": config.CLIENT_ID,
            "client_secret": config.CLIENT_SECRET,
            "refresh_token": config.REFRESH_TOKEN,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def publish_post(title, html_content, labels):
    token = access_token()
    body = {"kind": "blogger#post", "title": title, "content": html_content}
    if labels:
        body["labels"] = labels
    r = requests.post(
        f"https://www.googleapis.com/blogger/v3/blogs/{config.BLOG_ID}/posts",
        params={"isDraft": str(config.PUBLISH_DRAFT).lower()},
        headers={"Authorization": f"Bearer {token}"},
        json=body,
        timeout=60,
    )
    if r.status_code >= 300:
        raise RuntimeError(f"Blogger error {r.status_code}: {r.text[:300]}")
    return r.json().get("url", "")


def pinterest_share(article, post_url):
    if not (config.PIN_TOKEN and config.PIN_BOARD and post_url):
        return False
    hero = next((s["image"] for s in article["sections"] if s["image"]), None)
    if not hero:
        return False
    m = article["meta"]
    try:
        blurb = llm.chat(
            config.MODELS["writer"],
            "You write short, engaging Pinterest descriptions in English.",
            f"Story: {m['title']}\n{m['short_summary']}\n\nWrite a Pinterest description of 2-3 sentences (max 330 characters) that makes people want to read the full story, "
            f"naturally including the phrase '{m['focus_keyword']}', then 3-4 relevant hashtags at the end. No links.",
            temperature=0.5, num_predict=200,
        ).strip().strip('"')
    except llm.OutOfTime:
        blurb = m["meta_description"]
    desc = (blurb[:600] + "\n\nRead the full story: " + post_url)[:800]
    r = requests.post(
        f"{config.PIN_API}/pins",
        headers={"Authorization": f"Bearer {config.PIN_TOKEN}", "Content-Type": "application/json"},
        json={
            "board_id": config.PIN_BOARD,
            "title": m["title"][:100],
            "description": desc,
            "link": post_url,
            "alt_text": (article["sections"][0]["caption"] or m["title"])[:490],
            "media_source": {"source_type": "image_url", "url": hero["url"]},
        },
        timeout=60,
    )
    print("  Pinterest:", r.status_code, flush=True)
    return r.status_code < 300
