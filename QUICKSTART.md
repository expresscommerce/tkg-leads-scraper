# Local Development

## Setup (one-time)

### Backend
```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
```

### Frontend
```bash
cd frontend
npm install
```

## Running

### Terminal 1 — Backend (FastAPI)
```bash
cd backend
.venv/bin/python api.py
# Runs on http://localhost:8000
```

### Terminal 2 — Frontend (Next.js)
```bash
cd frontend
npm run dev
# Runs on http://localhost:3000
```

Open http://localhost:3000 in your browser.

## Project Structure
```
.
├── backend/        # FastAPI + Playwright scraper
│   ├── api.py
│   ├── scraper/
│   └── requirements.txt
└── frontend/       # Next.js UI
    ├── app/
    └── package.json
```
