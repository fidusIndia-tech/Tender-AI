# Local GeM Tender Search Agent

This agent runs on the office/local PC so GeM sees the office IP. Tender AI stays on Railway and receives only discovered tenders through secured APIs.

## Setup

```powershell
cd C:\Users\dell\Downloads\gem_tender_tool\gem-local-agent
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
copy .env.example .env
```

Set these in `.env`:

```env
TENDER_AI_BASE_URL=http://127.0.0.1:8000
LOCAL_AGENT_API_KEY=same-secret-as-backend
DRY_RUN=true
```

The backend must also have:

```env
ENABLE_LOCAL_GEM_AGENT=true
LOCAL_AGENT_API_KEY=same-secret-as-agent
```

## Commands

```powershell
python agent.py --test-keyword Siemens
python agent.py --search-new-tenders
python agent.py --run-all
python agent.py --dry-run
python agent.py --test-keyword Siemens --date 2026-07-04 --dry-run
```

## Flow

1. Admin adds active keywords in Tender AI under `GeM Local Agent`.
2. Admin chooses the scan date on the `GeM Local Agent` page.
3. Agent fetches active keywords from `/api/gem-search/keywords`.
4. Agent fetches scan config from `/api/gem-search/config`.
5. Agent searches GeM locally via `all-bids-data`.
6. Agent posts each discovered tender to `/api/gem-search/discovered-tender`.
7. Tender AI downloads the PDF, extracts, evaluates, inserts recommended tenders into All Tenders, and stores rejected/discovered rows separately.

Keep `DRY_RUN=true` until you confirm the discovered rows look correct.
