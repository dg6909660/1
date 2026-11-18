import base64
import time

import requests

import config
import llm

API = f"https://www.googleapis.com/blogger/v3/blogs/{config.BLOG_ID}"


class PublishError(Exception):
    """Credentials/permission problem - the automatic chain stops so you can fix it."""


def _req(method, url, **kw):
    last = None
    for i in range(3):
        try:
            r = requests.request(method, url, timeout=60, **kw)
            if r.status_code in (429, 500, 502, 503, 504):
                last = f"HTTP {r.status_code}"
                time.sleep(6 * (i + 1))
                continue
            return r
        except requests.RequestException as e:
            last = e
            time.sleep(6)
    raise RuntimeError(f"network/server error: {last}")


def access_token():
    r = _req(
        "POST",
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": config.CLIENT_ID,
            "client_secret": config.CLIENT_SECRET,
            "refresh_token": config.REFRESH_TOKEN,
            "grant_type": "refresh_token",
        },
    )
    if r.status_code != 200:
        msg = f"Google token error {r.status_code}: {r.text[:200]} -> check CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN"
        if r.status_code in (400, 401):
            raise PublishError(msg)
        raise RuntimeError(msg)
    return r.json()["access_token"]


def publish_post(title, html_content, labels, slug_title=None, html_after=None):
    """Publishes with the AI permalink phrase as the first title (Blogger makes the URL from it), then switches to the
    real headline and, once the final URL is known, rewrites the content with URL-aware SEO data (schema.org)."""
    headers = {"Authorization": f"Bearer {access_token()}"}
    body = {"kind": "blogger#post", "title": slug_title or title, "content": html_content, "readerComments": "ALLOW"}
    if labels:
        body["labels"] = labels
    params = {"isDraft": str(config.PUBLISH_DRAFT).lower()}
    r = _req("POST", f"{API}/posts", params=params, headers=headers, json=body)
    if r.status_code == 400 and "readerComments" in r.text:
        body.pop("readerComments", None)
        r = _req("POST", f"{API}/posts", params=params, headers=headers, json=body)
    if r.status_code in (401, 403, 404):
        raise PublishError(f"Blogger error {r.status_code}: {r.text[:300]} -> check BLOG_ID, account access and that Blogger API v3 is enabled")
    if r.status_code >= 300:
        raise RuntimeError(f"Blogger error {r.status_code}: {r.text[:300]}")
    data = r.json()
    post_id, url = data.get("id"), data.get("url", "")
    patch = {}
    if slug_title and slug_title != title:
        patch["title"] = title
    if html_after and url:
        try:
            patch["content"] = html_after(url)
        except Exception as e:
            print("  could not build final content:", e, flush=True)
    if patch and post_id:
        for _ in range(3):
            rp = _req("PATCH", f"{API}/posts/{post_id}", headers=headers, json=patch)
            if rp.status_code < 300:
                break
            print(f"  post update failed {rp.status_code}: {rp.text[:150]}", flush=True)
            time.sleep(5)
        else:
            print("  WARNING: post keeps its first title/content", flush=True)
    return url, post_id


# ------------------------------------------------------------ Pinterest
def _pin_token():
    if config.PIN_REFRESH and config.PIN_APP_ID and config.PIN_APP_SECRET:
        try:
            r = requests.post(
                f"{config.PIN_API}/oauth/token",
                auth=(config.PIN_APP_ID, config.PIN_APP_SECRET),
                data={"grant_type": "refresh_token", "refresh_token": config.PIN_REFRESH},
                timeout=30,
            )
            if r.status_code == 200 and r.json().get("access_token"):
                return r.json()["access_token"]
            print("  Pinterest token refresh failed:", r.status_code, r.text[:200], flush=True)
        except Exception as e:
            print("  Pinterest token refresh error:", e, flush=True)
    return config.PIN_TOKEN


def _board_id(tok):
    b = config.PIN_BOARD
    if b.isdigit():
        return b
    try:
        r = requests.get(f"{config.PIN_API}/boards", headers={"Authorization": f"Bearer {tok}"}, params={"page_size": 100}, timeout=30)
        items = r.json().get("items", [])
        for x in items:
            if b and x.get("name", "").strip().lower() == b.strip().lower():
                return x["id"]
        if not b and items:
            return items[0]["id"]
    except Exception as e:
        print("  Pinterest board lookup failed:", e, flush=True)
    return b


def _download(url):
    try:
        r = requests.get(url, headers={"User-Agent": config.BROWSER_UA}, timeout=30)
        ct = r.headers.get("content-type", "").split(";")[0].strip().lower()
        if r.status_code == 200 and ct in ("image/jpeg", "image/png") and len(r.content) < 9_000_000:
            return ct, base64.b64encode(r.content).decode()
    except Exception:
        pass
    return None, None


def pinterest_share(article, post_url):
    tok = _pin_token()
    if not tok:
        print("  Pinterest SKIPPED: PINTEREST_ACCESS_TOKEN secret is missing", flush=True)
        return False
    if not post_url:
        print("  Pinterest SKIPPED: no post URL (draft mode?)", flush=True)
        return False
    hero = next((s["image"] for s in article["sections"] if s["image"]), None)
    if not hero:
        print("  Pinterest SKIPPED: post has no image (a pin needs an image)", flush=True)
        return False
    board = _board_id(tok)
    if not board:
        print("  Pinterest SKIPPED: PINTEREST_BOARD_ID is missing and no board was found", flush=True)
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
    except Exception:
        blurb = m["meta_description"]
    desc = (blurb[:600] + "\n\nRead the full story: " + post_url)[:800]  # blog link at the bottom
    base = {
        "board_id": board,
        "title": m["title"][:100],
        "description": desc,
        "link": post_url,
        "alt_text": (article["sections"][0]["caption"] or m["title"])[:490],
    }
    sources_to_try = []
    ct, b64 = _download(hero["url"])
    if b64:
        sources_to_try.append({"source_type": "image_base64", "content_type": ct, "data": b64})
    sources_to_try.append({"source_type": "image_url", "url": hero["url"]})
    r = None
    for ms in sources_to_try:
        r = requests.post(
            f"{config.PIN_API}/pins",
            headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
            json={**base, "media_source": ms},
            timeout=90,
        )
        print(f"  Pinterest ({ms['source_type']}): {r.status_code} {r.text[:200]}", flush=True)
        if r.status_code < 300:
            return True
    if r is not None:
        if r.status_code == 401:
            print("  Pinterest hint: access token expired/invalid. Add PINTEREST_APP_ID, PINTEREST_APP_SECRET, PINTEREST_REFRESH_TOKEN for auto-renew.", flush=True)
        elif r.status_code in (403, 429):
            print("  Pinterest hint: the app needs pins:write + boards:read scopes and Standard access (Trial access only works on the sandbox API).", flush=True)
    return False
