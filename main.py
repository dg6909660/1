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
        log("  not enough source text, trying next story")
        st.mark_failed(item)
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

    log("  finding images (free-license first, then RSS/source images)...")
    images.attach_images(art, item, srcs)
    n_img = sum(1 for s in art["sections"] if s["image"])
    log(f"  images found: {n_img}")
    if n_img < config.MIN_IMAGES and not config.FORCE_PUBLISH:
        log("  not enough images, skipping")
        st.mark_failed(item)
        return False
    if n_img == 0:
        log("  WARNING: no image available, publishing without image")
    for s in art["sections"]:
        if s["has_image"] and not s["image"]:
            s["has_image"] = False

    if not art.get("quick"):
        log("  running 5 review rounds...")
        passed, remaining = reviewer.run_all(art, srcs)
        if remaining:
            log(f"  remaining minor issues: {[i['problem'] for i in remaining][:4]}")
        if not passed and not config.FORCE_PUBLISH:
            log("  reviewers not fully satisfied, skipping")
            st.mark_failed(item)
            return False
    else:
        reviewer.finalize(art, srcs)

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
    log(f"{len(items)} items fetched, {len(ranked)} new candidates")
    exit_code = 0
    posted = False
    try:
        for item in ranked[: config.MAX_CANDIDATES]:
            log(f"Candidate [{item['category']}] {item['title']}")
            try:
                if try_post(item, st):
                    posted = True
                    break
            except publisher.PublishError as e:
                log(f"PUBLISH ERROR: {e}")
                exit_code = 1
                break
            except OutOfTime:
                log("  run time budget reached, stopping")
                break
            except Exception:
                traceback.print_exc()
                st.mark_failed(item)
    finally:
        st.save()
    if not posted and exit_code == 0:
        log("NO POST PUBLISHED in this run (see messages above for the reason)")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
