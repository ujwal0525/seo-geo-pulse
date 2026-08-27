#!/usr/bin/env python3
"""
SEO / GEO Pulse — feed aggregator.

Fetches the configured RSS/Atom feeds, cleans and categorizes each story,
de-duplicates (by URL and by near-identical title), merges with whatever is
already in docs/data.json, keeps the last N days, and writes docs/data.json
for the static site to read.

Run locally:   python aggregate.py
On a schedule: see .github/workflows/update.yml
"""

import re
import html
import json
import hashlib
import difflib
from pathlib import Path
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, urlunparse

# ─────────────────────────────────────────────────────────────────────────────
#  EDIT YOUR SOURCES HERE
#  name -> shown on each card.   url -> the RSS/Atom feed URL.
#  Most blogs expose /feed or /feed/. Substack newsletters: append /feed to the
#  publication URL. If a feed 404s, the run skips it and keeps going — check the
#  log line for that source and fix the URL here.
# ─────────────────────────────────────────────────────────────────────────────
FEEDS = [
    # ── Daily news desks ────────────────────────────────────────────────────
    {"name": "Search Engine Roundtable",  "url": "https://www.seroundtable.com/index.xml"},
    {"name": "Search Engine Land",         "url": "https://searchengineland.com/feed"},
    {"name": "Search Engine Journal",      "url": "https://www.searchenginejournal.com/feed/"},
    {"name": "Search Engine Watch",        "url": "https://www.searchenginewatch.com/feed/"},

    # ── Official platform / AI sources ──────────────────────────────────────
    {"name": "Google Search Central",      "url": "https://developers.google.com/search/blog/feed.xml"},
    {"name": "Google (Search)",            "url": "https://blog.google/products/search/rss/"},
    {"name": "Bing Webmaster",             "url": "https://blogs.bing.com/webmaster/feed"},
    {"name": "OpenAI",                     "url": "https://openai.com/news/rss.xml"},

    # ── Tool-company research blogs ─────────────────────────────────────────
    {"name": "Ahrefs Blog",                "url": "https://ahrefs.com/blog/feed/"},
    {"name": "Semrush Blog",               "url": "https://www.semrush.com/blog/feed/"},
    {"name": "Moz Blog",                   "url": "https://moz.com/feeds/blog.rss"},
    {"name": "Backlinko",                  "url": "https://backlinko.com/feed"},
    {"name": "Yoast",                      "url": "https://yoast.com/feed/"},
    {"name": "Neil Patel",                 "url": "https://neilpatel.com/feed/"},

    # ── GEO / AI-search voices & newsletters ────────────────────────────────
    {"name": "Growth Memo",                "url": "https://www.growth-memo.com/feed"},
    {"name": "SparkToro",                  "url": "https://sparktoro.com/blog/feed/"},
    {"name": "Zyppy Signal",               "url": "https://signal.zyppy.com/feed"},
    {"name": "Marie Haynes",               "url": "https://www.mariehaynes.com/feed/"},
    {"name": "Glenn Gabe (GSQi)",          "url": "https://www.gsqi.com/marketing-blog/feed/"},
    {"name": "Aleyda Solis",               "url": "https://www.aleydasolis.com/en/feed"},
    {"name": "Detailed",                   "url": "https://detailed.com/feed/"},
    {"name": "SEOSLY",                     "url": "https://seosly.com/feed/"},

    # ── Needs a test-fetch: standard WordPress /feed guesses ────────────────
    #   If any of these shows an error or "0 items" in the run log, its URL
    #   needs a tweak — fix it here or just delete the line.
    {"name": "iPullRank (Rank Report)",    "url": "https://ipullrank.com/feed/"},
    {"name": "Lily Ray (Amsive)",          "url": "https://amsive.com/insights/feed/"},
    {"name": "Dan Petrovic (DEJAN)",       "url": "https://dejan.ai/feed/"},
    {"name": "Andrea Volpini (WordLift)",  "url": "https://wordlift.io/blog/en/feed/"},
    {"name": "Kristina Azarenko",          "url": "https://marketingsyrup.com/feed/"},
    {"name": "Otterly.ai",                 "url": "https://otterly.ai/blog/feed/"},
    {"name": "NewzDash (News SEO)",        "url": "https://www.newzdash.com/feed"},
    {"name": "Candour",                    "url": "https://candour.co.nz/feed/"},
    {"name": "Ross Simmonds (Foundation)", "url": "https://foundationinc.co/lab/feed/"},

    # ── Community — Reddit often blocks automated fetches; uncomment to try ──
    # {"name": "r/SEO",                    "url": "https://www.reddit.com/r/SEO/.rss"},
    # {"name": "r/bigseo",                 "url": "https://www.reddit.com/r/bigseo/.rss"},

    # ── No public RSS (NOT added) — need email→RSS or social ingestion:
    #   Profound, Peec AI, Scrunch AI, #SEOFOMO (its author Aleyda is covered
    #   above), SEO Notebook, #SEOForLunch, Google Search Liaison (X only).
    #   Cleanest path later: Kill-the-Newsletter turns their emails into a feed.
]

