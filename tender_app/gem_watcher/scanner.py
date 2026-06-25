"""GeM Tender Watcher scanner.

Searches https://bidplus.gem.gov.in/all-bids for configured keywords, keeps only
bids whose Bid Start Date matches the requested scan_target_date, downloads the
matching tender PDFs, runs them through the existing extraction/evaluation
pipeline, and auto-approves into the main `tenders` table anything scoring
>= GEM_AUTO_APPROVE_SCORE_THRESHOLD.

`run_scan()` has no FastAPI dependency so it can be triggered from the API
today (via BackgroundTasks) and from a cron job / CLI later:

    python -m gem_watcher.scanner --date 2026-06-24

Bid listings are read from GeM's own JSON search endpoint (all-bids-data),
the same one the website's search box calls. This filters by keyword
server-side, paginates reliably, and returns each bid's exact start/end dates
and the document id — far more robust than scraping the JS-rendered cards. We
still use a Playwright browser purely to obtain a valid session + CSRF token
and to download the PDFs.
"""
import json
import os
import tempfile
import threading
import time
import urllib.parse
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path

import ai_extractor
import database
from gem_watcher.evaluator import evaluate_gem_candidate

GEM_ALL_BIDS_URL = "https://bidplus.gem.gov.in/all-bids"
GEM_DATA_URL = "https://bidplus.gem.gov.in/all-bids-data"
GEM_PDF_URL_TEMPLATE = "https://bidplus.gem.gov.in/showbidDocument/{doc_id}"
GEM_RA_PDF_URL_TEMPLATE = "https://bidplus.gem.gov.in/showradocumentPdf/{doc_id}"
EXTRACTIONS_DIR = Path(__file__).resolve().parent.parent / "extractions"

KEYWORD_DELAY_SECONDS = float(os.environ.get("GEM_SCAN_KEYWORD_DELAY_SECONDS", "3"))
AUTO_APPROVE_THRESHOLD = int(os.environ.get("GEM_AUTO_APPROVE_SCORE_THRESHOLD", "8"))
HEADLESS = os.environ.get("PLAYWRIGHT_HEADLESS", "true").strip().lower() != "false"
PAGE_SIZE = 10  # GeM returns 10 docs per page
PDF_DOWNLOAD_TIMEOUT_MS = int(os.environ.get("GEM_PDF_DOWNLOAD_TIMEOUT_MS", "30000"))
PDF_DOWNLOAD_RETRIES = int(os.environ.get("GEM_PDF_DOWNLOAD_RETRIES", "3"))
PDF_DOWNLOAD_RETRY_DELAY_SECONDS = float(os.environ.get("GEM_PDF_DOWNLOAD_RETRY_DELAY_SECONDS", "2"))
MIN_PDF_BYTES = int(os.environ.get("GEM_MIN_PDF_BYTES", "1024"))
PROCESSING_WORKERS = max(1, int(os.environ.get("GEM_PROCESSING_WORKERS", "3")))
# Sort newest-start-date first so that, for a given scan date, the matching bids
# appear early and we can stop paging as soon as we pass below the target date.
GEM_SORT = "Bid-Start-Date-Latest"

_scan_lock = threading.Lock()

# Cooperative cancellation. The scan runs in the same process as the API (a
# FastAPI BackgroundTask), so a Stop request just adds the run_id here and the
# scan loop checks it between keywords and between candidates, halting cleanly.
_cancel_lock = threading.Lock()
_cancelled_runs = set()


def request_cancel(run_id: int):
    with _cancel_lock:
        _cancelled_runs.add(run_id)


def _is_cancelled(run_id: int) -> bool:
    with _cancel_lock:
        return run_id in _cancelled_runs


def _clear_cancel(run_id: int):
    with _cancel_lock:
        _cancelled_runs.discard(run_id)


class GemBlockedError(Exception):
    pass


class _ScanCancelled(Exception):
    """Raised internally when a Stop request is detected mid-scan."""
    pass


