# SEO / GEO Pulse

A live, always-on feed of SEO and GEO news, pulled from the major search
publications and newsletters and refreshed every few hours. One public page
anyone can open — no login, no server, no running cost.

**How it works** — a scheduled GitHub Action runs `aggregate.py`, which fetches
your RSS feeds, cleans and categorizes each story, de-duplicates, and writes
`docs/data.json`. GitHub Pages serves `docs/` as a static site; the page reads
`data.json` and renders it. Because the data is a static file on the same host,
there's no CORS problem and nothing to keep running.

```
RSS/Atom feeds ─► aggregate.py ─► docs/data.json ─► docs/index.html (GitHub Pages)
                       ▲                                   
              GitHub Actions cron (every 4h) commits the refreshed data.json
```

---

## Deploy it (about 10 minutes, free)

1. **Create a new GitHub repo** and put these files in it (drop in the folder
   contents, or `git init` here and push). A **public** repo gets free Pages +
   Actions.

2. **Turn on Pages.** Repo **Settings → Pages → Build and deployment**:
   - Source: **Deploy from a branch**
   - Branch: **main**, folder: **/docs** → **Save**

   Your site goes live at `https://<your-username>.github.io/<repo-name>/`
   within a minute. Right away it shows a built-in **sample feed** (real recent
   stories) so it's never blank — the masthead says "Sample feed" until the
   first real sync.

3. **Let Actions write to the repo** (this is the one easy-to-miss step). Repo
   **Settings → Actions → General → Workflow permissions** → choose **Read and
   write permissions** → **Save**. Without this, the job can't commit the
   refreshed `data.json`.

4. **Run the first sync now.** **Actions** tab → **Update feed** → **Run
   workflow**. In ~1 minute it fetches the feeds, commits `docs/data.json`, and
   the live page flips from the sample set to real stories. After that it runs
   itself every 4 hours.

That's it. Share the Pages URL with anyone.

---

## Make it yours

**Sources** — edit the `FEEDS` list at the top of `aggregate.py`. Each entry is
just a name and an RSS/Atom URL. Most blogs expose `/feed` or `/feed/`; Substack
newsletters use the publication URL + `/feed`. A dead feed is skipped with a log
line, so the run never breaks — just fix or remove the URL.

**How often it refreshes** — change the `cron` in
`.github/workflows/update.yml` (it's UTC). `0 */4 * * *` is every 4 hours;
`0 */2 * * *` every 2. Note: GitHub sometimes delays scheduled runs under load,
and on free accounts scheduled workflows pause after ~60 days with no repo
activity — any commit or a manual run wakes them back up.

**Categories** — stories are sorted into five buckets by keyword scoring in
`CATEGORY_RULES` (in `aggregate.py`). Add words your sources actually use to
tune it; the highest-scoring category wins, ties break by list order, and no
match falls back to *Industry & Platform*.

**Other knobs** (top of `aggregate.py`): `MAX_AGE_DAYS` (how far back to keep),
`MAX_ITEMS` (hard cap), `TITLE_DUP_RATIO` (how aggressively near-identical
headlines from different outlets get merged).

---

## Run it locally

```bash
pip install -r requirements.txt
python aggregate.py                # fetches feeds, writes docs/data.json
python -m http.server -d docs 8000 # then open http://localhost:8000
```

(Open via the local server, not the raw file — browsers block `fetch` of
`data.json` over `file://`.)

---

## Optional: AI-written summaries

By default, summaries are the cleaned excerpt from each feed and categorization
is keyword-based — free, fast, no API key. If you'd rather have a crisp one-line
summary and sharper categories, you can add a step in `fetch_feed` that calls an
LLM (e.g. Claude Haiku) per new item, gated behind an `ANTHROPIC_API_KEY` repo
secret so the free path keeps working when it's unset. Left out of v1 on purpose
to keep the thing zero-config and unbreakable.

---

## Files

| Path | What it is |
|------|-----------|
| `aggregate.py` | The aggregator — feeds, categorization, dedupe, output. **Edit sources here.** |
| `requirements.txt` | Python deps (`feedparser`, `python-dateutil`). |
| `.github/workflows/update.yml` | The 4-hour cron that runs the aggregator and commits data. |
| `docs/index.html` | The public page (self-contained: styles + logic inline). |
| `docs/data.json` | The generated feed. Ships empty; the Action fills it. |