# ─────────────────────────────────────────────────────────────────────────────
#  CATEGORIES  —  four buckets: GEO, Algorithms, SEO, Industry
#  Each story is scored against every bucket (count of keyword hits in the
#  title + summary). Highest score wins; ties break by CATEGORY_ORDER below;
#  zero hits falls back to "Industry". Tune by adding words your sources use.
# ─────────────────────────────────────────────────────────────────────────────
CATEGORY_RULES = {
    "GEO": [
        "ai overview", "ai overviews", "aio", "ai mode", "ai search", "ai answer",
        "generative engine", "geo ", " geo", "aeo", "llmo", "answer engine",
        "llm", "chatgpt", "searchgpt", "perplexity", "gemini", "copilot", "claude",
        "rag ", "retrieval", "citation", "cited", "ai citation", "brand visibility",
        "ai-generated", "ai assistant", "ai referral", "search live", "gpt", "prompt",
    ],
    "Algorithms": [
        "core update", "broad core", "spam update", "helpful content", "algorithm",
        "ranking update", "ranking volatility", "volatility", "ranking incident",
        "search status", "penalty", "manual action", "deindex", "reconsideration",
        "google dance", "everflux", "serp volatility", "ranking shuffle", "rollout",
        "unconfirmed update", "ranking drop", "traffic drop",
    ],
    "SEO": [
        "content", "e-e-a-t", "eeat", "authority", "backlink", "link building",
        "links", "digital pr", "anchor text", "topical", "keyword", "on-page",
        "off-page", "technical seo", "schema", "structured data", "internal link",
        "topic cluster", "crawl", "indexing", "index ", "sitemap", "canonical",
        "redirect", "core web vitals", "page speed", "local seo", "featured snippet",
        "rich result", "meta description", "title tag", "audit", "migration",
        "semrush", "ahrefs", "moz", "screaming frog", "search console", " gsc",
        "study", "research", "report", "benchmark", "tool", "tutorial", "guide", "tips",
    ],
    "Industry": [
        "acquisition", "acquire", "acquired", "merger", "funding", "raised",
        "valuation", "ipo", "layoff", "lawsuit", "antitrust", "regulation",
        "policy", "shutdown", "outage", "partnership", "earnings", "conference",
    ],
}
CATEGORY_ORDER = ["GEO", "Algorithms", "SEO", "Industry"]
DEFAULT_CATEGORY = "Industry"

# ─── Tunables ────────────────────────────────────────────────────────────────
MAX_AGE_DAYS = 45      # drop anything older than this
MAX_ITEMS = 250        # hard cap on stored stories
TITLE_DUP_RATIO = 0.90 # 0-1; higher = only merge very-similar titles
UA = "Mozilla/5.0 (compatible; SEO-GEO-Pulse/1.0; +https://github.com/)"
OUT = Path(__file__).resolve().parent / "docs" / "data.json"

# ─── Text helpers ────────────────────────────────────────────────────────────
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_NONALNUM_RE = re.compile(r"[^a-z0-9 ]+")


