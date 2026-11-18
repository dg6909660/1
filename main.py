import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

import config
import dedupe
import images
import indexing
import publisher
import reviewer
import seo
import sources
import writer
from llm import OutOfTime
from state import State


def log(*a):
    print(*a, flush=True)


def try_post(item, st, live):
    if dedupe.is_duplicate(item, st, live):
        log("  same story already published, skipping")
        st.mark_seen(item)
        st.save()
        return False

    srcs = sources.gather_sources(item)
    if not srcs:
        log("  not enough source text, trying next story")
        st.mark_failed(item)
        st.save()
        return False

    log("  writing article...")
    art = None
    try:
        art = writer.build_article(item, srcs)
    except OutOfTime:
        raise
    except Exception:
        traceback.print_exc()
        log("  normal pipeline failed, writing a quick article instead")
    if art is None:
        art = writer.quick_article(item, srcs)

    log("  SEO boost (title, description, keywords, headings, FAQ)...")
    seo.optimize(art, srcs)

    log("  taking the story's own image...")
    images.attach_images(art, item, srcs)
    for s in art["sections"]:
        if s["has_image"] and not s["image"]:
            s["has_image"] = False
    if not any(s["image"] for s in art["sections"]):
        log("  WARNING: no image available")
        if not config.FORCE_PUBLISH:
            st.mark_failed(item)
            st.save()
            return False

    if not art.get("quick"):
        log("  running 5 review rounds...")
        passed, remaining = reviewer.run_all(art, srcs)
        if remaining:
            log(f"  remaining minor issues: {[i['problem'] for i in remaining][:4]}")
        if not passed and not config.FORCE_PUBLISH:
            log("  reviewers not fully satisfied, skipping")
            st.mark_failed(item)
            st.save()
            return False
    else:
        reviewer.finalize(art, srcs)

    seo.finalize(art, srcs)  # keep the strongest title/description after the reviewers

    config.DEADLINE = max(config.DEADLINE, time.time() + 300)  # always leave time to publish + share
    html_content = writer.build_html(art)
    labels = [art["label"]] if art["label"] else []
    slug_title = writer.make_permalink(art)
    log(f"  permalink phrase: {slug_title}")
    if config.DRY_RUN:
        log(html_content)
        return True

    url, _pid = publisher.publish_post(
        art["meta"]["title"], html_content, labels, slug_title,
        html_after=lambda u: writer.build_html(art, url=u),  # final content with the real post URL in the schema data
    )
    log(f"  PUBLISHED: {url}  label={labels or 'Home only'}")
    st.add_post(item, art, url)
    st.save()  # remember immediately so this story is never posted twice
    try:
        publisher.pinterest_share(art, url)
    except Exception as e:
        log("  Pinterest failed:", e)
    try:
        indexing.index_post(url, art["label"])  # ask Google to crawl + index the new post
    except Exception as e:
        log("  Google indexing step failed:", e)
    return True


def main():
    st = State()
    feeds = sources.load_feeds()
    log(f"Loading {len(feeds)} feeds...")
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(lambda f: sources.fetch_feed(*f), feeds))
    items = [i for r in results for i in r]
    ranked = sources.rank(items, st)
    live = dedupe.live_titles()
    log(f"{len(items)} items fetched, {len(ranked)} new candidates, {len(live)} live posts checked")

    n = len(config.ROTATION)
    start = st.d.get("next_idx", 0) % n
    log(f"Category order: {config.ROTATION}; next up: {config.ROTATION[start]}")

    posted, attempts, exit_code = False, 0, 2
    try:
        for k in range(n):
            idx = (start + k) % n
            slot = config.ROTATION[idx]
            cands = [i for i in ranked if i["category"] == slot][:3]
            if not cands:
                log(f"[{slot}] no fresh stories, moving to the next category")
                continue
            for item in cands:
                if attempts >= config.MAX_CANDIDATES:
                    break
                attempts += 1
                log(f"Candidate [{slot}] {item['title']}")
                try:
                    if try_post(item, st, live):
                        st.set_next(idx)
                        posted = True
                        break
                except publisher.PublishError as e:
                    log(f"PUBLISH ERROR: {e}")
                    exit_code = 1
                    raise SystemExit(1)
                except OutOfTime:
                    log("  run time budget reached, stopping")
                    raise SystemExit(2)
                except SystemExit:
                    raise
                except Exception:
                    traceback.print_exc()
                    st.mark_failed(item)
            if posted or attempts >= config.MAX_CANDIDATES:
                break
        try:
            indexing.retry_pending()  # re-send recent posts whose Google notification failed earlier
        except Exception as e:
            log("  indexing retry failed:", e)
    except SystemExit as e:
        exit_code = e.code if isinstance(e.code, int) else 2
    finally:
        st.save()
    if posted:
        exit_code = 0
    elif exit_code == 2:
        log("NO POST PUBLISHED in this run (see messages above for the reason)")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
