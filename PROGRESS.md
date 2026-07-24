# Tender AI / Gem Tender Tool Progress

Last updated: 2026-07-10  
Repo path: `C:\Users\dell\Downloads\gem_tender_tool`  
Branch documented: `feature/fidus360-saas-agent`

This file is intended to be the single source of truth for the current project state. It is based on the current repository code, not on prior chat memory.

## 1. Project Overview

Tender AI / Gem Tender Tool is a FastAPI-based tender management system for Indian Government and GeM procurement workflows.

The project currently supports:

- Uploading GeM tender PDFs.
- Extracting tender fields, BOQ/items, required documents, and related metadata from PDFs.
- Evaluating tenders against a saved company capability profile.
- Maintaining an "All Tenders" operational register.
- Tracking participation status, filed dates, account managers, remarks, result status, RA status, and result details.
- Preparing missing tender documents through:
  - matching against a company document library,
  - AI-generated declarations/letters/undertakings,
  - manual upload workflows,
  - OEM/manual action recommendations.
- Searching GeM tenders through local Windows agents because Railway/cloud IPs are blocked or unreliable for GeM access.
- Discovering new tenders by keyword/date and moving qualified tenders into All Tenders.
- Watching GeM result/RA status through a local result watcher agent.
- Parsing result details from GeM result pages, including participants, technical evaluation, financial evaluation, and summary fields.
- Managing company profile, capability profile, branding assets, stamp/signature, and document library.
- Managing tender portal login credentials with encrypted password storage.
- Integrating with an external portal/SSO direction for Fidus360-style SaaS usage.

Important current direction:

- The cloud app should host the UI, API, database workflows, evaluation, document preparation, and ingestion endpoints.
- Local office PCs should perform GeM browser/network operations and push data to the cloud backend.
- SaaS/company-wise work has started and is additive, but full multi-tenant isolation still needs verification and completion.

## 2. Current Branch / Version

This progress file documents:

- Branch: `feature/fidus360-saas-agent`
- Working tree before creating this file: clean.
- Current date of documentation: 2026-07-10.

The branch contains the Fidus360/SaaS/company-wise direction:

- `companies` table.
- `company_id` scoping migration.
- `local_agents` table.
- local GeM agent token to company mapping.
- session-derived company resolution in backend.
- default company fallback for standalone mode.

## 3. Architecture

### Main Components

- `tender_app/main.py`
  - FastAPI application.
  - Serves API endpoints and the static SPA.
  - Initializes database on startup.
  - Starts result watcher scheduler on startup.
  - Includes GeM watcher router.
  - Handles auth/session, SSO, company resolution, uploads, tenders, result ingestion, company profile, document library, portal credentials, and local agent endpoints.

- `tender_app/database.py`
  - PostgreSQL database layer using `psycopg2`.
  - Creates and migrates schema idempotently during `init_db()`.
  - Includes additive company-scoping migration.
  - Contains CRUD/helper functions for tenders, uploads, documents, profiles, GeM search, GeM candidates, result watcher, result details, local agents, and portals.

- `tender_app/static/index.html`
  - Single static HTML/CSS/JS frontend.
  - Served as SPA from FastAPI static mount.
  - Contains Upload & Evaluate, All Tenders, GeM Local Agent, Document Library, Company Profile, Reports, Tender Portals, hidden legacy GeM watcher pages, notifications, modals, and result/debug/extension flows.

- `tender_app/ai_extractor.py`
  - Extracts PDF content with `pdfplumber`.
  - Uses deterministic parser from `gem_tender_tool/extract.py` when available.
  - Uses local regex fallback.
  - Uses OpenAI `gpt-4o` only if local/deterministic extraction is not good enough and `OPENAI_API_KEY` is configured.

- `tender_app/evaluation.py`
  - Shared rule-based evaluation.
  - Scores tenders against company capability profile.
  - Produces `BID`, `REVIEW`, or `SKIP`.
  - Current limitation: summary text still says "FIAPL capability profile"; this is a SaaS/company branding cleanup item.

- `tender_app/doc_matcher.py`
  - Classifies required documents into `company_library`, `ai_generated`, `oem_required`, `library_required`, or `missing`.
  - Matches against company document library with category keywords and fuzzy name matching.

- `tender_app/doc_generator.py`
  - Uses OpenAI `gpt-4o` and `python-docx` to generate declarations/letters/undertakings.
  - Does not generate government-issued documents.
  - Embeds stamp/signature images when available.

- `tender_app/result_watcher.py`
  - Server-side result watcher logic.
  - Can do direct network checks and Playwright fallback, but cloud GeM access is not reliable.
  - Provides status parsing, safe downgrade protection, history creation, result ingestion, eligible tender listing, recheck listing, run summary, and scheduler.

- `tender_app/gem_watcher/*`
  - Legacy/server-side GeM watcher scanner/evaluator/routes.
  - Uses Playwright and GeM `all-bids-data`.
  - Still present and functional behind hidden UI/router, but production direction is local agent because Railway is blocked by GeM.

- `gem-local-agent/agent.py`
  - Local Windows GeM search agent.
  - Runs on an office PC with browser access to GeM.
  - Polls backend for keywords/config/run requests.
  - Searches GeM, downloads PDFs locally, posts discovered tenders plus PDF bytes to backend.

- `gem-result-watcher-agent/watcher.py`
  - Local Windows result watcher.
  - Fetches pending/recheck tenders from backend.
  - Uses local browser/IP to query GeM result status.
  - Parses result details and pushes status/details back to backend.

- `chrome_extension/gem-bidplus-autofill`
  - Chrome/Edge extension.
  - Autofills GeM Bid/RA status searches.
  - Can fetch GeM result status from the user's browser/IP and hand raw response to app/backend.

- Deployment files:
  - `Dockerfile`
  - `railway.toml`
  - `Procfile`
  - `requirements.txt`
  - `DEPLOYMENT.md`

### Local Machine vs Cloud Roles

Cloud/Railway:

- Runs FastAPI app.
- Serves frontend.
- Connects to PostgreSQL via `DATABASE_URL`.
- Runs extraction/evaluation/document generation after files are uploaded.
- Stores uploaded PDFs and generated/company files in PostgreSQL bytea columns.
- Receives local agent discovered tenders and watcher result updates.
- Should not be relied on for GeM scraping unless `ENABLE_SERVER_GEM_RUNNER=true` is explicitly set for testing.

Local office PC:

- Runs `gem-local-agent` for GeM tender search/discovery.
- Runs `gem-result-watcher-agent` for result/RA checks and result details.
- Uses local browser profile and local IP/session to access GeM.
- Sends authenticated API requests back to cloud backend.

## 4. UI Features

Frontend file: `tender_app/static/index.html`

Visible top navigation:

- `Upload & Evaluate`
- `All Tenders`
- `GeM Local Agent`
- `Document Library`
- `Company Profile`
- `Reports`
- `Tender Portals`

Hidden/legacy navigation still present:

- `Gov Portals` legacy modal nav, hidden.
- `GeM Watcher`, hidden.
- `Review Tenders`, hidden.
- `Rejected Tenders`, hidden.

Global UI features:

- Session/auth check with portal redirect or local `/dev-login`.
- Sign out button.
- App switcher links to external portal apps:
  - inquiry tracker
  - price desk
  - lead clip
  - CRM
  - portal dashboard
- Tender result notifications bell/panel.
- Extension install/status prompt for GeM autofill/result browser flow.
- Deadline reminder modal.
- Attachment modal.
- Legacy government portals modal.

### Upload & Evaluate

Includes:

- Drag-and-drop PDF upload area.
- File picker for multiple PDFs.
- Upload queue.
- Upload start button.
- Clear queue button.
- Unified manual upload plus GeM scan workflow area.
- Scan date input.
- Approval score input.
- Keyword input.
- Add keyword button.
- Run GeM scan button.
- Stop scan button.
- Workflow filters:
  - All
  - Pending Approval
  - Approved
  - Review
  - Rejected
- Workflow scan history toggle.
- Current/manual upload and latest scan result table.

Tender edit/review tabs inside upload/review flow:

- Basic
- Items
- Documents
- Prepared
- Evaluation

Basic tender fields:

- GeM Bidding Number
- Tender / Bid Number
- Date
- Bid End Date / Time
- Bid Opening Date / Time
- Total Quantity
- Make / Brand
- VoT
- Department Name
- Organisation Name
- Office Name & Location

Actions:

- Save Tender
- Cancel
- Add BOQ row
- Add document row
- Prepare documents
- Evaluate tender
- Skip extracted tender
- Proceed further

### All Tenders

Includes:

- Search input.
- Reload button.
- Clear filters button.
- Export visible records to Excel.
- Select all checkbox.
- Sort/filter controls.
- Resizable columns.
- Organization search filter.
- Make search filter.
- Result filter:
  - All Results
  - Result Available
  - Not Available
  - Not Checked Yet
  - Failed to Check
- Participation status filter:
  - In Progress
  - Filed
  - Qualified
  - Disqualified
  - Won
  - Lost
  - Failed

Actions rendered in table/rows include, based on code paths:

- Expand row/details.
- View/open PDF.
- Open GeM result/status page.
- Check result.
- Share/open links where available.
- Edit tender fields.
- Update status.
- Upload/list/download attachments.
- Delete tender.
- Prepare documents.
- Evaluate/re-evaluate.
- Result details display for participants/technical/financial rows.

Needs verification:

- Exact final visible table column list should be verified in browser because the table HTML is assembled dynamically and has many renderer branches.

### GeM Local Agent