def _first(value):
    """GeM's Solr-backed JSON returns most fields as single-element arrays."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _parse_iso_date(value):
    """final_start_date_sort looks like '2026-06-19T17:08:42Z'. GeM labels its
    IST timestamps with a 'Z'; the time-of-day matches the site's own display,
    so the calendar date is simply the first 10 characters (no TZ conversion)."""
    s = _first(value)
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _capture_csrf(page):
    """Load the all-bids page and capture the CSRF token from the site's own
    all-bids-data request (the token is appended to that POST body)."""
    holder = {}

    def on_request(req):
        if req.url == GEM_DATA_URL and req.method == "POST" and "token" not in holder:
            pd = req.post_data or ""
            idx = pd.find("csrf_bd_gem_nk=")
            if idx != -1:
                holder["token"] = pd[idx + len("csrf_bd_gem_nk="):].split("&")[0].strip()

    page.on("request", on_request)
    page.goto(GEM_ALL_BIDS_URL, timeout=40000)
    page.wait_for_load_state("networkidle", timeout=20000)
    for _ in range(20):  # the listing AJAX fires shortly after load
        if "token" in holder:
            break
        page.wait_for_timeout(300)
    try:
        page.remove_listener("request", on_request)
    except Exception:
        pass
    return holder.get("token")


def _response_preview_bytes(body: bytes, limit: int = 180) -> str:
    if not body:
        return ""
    try:
        return body[:limit].decode("utf-8", errors="replace").replace("\n", " ").strip()
    except Exception:
        return ""


def _describe_response(resp) -> str:
    headers = resp.headers or {}
    content_type = headers.get("content-type", "unknown")
    try:
        body = resp.body()
    except Exception:
        body = b""
    preview = _response_preview_bytes(body)
    details = [
        f"HTTP {resp.status}",
        f"url={getattr(resp, 'url', GEM_DATA_URL)}",
        f"content-type={content_type}",
        f"bytes={len(body)}",
    ]
    if preview:
        details.append(f"preview={preview[:140]}")
    return ", ".join(details)


def _fetch_bids(page, keyword, csrf, page_no):
    """POST to GeM's JSON search endpoint. Returns (docs, num_found)."""
    body_json = {
        "param": {"searchBid": keyword, "searchType": "fullText"},
        "filter": {"bidStatusType": "ongoing_bids", "byType": "all", "highBidValue": "",
                   "byEndDate": {"from": "", "to": ""}, "sort": GEM_SORT},
    }
    if page_no > 1:
        body_json = {"page": page_no, **body_json}
    body = "payload=" + urllib.parse.quote(json.dumps(body_json)) + "&csrf_bd_gem_nk=" + (csrf or "")
    resp = page.request.post(GEM_DATA_URL, data=body, headers={
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
    }, fail_on_status_code=False)
    if resp.status != 200:
        raise GemBlockedError(
            "GeM all-bids-data unexpected response: "
            + _describe_response(resp)
            + " (possible session/CSRF issue, block page, or GeM site change)."
        )
    try:
        data = json.loads(resp.text())
    except Exception:
        raise GemBlockedError(
            "GeM all-bids-data returned non-JSON: "
            + _describe_response(resp)
            + " (possible block/captcha page)."
        )
    inner = (data.get("response") or {}).get("response") or {}
    return inner.get("docs") or [], int(inner.get("numFound") or 0)


def _doc_to_parsed(doc):
    bid_no = _first(doc.get("b_bid_number"))
    if not bid_no:
        return None

    parent_bid_no = _first(doc.get("b_bid_number_parent"))
    parent_id = _first(doc.get("b_id_parent"))
    doc_id = _first(doc.get("id")) or _first(doc.get("b_id"))
    is_ra = "/R/" in str(bid_no).upper() or _first(doc.get("b_bid_to_ra")) == 1

    if is_ra and parent_bid_no and parent_id:
        # Bid-to-RA: the actual tender — its real GeM bidding number, BOQ and
        # eligibility document — is the PARENT bid. Use that, not the R/ auction
        # number, so the bidding number is correct and we evaluate the real PDF.
        gem_bid_no = str(parent_bid_no).strip()
        pdf_url = GEM_PDF_URL_TEMPLATE.format(doc_id=parent_id)
    elif is_ra and doc_id:
        # RA with no exposed parent — fall back to the RA document endpoint.
        gem_bid_no = str(bid_no).strip()
        pdf_url = GEM_RA_PDF_URL_TEMPLATE.format(doc_id=doc_id)
    else:
        gem_bid_no = str(bid_no).strip()
        pdf_url = GEM_PDF_URL_TEMPLATE.format(doc_id=doc_id) if doc_id else None

    qty = _first(doc.get("b_total_quantity"))
    return {
        "gem_bid_no": gem_bid_no,
        "title": _first(doc.get("b_category_name")),
        "organisation": _first(doc.get("ba_official_details_minName")),
        "department": _first(doc.get("ba_official_details_deptName")),
        "quantity": str(qty) if qty is not None else None,
        "bid_start_date": _parse_iso_date(doc.get("final_start_date_sort")),
        "bid_end_date": _parse_iso_date(doc.get("final_end_date_sort")),
        "gem_detail_url": pdf_url,
        "pdf_url": pdf_url,
    }