def clean_text(s: str, limit: int = 280) -> str:
    """Strip HTML, unescape entities, collapse whitespace, truncate on a word."""
    if not s:
        return ""
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)
    s = _WS_RE.sub(" ", s).strip()
    if len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0].rstrip(".,;:—- ") + "…"
    return s


def normalize_url(u: str) -> str:
    """Lowercase host, drop www., strip query/fragment and trailing slash."""
    try:
        p = urlparse(u.strip())
        netloc = p.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        path = p.path.rstrip("/")
        return urlunparse(((p.scheme or "https").lower(), netloc, path, "", "", ""))
    except Exception:
        return (u or "").strip()


def hashid(url: str) -> str:
    return hashlib.sha1(normalize_url(url).encode("utf-8")).hexdigest()[:12]


def norm_title(t: str) -> str:
    return _NONALNUM_RE.sub("", (t or "").lower()).strip()


def similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def categorize(title: str, summary: str = "") -> str:
    text = f" {title} {summary} ".lower()
    best, best_score = DEFAULT_CATEGORY, 0
    for cat in CATEGORY_ORDER:
        score = sum(1 for kw in CATEGORY_RULES[cat] if kw in text)
        if score > best_score:
            best, best_score = cat, score
    return best


# ─────────────────────────────────────────────────────────────────────────────
#  PRIORITY / IMPACT SCORING  (balanced: source authority ≈ topic weight)
#
#  impact = source_weight + topic_weight + corroboration_boost   (floored at 0.5)
#    · source_weight   1–4  — how authoritative the outlet is for "what changed"
#    · topic_weight         — category base (+2 Algo/GEO, +1 SEO, +0.5 Industry)
#                             plus keyword adjust (+high-impact / −tutorial),
#                             clamped to ±3 so wording never dominates
#    · corroboration        — how many DISTINCT sources ran the same story
#                             (0 / +1.5 / +3 / +4.5 for 1 / 2 / 3 / 4+ sources)
#
#  The frontend then blends this with recency:  priority = impact /(age_days+2)^g
#  Tune any number below — it's all transparent.
# ─────────────────────────────────────────────────────────────────────────────
SOURCE_WEIGHTS = {
    # 4 — official / primary sources (they ARE the change)
    "Google Search Central": 4, "Google (Search)": 4, "Bing Webmaster": 4, "OpenAI": 4,
    # 3 — daily news desks that break & confirm updates
    "Search Engine Roundtable": 3, "Search Engine Land": 3,
    "Search Engine Journal": 3, "Search Engine Watch": 3,
    # 2 — original research & senior analysts
    "Growth Memo": 2, "SparkToro": 2, "Marie Haynes": 2, "Glenn Gabe (GSQi)": 2,
    "Aleyda Solis": 2, "iPullRank (Rank Report)": 2, "Lily Ray (Amsive)": 2,
    "Zyppy Signal": 2, "Ahrefs Blog": 2, "Semrush Blog": 2, "Moz Blog": 2,
    "Detailed": 2, "Dan Petrovic (DEJAN)": 2, "Andrea Volpini (WordLift)": 2,
    # everything else defaults to 1 (general / tutorial / niche)
}
DEFAULT_SOURCE_WEIGHT = 1

CATEGORY_IMPACT = {"Algorithms": 2, "GEO": 2, "SEO": 1, "Industry": 0.5}

# terms that signal a high-impact, act-on-it story (+1 each, capped)
IMPACT_HIGH = [
    "core update", "spam update", "algorithm update", "major update", "confirmed",
    "rolling out", "rollout", "now live", "launches", "launched", "announces",
    "announced", "penalty", "manual action", "de-indexed", "deindex", "volatility",
    "ranking drop", "traffic drop", "ai overview", "ai overviews", "ai mode",
    "policy", "outage", "leak", "acquires", "acquisition", "shuts down", "breaking",
]
# tutorial / evergreen / promo signals that should sink (−1 each, capped)
IMPACT_LOW = [
    "how to", "how-to", "ultimate guide", "complete guide", "guide to", "beginner",
    "tips", "tutorial", "webinar", "sponsored", "checklist", "template",
    "best practices", "step-by-step", "examples", "ways to",
]