Visible page for local-agent-based tender search.

Includes:

- Keyword management.
- Suggested keyword dropdown/datalist.
- Date/date range hidden config fields.
- Save keyword.
- Clear all discovered tenders.
- Refresh discovered tenders.
- Run Search Now via backend run request.
- Run request status display.
- Discovered tenders table.
- Manual insert / re-evaluate / delete flows.

### Document Library

Includes:

- Add/upload company document form.
- Document fields:
  - Document Name
  - Category
  - Financial Year
  - Brand / OEM
  - Tags
  - File
- Categories include GST, PAN, MSME, ITR, cancelled cheque, bank document, Aadhaar, OEM authorization, catalogue, trade license, etc.
- Upload document.
- Cancel upload.
- Preview document.
- Close preview.
- Delete document.

### Company Profile

Includes:

- Company profile fields:
  - Company Name
  - Address
  - GST Number
  - PAN Number
  - MSME/Udyam Number
  - Email
  - Phone
  - Bank Name
  - Account Number
  - IFSC Code
  - Signatory Name
  - Designation
- Capability profile fields:
  - Year Established
  - Years of Experience
  - Core Business
  - Product Categories
  - Brands Handled
  - Industries Served
  - Turnover Range
  - Typical Tender Value Range
  - Import Capability
  - Export Capability
  - OEM Support Available
  - OEM Authorizations
  - Engineering Support
  - Installation Support
  - GST/PAN/MSME/ITR/Bank Documents/Letterhead/Stamp/Signature availability
  - PSU Experience
  - Government Experience
  - Major Customers
  - Past Orders / Projects
- Branding asset upload/remove slots:
  - company logo
  - document logo
  - letterhead
  - header image
  - footer image
  - stamp
  - signature

### Reports

Includes:

- Participant reports.
- Brand reports.
- Multi-select style triggers for participants/brands.
- Result table/title area.

Needs verification:

- Reports appear frontend-only over current tender data; exact aggregation logic should be verified in the browser.

### Tender Portals

Includes:

- Search portals.
- Refresh.
- Add Portal.
- Portal form:
  - Name
  - URL
  - Login ID
  - Password
  - Notes
- Show/reveal password in form.
- Save Portal.
- Clear form.
- Portal table actions:
  - Open Portal
  - Edit
  - Reveal password
  - Copy login/password/all credentials
  - Deactivate/delete-style action

Backend stores encrypted passwords using Fernet.

### Extension Download

Backend endpoint:

- `GET /api/extensions/gem-bidplus-autofill.zip`

Purpose:

- Builds a ZIP in memory from `tender_app/extension_bundle`.
- Lets frontend/users download the extension bundle.

Separate source extension also exists at:

- `chrome_extension/gem-bidplus-autofill`

## 5. Upload & Evaluation Flow

### Single PDF Upload

Endpoint:

- `POST /api/tenders/upload`

Flow:

- Accepts only `.pdf`.
- Generates UUID file id.
- Reads bytes.
- Saves PDF to PostgreSQL `uploaded_files`.
- Writes a temporary PDF for extraction.
- Calls `ai_extractor.process_pdf()`.
- Saves extracted JSON to `tender_app/extractions/{file_id}.json`.
- Returns extracted data and file path `/files/{file_id}`.

Extraction behavior:

- `pdfplumber` reads text/tables.
- Relevant pages are filtered by tender keywords.
- Deterministic GeM parser from `gem_tender_tool/extract.py` is tried.
- Local regex fallback is tried.
- If enough critical fields are extracted locally, returns local result.
- Otherwise uses OpenAI `gpt-4o` if `OPENAI_API_KEY` is configured.
- If OpenAI is missing or fails, local/deterministic result is returned.

### Bulk PDF Upload

Endpoint:

- `POST /api/tenders/bulk-upload`

Flow:

- Accepts multiple PDFs.
- Processes each PDF independently.
- Saves file bytes to PostgreSQL.
- Extracts data.
- Saves extracted JSON.
- Returns per-file success/failure result.

### Evaluation

Endpoints:

- `POST /api/tenders/evaluate-extracted`
- `POST /api/tenders/{tender_id}/evaluate`

Evaluation module:

- `tender_app/evaluation.py`

Inputs:

- Tender data.
- BOQ items.
- Required documents.
- Company capability profile.

Signals:

- product category match
- brand match
- years of experience
- tender value fit
- turnover fit
- available/missing docs
- OEM support
- government/PSU experience

Output:

- decision: `BID`, `REVIEW`, or `SKIP`
- score: 0-100
- summary
- strengths
- risks
- checks

Known issue:

- Evaluation summary still says "FIAPL capability profile"; needs SaaS cleanup.

### Score / Rating / Recommendation

Manual evaluation returns score out of 100 and decision:

- `BID` if score >= 70.
- `REVIEW` if score >= 45.
- `SKIP` below 45.

GeM watcher candidate evaluation uses a separate out-of-10 scoring pipeline:

- `AUTO_APPROVE_THRESHOLD` default 8.
- `REVIEW_THRESHOLD` default 6.
- Keyword pre-evaluation and full evaluation can mark candidates approved/review/rejected.

### Missing Documents / Generated Documents

Endpoint:

- `POST /api/tenders/{tender_id}/prepare`

Flow:

- Loads tender required documents.
- Loads company document library.
- Clears old prepared docs.
- Uses `doc_matcher.match_all_documents`.
- Saves rows in `tender_prepared_documents`.

Prepared document actions:

- `GET /api/tenders/{tender_id}/prepared-documents`
- `POST /api/tenders/{tender_id}/prepared-documents/{doc_id}/generate`
- `POST /api/tenders/{tender_id}/prepared-documents/{doc_id}/approve`
- `POST /api/tenders/{tender_id}/prepared-documents/{doc_id}/upload`
- `GET /api/tenders/{tender_id}/prepared-documents/{doc_id}/download`

Generation:

- Uses OpenAI `gpt-4o`.
- Uses company profile and tender fields.
- Creates DOCX.
- Adds title, tender block, organization, body, signatory, GST, stamp/signature if present.
- Saves generated DOCX bytes into DB.

Rejected/reviewed/saved behavior:

- Uploaded tender can be skipped or proceeded/saved.
- GeM candidates can be approved to All Tenders, moved to review, rejected, deleted, re-extracted, or re-evaluated.
- Approved GeM candidates get status `SENT_TO_ALL_TENDERS` and link to `tenders.id`.

## 6. All Tenders Flow

Primary table:

- `tenders`

Important tender lifecycle/status fields:

- `status`
- `participation_status`
- `filed_date`
- `ac_manager`
- `remark`
- `result_available`
- `bid_result_available`
- `ra_created`
- `ra_result_available`
- `gem_result_status`
- `result_declared`
- `result_declared_at`
- `last_result_checked_at`
- `notification_sent`
- `ra_notified`
- `result_review_required`
- `result_check_warning`

Allowed participation statuses in backend:

- `IN PROGRESS`
- `FILED`
- `QUALIFIED`
- `DISQUALIFIED`
- `WON`
- `LOST`
- `FAILED`

Main endpoints:

- `GET /api/tenders`
- `GET /api/tenders/{tender_id}`
- `POST /api/tenders`
- `PUT /api/tenders/{tender_id}`
- `DELETE /api/tenders/{tender_id}`
- `PATCH /api/tenders/{tender_id}/record-fields`
- `PATCH /api/tenders/{tender_id}/status`
- `PATCH /api/tenders/{tender_id}/boq-items`

Manual insert:

- `POST /api/tenders` creates a tender from payload.
- `POST /api/gem-search/discovered-tenders/{gem_bid_number}/manual-insert` inserts from discovered tenders into All Tenders.
- `POST /api/gem-candidates/{candidate_id}/approve` inserts approved candidate into All Tenders.

Attachments:

- `POST /api/tenders/{tender_id}/attachments`
- `GET /api/tenders/{tender_id}/attachments`
- `GET /api/attachments/{attachment_id}/download`

PDF/file actions:

- `GET /files/{file_id}`
- `GET /api/tenders/{tender_id}/pdf`

Result actions:

- `POST /api/tenders/{tender_id}/check-result`
- `POST /api/tenders/{tender_id}/ingest-gem-result`
- `POST /api/tenders/{tender_id}/ingest-gem-result-error`
- `POST /api/tenders/{tender_id}/ingest-gem-result-details`
- `GET /api/tenders/{tender_id}/result-details`

Delete behavior:

- `database.delete_tender()` includes child-table cleanup helpers and FK child discovery.
- Known/pending item from project notes: verify delete endpoint for all child rows. The code appears improved, but production verification is still needed.

Filters/search/export:

- Frontend includes search, organization/make filters, result filters, participation status filters, sorting/resizing, select-all, and Excel export.

## 7. GeM Search / Discovered Tenders

There are two related implementations:

1. Current production direction: local search agent and `gem_search` endpoints.
2. Legacy/server-side watcher: `gem_watcher` router/scanner/candidates.

### Local Agent Search Flow

Backend endpoints:

- `GET /api/gem-search/stats`
- `GET /api/gem-search/admin/config`
- `PUT /api/gem-search/admin/config`
- `POST /api/gem-search/admin/run-local-agent`
- `POST /api/gem-search/admin/request-run`
- `GET /api/gem-search/admin/run-request-status`
- `GET /api/gem-search/run-request`
- `POST /api/gem-search/run-request/{request_id}/complete`
- `GET /api/gem-search/admin/keywords`
- `POST /api/gem-search/admin/keywords`
- `PUT /api/gem-search/admin/keywords/{keyword_id}`
- `DELETE /api/gem-search/admin/keywords/{keyword_id}`
- `GET /api/gem-search/keywords`
- `GET /api/gem-search/config`
- `GET /api/gem-search/discovered-tenders`
- `POST /api/gem-search/discovered-tender`
- `POST /api/gem-search/discovered-tenders/clear-all`
- `POST /api/gem-search/discovered-tenders/{gem_bid_number}/re-evaluate`
- `POST /api/gem-search/discovered-tenders/{gem_bid_number}/manual-insert`
- `DELETE /api/gem-search/discovered-tenders/{gem_bid_number}`

Keyword configuration:

- `gem_search_keywords` holds saved local-agent keywords.
- `active` controls inclusion.
- `last_scanned_at` is touched after scans.
- Local agent also supports one-off test keyword.

Date selector:

- `gem_search_settings` stores scan date/date range/mode values.
- Local agent reads `/api/gem-search/config`.
- Modes in local agent:
  - `date`
  - `range`
  - `all`
  - legacy/default `today`

Run search:

- UI creates a run request.
- Local agent loop polls `/api/gem-search/run-request`.
- Agent claims pending run for its company/token context.
- Agent searches GeM and posts discoveries.
- Agent calls `/api/gem-search/run-request/{request_id}/complete`.

Duplicate handling:

- `gem_discovered_tenders` has company-wise unique index on `(company_id, gem_bid_number)` after migration.
- `tenders` duplicate detection checks GeM bidding/tender number in same company.
- Local discovered flow can return actions such as:
  - discovered/evaluated
  - inserted
  - duplicate/already exists
  - rejected/review-type outcomes depending evaluation and duplicate state.

Needs verification:

- Exact response action strings should be checked with live endpoint responses. Code and UI refer to duplicate actions such as `DUPLICATE_ALREADY_EXISTS`, but exact values may vary by branch logic.

### Legacy/Server GeM Watcher Flow

Files:

- `tender_app/gem_watcher/routes.py`
- `tender_app/gem_watcher/scanner.py`
- `tender_app/gem_watcher/evaluator.py`

Endpoints:

- `GET /api/gem-watcher/keywords`
- `POST /api/gem-watcher/keywords`
- `PUT /api/gem-watcher/keywords/{keyword_id}`
- `DELETE /api/gem-watcher/keywords/{keyword_id}`
- `POST /api/gem-watcher/scan`
- `GET /api/gem-watcher/scan-runs`
- `GET /api/gem-watcher/scan-runs/{run_id}`
- `POST /api/gem-watcher/scan-runs/{run_id}/cancel`
- `GET /api/gem-candidates`
- `GET /api/gem-candidates/{candidate_id}`
- `POST /api/gem-candidates/{candidate_id}/approve`
- `POST /api/gem-candidates/{candidate_id}/review`
- `POST /api/gem-candidates/{candidate_id}/reject`
- `POST /api/gem-candidates/{candidate_id}/full-evaluate`
- `POST /api/gem-candidates/{candidate_id}/rerun-extraction`
- `DELETE /api/gem-candidates/{candidate_id}`

Scan behavior:

- Uses GeM `all-bids-data`.
- Filters by bid start date.
- Uses strict keyword matching to avoid fuzzy GeM false positives.
- Downloads tender PDFs.
- Saves PDF bytes.
- Extracts fields.
- Runs keyword pre-evaluation and full evaluation.
- Auto-approves candidates above threshold.
- Routes borderline/failed items to review.
- Supports cancellation.

Candidate statuses:

- `QUEUED`
- `PDF_DOWNLOADED`
- `REVIEW`
- `REJECTED`
- `APPROVED`
- `SENT_TO_ALL_TENDERS`
- scan statuses such as `DUPLICATE`, `PDF_DOWNLOAD_FAILED`, `EXTRACTION_FAILED`, `EVALUATION_FAILED`, `BELOW_APPROVAL_SCORE`.

## 8. Result Watcher

Main backend file:

- `tender_app/result_watcher.py`

Local agent:

- `gem-result-watcher-agent/watcher.py`

### Result Statuses

Backend constants:

- `NOT_CHECKED`
- `PENDING`
- `NOT_AVAILABLE_YET`
- `NOT_FOUND_ON_GEM`
- `BID_RESULT_AVAILABLE`
- `RA_CREATED`
- `RA_RESULT_AVAILABLE`
- `BID_AND_RA_RESULT_AVAILABLE`
- `FAILED_TO_CHECK`
- `REVIEW_REQUIRED`

### Result Checking

Backend endpoint:

- `POST /api/tenders/{tender_id}/check-result`

Agent/backend ingestion endpoints:

- `POST /api/tenders/{tender_id}/ingest-gem-result`
- `POST /api/tenders/{tender_id}/ingest-gem-result-error`
- `POST /api/tenders/{tender_id}/ingest-gem-result-details`

Watcher management:

- `POST /api/result-watcher/run`
- `GET /api/result-watcher/pending`
- `GET /api/result-watcher/recheck-targets`
- `POST /api/result-watcher/run-log`
- `GET /api/result-watcher/summary`

### Eligibility

A tender is eligible when:

- a canonical GeM bid number can be found,
- bid end date has passed,
- result is not already declared, or RA exists but RA result is not available.

### RA Detection

The watcher detects:

- RA number matching `GEM/YYYY/R/...`
- original bid result availability,
- RA created,
- RA result availability,
- RA URLs and result URLs,
- RA start/end dates where available.

### Result Details

Result detail ingestion stores:

- summary in `tender_result_summary`
- participants in `tender_result_participants`
- technical rows in `tender_technical_evaluation`
- financial rows in `tender_financial_evaluation`
- detail history in `tender_result_detail_history`

Notifications:

- Current design avoids "result is live" notification from noisy fast status code alone.
- Result-live notification should be created only when real technical/financial evaluation rows are ingested.
- RA created notification is still created from RA transition.

### Recheck / Repair Flow

Local result watcher supports:

- `--run-now`
- `--check-results`
- `--check-result-details`
- `--recheck-and-fix-statuses`
- `--repair-result-statuses`
- `--apply`
- `--dry-run`
- `--force-downgrade`

Safe downgrade protection:

- If an existing status is available and a new low/medium confidence result says not available, backend preserves existing available status.
- Marks `result_review_required`.
- Sets `result_check_warning`.
- Allows downgrade only with high confidence or `forceDowngrade`.

Tests cover:

- direct bid result available
- RA created without RA result
- RA result available
- not available only when exact tender found
- not found does not crash
- confident negative can downgrade stale available
- low-confidence downgrade is blocked
- current stage requires real technical/financial rows, not only headings

Known issues/current status:

- GeM blocks Railway/cloud IP, so local result watcher is required for reliable production checks.
- False-positive result/RA confusion has been addressed in code/tests, but production data still needs monitoring.
- Technical/financial parsing depends on GeM page DOM; if GeM changes, parser may need updates.
- `OUR_COMPANY_ALIASES` in local result watcher is still Fidus-specific; full SaaS should source aliases from company profile.

## 9. Local Agents

### gem-local-agent

Path:

- `gem-local-agent/agent.py`
- `gem-local-agent/README.md`
- `gem-local-agent/.env.example`
- `gem-local-agent/install-agent.bat`
- `gem-local-agent/uninstall-agent.bat`
- `gem-local-agent/start-agent-loop.bat`
- `gem-local-agent/run-agent-hidden.vbs`
- `gem-local-agent/logs/`
- `gem-local-agent/.browser-profile/`

Purpose:

- Run GeM tender searches from a normal Windows PC/IP.
- Poll Tender AI for keywords, search config, and on-demand run requests.
- Search GeM `all-bids-data`.
- Download matching tender PDFs locally.
- Send discovery payload and PDF base64 to backend.

Important commands:

```powershell
cd C:\Users\dell\Downloads\gem_tender_tool\gem-local-agent
python agent.py --test-keyword Siemens
python agent.py --search-new-tenders
python agent.py --run-all
python agent.py --loop --interval-minutes 30
python agent.py --test-keyword Siemens --dry-run
python agent.py --test-keyword Siemens --date 2026-07-10
```

Installer behavior:

- `install-agent.bat` creates `.env` from `.env.example` if missing.
- Opens Notepad to edit `.env`.
- Installs/startup background loop using Windows startup scripts.
- `run-agent-hidden.vbs` runs loop hidden.
- `uninstall-agent.bat` removes autostart.

Environment:

- `TENDER_AI_BASE_URL`
- `LOCAL_AGENT_API_KEY`
- `BROWSER_PROFILE_DIR`
- `PLAYWRIGHT_HEADLESS`
- `HEADLESS`
- `PLAYWRIGHT_BROWSER_CHANNEL`
- `RESET_BROWSER_PROFILE_ON_LOCK`
- `MAX_RESULTS_PER_KEYWORD`
- `KEYWORD_DELAY_SECONDS`
- `PAGE_DELAY_SECONDS`
- `RUN_REQUEST_POLL_SECONDS`
- `LOOP_INTERVAL_MINUTES`
- `SEARCH_DATE_MODE`
- `SEARCH_TARGET_DATE`
- `SEARCH_DATE_FROM`
- `SEARCH_DATE_TO`
- `DRY_RUN`
- `PLAYWRIGHT_EXTRA_ARGS`

Backend authentication:

- Backend requires `ENABLE_LOCAL_GEM_AGENT=true`.
- Request must include `Authorization: Bearer <LOCAL_AGENT_API_KEY>`.
- Token is hashed with SHA-256.
- Backend resolution order:
  - Find token hash in `local_agents`, use its `company_id`, update heartbeat.
  - Fallback: if token matches global `LOCAL_AGENT_API_KEY`, map to default company.

Company-wise token mapping:

- `local_agents` table maps token hash to company.
- `_provision_default_local_agent()` registers global token to default company at startup when local agent is enabled.
- Agent never sends `companyId`; backend derives company from token.

Scheduling/heartbeat/retry:

- Loop polls run request every `RUN_REQUEST_POLL_SECONDS`.
- Scheduled run interval defaults to 30 minutes.
- Browser context uses persistent profile.
- Profile lock handling can rename profile only when `RESET_BROWSER_PROFILE_ON_LOCK=true`.
- Logs are written to `gem-local-agent/logs/agent.log`.

### gem-result-watcher-agent

Path:

- `gem-result-watcher-agent/watcher.py`
- `gem-result-watcher-agent/README.md`
- `gem-result-watcher-agent/.env.example`
- `gem-result-watcher-agent/setup_scheduler.ps1`
- `gem-result-watcher-agent/run-result-check-now.bat`
- `gem-result-watcher-agent/logs/`
- `gem-result-watcher-agent/.browser-profile/`

Purpose:

- Check GeM Bid/RA status from local PC/IP.
- Fetch pending/recheck tenders from backend.
- Query GeM `all-bids-data` with result and ongoing filters.
- Parse bid/RA availability.
- Open result pages and parse real participant/technical/financial rows.
- Ingest status/details into backend.
- Write local logs, screenshots, and HTML snapshots.

Setup commands:

```powershell
cd C:\Users\dell\Downloads\gem_tender_tool\gem-result-watcher-agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
Copy-Item .env.example .env
notepad .env
```

Run commands:

```powershell
python watcher.py --test-bid GEM/2026/B/7586698
python watcher.py --run-now
python watcher.py --check-results
python watcher.py --check-result-details --apply
python watcher.py --test-result-details GEM/2026/B/7636848
python watcher.py --test-result-details GEM/2026/B/7636848 --dry-run
python watcher.py --test-result-url "https://bidplus.gem.gov.in/..."
python watcher.py --open-gem-login
python watcher.py --recheck-and-fix-statuses --apply
python watcher.py --repair-result-statuses --apply
```

Scheduler:

```powershell
cd C:\Users\dell\Downloads\gem_tender_tool\gem-result-watcher-agent
.\setup_scheduler.ps1 -PythonExe "C:\Users\dell\Downloads\gem_tender_tool\gem-result-watcher-agent\.venv\Scripts\python.exe"
```

Default scheduled tasks:

- `Tender AI GeM Result Watcher Agent` -> `watcher.py --run-now` at about 09:15 AM.
- `Tender AI GeM Result Recheck and Repair` -> `watcher.py --recheck-and-fix-statuses --apply` at about 11:00 PM.

Environment:

- `TENDER_AI_BASE_URL`
- `WATCHER_API_KEY`
- `GEM_BASE_URL`
- `CHECK_DELAY_SECONDS`
- `MAX_TENDERS_PER_RUN`
- `LOG_LEVEL`
- `PLAYWRIGHT_HEADLESS`
- `USE_PERSISTENT_PROFILE`
- `BROWSER_PROFILE_DIR`
- `BROWSER_CHANNEL`
- `GEM_RESULT_FILTER_TYPE`
- `GEM_ONGOING_FILTER_TYPE`

Backend auth:

- Uses `Authorization: Bearer <WATCHER_API_KEY>`.
- Backend accepts watcher key in `_require_watcher_or_admin`.
- Admin session can also call watcher endpoints.

Known SaaS limitation:

- Local watcher currently has hardcoded `OUR_COMPANY_ALIASES` for Fidus names. This should be company-profile-driven before full SaaS rollout.

## 10. Chrome Extension

Source path:

- `chrome_extension/gem-bidplus-autofill`

Backend bundle path:

- `tender_app/extension_bundle`

Files:

- `manifest.json`
- `content.js`
- `background.js`
- `app-bridge.js`
- `README.md`
- packaging script: `chrome_extension/package-extension.ps1`

Purpose:

- Autofill GeM Bid/RA status searches from URL hash/query.
- Set Bid/RA Status filter.
- Set Exact Search.
- Fill bid number.
- Click search.
- Bridge app messages to extension background worker.
- Fetch GeM result data from the user's browser/IP so backend does not need to reach GeM directly.

Expected GeM URL pattern:

```text
https://bidplus.gem.gov.in/all-bids#bidrastatus-search-7530121
```

Install locally:

1. Open `chrome://extensions/`.
2. Enable Developer mode.
3. Click Load unpacked.
4. Select:

```text
C:\Users\dell\Downloads\gem_tender_tool\chrome_extension\gem-bidplus-autofill
```

Package:

```powershell
cd C:\Users\dell\Downloads\gem_tender_tool
powershell -ExecutionPolicy Bypass -File chrome_extension\package-extension.ps1
```

Output:

```text
chrome_extension\gem-bidplus-autofill.zip
```

Manifest:

- Manifest V3.
- Version `1.1.0`.
- Permissions:
  - `cookies`
- Host permission:
  - `https://bidplus.gem.gov.in/*`
- App bridge matches localhost and Railway URLs.
- GeM content script matches `https://bidplus.gem.gov.in/all-bids*`.

Known limitation:

- If GeM DOM ids or page JS changes, extension autofill/result handoff may need updates.

## 11. SaaS / Company-wise Work

Completed/started in this branch:

- `companies` table exists.
- Default company seeded as:
  - name: `Default Company`
  - slug: `default-company`
  - status: `ACTIVE`
- `local_agents` table exists.
- Additive `company_id` migration in `database.py`.
- Migration file exists at `tender_app/migrations/0001_add_company_scoping.sql`.
- Backend helper `get_current_company_id(request)` resolves company from signed session cookie.
- Fallback to default company preserves standalone mode.
- Local GeM agent token maps to company via `local_agents`.
- Global `LOCAL_AGENT_API_KEY` maps to default company fallback.
- Company-scoped duplicate indexes for:
  - `gem_search_keywords(company_id, keyword)`
  - `gem_discovered_tenders(company_id, gem_bid_number)`
  - `gem_candidate_tenders(company_id, gem_bid_no)`
- Many APIs now pass `company_id=get_current_company_id(request)`.
- Local agent endpoints derive company from token, not frontend-sent company id.
- Company profile supports branding assets:
  - company logo
  - document logo
  - letterhead
  - header image
  - footer image
  - stamp
  - signature
- Company profile and capability profile are present.
- Portal URL defaults reference external portal/Fidus360 direction:
  - `PORTAL_URL`
  - `RAILWAY_PUBLIC_DOMAIN`
  - `SSO_SECRET`

Company-wise/default-company behavior:

- If no session cookie exists or session lacks company id, default company is used.
- SSO token verification exists, but `_make_session(username, role)` currently only stores `sub`, `role`, and `exp`; it does not preserve `company_id` from verified SSO payload.
- `get_current_company_id()` can read `company_id` or `companyId` from session payload, but current session creation does not yet include it.

Needs verification / pending SaaS items:

- Test Company 2 isolation should be verified with real company/session data.
- SSO session must include company id for real multi-tenant SaaS.
- Audit every database function for company scoping:
  - some functions are scoped,
  - some still default to global/default behavior,
  - some child rows inherit company indirectly,
  - some legacy functions have no request/company parameter.
- `company_documents` does not appear in the phase-1 scoped table list; needs verification and likely company scoping.
- `tender_items`, `tender_required_documents`, `tender_prepared_documents`, `tender_attachments`, and result detail rows are child tables and mostly scoped through parent tender rather than explicit `company_id`.
- Result watcher APIs use watcher/admin auth but need company-aware behavior for multi-company operations.
- `evaluation.py` summary still says FIAPL.
- `gem-result-watcher-agent/watcher.py` has Fidus-specific `OUR_COMPANY_ALIASES`.
- Static asset `tender_app/static/logo FIAPL.png` remains in repo; verify whether UI still uses it.
- Any FIAPL/FIDUS hardcoding should be searched and cleaned before SaaS launch.

## 12. Database

Database engine:

- PostgreSQL.
- Connection via `DATABASE_URL`.
- Driver: `psycopg2`.
- Tables created/altered idempotently in `database.init_db()`.

### companies

Purpose:

- Company registry for SaaS scoping.

Fields:

- `id`
- `name`
- `slug`
- `status`
- `created_at`
- `updated_at`

Default:

- `Default Company` / `default-company`.

### local_agents

Purpose:

- Maps local GeM agent bearer token hashes to companies.

Fields:

- `id`
- `company_id`
- `agent_name`
- `token_hash`
- `status`
- `last_heartbeat_at`
- `created_at`
- `updated_at`

### uploaded_files

Purpose:

- Stores uploaded PDF and document bytes in PostgreSQL.

Fields:

- `id`
- `file_name`
- `original_name`
- `content_type`
- `file_size`
- `file_data`
- `file_category`
- `created_at`
- `company_id` after migration

### tenders

Purpose:

- Main All Tenders table.

Important fields:

- `id`
- `gem_bidding_number`
- `tender_number`
- `date`
- `bid_end_datetime`
- `bid_opening_datetime`
- `department_name`
- `organization_name`
- `office_name_location`
- `total_quantity`
- `make`
- `tender_approx_value`
- `won_text`
- `lost_text`
- `participant_text`
- `expand_sections_json`
- `uploaded_at`
- `pdf_path`
- `extraction_json_path`
- `status`
- `participation_status`
- `filed_date`
- `ac_manager`
- `remark`
- result watcher fields listed in section 8
- `company_id` after migration

