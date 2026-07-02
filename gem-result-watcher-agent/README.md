# GeM Result Watcher Agent

This agent runs on an office Windows PC or laptop, checks GeM through the local browser/IP, and updates the Tender AI website deployed on Railway.

## What It Does

- Fetches pending tenders from Tender AI using `WATCHER_API_KEY`.
- Opens/reuses a local Playwright browser profile for `https://bidplus.gem.gov.in/all-bids`.
- Searches GeM Bid/RA Status with each tender's real bid number.
- Parses returned GeM docs for bid result and RA result availability.
- Sends successful result data or check errors back to Tender AI.
- Writes local logs in `logs/`.
- Can be scheduled twice daily with Windows Task Scheduler.

## Setup

1. Create and activate a virtual environment:

```powershell
cd C:\Users\dell\Downloads\gem_tender_tool\gem-result-watcher-agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
```

PowerShell uses `Activate.ps1`, not `activate`. If activation is blocked or unnecessary, you can also run the agent with plain `python` from this folder as long as `playwright` and `python-dotenv` are installed.

If `pip install` fails with a PyPI SSL certificate error, use:

```powershell
python -m pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org -r requirements.txt
```

2. Create `.env` from `.env.example`:

```powershell
Copy-Item .env.example .env
notepad .env
```

3. Set these values:

```env
TENDER_AI_BASE_URL=https://your-tender-ai-app.up.railway.app
WATCHER_API_KEY=the-same-secret-set-in-railway
PLAYWRIGHT_HEADLESS=false
```

4. Add the same `WATCHER_API_KEY` value in Railway environment variables for the Tender AI service.

## First Run

Run one known bid first:

```powershell
python watcher.py --test-bid GEM/2026/B/7586698
```

The first run opens a browser profile under `.browser-profile`. If GeM needs any session/cookie setup, complete it in that browser window, then run the command again.

## Run Pending Tenders

```powershell
python watcher.py --run-now
```

The agent waits between checks and continues if one tender fails.

## Windows Task Scheduler

Create the twice-daily task at 10:00 AM and 5:00 PM:

```powershell
.\setup_scheduler.ps1 -PythonExe "C:\Users\dell\Downloads\gem_tender_tool\gem-result-watcher-agent\.venv\Scripts\python.exe"
```

The scheduled task name is:

```text
Tender AI GeM Result Watcher Agent
```

## Logs

Daily logs are written to:

```text
gem-result-watcher-agent\logs\watcher-YYYY-MM-DD.log
```

Every tender log includes tender id, bid number, GeM response status, returned docs, matched doc status, result availability, RA number, and ingest success/failure.

## Status Rules

- `BID_RESULT_AVAILABLE`: original bid result id is present.
- `RA_RESULT_AVAILABLE`: RA number and RA result id are present.
- `BID_AND_RA_RESULT_AVAILABLE`: both original bid and RA result links are present.
- `NOT_AVAILABLE_YET`: exact tender matched, but no result ids were returned.
- `FAILED_TO_CHECK`: GeM request failed or exact tender was not found in the GeM response.

The agent does not create notifications directly. Tender AI creates one notification when a tender changes from no result to result available.