def _search_keyword_matches(page, keyword, csrf, scan_target_date, counters):
    """Return the parsed bids whose bid_start_date equals scan_target_date.

    Results are sorted newest-start-date first (GEM_SORT), so once we encounter
    a bid whose start date is OLDER than the target, every remaining bid is
    older too and we can stop paging immediately — usually after just the first
    page or two for a recent scan date. Updates total_found /
    skipped_wrong_start_date counters as it goes."""
    matches = []
    page_no = 1
    total = None
    collected = 0
    while page_no <= 500:  # safety ceiling (5000 bids/keyword)
        docs, num_found = _fetch_bids(page, keyword, csrf, page_no)
        if total is None:
            total = num_found
        if not docs:
            break
        stop = False
        for doc in docs:
            parsed = _doc_to_parsed(doc)
            if not parsed:
                continue
            counters["total_found"] += 1
            start = parsed["bid_start_date"]
            if start == scan_target_date:
                matches.append(parsed)
            else:
                counters["skipped_wrong_start_date"] += 1
                if start is not None and start < scan_target_date:
                    # Sorted newest-first → nothing after this can match.
                    stop = True
                    break
        collected += len(docs)
        if stop or collected >= total or len(docs) < PAGE_SIZE:
            break
        page_no += 1
    return matches


def _download_pdf_bytes(page, pdf_url):
    last_error = "unknown download error"
    for attempt in range(1, PDF_DOWNLOAD_RETRIES + 1):
        try:
            response = page.request.get(
                pdf_url,
                timeout=PDF_DOWNLOAD_TIMEOUT_MS,
                fail_on_status_code=False,
            )
            status = response.status
            headers = response.headers or {}
            content_type = headers.get("content-type", "")
            body = response.body()
            is_pdf = body.lstrip().startswith(b"%PDF-")

            if response.ok and len(body) >= MIN_PDF_BYTES and is_pdf:
                if attempt > 1:
                    print(f"[gem_watcher] PDF download recovered on retry {attempt}: {pdf_url}")
                return body, None

            preview = ""
            if body and not is_pdf:
                try:
                    preview = body[:160].decode("utf-8", errors="replace").replace("\n", " ").strip()
                except Exception:
                    preview = ""
            last_error = (
                f"attempt {attempt}/{PDF_DOWNLOAD_RETRIES}: "
                f"HTTP {status}, content-type={content_type or 'unknown'}, bytes={len(body)}, "
                f"pdf_signature={'yes' if is_pdf else 'no'}"
            )
            if preview:
                last_error += f", preview={preview[:120]}"
            print(f"[gem_watcher] {last_error} for {pdf_url}")
        except Exception as e:
            last_error = f"attempt {attempt}/{PDF_DOWNLOAD_RETRIES}: {type(e).__name__}: {e}"
            print(f"[gem_watcher] PDF download exception for {pdf_url}: {last_error}")

        if attempt < PDF_DOWNLOAD_RETRIES:
            time.sleep(PDF_DOWNLOAD_RETRY_DELAY_SECONDS * attempt)

    return None, last_error