### tender_items

Purpose:

- BOQ/item rows.

Fields:

- `id`
- `tender_id`
- `part_number`
- `item_description`
- `quantity`
- `source_type`

### tender_required_documents

Purpose:

- Required documents extracted from tender.

Fields:

- `id`
- `tender_id`
- `label`

### tender_attachments

Purpose:

- Additional uploaded files against a tender.

Fields:

- `id`
- `tender_id`
- `original_file_name`
- `content_type`
- `file_size`
- `file_data`
- `uploaded_at`

### company_profile

Purpose:

- Company identity, bank details, signatory, and branding assets.

Fields include:

- company identity fields
- bank/signatory fields
- `stamp_file_path`
- `signature_file_path`
- `company_logo_data/content_type/original_name`
- `document_logo_data/content_type/original_name`
- `letterhead_data/content_type/original_name`
- `header_image_data/content_type/original_name`
- `footer_image_data/content_type/original_name`
- `stamp_data/content_type/original_name`
- `signature_data/content_type/original_name`
- `company_id` after migration

### company_capability_profile

Purpose:

- Tender evaluation profile.

Fields:

- `year_established`
- `core_business`
- `product_categories`
- `brands_handled`
- `industries_served`
- `turnover_range`
- `typical_tender_value_range`
- `import_capability`
- `export_capability`
- `oem_support_available`
- `oem_authorizations`
- `engineering_support`
- `installation_support`
- `gst_available`
- `pan_available`
- `msme_available`
- `itr_available`
- `bank_documents_available`
- `letterhead_available`
- `stamp_available`
- `signature_available`
- `psu_experience`
- `government_experience`
- `major_customers`
- `past_orders_projects`
- `company_id` after migration

### company_documents

Purpose:

- Document library.

Fields:

- `id`
- `document_name`
- `category`
- `financial_year`
- `brand_oem`
- `file_path`
- `tags`
- `uploaded_at`
- `file_data`
- `content_type`
- `original_name`
- `file_size`

Needs verification:

- Explicit `company_id` scoping was not found in phase-1 scoped table list for this table.

### tender_prepared_documents

Purpose:

- Missing/matched/generated document plan per tender.

Fields:

- `id`
- `tender_id`
- `required_document_label`
- `document_name`
- `source_type`
- `source_document_id`
- `generated_file_path`
- `status`
- `remarks`
- `recommended_action`
- `created_at`
- `generated_file_data`
- `generated_file_name`

### tender_notifications

Purpose:

- Result/RA notifications.

Fields:

- `id`
- `tender_id`
- `title`
- `message`
- `type`
- `notification_type`
- `is_read`
- `is_valid`
- `invalidated_at`
- `invalidation_reason`
- `created_at`
- `company_id` after migration

### result_watcher_run_logs

Purpose:

- Local/server watcher run summary.

Fields:

- `id`
- `started_at`
- `finished_at`
- `total_pending`
- `checked`
- `results_found`
- `not_available`
- `failed`
- `skipped`
- `run_source`
- `created_at`

### gem_result_check_history

Purpose:

- Historical status checks and old/new transitions.

Fields:

- `id`
- `tender_id`
- `gem_bid_number`
- old/new result status and flags
- `old_urls`
- `new_urls`
- `reason`
- `confidence`
- `raw_gem_response`
- `checked_at`
- `source`
- `company_id` after migration

### tender_result_summary

Purpose:

- Current parsed result summary.

Fields:

- `tender_id`
- `gem_bid_number`
- `current_source_type`
- `current_bid_or_ra_number`
- bid/RA result and stage flags
- our company participation/status/rank/price
- `last_checked_at`
- `last_successful_parse_at`
- `parse_error`
- `result_url`

### tender_result_participants

Purpose:

- Parsed participants rows.

Fields:

- `tender_id`
- `source_type`
- `source_number`
- `seller_name`
- `offered_item`
- `make`
- `model`
- `title`
- `participated_on`
- `mse_mii_status`
- `status`
- `raw_data`

### tender_technical_evaluation

Purpose:

- Parsed technical evaluation rows.

Fields:

- `tender_id`
- `source_type`
- `source_number`
- `seller_name`
- `offered_item`
- `make`
- `model`
- `title`
- `participated_on`
- `mse_mii_status`
- `technical_status`
- `raw_data`

### tender_financial_evaluation

Purpose:

- Parsed financial evaluation rows.

Fields:

- `tender_id`
- `source_type`
- `source_number`
- `seller_name`
- `offered_item`
- `total_price`
- `rank`
- `financial_status`
- `raw_data`

### tender_result_detail_history

Purpose:

- Result detail parse history and stage transitions.

Fields:

- `tender_id`
- `source_type`
- `source_number`
- `old_stage`
- `new_stage`
- `changes_detected`
- `raw_summary`
- `checked_at`
- `error`

### government_portals / tender_portals

Purpose:

- Portal credentials and migrated newer portal table.

`government_portals` fields:

- `id`
- `name`
- `url`
- `username`
- `password_encrypted`
- `notes`
- `created_at`

`tender_portals` fields:

- `id`
- `portal_name`
- `portal_url`
- `login_id`
- `encrypted_password`
- `notes`
- `status`
- `created_at`
- `updated_at`

### gem_keywords

Purpose:

- Legacy/server GeM watcher keyword master list.

Fields:

- `id`
- `keyword`
- `is_active`
- `created_at`
- `updated_at`
- `last_checked_at`

### gem_scan_runs

Purpose:

- Legacy/server GeM watcher scan runs.

Fields:

- `scan_target_date`
- `started_at`
- `finished_at`
- `status`
- counters for found/new/duplicates/skipped/approved/review/rejected/errors
- `current_step`
- `error_message`
- `error_stack`
- `company_id` after migration

### gem_candidate_tenders

Purpose:

- Legacy/server GeM watcher extracted/evaluated candidates.

Fields include:

- `gem_bid_no`
- `matched_keywords`
- `matched_brands`
- `matched_products`
- title/org/dept/quantity/dates
- URLs
- `pdf_file_id`
- `tender_id`
- scan/extraction/evaluation status fields
- keyword fit score/decision/reason
- negative keyword fields
- `requires_full_evaluation`
- `scan_run_id`
- `evaluation_score`
- `evaluation_reason`
- `evaluation_json`
- `status`
- timestamps
- `company_id` after migration

### gem_search_keywords

Purpose:

- Current local-agent keyword table.

Fields:

- `id`
- `keyword`
- `active`
- `created_at`
- `updated_at`
- `last_scanned_at`
- `company_id` after migration

### gem_search_settings

Purpose:

- Key/value scan settings.

Fields:

- `key`
- `value`
- `updated_at`

Needs verification:

- Scoping model for settings is unclear; table is key/value and not listed as company-scoped in migration.

### gem_discovered_tenders

Purpose:

- Current local-agent discovered tenders.

Fields:

- `gem_bid_number`
- `keyword_matched`
- raw title/org/dept/quantity
- bid start/end
- `gem_pdf_url`
- `stored_pdf_file_id`
- `raw_gem_data`
- `extracted_data`
- `evaluation_score`
- `evaluation_decision`
- `evaluation_reason`
- `action_taken`
- `all_tender_id`
- `source`
- timestamps
- `company_id` after migration

### gem_run_requests

Purpose:

- On-demand local agent run requests.

Fields:

- `id`
- `keyword`
- `status`
- `summary`
- `requested_at`
- `claimed_at`
- `completed_at`
- `company_id` after migration

### tender_evaluations

Purpose:

- Candidate evaluation history.

Fields:

- `candidate_id`
- `score`
- `rating_label`
- `matched_brands`
- `matched_products`
- `negative_keywords`
- keyword fit fields
- `evaluation_stage`
- `eligibility_status`
- `rejection_reason`
- `evaluation_json`
- `created_at`
- `company_id` after migration

### Migrations

Migration file:

- `tender_app/migrations/0001_add_company_scoping.sql`

Important note:

- Runtime `database.init_db()` has its own guarded additive migration logic.
- The SQL file includes manual/company-scoping statements but comments warn some default id values must be filled after selecting default company id.

## 13. APIs

All main APIs are in:

- `tender_app/main.py`
- `tender_app/gem_watcher/routes.py`

Company-scoped means backend uses session/default/local-agent token company where code passes `company_id`. Some legacy endpoints still need audit.

### Health / Extension

- `GET /api/health`
  - Health/env debug.
  - Not company-scoped.
  - UI/admin/debug.

- `GET /api/extensions/gem-bidplus-autofill.zip`
  - Download extension bundle.
  - Not company-scoped.
  - UI/user.

### Tender Upload / CRUD / Evaluation

- `POST /api/tenders/upload`
  - Upload one PDF and extract.
  - Needs company-scope verification; saves uploaded file with backend helper defaults unless request company passed.
  - UI.

- `POST /api/tenders/bulk-upload`
  - Upload multiple PDFs and extract each.
  - Needs company-scope verification.
  - UI.

- `POST /api/tenders`
  - Create tender manually/from extracted payload.
  - Needs company-scope verification; current route does not take request in inspected snippet.
  - UI.

- `POST /api/tenders/evaluate-extracted`
  - Evaluate unsaved extracted tender payload.
  - Uses capability profile, needs company-scope verification.
  - UI.