CORRO_STEP = 1.5      # boost per additional source
CORRO_MAX_EXTRA = 3   # count at most 3 extra sources (so 4+ all cap out)
KEYWORD_CLAMP = 3     # keyword adjust limited to ±this


def source_weight(name: str) -> float:
    return SOURCE_WEIGHTS.get(name, DEFAULT_SOURCE_WEIGHT)


def impact_score(title: str, summary: str, source: str, category: str, coverage: int) -> float:
    text = f" {title} {summary} ".lower()
    hi = sum(1 for kw in IMPACT_HIGH if kw in text)
    lo = sum(1 for kw in IMPACT_LOW if kw in text)
    kw_adj = max(-KEYWORD_CLAMP, min(KEYWORD_CLAMP, hi - lo))
    topic = CATEGORY_IMPACT.get(category, 0.5) + kw_adj
    corro = min(max(coverage - 1, 0), CORRO_MAX_EXTRA) * CORRO_STEP
    return round(max(0.5, source_weight(source) + topic + corro), 2)


# ─────────────────────────────────────────────────────────────────────────────
#  PLATFORM TIMELINE  —  milestones for search engines & LLM models only
#
#  Two sources feed the timeline:
#    1. CURATED_MILESTONES below — a hand-kept list you edit when something big
#       ships. Add a line: date, family ("engine"|"model"), platform, title, url.
#    2. Auto-append — each run, high-impact launch/update stories about the
#       tracked platforms are detected from the feed and remembered (persisted in
#       data.json, kept ~200 days) so the timeline grows on its own.
#  Curated always wins over an auto-detected copy of the same event.
# ─────────────────────────────────────────────────────────────────────────────
MILESTONE_MIN_IMPACT = 6       # auto-detect only genuinely high-impact events
MILESTONE_MAX_AGE_DAYS = 200   # how long auto-detected milestones stay
MILESTONE_MAX = 80

# family + platform detection; checked in order (model names before "google")
PLATFORM_RULES = [
    ("model",  "OpenAI",     ["gpt-5", "gpt-6", "gpt 5", "chatgpt", "openai", "sora", "searchgpt"]),
    ("model",  "Gemini",     ["gemini"]),
    ("model",  "Anthropic",  ["claude", "anthropic"]),
    ("model",  "Perplexity", ["perplexity"]),
    ("model",  "Meta AI",    ["llama", "meta ai"]),
    ("model",  "xAI",        ["grok"]),
    ("engine", "Google",     ["core update", "spam update", "ai overview", "ai overviews",
                              "ai mode", "google search", "search central", "helpful content",
                              "search generative", "google"]),
    ("engine", "Microsoft",  ["bing", "copilot", "microsoft"]),
]
# must also read like a launch/update/rollout — not a how-to or opinion piece
MILESTONE_SIGNALS = [
    "launch", "launches", "launched", "release", "releases", "released", "rolling out",
    "rollout", "roll out", "rolls out", "now available", "introduc", "unveil", "announce",
    "announced", "announces", "core update", "spam update", "new model", "goes live",
    "now live", "general availability", "debuts", "shipping",
]

