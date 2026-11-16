import sys
import traceback
from concurrent.futures import ThreadPoolExecutor

import config
import images
import publisher
import reviewer
import sources
import writer
from llm import OutOfTime
from state import State


def log(*a):
    print(*a, flush=True)


def try_post(item, st):
    srcs = sources.gather_sources(item)
    if not srcs:
        log("  not enough source text")
        st.mark_failed(item)
        return False
    log("  writing article...")
    art = writer.build_article(item, srcs)
    log("  finding images...")
    images.attach_images(art, item, srcs)
    n_img = sum(1 for s in art["sections"] if s["image"])
    if n_img < config.MIN_IMAGES:
        log(f"  only {n_img} images found, skipping")
        st.mark_failed(item)
        return False
    # drop image slots that stayed empty so text and captions stay consistent
    for s in art["sections"]:
        if s["has_image"] and not s["image"]:
            s["has_image"] = False
    log("  running 5 review rounds...")
    if not reviewer.run_all(art, srcs):
        st.mark_failed(item)
        return False
    html_content = writer.build_html(art)
    labels = [art["label"]] if art["label"] else []
    if config.DRY_RUN:
        log(html_content)
        return True
    url = publisher.publish_post(art["meta"]["title"], html_content, labels)
    log(f"  PUBLISHED: {url}  label={labels or 'Home only'}")
    st.add_post(item, art, url)
    try:
        publisher.pinterest_share(art, url)
    except OutOfTime:
        pass
    except Exception as e:
        log("  Pinterest failed:", e)
    return True


def main():
    st = State()
    feeds = sources.load_feeds()
    log(f"Loading {len(feeds)} feeds...")
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(lambda f: sources.fetch_feed(*f), feeds))
    items = [i for r in results for i in r]
    ranked = sources.rank(items, st)
    log(f"{len(items)} items, {len(ranked)} new candidates")
    try:
        for item in ranked[: config.MAX_CANDIDATES]:
            log(f"Candidate [{item['category']}] {item['title']}")
            try:
                if try_post(item, st):
                    break
            except OutOfTime:
                log("  run time budget reached, stopping")
                break
            except Exception:
                traceback.print_exc()
                st.mark_failed(item)
    finally:
        st.save()
    sys.exit(0)


if __name__ == "__main__":
    main()