- `GET /api/tenders`
  - List tenders for current company.
  - Company-scoped.
  - UI/result watcher agent may use unscoped all-tenders fetch in local watcher helper.

- `GET /api/tenders/{tender_id}`
  - Get tender.
  - Needs company-scope verification; route snippet did not pass request company.
  - UI.

- `PATCH /api/tenders/{tender_id}/boq-items`
  - Update BOQ items.
  - UI.

- `POST /api/tenders/{tender_id}/evaluate`
  - Evaluate saved tender.
  - UI.

- `PUT /api/tenders/{tender_id}`
  - Update tender.
  - UI.

- `DELETE /api/tenders/{tender_id}`
  - Delete tender.
  - UI/admin.
  - Needs full production verification for child rows.

- `PATCH /api/tenders/{tender_id}/record-fields`
  - Update operational fields such as filed date/account manager/remarks.
  - UI.

- `PATCH /api/tenders/{tender_id}/status`
  - Update participation status.
  - UI.

### Files / Attachments

- `GET /files/{file_id}`
  - Download/serve uploaded file.
  - UI/backend.

- `GET /api/tenders/{tender_id}/pdf`
  - Get tender PDF.
  - UI.

- `POST /api/tenders/{tender_id}/attachments`
  - Upload attachment files.
  - UI.

- `GET /api/tenders/{tender_id}/attachments`
  - List attachments.
  - UI.

- `GET /api/attachments/{attachment_id}/download`
  - Download attachment.
  - UI.

### Admin / Recovery

- `POST /api/admin/clear-tender-data`
  - Clear tender data.
  - Admin.
  - Destructive; use with caution.

- `POST /api/admin/recover-tenders`
  - Re-extract and restore tender rows from PDFs still in PostgreSQL.
  - Admin/recovery.

### Result Watcher

- `POST /api/tenders/{tender_id}/check-result`
  - Check one tender result.
  - UI/admin.

- `POST /api/tenders/{tender_id}/ingest-gem-result`
  - Ingest result from watcher/extension/agent.
  - Watcher/admin.

- `POST /api/tenders/{tender_id}/ingest-gem-result-error`
  - Ingest watcher error.
  - Watcher/admin.

- `POST /api/tenders/{tender_id}/ingest-gem-result-details`
  - Ingest parsed participants/technical/financial details.
  - Watcher/admin.

- `GET /api/tenders/{tender_id}/result-details`
  - Return parsed result details.
  - UI.

- `POST /api/result-watcher/run`
  - Run server-side eligible checks.
  - Admin/watcher.

- `GET /api/result-watcher/pending`
  - Pending tenders for local watcher.
  - Watcher/admin.

- `GET /api/result-watcher/recheck-targets`
  - Ended/recheck target tenders for repair/detail parse.
  - Watcher/admin.

- `POST /api/result-watcher/run-log`
  - Save local watcher run log.
  - Watcher/admin.

- `GET /api/result-watcher/summary`
  - Summary counters/history.
  - UI/admin.

- `GET /api/tender-notifications`
  - List notifications.
  - Company-scoped by request where implemented.
  - UI.

- `PATCH /api/tender-notifications/{notification_id}/read`
  - Mark notification read.
  - UI.

- `POST /api/gem-result-debug`
  - Debug exact GeM result search.
  - UI/debug.

### Local GeM Search

- `GET /api/gem-search/stats`
  - Dashboard stats.
  - Admin/UI.

- `GET /api/gem-search/admin/config`
  - Admin scan config.
  - Admin/UI.

- `PUT /api/gem-search/admin/config`
  - Update scan date/range/mode config.
  - Admin/UI.

- `POST /api/gem-search/admin/run-local-agent`
  - Server-side attempt to run local-agent script if enabled.
  - Admin/testing.
  - Production should use installed local agent.

- `POST /api/gem-search/admin/request-run`
  - Enqueue run request for local agent.
  - Admin/UI.

- `GET /api/gem-search/admin/run-request-status`
  - Latest run request status.
  - Admin/UI.

- `GET /api/gem-search/run-request`
  - Local agent polls pending request.
  - Local-agent authenticated.
  - Company derived from token.

- `POST /api/gem-search/run-request/{request_id}/complete`
  - Local agent completes request.
  - Local-agent authenticated.
  - Company derived from token.

- `GET /api/gem-search/admin/keywords`
  - List admin keywords.
  - Admin/UI.

- `POST /api/gem-search/admin/keywords`
  - Create keyword.
  - Admin/UI.

- `PUT /api/gem-search/admin/keywords/{keyword_id}`
  - Update keyword.
  - Admin/UI.

- `DELETE /api/gem-search/admin/keywords/{keyword_id}`
  - Delete/deactivate keyword.
  - Admin/UI.

- `GET /api/gem-search/keywords`
  - Local agent fetches active keywords.
  - Local-agent authenticated.
  - Company derived from token.

- `GET /api/gem-search/config`
  - Local agent fetches scan config.
  - Local-agent authenticated.

- `GET /api/gem-search/discovered-tenders`
  - UI lists discovered tenders.
  - Company-scoped.

- `POST /api/gem-search/discovered-tender`
  - Local agent posts one discovered tender and optional PDF base64.
  - Local-agent authenticated.
  - Company derived from token.

- `POST /api/gem-search/discovered-tenders/clear-all`
  - Clear discovered tenders.
  - Admin/UI.

- `POST /api/gem-search/discovered-tenders/{gem_bid_number}/re-evaluate`
  - Re-evaluate discovered tender.
  - Admin/UI.

- `POST /api/gem-search/discovered-tenders/{gem_bid_number}/manual-insert`
  - Insert discovered tender into All Tenders.
  - Admin/UI.

- `DELETE /api/gem-search/discovered-tenders/{gem_bid_number}`
  - Delete discovered tender.
  - Admin/UI.

### Company / Documents / Profile

- `GET /api/company/profile`
  - Get company profile.
  - Company-scoped.

- `PUT /api/company/profile`
  - Update company profile.
  - Company-scoped.

- `GET /api/company/capability-profile`
  - Get capability profile.
  - Company-scoped.

- `PUT /api/company/capability-profile`
  - Update capability profile.
  - Company-scoped.

- `GET /api/company/evaluation-profile`
  - Combined evaluation profile.
  - Company-scoped.

- `POST /api/company/profile/assets/{asset_key}`
  - Upload branding asset.
  - Company-scoped.

- `GET /api/company/profile/assets/{asset_key}/file`
  - Get branding asset.
  - Company-scoped.

- `DELETE /api/company/profile/assets/{asset_key}`
  - Delete branding asset.
  - Company-scoped.

- `POST /api/company/profile/stamp`
  - Legacy stamp upload.
  - Company-scoped.

- `POST /api/company/profile/signature`
  - Legacy signature upload.
  - Company-scoped.

- `GET /api/company/profile/stamp/file`
  - Get stamp file.
  - Company-scoped.

- `GET /api/company/profile/signature/file`
  - Get signature file.
  - Company-scoped.

- `DELETE /api/company/profile/stamp`
  - Delete stamp.
  - Company-scoped.

- `DELETE /api/company/profile/signature`
  - Delete signature.
  - Company-scoped.

- `GET /api/company/documents`
  - List company documents.
  - Needs company-scope verification.

- `POST /api/company/documents`
  - Upload company document.
  - Needs company-scope verification.

- `DELETE /api/company/documents/{doc_id}`
  - Delete company document.
  - Needs company-scope verification.

- `GET /api/company/documents/{doc_id}/file`
  - View/download company document.
  - Needs company-scope verification.

### Prepared Documents

- `POST /api/tenders/{tender_id}/prepare`
  - Match required docs to library/generation/manual actions.
  - UI.

- `GET /api/tenders/{tender_id}/prepared-documents`
  - List prepared docs.
  - UI.

- `POST /api/tenders/{tender_id}/prepared-documents/{doc_id}/generate`
  - Generate AI DOCX.
  - UI.

- `POST /api/tenders/{tender_id}/prepared-documents/{doc_id}/approve`
  - Mark approved.
  - UI.

- `POST /api/tenders/{tender_id}/prepared-documents/{doc_id}/upload`
  - Manual upload for missing doc.
  - UI.

- `GET /api/tenders/{tender_id}/prepared-documents/{doc_id}/download`
  - Download generated/uploaded DOCX.
  - UI.

### Auth / SSO

- `GET /api/auth/sso`
  - Verify SSO token, set session cookie, redirect to app.
  - Portal integration.

- `GET /dev-login`
  - Local development admin login when `SSO_SECRET` is not set.
  - Disabled when `SSO_SECRET` is configured.

- `GET /api/auth/me`
  - Current session user/role.
  - UI.

- `POST /api/auth/logout`
  - Clear session cookie.
  - UI.

### Tender Portals

- `GET /api/tender-portals`
  - List portals.
  - Admin/UI.

- `POST /api/tender-portals`
  - Create portal.
  - Admin/UI.

- `PUT /api/tender-portals/{portal_id}`
  - Update portal.
  - Admin/UI.

- `POST /api/tender-portals/{portal_id}/reveal-password`
  - Decrypt/reveal password.
  - Admin/UI.

- `PATCH /api/tender-portals/{portal_id}/deactivate`
  - Deactivate portal.
  - Admin/UI.

- `PATCH /api/tender-portals/{portal_id}/activate`
  - Activate portal.
  - Admin/UI.

Legacy aliases:

- `GET /api/portals`
- `POST /api/portals`
- `PUT /api/portals/{portal_id}`
- `DELETE /api/portals/{portal_id}`
- `POST /api/portals/{portal_id}/reveal`