# Hand-kept milestones (edit freely). id/near-dup handled in merge_milestones().
CURATED_MILESTONES = [
    # ── Google Search (engine) ──
    {"date": "2026-02-05", "family": "engine", "platform": "Google", "title": "February 2026 Discover core update", "url": "https://ahrefs.com/google-algorithm-updates", "curated": True},
    {"date": "2026-03-24", "family": "engine", "platform": "Google", "title": "March 2026 spam update", "url": "https://ahrefs.com/google-algorithm-updates", "curated": True},
    {"date": "2026-03-27", "family": "engine", "platform": "Google", "title": "March 2026 core update", "url": "https://www.searchenginejournal.com/google-confirms-march-2026-core-update-is-complete/571459/", "curated": True},
    {"date": "2026-05-19", "family": "engine", "platform": "Google", "title": "Google I/O: AI Mode 'intelligent search box' (Gemini 3.5 Flash)", "url": "", "curated": True},
    {"date": "2026-05-21", "family": "engine", "platform": "Google", "title": "May 2026 core update", "url": "https://searchengineland.com/google-may-2026-core-update-rollout-is-now-complete-479119", "curated": True},
    {"date": "2026-06-26", "family": "engine", "platform": "Google", "title": "June 2026 spam update", "url": "https://ahrefs.com/google-algorithm-updates", "curated": True},
    # ── OpenAI (model) ──
    {"date": "2026-03-06", "family": "model", "platform": "OpenAI", "title": "GPT-5.4", "url": "https://en.wikipedia.org/wiki/GPT-5.6", "curated": True},
    {"date": "2026-04-23", "family": "model", "platform": "OpenAI", "title": "GPT-5.5", "url": "https://en.wikipedia.org/wiki/GPT-5.6", "curated": True},
    {"date": "2026-07-09", "family": "model", "platform": "OpenAI", "title": "GPT-5.6 — Luna, Terra, Sol", "url": "https://en.wikipedia.org/wiki/GPT-5.6", "curated": True},
    {"date": "2026-08-01", "family": "model", "platform": "OpenAI", "title": "OpenAI names Astra (next model)", "url": "https://en.wikipedia.org/wiki/GPT-5.6", "curated": True},
    {"date": "2026-08-10", "family": "model", "platform": "OpenAI", "title": "GPT-5.6-Cyber", "url": "https://en.wikipedia.org/wiki/GPT-5.6", "curated": True},
    # ── Google Gemini (model) ──
    {"date": "2026-02-12", "family": "model", "platform": "Gemini", "title": "Gemini 3 Deep Think", "url": "https://en.wikipedia.org/wiki/Google_Gemini", "curated": True},
    {"date": "2026-02-19", "family": "model", "platform": "Gemini", "title": "Gemini 3.1 Pro", "url": "https://en.wikipedia.org/wiki/Google_Gemini", "curated": True},
    {"date": "2026-05-19", "family": "model", "platform": "Gemini", "title": "Gemini 3.5 Flash", "url": "https://en.wikipedia.org/wiki/Google_Gemini", "curated": True},
    {"date": "2026-08-13", "family": "model", "platform": "Gemini", "title": "Gemini 3.7 Flash", "url": "https://en.wikipedia.org/wiki/Google_Gemini", "curated": True},
    # ── Anthropic Claude (model) ──
    {"date": "2026-02-17", "family": "model", "platform": "Anthropic", "title": "Claude Sonnet 4.6", "url": "https://en.wikipedia.org/wiki/Claude_(language_model)", "curated": True},
    {"date": "2026-05-28", "family": "model", "platform": "Anthropic", "title": "Claude Opus 4.8", "url": "https://en.wikipedia.org/wiki/Claude_(language_model)", "curated": True},
    {"date": "2026-06-09", "family": "model", "platform": "Anthropic", "title": "Claude Fable 5 & Mythos 5", "url": "https://en.wikipedia.org/wiki/Claude_(language_model)", "curated": True},
]


def classify_platform(text: str):
    t = f" {text.lower()} "
    for fam, plat, kws in PLATFORM_RULES:
        if any(k in t for k in kws):
            return fam, plat
    return None, None


def _ms_id(m: dict) -> str:
    return hashlib.sha1((m.get("date", "") + "|" + m.get("title", "")).encode("utf-8")).hexdigest()[:12]


def detect_milestone(item: dict):
    """Return a milestone dict if this scored item is a platform launch/update."""
    if item.get("impact", 0) < MILESTONE_MIN_IMPACT:
        return None
    text = f"{item.get('title','')} {item.get('summary','')}"
    fam, plat = classify_platform(text)
    if not plat:
        return None
    if not any(sig in f" {text.lower()} " for sig in MILESTONE_SIGNALS):
        return None
    return {
        "date": (item.get("published") or now_iso())[:10],
        "family": fam,
        "platform": plat,
        "title": item.get("title", ""),
        "url": item.get("url", ""),
        "source": item.get("source", ""),
        "curated": False,
    }