def _extract_and_evaluate_candidate(candidate_id, capability):
    candidate = database.get_gem_candidate(candidate_id)
    if not candidate:
        return "SKIPPED"
    if candidate.get("status") in ("APPROVED", "REJECTED", "REVIEW"):
        return "SKIPPED"
    if not candidate.get("pdf_file_id"):
        database.update_gem_candidate(
            candidate_id,
            status="ERROR",
            evaluation_reason="Candidate reached extraction stage without a saved PDF",
        )
        print(f"[gem_watcher] candidate {candidate_id} {candidate.get('gem_bid_no')}: missing saved PDF file id")
        return "ERROR"

    file_row = database.get_uploaded_file(candidate["pdf_file_id"])
    if not file_row or not file_row.get("file_data"):
        database.update_gem_candidate(
            candidate_id,
            status="ERROR",
            evaluation_reason="Saved PDF file data is missing from uploaded_files",
        )
        print(f"[gem_watcher] candidate {candidate_id} {candidate.get('gem_bid_no')}: saved PDF bytes missing")
        return "ERROR"

    pdf_bytes = bytes(file_row["file_data"])
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tender_text = ""
    try:
        database.update_gem_candidate(candidate_id, status="EXTRACTING")
        tmp.write(pdf_bytes)
        tmp.flush()
        tmp.close()
        raw = ai_extractor.process_pdf(tmp.name)
        # Also pull the relevant tender text so the LLM evaluator can read
        # the eligibility/technical/OEM clauses, not just structured fields.
        try:
            pages = ai_extractor.extract_pages(tmp.name)
            relevant = ai_extractor.filter_relevant_pages(pages)
            tender_text = "\n\n".join(p["content"] for p in relevant)
        except Exception:
            tender_text = ""
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    EXTRACTIONS_DIR.mkdir(exist_ok=True)
    with open(EXTRACTIONS_DIR / f"{candidate['pdf_file_id']}.json", "w", encoding="utf-8") as f:
        json.dump(raw, f, indent=2, ensure_ascii=False)

    ti = raw.get("tender_information", {})
    tender = dict(ti)
    tender["gem_bidding_number"] = candidate["gem_bid_no"]
    tender.setdefault("tender_number", candidate["gem_bid_no"])
    tender["organization_name"] = tender.get("organization_name") or candidate.get("organisation")
    tender["department_name"] = tender.get("department_name") or candidate.get("department")
    tender["total_quantity"] = tender.get("total_quantity") or candidate.get("quantity")
    tender["boq_items"] = raw.get("items", [])
    docs = raw.get("required_documents", [])
    tender["required_documents"] = [{"label": d} if isinstance(d, str) else d for d in docs]
    tender["pdf_path"] = f"/files/{candidate['pdf_file_id']}"

    database.update_gem_candidate(candidate_id, status="EVALUATING")
    evaluation = evaluate_gem_candidate(tender, capability, candidate.get("bid_end_date"), tender_text=tender_text)
    database.save_gem_tender_evaluation(
        candidate_id, evaluation["score"], evaluation["rating_label"],
        evaluation["matched_brands"], evaluation["eligibility_status"],
        evaluation["reason"], evaluation["evaluation_json"],
    )
    database.update_gem_candidate(
        candidate_id,
        evaluation_score=evaluation["score"],
        evaluation_reason=evaluation["reason"],
        evaluation_json=evaluation["evaluation_json"],
        status="EVALUATED",
    )

    if evaluation["eligibility_status"] == "APPROVED":
        duplicate = database.find_tender_duplicate(candidate["gem_bid_no"], tender.get("tender_number"))
        if duplicate:
            database.update_gem_candidate(
                candidate_id, status="APPROVED", tender_id=duplicate["id"],
                evaluation_reason=(evaluation["reason"] or "") + " (already exists in All Tenders)",
            )
        else:
            tender_id = database.save_tender(
                {k: v for k, v in tender.items() if k not in ("boq_items", "required_documents")},
                tender["boq_items"], tender["required_documents"],
            )
            database.update_gem_candidate(candidate_id, status="APPROVED", tender_id=tender_id)
        print(f"[gem_watcher] candidate {candidate_id} {candidate.get('gem_bid_no')}: approved")
        return "APPROVED"
    elif evaluation["eligibility_status"] == "REVIEW":
        database.update_gem_candidate(candidate_id, status="REVIEW")
        print(f"[gem_watcher] candidate {candidate_id} {candidate.get('gem_bid_no')}: review")
        return "REVIEW"
    else:
        database.update_gem_candidate(candidate_id, status="REJECTED")
        print(f"[gem_watcher] candidate {candidate_id} {candidate.get('gem_bid_no')}: rejected")
        return "REJECTED"