### SPA Routes

- `GET /gem-watcher`
- `GET /gem-candidates`
- `GET /tender-portals`
- `GET /gem-result-debug`
- `GET /gem-local-agent`
- static mount `/`

### Legacy GeM Watcher / Candidates

See section 7 for full endpoint list.

## 14. Environment Variables

### Backend / Railway

- `DATABASE_URL`
  - Required PostgreSQL connection string.

- `OPENAI_API_KEY`
  - Used by extraction fallback and document generation.

- `PORT`
  - Used by uvicorn/Railway.

- `RAILWAY_ENVIRONMENT`
  - Reported by health endpoint.

- `PORTAL_ENCRYPTION_KEY`
  - Fernet key for persistent tender portal password encryption.
  - If missing, backend generates ephemeral key and warns that passwords will be unreadable after restart.

- `WATCHER_API_KEY`
  - Bearer key for result watcher agent endpoints.

- `ENABLE_LOCAL_GEM_AGENT`
  - Must be true/1/yes/on to allow local GeM agent ingestion.

- `LOCAL_AGENT_API_KEY`
  - Global local agent key.
  - Used as fallback default-company local agent token.

- `ENABLE_SERVER_GEM_RUNNER`
  - Enables server-side scraper runner.
  - Should remain unset/false in Railway production because GeM blocks cloud IP.

- `SSO_SECRET`
  - HMAC secret for SSO/session tokens.

- `PORTAL_URL`
  - External portal URL.
  - Default in code: `https://practical-amazement-production-3539.up.railway.app`.

- `RAILWAY_PUBLIC_DOMAIN`
  - Self/public URL used by backend default.
  - Default in code: `https://tender-ai-production-5a7d.up.railway.app`.

- `AI_EXTRACT_MIN_CRITICAL_FIELDS`
  - Minimum local critical fields before skipping OpenAI extraction.
  - Default `5`.

### Backend GeM Scanner / Result Watcher

- `PLAYWRIGHT_HEADLESS`
- `PLAYWRIGHT_BROWSER_CHANNEL`
- `PLAYWRIGHT_ENABLE_EDGE_FALLBACK`
- `PLAYWRIGHT_BROWSER_EXECUTABLE`
- `GEM_SCAN_KEYWORD_DELAY_SECONDS`
- `GEM_AUTO_APPROVE_SCORE_THRESHOLD`
- `GEM_REVIEW_SCORE_THRESHOLD`
- `GEM_LLM_MIN_RULE_SCORE`
- `GEM_STRICT_LISTING_GATE`
- `GEM_KEYWORD_EVAL_MODEL`
- `GEM_KEYWORD_CONTEXT_WORDS`
- `GEM_EVAL_MODEL`
- `GEM_KEYWORD_PRE_EVAL_MODE`
- `GEM_PDF_DOWNLOAD_TIMEOUT_MS`
- `GEM_PDF_DOWNLOAD_RETRIES`
- `GEM_PDF_DOWNLOAD_RETRY_DELAY_SECONDS`
- `GEM_MIN_PDF_BYTES`
- `GEM_MIN_VALID_PDF_BYTES`
- `GEM_MIN_EXTRACTED_TEXT_CHARS`
- `GEM_PROCESSING_WORKERS`
- `GEM_MIN_DEADLINE_DAYS`

Backend result watcher:

- `GEM_RESULT_NETWORK_USER_AGENT`
- `GEM_RESULT_NETWORK_COOKIES`
- `GEM_RESULT_FILTER_TYPE`
- `GEM_ONGOING_FILTER_TYPE`
- `GEM_RESULT_SEARCH_URLS`
- `GEM_RESULT_CHECK_DELAY_MIN_SECONDS`
- `GEM_RESULT_CHECK_DELAY_MAX_SECONDS`
- `GEM_RESULT_PAGE_TIMEOUT_MS`
- `GEM_RESULT_WATCHER_HEADLESS`
- `GEM_RESULT_WATCHER_HOURS`
- `GEM_RESULT_WATCHER_TIMEZONE`
- `GEM_RESULT_WATCHER_POLL_SECONDS`

### gem-local-agent `.env`

From `gem-local-agent/.env.example`:

- `TENDER_AI_BASE_URL`
- `LOCAL_AGENT_API_KEY`
- `BROWSER_PROFILE_DIR`
- `PLAYWRIGHT_HEADLESS`
- `HEADLESS`
- `PLAYWRIGHT_BROWSER_CHANNEL`
- `RESET_BROWSER_PROFILE_ON_LOCK`
- `MAX_RESULTS_PER_KEYWORD`
- `KEYWORD_DELAY_SECONDS`
- `PAGE_DELAY_SECONDS`
- `RUN_REQUEST_POLL_SECONDS`
- `LOOP_INTERVAL_MINUTES`
- `SEARCH_DATE_MODE`
- `SEARCH_TARGET_DATE`
- `SEARCH_DATE_FROM`
- `SEARCH_DATE_TO`
- `DRY_RUN`
- `PLAYWRIGHT_EXTRA_ARGS`

### gem-result-watcher-agent `.env`

From `.env.example` and code:

- `TENDER_AI_BASE_URL`
- `WATCHER_API_KEY`
- `GEM_BASE_URL`
- `CHECK_DELAY_SECONDS`
- `MAX_TENDERS_PER_RUN`
- `LOG_LEVEL`
- `PLAYWRIGHT_HEADLESS`
- `USE_PERSISTENT_PROFILE`
- `BROWSER_PROFILE_DIR`
- `BROWSER_CHANNEL`
- `GEM_RESULT_FILTER_TYPE`
- `GEM_ONGOING_FILTER_TYPE`

## 15. Local Development Commands

### Backend