def merge_milestones(existing_ms: list, curated: list, detected: list) -> list:
    """Merge curated + auto milestones; curated wins; drop near-dups; age-filter."""
    from datetime import date as _date
    cutoff = (_now() - timedelta(days=MILESTONE_MAX_AGE_DAYS)).strftime("%Y-%m-%d")

    def pd(s):
        try:
            return _date.fromisoformat((s or "")[:10])
        except Exception:
            return None

    # de-dupe exact (date+title) collisions, curated preferred
    by_id = {}
    for m in list(existing_ms) + list(detected) + list(curated):
        m = dict(m)
        m["id"] = _ms_id(m)
        prev = by_id.get(m["id"])
        if prev is None or (m.get("curated") and not prev.get("curated")):
            by_id[m["id"]] = m

    items = [m for m in by_id.values() if m.get("curated") or m.get("date", "") >= cutoff]

    def same_event(a, b):
        # only ever merge within one platform and a two-week window, so distinct
        # monthly updates / different model versions always stay separate
        if a.get("platform") and b.get("platform") and a["platform"] != b["platform"]:
            return False
        da, db = pd(a.get("date")), pd(b.get("date"))
        if da and db and abs((da - db).days) > 14:
            return False
        ta, tb = norm_title(a["title"]), norm_title(b["title"])
        if not ta or not tb:
            return False
        if similar(ta, tb) >= 0.85:
            return True
        short, lng = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
        return short in lng   # terse curated label contained in a verbose headline

    # collapse near-duplicate events (curated kept as the representative)
    ranked = sorted(items, key=lambda m: (bool(m.get("curated")), m.get("date", "")), reverse=True)
    kept = []
    for m in ranked:
        if any(same_event(m, k) for k in kept):
            continue
        kept.append(m)

    kept.sort(key=lambda m: m.get("date", ""), reverse=True)
    return kept[:MILESTONE_MAX]


# ─── Date helpers ────────────────────────────────────────────────────────────
def _now() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return _now().strftime("%Y-%m-%dT%H:%M:%SZ")


def to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        try:
            from dateutil import parser as dp
            dt = dp.parse(s)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None


def parse_entry_date(entry) -> datetime:
    """Best-effort published date from a feedparser entry."""
    import time
    for key in ("published_parsed", "updated_parsed"):
        st = entry.get(key)
        if st:
            try:
                return datetime.fromtimestamp(time.mktime(st), tz=timezone.utc)
            except Exception:
                pass
    for key in ("published", "updated", "created"):
        val = entry.get(key)
        dt = parse_iso(val) if val else None
        if dt:
            return dt
    return _now()


# ─── Fetch ───────────────────────────────────────────────────────────────────
def fetch_feed(feed: dict) -> list:
    """Return a list of raw item dicts for one feed. Never raises."""
    import feedparser  # lazy: keeps the rest of the module importable without it
    items = []
    try:
        parsed = feedparser.parse(feed["url"], agent=UA)
    except Exception as exc:
        print(f"  ! {feed['name']}: fetch error — {exc}")
        return items
    if getattr(parsed, "bozo", 0) and not parsed.entries:
        print(f"  ! {feed['name']}: could not parse feed")
        return items
    for e in parsed.entries:
        title = clean_text(e.get("title", ""), 300)
        link = (e.get("link") or "").strip()
        if not title or not link:
            continue
        raw = e.get("summary", "")
        if not raw and e.get("content"):
            try:
                raw = e["content"][0].get("value", "")
            except Exception:
                raw = ""
        items.append({
            "title": title,
            "url": link,
            "source": feed["name"],
            "summary": clean_text(raw),
            "published_dt": parse_entry_date(e),
        })
    print(f"  · {feed['name']}: {len(items)} items")
    return items


