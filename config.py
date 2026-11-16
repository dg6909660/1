import os
import time


def env(name, default=""):
    v = os.getenv(name)
    return v.strip() if v and v.strip() else default


SITE_NAME = env("SITE_NAME", "The News Detail")

# Blogger (GitHub Secrets)
BLOG_ID = env("BLOG_ID")
CLIENT_ID = env("CLIENT_ID")
CLIENT_SECRET = env("CLIENT_SECRET")
REFRESH_TOKEN = env("REFRESH_TOKEN")

# Pinterest (optional)
PIN_TOKEN = env("PINTEREST_ACCESS_TOKEN")
PIN_BOARD = env("PINTEREST_BOARD_ID")
PIN_API = env("PINTEREST_API_BASE", "https://api.pinterest.com/v5")

# Local AI (Ollama)
OLLAMA_URL = env("OLLAMA_URL", "http://127.0.0.1:11434")
MODELS = {
    "writer": env("WRITER_MODEL", "qwen2.5:3b"),
    "fact": env("FACT_MODEL", "llama3.2:3b"),
    "style": env("STYLE_MODEL", "gemma2:2b"),
    "seo": env("SEO_MODEL", "qwen2.5:3b"),
    "final": env("FINAL_MODEL", "phi3.5"),
}

CATEGORIES = ["Health", "Business", "World News", "Political", "Science", "Education", "Movie"]

UA = "TheNewsDetailBot/1.0 (news publishing bot)"

MIN_IMAGES = int(env("MIN_IMAGES", "4"))
MIN_WORDS = int(env("MIN_WORDS", "900"))
MAX_AGE_HOURS = int(env("MAX_AGE_HOURS", "36"))
MAX_CANDIDATES = int(env("MAX_CANDIDATES", "3"))
MAX_FIX = int(env("MAX_FIX_ATTEMPTS", "3"))
CHECK_TOLERANCE = int(env("CHECK_TOLERANCE", "1"))
USE_SOURCE_IMAGE = env("USE_SOURCE_IMAGE", "0") == "1"
PUBLISH_DRAFT = env("PUBLISH_DRAFT", "0") == "1"
DRY_RUN = env("DRY_RUN", "0") == "1"
ADD_JSONLD = env("ADD_JSONLD", "1") == "1"

RUN_BUDGET_MIN = int(env("RUN_BUDGET_MIN", "22"))
DEADLINE = time.time() + RUN_BUDGET_MIN * 60