```powershell
cd C:\Users\dell\Downloads\gem_tender_tool
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:DATABASE_URL="postgresql://USER:PASSWORD@HOST:PORT/DBNAME"
$env:OPENAI_API_KEY="sk-..."
cd tender_app
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

Open:

```text
http://127.0.0.1:8000/dev-login
http://127.0.0.1:8000/
```

### Run Local GeM Search Agent

```powershell
cd C:\Users\dell\Downloads\gem_tender_tool\gem-local-agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
Copy-Item .env.example .env
notepad .env
python agent.py --test-keyword Siemens
python agent.py --search-new-tenders
python agent.py --loop --interval-minutes 30
```

### Test One Keyword With Date

```powershell
cd C:\Users\dell\Downloads\gem_tender_tool\gem-local-agent
.\.venv\Scripts\Activate.ps1
python agent.py --test-keyword Siemens --date 2026-07-10
```

### Run Result Watcher

```powershell
cd C:\Users\dell\Downloads\gem_tender_tool\gem-result-watcher-agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
Copy-Item .env.example .env
notepad .env
python watcher.py --test-bid GEM/2026/B/7586698
python watcher.py --run-now
python watcher.py --recheck-and-fix-statuses --apply
```

### Scheduler

```powershell
cd C:\Users\dell\Downloads\gem_tender_tool\gem-result-watcher-agent
.\setup_scheduler.ps1 -PythonExe "C:\Users\dell\Downloads\gem_tender_tool\gem-result-watcher-agent\.venv\Scripts\python.exe"
```

### Run Tests

```powershell
cd C:\Users\dell\Downloads\gem_tender_tool
python -m pytest tests
```

If pytest is missing:

```powershell
pip install pytest
python -m pytest tests
```

### Package Extension

```powershell
cd C:\Users\dell\Downloads\gem_tender_tool
powershell -ExecutionPolicy Bypass -File chrome_extension\package-extension.ps1
```

### Install Extension Unpacked

```text
chrome://extensions/ -> Developer mode -> Load unpacked -> C:\Users\dell\Downloads\gem_tender_tool\chrome_extension\gem-bidplus-autofill
```

## 16. Production / Railway Deployment

Deployment files:

- `Dockerfile`
- `railway.toml`
- `Procfile`
- `requirements.txt`
- `DEPLOYMENT.md`

Dockerfile:

- Base image: `mcr.microsoft.com/playwright/python:v1.49.0-jammy`
- Installs `requirements.txt`.
- Runs `python -m playwright install chromium`.
- Copies `tender_app/` and `gem-local-agent/`.
- Exposes port 8000.
- Starts:

```sh
cd tender_app && uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
```

Railway:

- `railway.toml` uses Dockerfile builder.
- Restart policy: `ON_FAILURE`, max retries 3.

Procfile:

```text
web: cd tender_app && uvicorn main:app --host 0.0.0.0 --port $PORT
```

Production required env vars:

- `DATABASE_URL`
- `OPENAI_API_KEY`
- `PORTAL_ENCRYPTION_KEY`
- `WATCHER_API_KEY`
- `ENABLE_LOCAL_GEM_AGENT=true`
- `LOCAL_AGENT_API_KEY`
- `SSO_SECRET` for real portal SSO
- `PORTAL_URL`
- `RAILWAY_PUBLIC_DOMAIN`

Production GeM note:

- GeM blocks/unreliably serves Railway/cloud IPs.
- Do not depend on server-side GeM scraping in production.
- Keep `ENABLE_SERVER_GEM_RUNNER` unset/false.
- Install `gem-local-agent` on an office PC for search/discovery.
- Install `gem-result-watcher-agent` on an office PC for result checking.

Deployment workflow from `DEPLOYMENT.md`:

- `main` is production only.
- Feature work should happen on separate branches.
- Merge to `main` only after local verification.
- Railway should deploy from `main`.
- Before production testing:
  - verify GitHub `main`,
  - verify Railway active commit,
  - clear cache/incognito.

Database backup notes:

- Before applying significant schema/data changes in production, take a PostgreSQL backup from Railway.
- `init_db()` applies additive migrations on startup, so deploying code can mutate schema.
- `PORTAL_ENCRYPTION_KEY` must be stable before relying on stored portal passwords.

## 17. Known Issues / Bugs

Confirmed or code-visible:

- GeM blocks or challenges Railway/cloud IPs.
- Local agents are required for reliable GeM search/result production flows.
- `evaluation.py` summary still references FIAPL.
- `gem-result-watcher-agent/watcher.py` has Fidus-specific `OUR_COMPANY_ALIASES`.
- `tender_app/static/logo FIAPL.png` remains in repo; verify if used.
- Full SaaS session company id is not complete: `_make_session()` does not persist company id from SSO payload.
- `company_documents` scoping needs audit/verification.
- `gem_search_settings` scoping needs audit/verification.
- Some API routes still do not pass `request` or `company_id`; full multi-tenant audit is pending.
- Result detail parsing depends on GeM DOM and table text; changes on GeM can break parsing.
- Result watcher false positives have been addressed by safer notification rules and tests, but production monitoring is still required.
- Delete tender child-row issue is marked as pending verification: code has child cleanup logic, but all real child combinations need production/local tests.
- Technical/financial result details can be partial when GeM page has headings but no rows; tests now ensure headings alone do not advance stage.
- Extension may need updates if GeM DOM/JS ids change.
- Portal passwords become unreadable after restart if `PORTAL_ENCRYPTION_KEY` changes or was previously ephemeral.
- Server-side GeM watcher still exists and may confuse users if hidden routes are opened; production docs should steer users to local agent.

Needs verification:

- Exact local-agent discovered action strings in all duplicate/evaluated/manual insert flows.
- Reports calculations and export behavior in live UI.
- Complete Test Company 2 isolation.
- Railway active deployment branch/commit.

## 18. Completed Work

- [x] FastAPI backend created.
- [x] Static SPA frontend created.
- [x] PostgreSQL database layer created.
- [x] PDF upload stored in DB.
- [x] Bulk PDF upload implemented.
- [x] Hybrid tender extraction implemented.
- [x] Deterministic GeM parser integration added.
- [x] Local fallback extraction added.
- [x] OpenAI extraction fallback added.
- [x] Rule-based tender evaluation added.
- [x] Company capability profile added.
- [x] Company profile added.
- [x] Company branding assets added.
- [x] Stamp/signature upload and embedding support added.
- [x] Document library added.
- [x] Required document matcher added.
- [x] AI DOCX generator added.
- [x] Prepared document workflow added.
- [x] All Tenders register added.
- [x] Tender operational statuses added.
- [x] Result watcher fields added to tenders.
- [x] Result watcher history table added.
- [x] Result watcher run logs added.
- [x] Result detail tables added.
- [x] Result notifications added.
- [x] Local result watcher agent added.
- [x] Result watcher repair/recheck commands added.
- [x] Tests for result watcher parsing/stage/downgrade behavior added.
- [x] Local GeM search agent added.
- [x] Local agent run request polling added.
- [x] Local agent PDF upload/discovery ingestion added.
- [x] GeM search keyword/config/discovered tender APIs added.
- [x] Legacy server-side GeM watcher/candidate pipeline added.
- [x] Chrome/Edge GeM autofill extension added.
- [x] Extension download endpoint added.
- [x] Tender portal credential manager added.
- [x] Fernet portal password encryption added.
- [x] SSO/dev-login session flow added.
- [x] Companies table added.
- [x] Local agents table added.
- [x] Default company fallback added.
- [x] Additive company-scoping migration added.
- [x] Local agent token to company mapping added.
- [x] Deployment files added.
- [x] Deployment workflow documentation added.

## 19. Pending Work / Roadmap

### SaaS / Fidus360 Integration

- [ ] Preserve `company_id`/`companyId` from SSO token into session cookie.
- [ ] Confirm Fidus360 portal token payload format.
- [ ] Map portal users, roles, and companies.
- [ ] Add company selector/admin if required.
- [ ] Add company onboarding flow.
- [ ] Add subscription/app access controls.
- [ ] Add per-company local agent token generation UI.
- [ ] Audit every API for company scoping.
- [ ] Audit every DB function for company scoping.
- [ ] Scope `company_documents`.
- [ ] Scope or redesign `gem_search_settings`.
- [ ] Make result watcher pending/recheck company-aware.
- [ ] Replace Fidus hardcoded company aliases with company profile aliases.
- [ ] Remove/replace FIAPL/FIDUS hardcoded UI text/assets.
- [ ] Test with Test Company 2 end-to-end.

### Native Module Migration

- [ ] Split large `index.html` into maintainable frontend modules or migrate to a frontend framework.
- [ ] Split `main.py` routes into routers by domain.
- [ ] Split `database.py` into schema/migrations/repositories or use a migration tool.
- [ ] Replace startup DDL migration with explicit migration workflow for production.

### Local Agent Migration / Hardening

- [ ] Add local agent registration/admin UI.
- [ ] Generate per-company tokens securely.
- [ ] Add heartbeat/status UI for local agents.
- [ ] Add local agent version reporting.
- [ ] Add backoff/retry visibility.
- [ ] Add Windows installer docs/screenshots.
- [ ] Add signed/package installer if needed.
- [ ] Ensure local agent never defaults to `DRY_RUN=true` in production config.

### Result Watcher Hardening

- [ ] Source company aliases from backend/company profile.
- [ ] Add browser session health checks.
- [ ] Add alert when watcher has not run recently.
- [ ] Improve result detail parser with more GeM layout samples.
- [ ] Add tests for more RA and financial table variants.
- [ ] Verify notifications on real GeM pages.
- [ ] Add UI for review-required downgrades/warnings.

### Delete Endpoint / Data Cleanup

- [ ] Test deleting tenders with attachments.
- [ ] Test deleting tenders with prepared documents.
- [ ] Test deleting tenders with notifications.
- [ ] Test deleting tenders with result details/history.
- [ ] Test deleting tenders linked from discovered/candidate tables.
- [ ] Add regression tests.

### Production Verification

- [ ] Verify Railway deploy from `main`.
- [ ] Verify current branch merge plan.
- [ ] Verify all required env vars in Railway.
- [ ] Verify `PORTAL_ENCRYPTION_KEY` is stable.
- [ ] Verify local agent to production.
- [ ] Verify result watcher agent to production.
- [ ] Verify extension against production URL.
- [ ] Verify backup/restore procedure.

### Notifications / Reports

- [ ] Add email/WhatsApp/Teams notifications if required.
- [ ] Add daily digest for new discovered tenders.
- [ ] Add result available digest.
- [ ] Harden Reports page and document exact metrics.
- [ ] Add export/report APIs if current reports are frontend-only.

## 20. Handoff Instructions

Repo path:

```text
C:\Users\dell\Downloads\gem_tender_tool
```

Current important branch:

```text
feature/fidus360-saas-agent
```

Do not assume production is this branch. `DEPLOYMENT.md` says production should deploy from `main`.

First commands for a new developer/AI assistant:

```powershell
cd C:\Users\dell\Downloads\gem_tender_tool
git branch --show-current
git status --short
rg --files
```

Backend run:

```powershell
cd C:\Users\dell\Downloads\gem_tender_tool
.\.venv\Scripts\Activate.ps1
$env:DATABASE_URL="postgresql://USER:PASSWORD@HOST:PORT/DBNAME"
cd tender_app
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

Local search agent run:

```powershell
cd C:\Users\dell\Downloads\gem_tender_tool\gem-local-agent
.\.venv\Scripts\Activate.ps1
python agent.py --test-keyword Siemens
```

Result watcher run:

```powershell
cd C:\Users\dell\Downloads\gem_tender_tool\gem-result-watcher-agent
.\.venv\Scripts\Activate.ps1
python watcher.py --test-bid GEM/2026/B/7586698
python watcher.py --run-now
```

Tests:

```powershell
cd C:\Users\dell\Downloads\gem_tender_tool
python -m pytest tests
```

Architecture summary:

- FastAPI backend in `tender_app/main.py`.
- DB schema/helpers in `tender_app/database.py`.
- Static SPA in `tender_app/static/index.html`.
- Extraction/evaluation/document workflows in `ai_extractor.py`, `evaluation.py`, `doc_matcher.py`, `doc_generator.py`.
- Result watcher server logic in `tender_app/result_watcher.py`.
- Current production GeM search through `gem-local-agent`.
- Current production result checking through `gem-result-watcher-agent`.
- Chrome extension supports user-browser GeM search/result handoff.
- Railway hosts app; local PCs access GeM.

Do not break:

- Uploaded file byte storage in PostgreSQL.
- Existing default-company standalone fallback.
- Local agent bearer-token auth.
- Result watcher safe downgrade protection.
- Notification rule: do not notify result-live from status code alone.
- Portal password encryption key stability.
- Existing `main` production workflow.
- User data in local `company_docs`, generated outputs, uploads, logs, and browser profiles.

Next recommended task:

1. Complete SaaS company-id propagation from Fidus360 SSO into Tender AI session.
2. Audit and fix company scoping for every API/table, especially company documents, settings, result watcher targets, and legacy paths.
3. Replace remaining FIAPL/FIDUS hardcoded strings/aliases/assets with company profile values.
4. Verify with two companies using separate local-agent tokens.
5. Add regression tests for data isolation and delete-tender child cleanup.