# ─── Build ───────────────────────────────────────────────────────────────────
def build_dataset(new_items: list, existing: dict) -> dict:
    """Merge new + existing items, dedupe, age-filter, sort, cap."""
    cutoff = _now() - timedelta(days=MAX_AGE_DAYS)
    merged: dict = {}

    def add(rec_title, rec_url, rec_source, rec_summary, rec_category, rec_published):
        nid = hashid(rec_url)
        cat = rec_category or categorize(rec_title, rec_summary)
        rec = {
            "id": nid,
            "title": rec_title,
            "url": rec_url,
            "source": rec_source,
            "summary": rec_summary or "",
            "category": cat,
            "published": rec_published,
        }
        old = merged.get(nid)
        # keep the version with the richer summary
        if old is None or len(rec["summary"]) > len(old["summary"]):
            merged[nid] = rec

    # existing stories first — re-categorize with the CURRENT rules so that
    # changing the buckets re-buckets the whole archive on the next run
    for it in existing.get("items", []):
        add(it.get("title", ""), it.get("url", ""), it.get("source", ""),
            it.get("summary", ""), None, it.get("published"))

    # then the freshly fetched ones
    for it in new_items:
        dt = it.get("published_dt")
        add(it["title"], it["url"], it["source"], it.get("summary", ""),
            it.get("category"), to_iso(dt) if dt else now_iso())

    items = list(merged.values())

    # age filter (drop undated-parse failures too)
    items = [i for i in items
             if i["published"] and (parse_iso(i["published"]) or cutoff) >= cutoff]

    # cross-source clustering: group near-identical headlines, keep the most
    # authoritative (then newest) copy as the representative, and count how many
    # DISTINCT sources ran the story — that count drives the corroboration boost.
    items.sort(key=lambda i: (source_weight(i["source"]), i["published"] or ""),
               reverse=True)
    clusters = []  # each: {"nt": normalized_title, "rep": item, "sources": set}
    for it in items:
        nt = norm_title(it["title"])
        home = None
        if nt:
            for cl in clusters:
                if similar(nt, cl["nt"]) >= TITLE_DUP_RATIO:
                    home = cl
                    break
        if home is None:
            clusters.append({"nt": nt, "rep": it, "sources": {it["source"]}})
        else:
            home["sources"].add(it["source"])

    scored = []
    for cl in clusters:
        rep = dict(cl["rep"])
        rep["coverage"] = len(cl["sources"])
        rep["impact"] = impact_score(rep["title"], rep["summary"],
                                     rep["source"], rep["category"], rep["coverage"])
        scored.append(rep)

    # store newest-first (the frontend re-sorts live by priority); cap the archive
    scored.sort(key=lambda i: i["published"], reverse=True)
    scored = scored[:MAX_ITEMS]
    sources = sorted({i["source"] for i in scored})

    # platform timeline: detect milestones from this run, merge with the kept
    # history + the curated seed list
    detected = [m for m in (detect_milestone(i) for i in scored) if m]
    milestones = merge_milestones(existing.get("milestones", []), CURATED_MILESTONES, detected)

    return {
        "updated": now_iso(),
        "count": len(scored),
        "sources": sources,
        "milestones": milestones,
        "items": scored,
    }


def load_existing() -> dict:
    if OUT.exists():
        try:
            data = json.loads(OUT.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("items"), list):
                return data
        except Exception as exc:
            print(f"  ! could not read existing data.json ({exc}); starting fresh")
    return {"items": []}


def write_dataset(ds: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(ds, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    print("SEO / GEO Pulse — aggregating…")
    existing = load_existing()
    new_items = []
    for feed in FEEDS:
        new_items.extend(fetch_feed(feed))
    ds = build_dataset(new_items, existing)
    write_dataset(ds)
    by_cat = {}
    for i in ds["items"]:
        by_cat[i["category"]] = by_cat.get(i["category"], 0) + 1
    print(f"\nDone → {OUT}")
    print(f"  {ds['count']} stories across {len(ds['sources'])} sources")
    for cat in CATEGORY_ORDER:
        if by_cat.get(cat):
            print(f"    {cat}: {by_cat[cat]}")


if __name__ == "__main__":
    main()
