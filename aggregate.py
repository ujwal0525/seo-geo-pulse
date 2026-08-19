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
    # Core SEO news
    {"name": "Search Engine Land",       "url": "https://searchengineland.com/feed"},
    {"name": "Search Engine Journal",    "url": "https://www.searchenginejournal.com/feed/"},
    {"name": "Search Engine Roundtable", "url": "https://www.seroundtable.com/feed"},
    {"name": "Search Engine Watch",      "url": "https://www.searchenginewatch.com/feed/"},
    {"name": "Google Search Central",    "url": "https://developers.google.com/search/blog/feed.xml"},
    {"name": "Google (Search)",          "url": "https://blog.google/products/search/rss/"},

    # Tools / data / research
    {"name": "Moz Blog",                 "url": "https://moz.com/blog/feed"},
    {"name": "Ahrefs Blog",              "url": "https://ahrefs.com/blog/feed/"},
    {"name": "Semrush Blog",             "url": "https://www.semrush.com/blog/feed/"},

    # GEO / AI-search voices & newsletters (RSS where available)
    {"name": "Growth Memo",              "url": "https://www.growth-memo.com/feed"},
    {"name": "SparkToro",                "url": "https://sparktoro.com/blog/feed/"},
    {"name": "Marie Haynes",             "url": "https://www.mariehaynes.com/feed/"},
    {"name": "Detailed",                 "url": "https://detailed.com/feed/"},

    # ↓ add your own must-have sources below ↓
    # {"name": "Aleyda / SEOFOMO",       "url": "https://www.aleydasolis.com/en/feed/"},
    # {"name": "Lily Ray (Amsive)",      "url": "https://www.amsive.com/insights/feed/"},
]

# ─────────────────────────────────────────────────────────────────────────────
#  CATEGORIES
#  Each story is scored against every category (count of keyword hits in the
#  title + summary). Highest score wins; ties break by the order in
#  CATEGORY_ORDER; zero hits falls back to "Industry & Platform".
#  Tune by adding words your sources actually use.
# ─────────────────────────────────────────────────────────────────────────────
CATEGORY_RULES = {
    "AI Search & GEO": [
        "ai overview", "ai overviews", "aio", "ai mode", "ai search", "ai answer",
        "generative engine", "geo ", " geo", "llm", "llmo", "aeo", "answer engine",
        "chatgpt", "perplexity", "gemini", "copilot", "claude", "rag ", "retrieval",
        "citation", "cited", "ai citation", "brand visibility", "ai-generated",
        "ai assistant", "ai referral", "search live", "gpt",
    ],
    "Algorithm & Core Updates": [
        "core update", "broad core", "spam update", "helpful content", "algorithm",
        "ranking update", "ranking volatility", "volatility", "ranking incident",
        "search status", "penalty", "manual action", "deindex", "reconsideration",
        "google dance", "everflux", "serp volatility", "ranking shuffle", "rollout",
    ],
    "Tools & Data": [
        "semrush", "ahrefs", "moz", "screaming frog", "search console", " gsc",
        "analytics", "study", "research", "report", "dataset", "data ", "benchmark",
        "indexing", "index ", "tool", "dashboard", " api", "tracking", "case study",
    ],
    "Content & Strategy": [
        "content", "e-e-a-t", "eeat", "strategy", "authority", "backlink",
        "link building", "digital pr", "topical", "keyword research", "on-page",
        "schema", "structured data", "internal link", "topic cluster", "byline",
    ],
    "Industry & Platform": [
        "acquisition", "acquire", "funding", "launch", "lawsuit", "antitrust",
        "policy", "cloudflare", "reddit", "wordpress", "bing", "microsoft",
        "openai", "partnership", "shutdown", "outage",
    ],
}
CATEGORY_ORDER = [
    "AI Search & GEO",
    "Algorithm & Core Updates",
    "Tools & Data",
    "Content & Strategy",
    "Industry & Platform",
]
DEFAULT_CATEGORY = "Industry & Platform"

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

    # existing stories first (they already carry category + ISO published)
    for it in existing.get("items", []):
        add(it.get("title", ""), it.get("url", ""), it.get("source", ""),
            it.get("summary", ""), it.get("category"), it.get("published"))

    # then the freshly fetched ones
    for it in new_items:
        dt = it.get("published_dt")
        add(it["title"], it["url"], it["source"], it.get("summary", ""),
            it.get("category"), to_iso(dt) if dt else now_iso())

    items = list(merged.values())

    # age filter (drop undated-parse failures too)
    items = [i for i in items
             if i["published"] and (parse_iso(i["published"]) or cutoff) >= cutoff]

    # newest first
    items.sort(key=lambda i: i["published"], reverse=True)

    # near-duplicate-title pass (keeps the newest of a near-dup pair)
    deduped, kept_titles = [], []
    for i in items:
        nt = norm_title(i["title"])
        if nt and any(similar(nt, kt) >= TITLE_DUP_RATIO for kt in kept_titles):
            continue
        deduped.append(i)
        kept_titles.append(nt)

    deduped = deduped[:MAX_ITEMS]
    sources = sorted({i["source"] for i in deduped})
    return {
        "updated": now_iso(),
        "count": len(deduped),
        "sources": sources,
        "items": deduped,
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
