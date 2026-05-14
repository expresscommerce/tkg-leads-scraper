# 🔎 Lead Scraper

> **Local-business lead generation, end-to-end.**
> Scrape Google Maps → enrich each business from its website → flag the ones running active Meta ads → export a clean CSV.

![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)
![Playwright](https://img.shields.io/badge/Playwright-Chromium-2EAD33?logo=playwright&logoColor=white)
![Next.js](https://img.shields.io/badge/UI-Next.js-000000?logo=next.js&logoColor=white)
![FastAPI](https://img.shields.io/badge/API-FastAPI-009688?logo=fastapi&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green)

---

## ✨ What it does

| Stage | Source | Output |
|------|------|--------|
| **1. Discover** | Google Maps (Playwright) | name, address, phone, rating, website, category |
| **2. Enrich** | each business website (concurrent) | emails, extra phones, Facebook page |
| **3. Qualify** | Meta Ad Library — public site, **no API key needed** | active-ad flag, ad count, ad copy, library URL |
| **4. Export** | pandas / CSV | one tidy row per lead |

The project has three interchangeable surfaces backed by the same `scraper/` package:

- **Next.js web UI** (`frontend/`) — live progress, filters, CSV download
- **FastAPI** server (`backend/api.py`) — background jobs, REST endpoints
- **CLI** (`cli.py`) — one-off runs from a terminal

## 🚀 Quick start

### 1. Backend (Python + Playwright)

```bash
git clone <your-fork>
cd lead-scraper

python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
PLAYWRIGHT_BROWSERS_PATH=$PWD/backend/.playwright playwright install chromium

# Run the API (serves the frontend at http://localhost:8000/docs)
.venv/bin/python backend/api.py
```

> `backend/.env` is optional — copy `backend/.env.example` to `backend/.env` only if you want to override defaults.

### 2. Frontend (Next.js, optional)

```bash
cd frontend
npm install
npm run dev          # → http://localhost:3000
```

The frontend talks to the FastAPI server at `http://localhost:8000`.

## 📸 Four ways to use it

```bash
# 1. Web UI (Next.js + FastAPI)
.venv/bin/python backend/api.py   # terminal 1
cd frontend && npm run dev        # terminal 2  → http://localhost:3000

# 2. CLI — single keyword & location
python cli.py "plumber" "Dallas, Texas" 25

# 2b. CLI — multiple keywords AND/OR locations (semicolon-separated)
python cli.py "plumber; emergency plumber; drain cleaning" \
              "Austin, TX; Dallas, TX; Houston, TX" 25

# 2c. CLI — load keywords / locations from files
python cli.py --keywords-file keywords.txt --locations-file cities.txt 25

# 2d. CLI — skip stages
python cli.py "plumber" "Dallas, TX" 25 --no-websites   # skip website enrichment
python cli.py "plumber" "Dallas, TX" 25 --no-meta       # skip Meta Ad Library check

# 3. Re-check Meta ads on an existing CSV (no re-scraping of Maps/websites)
python backend/recheck_meta.py backend/output/<file>_websites.csv
# Smoke-test on a few rows first with a visible browser:
python backend/recheck_meta.py backend/output/<file>_websites.csv --limit 5 --headed

# 4. REST API directly
.venv/bin/python backend/api.py   # → http://localhost:8000/docs
```

### Scaling tips

Google Maps caps each individual query at roughly **120–200 listings**. To collect thousands of leads, fan out across **keywords × locations**:

| Keywords × Cities × per-search | Expected unique leads (after dedupe) |
|---|---|
| 1 × 10 × 100 | ~700–900 |
| 1 × 25 × 100 | ~1,800–2,200 |
| 1 × 50 × 100 | ~3,500–4,500 |
| 3 × 25 × 100 | ~4,500–6,500 |
| 3 × 50 × 100 | ~7,000–10,000 |

In the **Next.js UI** (or any client of the API) pass one keyword per line in the **Business type / keyword(s)** box and one location per line in **Location(s)**. The pipeline runs the cartesian product, merges results, and dedupes by `(name, address)` before website/Meta enrichment runs once on the merged set.

## 🧱 Project layout

```
lead-scraper/
├── cli.py                  # command-line entry point (adds backend/ to sys.path)
├── QUICKSTART.md
├── backend/
│   ├── api.py              # FastAPI server with background jobs
│   ├── recheck_meta.py     # re-run Meta stage on an existing CSV
│   ├── requirements.txt
│   ├── .env.example
│   ├── .playwright/        # project-local Playwright browser cache
│   ├── output/             # generated CSVs land here
│   └── scraper/
│       ├── config.py       # dataclass config, loaded from environment
│       ├── pipeline.py     # orchestrator + progress events
│       ├── maps.py         # Google Maps scraper (Playwright)
│       ├── website.py      # concurrent website enrichment
│       ├── meta_ads.py     # Meta Ad Library scraper (Playwright, no API)
│       └── utils.py        # logging, regex extraction, CSV I/O
└── frontend/               # Next.js 14 dashboard (Tailwind + lucide-react)
    ├── app/                # App Router pages
    ├── components/
    ├── lib/
    └── package.json
```

Every stage is independently importable from inside `backend/`, so you can plug pieces into your own workflow:

```python
import sys; sys.path.insert(0, "backend")
from scraper.pipeline import run_pipeline

result = run_pipeline("dentist", "Austin, Texas", max_results=30)
print(len(result.businesses), "leads →", result.csv_path)
```

## ⚙️ Configuration

**The `backend/.env` file is optional.** Sensible defaults are baked in and **no API tokens are required**. Copy `backend/.env.example` to `backend/.env` only if you want to override one of the variables below.

| Variable | Default | Purpose |
|----------|---------|---------|
| `META_ENABLED` | `1` | Toggle the Meta Ad Library stage |
| `META_AD_COUNTRY` | `US` | Country filter passed to the Ad Library |
| `META_HEADLESS` | `1` | Run Chromium headless during the Meta check |
| `META_DELAY_SECONDS` | `2.0` | Pause between Ad Library lookups |
| `MAPS_HEADLESS` | `1` | Run Chromium headless during Maps scraping |
| `MAPS_MAX_SCROLLS` | `60` | Maximum scrolls of the results pane |
| `WEBSITE_MAX_WORKERS` | `6` | Concurrent website fetches |
| `WEBSITE_TIMEOUT` | `8` | Per-page timeout in seconds |
| `OUTPUT_DIR` | `./output` | Where CSVs are written |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, … |

## 📦 Output schema

The CSV is **exploded by email** — each email a business has becomes its own row with all other business fields repeated. Businesses with zero emails still produce a single row (`email` blank) so they aren't lost.

```
business_name, category, address, phone, website,
rating, reviews, email,
facebook_url,
running_meta_ads, meta_ad_count, meta_ad_library_url,
meta_ad_start_date, meta_ad_copy, meta_ad_platforms,
maps_url
```

- `phone` merges the Maps phone with website-discovered phones (deduped by digits, joined with ` | `).
- `email` contains a single address; emails go through a junk filter that drops `noreply@`, Wix/Sentry/WordPress/Squarespace platform addresses, placeholder domains (`example.com`, `domain.com`, `yourdomain.com`, …), and image-extension false positives. See `JUNK_DOMAINS` in `backend/scraper/utils.py` if you need to extend it.
- The pipeline writes **two checkpoint CSVs** alongside the final file: `<basename>_maps.csv` after Maps scraping and `<basename>_websites.csv` after website enrichment. If the Meta stage fails or returns bad data, you can resume just that stage with `python backend/recheck_meta.py backend/output/<basename>_websites.csv` — it groups the exploded email rows back into unique businesses, re-runs the Ad Library check, and rewrites the CSV in place (with a `.bak`).

## 🛣️ REST API

```bash
curl -X POST http://localhost:8000/run \
  -H 'Content-Type: application/json' \
  -d '{"query":"plumber","location":"Dallas, Texas","max_results":15}'

# → {"job_id":"…","status":"queued"}

curl http://localhost:8000/status/<job_id>
curl http://localhost:8000/logs/<job_id>?tail=20
curl -X POST http://localhost:8000/stop/<job_id>          # cooperative cancel
curl -OJ http://localhost:8000/download/<job_id>          # download the CSV
curl http://localhost:8000/jobs                           # list all jobs
```

Interactive docs are served at `http://localhost:8000/docs`.

## 🧠 Design notes

- **Stage isolation** — `maps.py`, `website.py`, `meta_ads.py` each export a single function operating on the shared `Business` dataclass. New stages plug into `pipeline.run_pipeline` in one line.
- **Progress callbacks** — every stage reports `(stage, current, total, message)` events. The CLI prints a progress bar, the API exposes them as job logs at `GET /logs/{job_id}`, and the Next.js frontend polls those logs for its live progress view.
- **No hidden state** — everything configurable lives in `backend/scraper/config.py`, loaded once from environment variables.
- **No API keys** — the Meta Ad Library is scraped directly from the public website, so the project works out of the box.

## ⚠️ Responsible use

This project is for legitimate B2B lead research. Respect each platform's Terms of Service, `robots.txt`, and applicable privacy laws (GDPR, CAN-SPAM, etc.). Use reasonable rate limits.

## 📄 License

MIT