def _process_new_candidates(page, candidate_ids, counters, error_messages, run_id=None):
    capability = database.get_company_capability_profile()
    ready_candidate_ids = []

    # Stage 1: download PDFs first for every pending candidate so one slow
    # extraction/evaluation step doesn't stop later rows from at least getting
    # their PDF fetched and saved.
    for candidate_id in sorted(candidate_ids):
        if run_id is not None and _is_cancelled(run_id):
            # Stop requested — leave the rest queued (status FOUND); re-running
            # the same scan date later will pick them up.
            raise _ScanCancelled()
        try:
            candidate = database.get_gem_candidate(candidate_id)
            if not candidate:
                continue
            if candidate.get("status") in ("APPROVED", "REJECTED", "REVIEW"):
                continue
            if candidate.get("pdf_file_id"):
                ready_candidate_ids.append(candidate_id)
                continue

            pdf_url = candidate.get("pdf_url") or candidate.get("gem_detail_url")
            if not pdf_url:
                database.update_gem_candidate(
                    candidate_id,
                    status="ERROR",
                    evaluation_reason="Missing GeM PDF URL on candidate row",
                )
                print(f"[gem_watcher] candidate {candidate_id} {candidate.get('gem_bid_no')}: missing PDF URL")
                continue

            database.update_gem_candidate(candidate_id, status="DOWNLOADING_PDF", evaluation_reason=None)
            print(f"[gem_watcher] candidate {candidate_id} {candidate.get('gem_bid_no')}: downloading PDF")
            pdf_bytes, download_error = _download_pdf_bytes(page, pdf_url)
            if not pdf_bytes:
                database.update_gem_candidate(
                    candidate_id,
                    status="ERROR",
                    evaluation_reason=f"PDF download failed: {download_error}",
                )
                print(f"[gem_watcher] candidate {candidate_id} {candidate.get('gem_bid_no')}: PDF download failed ({download_error})")
                continue

            file_id = str(uuid.uuid4())
            database.save_uploaded_file(
                file_id=file_id,
                file_name=f"{file_id}_{candidate['gem_bid_no']}.pdf",
                original_name=f"{candidate['gem_bid_no']}.pdf",
                content_type="application/pdf",
                file_size=len(pdf_bytes),
                file_data=pdf_bytes,
                file_category="gem_candidate_pdf",
            )
            database.update_gem_candidate(candidate_id, pdf_file_id=file_id, status="PDF_DOWNLOADED")
            ready_candidate_ids.append(candidate_id)
            print(f"[gem_watcher] candidate {candidate_id} {candidate.get('gem_bid_no')}: PDF saved ({len(pdf_bytes)} bytes)")

        except _ScanCancelled:
            raise
        except Exception as e:
            error_messages.append(f"candidate {candidate_id}: {e}")
            try:
                database.update_gem_candidate(candidate_id, status="ERROR", evaluation_reason=str(e))
            except Exception:
                pass

    # Stage 2: extract and evaluate from already-saved PDFs using a controlled
    # worker pool. The browser/session-bound GeM download step above stays
    # single-threaded; only the local PDF parsing + AI extraction/evaluation is
    # parallelised here.
    for batch_start in range(0, len(ready_candidate_ids), PROCESSING_WORKERS):
        if run_id is not None and _is_cancelled(run_id):
            raise _ScanCancelled()

        batch_ids = ready_candidate_ids[batch_start:batch_start + PROCESSING_WORKERS]
        with ThreadPoolExecutor(max_workers=PROCESSING_WORKERS) as executor:
            future_map = {
                executor.submit(_extract_and_evaluate_candidate, candidate_id, capability): candidate_id
                for candidate_id in batch_ids
            }
            for future in as_completed(future_map):
                candidate_id = future_map[future]
                try:
                    outcome = future.result()
                    if outcome == "APPROVED":
                        counters["approved_count"] += 1
                    elif outcome == "REJECTED":
                        counters["rejected_count"] += 1
                except Exception as e:
                    error_messages.append(f"candidate {candidate_id}: {e}")
                    try:
                        database.update_gem_candidate(candidate_id, status="ERROR", evaluation_reason=str(e))
                    except Exception:
                        pass

                # Flush live progress so the scan log reflects completed worker
                # results during large backlogs instead of only at the end.
                if run_id is not None:
                    try:
                        database.update_gem_scan_run(
                            run_id,
                            approved_count=counters["approved_count"],
                            rejected_count=counters["rejected_count"],
                        )
                    except Exception:
                        pass


def start_scan(scan_target_date):
    """Validate input and create the scan_run row synchronously, so the caller
    (the HTTP endpoint) gets a run_id to poll immediately. The actual browser
    work happens in execute_scan(), meant to be run in a background task."""
    if isinstance(scan_target_date, str):
        scan_target_date = datetime.strptime(scan_target_date, "%Y-%m-%d").date()
    if scan_target_date > date.today():
        raise ValueError("scan_target_date cannot be in the future")
    if database.is_gem_scan_running():
        raise RuntimeError("A scan is already running")
    keywords = database.list_gem_keywords(active_only=True)
    run_id = database.create_gem_scan_run(scan_target_date, len(keywords))
    return run_id, scan_target_date


