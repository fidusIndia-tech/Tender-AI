# GeM Tender Search Agent

GeM blocks the cloud server's IP, so the GeM search must run from a normal PC
(with a normal internet connection). This small agent does that: it runs quietly
in the background on a Windows PC, searches GeM, and sends results to the Tender
AI website. Nobody has to open a terminal for day-to-day use.

The website's **"Run Search Now"** button sends a request that this agent picks
up within seconds and runs — so clicking Run in the browser "just works", as long
as this agent is installed and the PC is on.

## Install (one time, for whoever will run the searches)

1. Copy the `gem-local-agent` folder onto the Windows PC that will do the searches.
2. Make sure Python 3.10+ is installed (https://www.python.org/downloads/ — tick
   **"Add python.exe to PATH"** during install). Microsoft Edge is already on
   Windows and is used automatically.
3. Double-click **`install-agent.bat`**.
   - The first run creates `.env` and opens it in Notepad. Set:
     - `TENDER_AI_BASE_URL` = the Tender AI website address (e.g.
       `https://tender-ai-production-5a7d.up.railway.app`)
     - `LOCAL_AGENT_API_KEY` = the secret key you were given (must match the server)
   - Save `.env`, then double-click **`install-agent.bat`** again.

That's it. The agent now:
- starts automatically, hidden, every time that PC logs in, and
- runs a background search every 30 minutes, and
- runs immediately whenever someone clicks **Run Search Now** on the website.

Results appear on the Tender AI website automatically. No terminal, no commands.

To stop it / remove auto-start: double-click **`uninstall-agent.bat`**.

## Server (Railway) settings — set once

```env
ENABLE_LOCAL_GEM_AGENT=true
LOCAL_AGENT_API_KEY=<same secret as the agent's .env>
```

(`ENABLE_SERVER_GEM_RUNNER` is intentionally left unset — the cloud server cannot
reach GeM, so it never runs the scraper itself.)

## Manual commands (optional, for testing only)

```powershell
python agent.py --test-keyword Siemens        # search one keyword now
python agent.py --search-new-tenders          # search all active keywords now
python agent.py --loop --interval-minutes 30  # background mode (what the installer runs)
python agent.py --test-keyword Siemens --dry-run   # search but don't send
```

## How it works

1. Users manage keywords and the scan date on the website (`GeM Local Agent` page).
2. This agent (in `--loop` mode) polls the site for:
   - **on-demand run requests** (someone clicked "Run Search Now"), and
   - a **30-minute timer** for automatic background runs.
3. For each run it searches GeM via `all-bids-data`, downloads each matching
   tender PDF (the PC can reach GeM), and posts the tender + PDF bytes to
   `/api/gem-search/discovered-tender`.
4. The server extracts the PDF, evaluates it, and inserts recommended tenders
   into All Tenders — without ever contacting GeM itself.