def execute_scan(run_id: int, scan_target_date):
    if not _scan_lock.acquire(blocking=False):
        database.update_gem_scan_run(run_id, finished_at=datetime.now(), status="FAILED",
                                      error_message="A scan is already running")
        return run_id

    counters = {"total_found": 0, "new_found": 0, "skipped_wrong_start_date": 0,
                "approved_count": 0, "rejected_count": 0}
    error_messages = []
    status = "FAILED"

    try:
        keywords = database.list_gem_keywords(active_only=True)

        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=HEADLESS)
            try:
                page = browser.new_page()
                csrf = _capture_csrf(page)
                if not csrf:
                    raise GemBlockedError("Could not obtain a GeM session/CSRF token (possible block or site change).")

                new_candidate_ids = []
                for kw_row in keywords:
                    if _is_cancelled(run_id):
                        raise _ScanCancelled()
                    keyword = kw_row["keyword"]
                    try:
                        try:
                            matches = _search_keyword_matches(page, keyword, csrf, scan_target_date, counters)
                        except GemBlockedError as e:
                            # GeM sometimes invalidates the session/CSRF token mid-run.
                            # Refresh the listing page once and retry the same keyword
                            # before marking the whole scan blocked.
                            print(f"[gem_watcher] keyword {keyword}: initial GeM fetch failed; refreshing session once. {e}")
                            csrf = _capture_csrf(page)
                            if not csrf:
                                raise GemBlockedError(
                                    f"{e} Follow-up refresh could not obtain a new CSRF token."
                                )
                            matches = _search_keyword_matches(page, keyword, csrf, scan_target_date, counters)
                        for parsed in matches:
                            candidate_id = database.upsert_gem_candidate(
                                parsed["gem_bid_no"], keyword,
                                {
                                    "title": parsed.get("title"),
                                    "organisation": parsed.get("organisation"),
                                    "department": parsed.get("department"),
                                    "quantity": parsed.get("quantity"),
                                    "bid_start_date": parsed.get("bid_start_date"),
                                    "bid_end_date": parsed.get("bid_end_date"),
                                    "gem_detail_url": parsed.get("gem_detail_url"),
                                    "pdf_url": parsed.get("pdf_url"),
                                },
                            )
                            new_candidate_ids.append(candidate_id)
                        database.touch_gem_keyword_checked(kw_row["id"])
                    except GemBlockedError:
                        raise
                    except Exception as e:
                        error_messages.append(f"{keyword}: {e}")
                    time.sleep(KEYWORD_DELAY_SECONDS)

                counters["new_found"] = len(set(new_candidate_ids))
                # Flush search-phase counts so the logs table shows them while
                # the (slower) evaluation phase runs.
                database.update_gem_scan_run(
                    run_id,
                    total_found=counters["total_found"],
                    new_found=counters["new_found"],
                    skipped_wrong_start_date=counters["skipped_wrong_start_date"],
                )
                _process_new_candidates(page, set(new_candidate_ids), counters, error_messages, run_id=run_id)
                status = "COMPLETED"
            finally:
                browser.close()

    except _ScanCancelled:
        status = "CANCELLED"
        error_messages.append("Scan stopped by admin. Unprocessed candidates remain queued — re-run the same date to continue.")
    except GemBlockedError as e:
        status = "BLOCKED"
        error_messages.append(str(e))
    except Exception as e:
        status = "FAILED"
        error_messages.append(str(e))
    finally:
        _scan_lock.release()
        _clear_cancel(run_id)

    database.update_gem_scan_run(
        run_id,
        finished_at=datetime.now(),
        status=status,
        total_found=counters["total_found"],
        new_found=counters["new_found"],
        skipped_wrong_start_date=counters["skipped_wrong_start_date"],
        approved_count=counters["approved_count"],
        rejected_count=counters["rejected_count"],
        error_message="; ".join(error_messages)[:2000] if error_messages else None,
    )
    return run_id


def run_scan(scan_target_date) -> int:
    """Convenience wrapper for CLI/cron use: create + execute in one call."""
    run_id, parsed_date = start_scan(scan_target_date)
    return execute_scan(run_id, parsed_date)


if __name__ == "__main__":
    import argparse
    from zoneinfo import ZoneInfo

    parser = argparse.ArgumentParser(description="Run a GeM Tender Watcher scan (cron-ready entrypoint).")
    parser.add_argument("--date", help="Scan target date YYYY-MM-DD (default: today in Asia/Kolkata)")
    args = parser.parse_args()

    target = args.date or datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d")
    print(f"[gem_watcher] running scan for {target}")
    result_run_id = run_scan(target)
    print(f"[gem_watcher] scan run {result_run_id} finished")
