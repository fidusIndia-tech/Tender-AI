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
import re
import tempfile
import threading
import time
import traceback
import urllib.parse
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path

import ai_extractor
import database
from gem_watcher.evaluator import (
    evaluate_gem_candidate,
    keyword_pre_evaluate_gem_candidate,
    prequalify_gem_listing,
)

GEM_ALL_BIDS_URL = "https://bidplus.gem.gov.in/all-bids"
GEM_DATA_URL = "https://bidplus.gem.gov.in/all-bids-data"
GEM_PDF_URL_TEMPLATE = "https://bidplus.gem.gov.in/showbidDocument/{doc_id}"
GEM_RA_PDF_URL_TEMPLATE = "https://bidplus.gem.gov.in/showradocumentPdf/{doc_id}"
EXTRACTIONS_DIR = Path(__file__).resolve().parent.parent / "extractions"

KEYWORD_DELAY_SECONDS = float(os.environ.get("GEM_SCAN_KEYWORD_DELAY_SECONDS", "3"))
AUTO_APPROVE_THRESHOLD = int(os.environ.get("GEM_AUTO_APPROVE_SCORE_THRESHOLD", "8"))
HEADLESS = os.environ.get("PLAYWRIGHT_HEADLESS", "true").strip().lower() != "false"
PLAYWRIGHT_BROWSER_CHANNEL = os.environ.get("PLAYWRIGHT_BROWSER_CHANNEL", "").strip()
PLAYWRIGHT_ENABLE_EDGE_FALLBACK = os.environ.get("PLAYWRIGHT_ENABLE_EDGE_FALLBACK", "true").strip().lower() != "false"
PLAYWRIGHT_BROWSER_EXECUTABLE = os.environ.get("PLAYWRIGHT_BROWSER_EXECUTABLE", "").strip()
PAGE_SIZE = 10  # GeM returns 10 docs per page
PDF_DOWNLOAD_TIMEOUT_MS = int(os.environ.get("GEM_PDF_DOWNLOAD_TIMEOUT_MS", "30000"))
PDF_DOWNLOAD_RETRIES = int(os.environ.get("GEM_PDF_DOWNLOAD_RETRIES", "3"))
PDF_DOWNLOAD_RETRY_DELAY_SECONDS = float(os.environ.get("GEM_PDF_DOWNLOAD_RETRY_DELAY_SECONDS", "2"))
MIN_PDF_BYTES = int(os.environ.get("GEM_MIN_PDF_BYTES", "1024"))
MIN_VALID_PDF_BYTES = int(os.environ.get("GEM_MIN_VALID_PDF_BYTES", str(20 * 1024)))
MIN_EXTRACTED_TEXT_CHARS = int(os.environ.get("GEM_MIN_EXTRACTED_TEXT_CHARS", "500"))
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


SCAN_STEPS = {
    "STARTED": "STARTED",
    "OPENING_GEM_PAGE": "OPENING_GEM_PAGE",
    "APPLYING_DATE_FILTER": "APPLYING_DATE_FILTER",
    "SEARCHING_KEYWORD": "SEARCHING_KEYWORD",
    "READING_RESULTS": "READING_RESULTS",
    "DOWNLOADING_DOCUMENTS": "DOWNLOADING_DOCUMENTS",
    "EXTRACTING_FIELDS": "EXTRACTING_FIELDS",
    "RUNNING_KEYWORD_EVALUATION": "RUNNING_KEYWORD_EVALUATION",
    "SAVING_TENDERS": "SAVING_TENDERS",
    "COMPLETED": "COMPLETED",
}


def _log_scan_step(run_id: int | None, step: str, target_date=None, keyword: str | None = None, counters: dict | None = None):
    parts = [f"[gem_watcher] step={step}"]
    if target_date is not None:
        parts.append(f"target_date={target_date}")
    if keyword:
        parts.append(f"keyword={keyword}")
    if counters is not None:
        parts.append(
            "counts="
            f"found:{counters.get('total_found', 0)} "
            f"new:{counters.get('new_found', 0)} "
            f"duplicates:{counters.get('duplicates_count', 0)} "
            f"below_score:{counters.get('below_score_count', 0)} "
            f"pdf_failed:{counters.get('pdf_failed_count', 0)} "
            f"extraction_failed:{counters.get('extraction_failed_count', 0)} "
            f"evaluation_failed:{counters.get('evaluation_failed_count', 0)} "
            f"approved:{counters.get('approved_count', 0)} "
            f"review:{counters.get('review_count', 0)} "
            f"rejected:{counters.get('rejected_count', 0)} "
            f"skipped:{counters.get('skipped_wrong_start_date', 0)}"
        )
    print(" ".join(parts))
    if run_id is not None:
        try:
            database.update_gem_scan_run(run_id, current_step=step)
        except Exception:
            pass


def _launch_browser(pw, channel: str | None = None):
    launch_kwargs = {
        "headless": HEADLESS,
        "args": [
            "--disable-features=RendererCodeIntegrity",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
        ],
    }
    if PLAYWRIGHT_BROWSER_EXECUTABLE:
        launch_kwargs["executable_path"] = PLAYWRIGHT_BROWSER_EXECUTABLE
    if channel:
        launch_kwargs["channel"] = channel
    return pw.chromium.launch(**launch_kwargs)


def _should_retry_with_edge(exc: Exception) -> bool:
    message = str(exc or "")
    return os.name == "nt" and "ERR_NETWORK_ACCESS_DENIED" in message


def _friendly_scan_error(exc: Exception) -> str:
    message = str(exc or "")
    if "Executable doesn't exist" in message and "playwright" in message.lower():
        return (
            "Playwright browser binary is missing in this environment. "
            "Install it during deploy with: python -m playwright install --with-deps chromium"
        )
    return message


def _detect_edge_executable() -> str | None:
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    return None


def _first(value):
    """GeM's Solr-backed JSON returns most fields as single-element arrays."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _parse_gem_date(value):
    """Normalize GeM date values and compare only the calendar date.

    Supported inputs include:
    - 2026-06-19T17:08:42Z
    - 2026-06-19
    - 23-06-2026 2:40 PM
    - 23-06-2026
    """
    s = _first(value)
    if not s:
        return None
    text = _norm_space(s)
    iso_match = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if iso_match:
        try:
            return date(int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3)))
        except ValueError:
            return None
    dmy_match = re.search(r"(\d{2})-(\d{2})-(\d{4})", text)
    if dmy_match:
        try:
            return date(int(dmy_match.group(3)), int(dmy_match.group(2)), int(dmy_match.group(1)))
        except ValueError:
            return None
    for fmt in ("%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def _norm_space(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _keyword_has_strict_match(keyword: str, parsed: dict) -> bool:
    """Guard against GeM fullText false positives.

    GeM's search endpoint can return fuzzy matches for short queries like ABB.
    Before inserting a candidate into our pipeline, require the keyword to
    appear in the visible listing fields with a stricter boundary-aware match.
    """
    kw = _norm_space(keyword)
    if not kw:
        return False

    haystacks = [
        parsed.get("gem_bid_no"),
        parsed.get("title"),
        parsed.get("organisation"),
        parsed.get("department"),
    ]
    text = " | ".join(_norm_space(v) for v in haystacks if v).lower()
    if not text:
        return False

    kw_lower = kw.lower()
    # Short brand/acronym-style queries must match as a whole token.
    if len(re.sub(r"[^a-z0-9]+", "", kw_lower)) <= 4:
        pattern = r"(?<![a-z0-9])" + re.escape(kw_lower) + r"(?![a-z0-9])"
        return re.search(pattern, text) is not None

    # Longer queries may match as tokenized phrase or plain substring.
    words = [w for w in re.split(r"\s+", kw_lower) if w]
    if words:
        phrase_pattern = r"(?<![a-z0-9])" + r"\s+".join(re.escape(w) for w in words) + r"(?![a-z0-9])"
        if re.search(phrase_pattern, text) is not None:
            return True
    return kw_lower in text


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
        "bid_start_date": _parse_gem_date(doc.get("final_start_date_sort") or doc.get("final_start_date")),
        "bid_end_date": _parse_gem_date(doc.get("final_end_date_sort") or doc.get("final_end_date")),
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
            start = parsed["bid_start_date"]
            if start == scan_target_date:
                counters["total_found"] += 1
                parsed["keyword_strict_match"] = _keyword_has_strict_match(keyword, parsed)
                if not parsed["keyword_strict_match"]:
                    parsed["skip_reason"] = (
                        "GeM returned this row for the keyword search, but the keyword was not "
                        "confirmed in the visible listing fields. Manual review required."
                    )
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


def _capture_csrf_with_retry(page, run_id=None, scan_target_date=None, retries: int = 2):
    last_exc = None
    for attempt in range(1, retries + 2):
        try:
            if run_id is not None:
                _log_scan_step(run_id, SCAN_STEPS["OPENING_GEM_PAGE"], scan_target_date, counters=None)
            csrf = _capture_csrf(page)
            if not csrf:
                raise GemBlockedError("Could not obtain a GeM session/CSRF token (possible block or site change).")
            if run_id is not None:
                _log_scan_step(run_id, SCAN_STEPS["APPLYING_DATE_FILTER"], scan_target_date, counters=None)
            return csrf
        except Exception as e:
            last_exc = e
            print(f"[gem_watcher] page-open attempt {attempt}/{retries + 1} failed: {type(e).__name__}: {e}")
            if attempt <= retries:
                time.sleep(min(2 * attempt, 5))
                continue
            raise last_exc


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


def _validate_downloaded_pdf(pdf_bytes: bytes | None, content_type: str | None = None) -> tuple[bool, str | None]:
    if not pdf_bytes:
        return False, "Downloaded file is empty or missing."
    body = bytes(pdf_bytes)
    if len(body) <= 0:
        return False, "Downloaded file is empty."
    stripped = body.lstrip()
    starts_pdf = stripped.startswith(b"%PDF-")
    content_type = (content_type or "").lower().strip()
    looks_html = stripped.startswith(b"<!doctype html") or stripped.startswith(b"<html") or b"<html" in stripped[:200].lower()
    if looks_html and not starts_pdf:
        return False, "Downloaded file is an HTML/error page, not a PDF."
    if not starts_pdf and "application/pdf" not in content_type:
        return False, f"Downloaded file failed PDF signature/content-type validation (content-type={content_type or 'unknown'})."
    if len(body) < MIN_VALID_PDF_BYTES:
        return False, f"Downloaded PDF is too small ({len(body)} bytes)."
    return True, None


def _assess_extraction_quality(candidate: dict, raw: dict, tender_text: str) -> tuple[str, str, list[str]]:
    issues = []
    ti = raw.get("tender_information") or {}
    if not ti:
        return "FAILED", "LOW", ["Tender information could not be extracted from the downloaded PDF."]

    if len((tender_text or "").strip()) < MIN_EXTRACTED_TEXT_CHARS:
        issues.append(f"Extracted tender text is too small ({len((tender_text or '').strip())} chars).")

    important_fields = {
        "title": candidate.get("title") or ti.get("title"),
        "organization_name": ti.get("organization_name") or candidate.get("organisation"),
        "department_name": ti.get("department_name") or candidate.get("department"),
        "bid_end_date": ti.get("bid_end_datetime") or ti.get("bid_end_date") or candidate.get("bid_end_date"),
    }
    missing_important = [name for name, value in important_fields.items() if not str(value or "").strip()]
    if len(missing_important) >= 2:
        issues.append("Important extracted fields are missing: " + ", ".join(missing_important))
    elif missing_important:
        issues.append("Some important extracted fields are missing: " + ", ".join(missing_important))

    if issues:
        confidence = "LOW" if len(missing_important) >= 2 or len((tender_text or "").strip()) < MIN_EXTRACTED_TEXT_CHARS else "MEDIUM"
        return "PARTIAL", confidence, issues
    return "SUCCESS", "HIGH", []


def _extract_and_evaluate_candidate(candidate_id, capability, force_full_evaluation: bool = False):
    candidate = database.get_gem_candidate(candidate_id)
    if not candidate:
        return "SKIPPED"
    if not force_full_evaluation and candidate.get("status") in ("APPROVED", "REJECTED"):
        return "SKIPPED"
    if not candidate.get("pdf_file_id"):
        database.update_gem_candidate(
            candidate_id,
            status="REVIEW",
            scan_status="EXTRACTION_FAILED",
            extraction_status="FAILED",
            extraction_confidence="LOW",
            extraction_error_message="Candidate reached extraction stage without a saved PDF",
            extraction_error="Candidate reached extraction stage without a saved PDF",
            evaluation_reason="Extraction failed or incomplete. Manual review required.",
            review_reason="Extraction failed or incomplete. Manual review required.",
            decision_reason="Extraction failed or incomplete. Manual review required.",
        )
        print(f"[gem_watcher] candidate {candidate_id} {candidate.get('gem_bid_no')}: missing saved PDF file id")
        return "REVIEW"

    file_row = database.get_uploaded_file(candidate["pdf_file_id"])
    if not file_row or not file_row.get("file_data"):
        database.update_gem_candidate(
            candidate_id,
            status="REVIEW",
            scan_status="EXTRACTION_FAILED",
            extraction_status="FAILED",
            extraction_confidence="LOW",
            extraction_error_message="Saved PDF file data is missing from uploaded_files",
            extraction_error="Saved PDF file data is missing from uploaded_files",
            evaluation_reason="Extraction failed or incomplete. Manual review required.",
            review_reason="Extraction failed or incomplete. Manual review required.",
            decision_reason="Extraction failed or incomplete. Manual review required.",
        )
        print(f"[gem_watcher] candidate {candidate_id} {candidate.get('gem_bid_no')}: saved PDF bytes missing")
        return "REVIEW"

    pdf_bytes = bytes(file_row["file_data"])
    is_valid_pdf, pdf_validation_error = _validate_downloaded_pdf(pdf_bytes, file_row.get("content_type"))
    if not is_valid_pdf:
        database.update_gem_candidate(
            candidate_id,
            status="REVIEW",
            scan_status="PDF_DOWNLOAD_FAILED",
            evaluation_stage="PDF_DOWNLOAD_FAILED",
            extraction_status="FAILED",
            extraction_confidence="LOW",
            extraction_error_message=pdf_validation_error,
            pdf_error=pdf_validation_error,
            evaluation_reason="Extraction failed or incomplete. Manual review required.",
            review_reason=pdf_validation_error or "Extraction failed or incomplete. Manual review required.",
            decision_reason="Extraction failed or incomplete. Manual review required.",
        )
        print(f"[gem_watcher] candidate {candidate_id} {candidate.get('gem_bid_no')}: invalid downloaded PDF ({pdf_validation_error})")
        return "REVIEW"

    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tender_text = ""
    raw = {}
    try:
        database.update_gem_candidate(
            candidate_id,
            status="EXTRACTING",
            scan_status="PDF_DOWNLOADED",
            extraction_status="IN_PROGRESS",
            extraction_confidence="LOW",
            extraction_error_message=None,
            extraction_error=None,
        )
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
    except Exception as e:
        database.update_gem_candidate(
            candidate_id,
            status="REVIEW",
            scan_status="EXTRACTION_FAILED",
            evaluation_stage="EXTRACTION_FAILED",
            extraction_status="FAILED",
            extraction_confidence="LOW",
            extraction_error_message=f"{type(e).__name__}: {e}",
            extraction_error=f"{type(e).__name__}: {e}",
            evaluation_reason="Extraction failed or incomplete. Manual review required.",
            review_reason="Extraction failed or incomplete. Manual review required.",
            decision_reason="Extraction failed or incomplete. Manual review required.",
        )
        print(f"[gem_watcher] candidate {candidate_id} {candidate.get('gem_bid_no')}: extraction failed ({type(e).__name__}: {e})")
        return "REVIEW"
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    extraction_status, extraction_confidence, extraction_issues = _assess_extraction_quality(candidate, raw, tender_text)
    database.update_gem_candidate(
        candidate_id,
        extraction_status=extraction_status,
        extraction_confidence=extraction_confidence,
        extraction_error_message="; ".join(extraction_issues) if extraction_issues else None,
        extraction_error="; ".join(extraction_issues) if extraction_issues else None,
    )

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
    tender["title"] = candidate.get("title")
    tender["matched_keywords"] = candidate.get("matched_keywords") or []
    tender["matched_keywords_text"] = ", ".join(candidate.get("matched_keywords") or [])
    tender["boq_items"] = raw.get("items", [])
    docs = raw.get("required_documents", [])
    tender["required_documents"] = [{"label": d} if isinstance(d, str) else d for d in docs]
    tender["emd"] = raw.get("emd") or {}
    tender["technical_specifications"] = raw.get("technical_specifications") or []
    tender["pdf_path"] = f"/files/{candidate['pdf_file_id']}"

    merged_eval_json = dict(candidate.get("evaluation_json") or {})
    merged_eval_json["extraction_status"] = extraction_status
    merged_eval_json["extraction_confidence"] = extraction_confidence
    if extraction_issues:
        merged_eval_json["extraction_issues"] = extraction_issues

    if extraction_status != "SUCCESS":
        extraction_review_reason = "Extraction failed or incomplete. Manual review required."
        database.update_gem_candidate(
            candidate_id,
            status="REVIEW",
            scan_status="EXTRACTION_FAILED",
            evaluation_stage="EXTRACTION_LOW_CONFIDENCE",
            evaluation_score=None,
            evaluation_reason=extraction_review_reason,
            review_reason="; ".join(extraction_issues) if extraction_issues else extraction_review_reason,
            decision_reason=extraction_review_reason,
            evaluation_json=merged_eval_json,
        )
        print(f"[gem_watcher] candidate {candidate_id} {candidate.get('gem_bid_no')}: extraction incomplete -> review")
        return "REVIEW"

    if not force_full_evaluation:
        database.update_gem_candidate(candidate_id, status="KEYWORD_EVALUATING", evaluation_stage="KEYWORD_FIT_EVALUATING")
        keyword_eval = keyword_pre_evaluate_gem_candidate(candidate, tender, capability, tender_text=tender_text)
        merged_eval_json["keyword_pre_evaluation"] = keyword_eval.get("keyword_evaluation_json") or {}
        database.update_gem_candidate(
            candidate_id,
            evaluation_stage="KEYWORD_FIT_APPROVED",
            scan_status="FOUND",
            keyword_pre_score=keyword_eval["keyword_pre_score"],
            keyword_fit_score=keyword_eval.get("keyword_fit_score"),
            keyword_fit_decision=keyword_eval.get("keyword_fit_decision"),
            matched_brands=keyword_eval.get("matched_brands"),
            matched_products=keyword_eval.get("matched_products"),
            keyword_decision=keyword_eval["keyword_decision"],
            matched_product_keywords=keyword_eval["matched_product_keywords"],
            negative_keywords=keyword_eval.get("negative_keywords"),
            negative_keywords_found=keyword_eval["negative_keywords_found"],
            keyword_context_type=keyword_eval["keyword_context_type"],
            keyword_fit_reason=keyword_eval.get("keyword_fit_reason"),
            keyword_evaluation_reason=keyword_eval["keyword_evaluation_reason"],
            evaluation_confidence=keyword_eval.get("confidence", "MEDIUM"),
            requires_full_evaluation=keyword_eval["requires_full_evaluation"],
            evaluation_json=merged_eval_json,
        )
        if keyword_eval["keyword_decision"] == "REJECT":
            database.update_gem_candidate(
                candidate_id,
                status="REVIEW" if keyword_eval.get("matched_brands") and keyword_eval.get("matched_products") else "REJECTED",
                scan_status="BELOW_APPROVAL_SCORE" if keyword_eval.get("matched_brands") and keyword_eval.get("matched_products") else "PRE_EVALUATION_FAILED",
                evaluation_stage="KEYWORD_FIT_REVIEW" if keyword_eval.get("matched_brands") and keyword_eval.get("matched_products") else "KEYWORD_FIT_REJECTED",
                evaluation_score=keyword_eval["keyword_pre_score"],
                evaluation_reason=keyword_eval.get("keyword_fit_reason") or keyword_eval["keyword_evaluation_reason"],
                review_reason=keyword_eval.get("keyword_fit_reason") if keyword_eval.get("matched_brands") and keyword_eval.get("matched_products") else None,
                rejection_reason=None if keyword_eval.get("matched_brands") and keyword_eval.get("matched_products") else (keyword_eval.get("keyword_fit_reason") or keyword_eval["keyword_evaluation_reason"]),
                decision_reason=keyword_eval.get("keyword_fit_reason") or keyword_eval["keyword_evaluation_reason"],
                evaluation_json=merged_eval_json,
            )
            final_outcome = "REVIEW" if keyword_eval.get("matched_brands") and keyword_eval.get("matched_products") else "REJECTED"
            print(f"[gem_watcher] candidate {candidate_id} {candidate.get('gem_bid_no')}: {final_outcome.lower()} at keyword pre-evaluation")
            return final_outcome
        if keyword_eval["keyword_decision"] == "REVIEW":
            database.update_gem_candidate(
                candidate_id,
                status="REVIEW",
                scan_status="BELOW_APPROVAL_SCORE",
                evaluation_stage="KEYWORD_FIT_REVIEW",
                evaluation_score=keyword_eval["keyword_pre_score"],
                evaluation_reason=keyword_eval.get("keyword_fit_reason") or keyword_eval["keyword_evaluation_reason"],
                review_reason=keyword_eval.get("keyword_fit_reason") or keyword_eval["keyword_evaluation_reason"],
                decision_reason=keyword_eval.get("keyword_fit_reason") or keyword_eval["keyword_evaluation_reason"],
                evaluation_json=merged_eval_json,
            )
            print(f"[gem_watcher] candidate {candidate_id} {candidate.get('gem_bid_no')}: review at keyword pre-evaluation")
            return "REVIEW"

    database.update_gem_candidate(candidate_id, status="EVALUATING", evaluation_stage="FULL_EVALUATION")
    try:
        evaluation = evaluate_gem_candidate(tender, capability, candidate.get("bid_end_date"), tender_text=tender_text)
    except Exception as e:
        database.update_gem_candidate(
            candidate_id,
            status="REVIEW",
            scan_status="EVALUATION_FAILED",
            evaluation_stage="EVALUATION_FAILED",
            evaluation_reason=f"{type(e).__name__}: {e}",
            review_reason="Full evaluation failed. Manual review required.",
            decision_reason="Full evaluation failed. Manual review required.",
        )
        print(f"[gem_watcher] candidate {candidate_id} {candidate.get('gem_bid_no')}: full evaluation failed ({type(e).__name__}: {e})")
        return "EVALUATION_FAILED"
    merged_eval_json.update(evaluation["evaluation_json"] or {})
    database.save_gem_tender_evaluation(
        candidate_id, evaluation["score"], evaluation["rating_label"],
        evaluation["matched_brands"], evaluation["eligibility_status"],
        evaluation["reason"], merged_eval_json,
        matched_products=keyword_eval.get("matched_products") if not force_full_evaluation else None,
        negative_keywords=keyword_eval.get("negative_keywords") if not force_full_evaluation else None,
        keyword_fit_score=keyword_eval.get("keyword_fit_score") if not force_full_evaluation else None,
        keyword_fit_decision=keyword_eval.get("keyword_fit_decision") if not force_full_evaluation else None,
        keyword_fit_reason=keyword_eval.get("keyword_fit_reason") if not force_full_evaluation else None,
        evaluation_stage="FULL_EVALUATED",
    )
    database.update_gem_candidate(
        candidate_id,
        evaluation_score=evaluation["score"],
        scan_status="APPROVED" if evaluation["eligibility_status"] == "APPROVED" else "BELOW_APPROVAL_SCORE" if evaluation["eligibility_status"] == "REVIEW" else "PRE_EVALUATION_FAILED",
        evaluation_reason=evaluation["reason"],
        evaluation_confidence=(evaluation.get("evaluation_json") or {}).get("confidence") or (evaluation.get("evaluation_json") or {}).get("evaluation_confidence") or "MEDIUM",
        decision_reason=(evaluation.get("evaluation_json") or {}).get("decision_reason") or evaluation["reason"],
        review_reason=((evaluation.get("evaluation_json") or {}).get("review_reason") or (evaluation.get("evaluation_json") or {}).get("decision_reason") or evaluation["reason"]) if evaluation["eligibility_status"] == "REVIEW" else None,
        rejection_reason=((evaluation.get("evaluation_json") or {}).get("rejection_reason") or (evaluation.get("evaluation_json") or {}).get("decision_reason") or evaluation["reason"]) if evaluation["eligibility_status"] == "REJECTED" else None,
        evaluation_json=merged_eval_json,
        requires_full_evaluation=True,
        evaluation_stage="FULL_EVALUATED",
        status="EVALUATED",
    )

    if evaluation["eligibility_status"] == "APPROVED":
        duplicate = database.find_tender_duplicate(candidate["gem_bid_no"], tender.get("tender_number"))
        if duplicate:
            database.update_gem_candidate(
                candidate_id,
                status="SENT_TO_ALL_TENDERS",
                scan_status="DUPLICATE",
                tender_id=duplicate["id"],
                duplicate_reason="GeM bid number already exists in All Tenders.",
                evaluation_reason=(evaluation["reason"] or "") + " (already exists in All Tenders)",
            )
        else:
            tender_id = database.save_tender(
                {k: v for k, v in tender.items() if k not in ("boq_items", "required_documents")},
                tender["boq_items"], tender["required_documents"],
            )
            database.update_gem_candidate(candidate_id, status="SENT_TO_ALL_TENDERS", scan_status="SENT_TO_ALL_TENDERS", tender_id=tender_id)
        print(f"[gem_watcher] candidate {candidate_id} {candidate.get('gem_bid_no')}: approved score={evaluation['score']}")
        return "APPROVED"
    elif evaluation["eligibility_status"] == "REVIEW":
        database.update_gem_candidate(candidate_id, status="REVIEW", scan_status="BELOW_APPROVAL_SCORE")
        print(f"[gem_watcher] candidate {candidate_id} {candidate.get('gem_bid_no')}: review score={evaluation['score']}")
        return "REVIEW"
    else:
        database.update_gem_candidate(candidate_id, status="REJECTED", scan_status="REJECTED")
        print(f"[gem_watcher] candidate {candidate_id} {candidate.get('gem_bid_no')}: rejected score={evaluation['score']} reason={evaluation['reason'][:220]}")
        return "REJECTED"


def run_full_evaluation_for_candidate(candidate_id: int):
    """Manual override entrypoint: skip keyword pre-eval and run full evaluation."""
    capability = database.get_company_capability_profile()
    return _extract_and_evaluate_candidate(candidate_id, capability, force_full_evaluation=True)


def rerun_extraction_for_candidate(candidate_id: int):
    """Manual override entrypoint: re-run extraction + normal watcher evaluation flow."""
    capability = database.get_company_capability_profile()
    return _extract_and_evaluate_candidate(candidate_id, capability, force_full_evaluation=False)


def _process_new_candidates(page, candidate_ids, counters, error_messages, run_id=None):
    capability = database.get_company_capability_profile()
    ready_candidate_ids = []
    if run_id is not None:
        _log_scan_step(run_id, SCAN_STEPS["DOWNLOADING_DOCUMENTS"], counters=counters)

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
            if str(candidate.get("scan_status") or "").upper() == "DUPLICATE" and str(candidate.get("status") or "").upper() in {"REVIEW", "REJECTED", "SENT_TO_ALL_TENDERS", "APPROVED"}:
                continue
            if candidate.get("tender_id"):
                database.update_gem_candidate(
                    candidate_id,
                    scan_status="DUPLICATE",
                    duplicate_reason=candidate.get("duplicate_reason") or "GeM bid number already exists in All Tenders.",
                )
                continue
            if candidate.get("status") in ("SENT_TO_ALL_TENDERS", "APPROVED", "REJECTED"):
                continue
            if candidate.get("pdf_file_id"):
                ready_candidate_ids.append(candidate_id)
                continue

            prefilter = prequalify_gem_listing(candidate, capability)
            if not prefilter["should_download_pdf"]:
                if prefilter["status"] == "REVIEW":
                    counters["review_count"] += 1
                elif prefilter["status"] == "REJECTED":
                    counters["rejected_count"] += 1
                database.update_gem_candidate(
                    candidate_id,
                    status=prefilter["status"],
                    scan_status="BELOW_APPROVAL_SCORE" if prefilter["status"] == "REVIEW" else "PRE_EVALUATION_FAILED",
                    evaluation_stage="LISTING_PREFILTER",
                    keyword_pre_score=prefilter["score"],
                    keyword_fit_score=prefilter["score"],
                    keyword_fit_decision="REJECT" if prefilter["status"] == "REJECTED" else "REVIEW",
                    keyword_decision="REJECT" if prefilter["status"] == "REJECTED" else "REVIEW",
                    matched_brands=prefilter["evaluation_json"].get("matched_brands", []),
                    matched_products=prefilter["evaluation_json"].get("matched_products", []),
                    matched_product_keywords=prefilter["evaluation_json"].get("matched_products", []),
                    negative_keywords=prefilter["evaluation_json"].get("hard_failures", []),
                    negative_keywords_found=prefilter["evaluation_json"].get("hard_failures", []),
                    keyword_context_type=prefilter["evaluation_json"].get("brand_context"),
                    keyword_fit_reason=prefilter["reason"],
                    keyword_evaluation_reason=prefilter["reason"],
                    requires_full_evaluation=False,
                    evaluation_score=prefilter["score"],
                    skip_reason=prefilter["reason"],
                    evaluation_reason=prefilter["reason"],
                    evaluation_json={"listing_prefilter": prefilter["evaluation_json"]},
                )
                if prefilter["status"] == "REVIEW":
                    counters["below_score_count"] += 1
                print(
                    f"[gem_watcher] candidate {candidate_id} {candidate.get('gem_bid_no')}: "
                    f"stopped before PDF download ({prefilter['status']})"
                )
                continue

            pdf_url = candidate.get("pdf_url") or candidate.get("gem_detail_url")
            if not pdf_url:
                database.update_gem_candidate(
                    candidate_id,
                    status="REVIEW",
                    scan_status="PDF_DOWNLOAD_FAILED",
                    evaluation_reason="Missing GeM PDF URL on candidate row",
                    pdf_error="Missing GeM PDF URL on candidate row",
                    review_reason="Missing GeM PDF URL on candidate row",
                )
                counters["pdf_failed_count"] += 1
                print(f"[gem_watcher] candidate {candidate_id} {candidate.get('gem_bid_no')}: missing PDF URL")
                continue

            database.update_gem_candidate(
                candidate_id,
                status="DOWNLOADING_PDF",
                scan_status="FOUND",
                evaluation_stage="DOWNLOADING_PDF",
                evaluation_reason=None,
            )
            if run_id is not None:
                _log_scan_step(run_id, SCAN_STEPS["DOWNLOADING_DOCUMENTS"], counters=counters)
            print(f"[gem_watcher] candidate {candidate_id} {candidate.get('gem_bid_no')}: downloading PDF")
            pdf_bytes, download_error = _download_pdf_bytes(page, pdf_url)
            if not pdf_bytes:
                database.update_gem_candidate(
                    candidate_id,
                    status="REVIEW",
                    scan_status="PDF_DOWNLOAD_FAILED",
                    evaluation_stage="PDF_DOWNLOAD_FAILED",
                    extraction_status="FAILED",
                    extraction_confidence="LOW",
                    extraction_error_message=f"PDF download failed: {download_error}",
                    pdf_error=f"PDF download failed: {download_error}",
                    evaluation_reason="Extraction failed or incomplete. Manual review required.",
                    review_reason=f"PDF download failed: {download_error}",
                    decision_reason="Extraction failed or incomplete. Manual review required.",
                )
                counters["pdf_failed_count"] += 1
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
            database.update_gem_candidate(
                candidate_id,
                pdf_file_id=file_id,
                status="PDF_DOWNLOADED",
                scan_status="PDF_DOWNLOADED",
                evaluation_stage="PDF_DOWNLOADED",
                extraction_status="PENDING",
                extraction_confidence="LOW",
                pdf_error=None,
            )
            ready_candidate_ids.append(candidate_id)
            print(f"[gem_watcher] candidate {candidate_id} {candidate.get('gem_bid_no')}: PDF saved ({len(pdf_bytes)} bytes)")

        except _ScanCancelled:
            raise
        except Exception as e:
            error_messages.append(f"candidate {candidate_id}: {e}")
            try:
                database.update_gem_candidate(
                    candidate_id,
                    status="REVIEW",
                    scan_status="PDF_DOWNLOAD_FAILED",
                    evaluation_reason=str(e),
                    pdf_error=str(e),
                    review_reason="PDF download or preparation failed. Manual review required.",
                )
                counters["pdf_failed_count"] += 1
            except Exception:
                pass

    # Stage 2: extract and evaluate from already-saved PDFs using a controlled
    # worker pool. The browser/session-bound GeM download step above stays
    # single-threaded; only the local PDF parsing + AI extraction/evaluation is
    # parallelised here.
    if run_id is not None and ready_candidate_ids:
        _log_scan_step(run_id, SCAN_STEPS["EXTRACTING_FIELDS"], counters=counters)
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
                    latest_candidate = database.get_gem_candidate(candidate_id) or {}
                    latest_scan_status = str(latest_candidate.get("scan_status") or "").upper()
                    if outcome == "APPROVED":
                        counters["approved_count"] += 1
                    elif outcome == "REVIEW":
                        counters["review_count"] += 1
                    elif outcome == "REJECTED":
                        counters["rejected_count"] += 1
                    elif outcome == "EVALUATION_FAILED":
                        counters["review_count"] += 1
                    if latest_scan_status == "BELOW_APPROVAL_SCORE":
                        counters["below_score_count"] += 1
                    elif latest_scan_status == "EXTRACTION_FAILED":
                        counters["extraction_failed_count"] += 1
                    elif latest_scan_status == "PDF_DOWNLOAD_FAILED":
                        counters["pdf_failed_count"] += 1
                    elif latest_scan_status == "EVALUATION_FAILED":
                        counters["evaluation_failed_count"] += 1
                    if run_id is not None:
                        step = SCAN_STEPS["RUNNING_KEYWORD_EVALUATION"] if outcome in {"REVIEW", "REJECTED"} else SCAN_STEPS["SAVING_TENDERS"]
                        _log_scan_step(run_id, step, counters=counters)
                except Exception as e:
                    error_messages.append(f"candidate {candidate_id}: {e}")
                    try:
                        database.update_gem_candidate(
                            candidate_id,
                            status="REVIEW",
                            scan_status="EVALUATION_FAILED",
                            evaluation_reason=str(e),
                            review_reason="Full evaluation failed. Manual review required.",
                        )
                        counters["evaluation_failed_count"] += 1
                    except Exception:
                        pass

                # Flush live progress so the scan log reflects completed worker
                # results during large backlogs instead of only at the end.
                if run_id is not None:
                    try:
                        database.update_gem_scan_run(
                            run_id,
                            approved_count=counters["approved_count"],
                            duplicates_count=counters["duplicates_count"],
                            below_score_count=counters["below_score_count"],
                            pdf_failed_count=counters["pdf_failed_count"],
                            extraction_failed_count=counters["extraction_failed_count"],
                            evaluation_failed_count=counters["evaluation_failed_count"],
                            review_count=counters["review_count"],
                            rejected_count=counters["rejected_count"],
                        )
                    except Exception:
                        pass


def _resolve_keywords(keyword=None):
    """Either a single ad-hoc keyword typed in the search box (one-shot, not
    persisted) or the saved active keywords from the DB."""
    if keyword and str(keyword).strip():
        return [{"id": None, "keyword": str(keyword).strip()}]
    return database.list_gem_keywords(active_only=True)


def start_scan(scan_target_date, keyword=None):
    """Validate input and create the scan_run row synchronously, so the caller
    (the HTTP endpoint) gets a run_id to poll immediately. The actual browser
    work happens in execute_scan(), meant to be run in a background task.

    `keyword`, if given, runs a one-shot ad-hoc search for just that keyword
    (no need to add it to the saved keyword list first)."""
    if isinstance(scan_target_date, str):
        scan_target_date = datetime.strptime(scan_target_date, "%Y-%m-%d").date()
    if scan_target_date > date.today():
        raise ValueError("scan_target_date cannot be in the future")
    if database.is_gem_scan_running():
        raise RuntimeError("A scan is already running")
    keywords = _resolve_keywords(keyword)
    if not keywords:
        raise ValueError("No keyword to scan — type a keyword or activate at least one saved keyword.")
    run_id = database.create_gem_scan_run(scan_target_date, len(keywords))
    return run_id, scan_target_date


def execute_scan(run_id: int, scan_target_date, keyword=None):
    if not _scan_lock.acquire(blocking=False):
        database.update_gem_scan_run(
            run_id,
            finished_at=datetime.now(),
            status="FAILED",
            current_step=SCAN_STEPS["STARTED"],
            error_message="A scan is already running",
        )
        return run_id

    counters = {
        "total_found": 0,
        "new_found": 0,
        "duplicates_count": 0,
        "below_score_count": 0,
        "pdf_failed_count": 0,
        "extraction_failed_count": 0,
        "evaluation_failed_count": 0,
        "skipped_wrong_start_date": 0,
        "approved_count": 0,
        "review_count": 0,
        "rejected_count": 0,
    }
    error_messages = []
    keyword_failures = []
    status = "FAILED"
    current_step = SCAN_STEPS["STARTED"]
    error_stack = None

    try:
        _log_scan_step(run_id, SCAN_STEPS["STARTED"], scan_target_date, counters=counters)
        keywords = _resolve_keywords(keyword)

        def _run_with_browser(page, csrf_token):
            new_candidate_ids = []
            csrf = csrf_token
            for kw_row in keywords:
                if _is_cancelled(run_id):
                    raise _ScanCancelled()
                keyword = kw_row["keyword"]
                _log_scan_step(run_id, SCAN_STEPS["SEARCHING_KEYWORD"], scan_target_date, keyword=keyword, counters=counters)
                try:
                    try:
                        _log_scan_step(run_id, SCAN_STEPS["READING_RESULTS"], scan_target_date, keyword=keyword, counters=counters)
                        matches = _search_keyword_matches(page, keyword, csrf, scan_target_date, counters)
                    except GemBlockedError as e:
                        print(f"[gem_watcher] keyword {keyword}: initial GeM fetch failed; refreshing session once. {e}")
                        csrf = _capture_csrf_with_retry(page, run_id=run_id, scan_target_date=scan_target_date, retries=1)
                        if not csrf:
                            raise GemBlockedError(
                                f"{e} Follow-up refresh could not obtain a new CSRF token."
                            )
                        _log_scan_step(run_id, SCAN_STEPS["READING_RESULTS"], scan_target_date, keyword=keyword, counters=counters)
                        matches = _search_keyword_matches(page, keyword, csrf, scan_target_date, counters)
                    for parsed in matches:
                        candidate_meta = database.upsert_gem_candidate(
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
                            scan_run_id=run_id,
                        )
                        candidate_id = candidate_meta["id"]
                        scan_status = "NEW" if candidate_meta["is_new"] else "DUPLICATE"
                        duplicate_reason = None
                        if candidate_meta["is_duplicate"]:
                            duplicate_reason = "GeM bid number was already captured in an earlier scan."
                            counters["duplicates_count"] += 1
                        else:
                            counters["new_found"] += 1
                        database.update_gem_candidate(
                            candidate_id,
                            scan_status=scan_status,
                            duplicate_reason=duplicate_reason,
                            skip_reason=parsed.get("skip_reason"),
                        )
                        new_candidate_ids.append(candidate_id)
                    if kw_row.get("id") is not None:
                        database.touch_gem_keyword_checked(kw_row["id"])
                    print(
                        f"[gem_watcher] keyword={keyword} target_date={scan_target_date} "
                        f"found={counters['total_found']} new={counters['new_found']} duplicates={counters['duplicates_count']}"
                    )
                except GemBlockedError as e:
                    message = f"{keyword}: {e}"
                    keyword_failures.append(message)
                    error_messages.append(message)
                    print(f"[gem_watcher] keyword failed with GeM block/session issue: {message}")
                except Exception as e:
                    message = f"{keyword}: {type(e).__name__}: {e}"
                    keyword_failures.append(message)
                    error_messages.append(message)
                    print(f"[gem_watcher] keyword failed: {message}")
                    print(traceback.format_exc())
                time.sleep(KEYWORD_DELAY_SECONDS)
            return list(dict.fromkeys(new_candidate_ids))

        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser_attempts = []
            detected_edge_path = _detect_edge_executable() if os.name == "nt" else None
            if PLAYWRIGHT_BROWSER_CHANNEL:
                browser_attempts.append(PLAYWRIGHT_BROWSER_CHANNEL)
            else:
                browser_attempts.append(None)
                if PLAYWRIGHT_ENABLE_EDGE_FALLBACK and os.name == "nt":
                    browser_attempts.append("msedge")
                    if detected_edge_path:
                        browser_attempts.append({"name": "msedge-explicit", "executable_path": detected_edge_path})

            last_exc = None
            for browser_target in browser_attempts:
                browser_channel = browser_target if isinstance(browser_target, str) else None
                browser_label = browser_channel or (browser_target.get("name") if isinstance(browser_target, dict) else "chromium") or "chromium"
                if isinstance(browser_target, dict) and browser_target.get("executable_path"):
                    browser = pw.chromium.launch(
                        headless=HEADLESS,
                        executable_path=browser_target["executable_path"],
                        args=[
                            "--disable-features=RendererCodeIntegrity",
                            "--no-sandbox",
                            "--disable-setuid-sandbox",
                            "--disable-dev-shm-usage",
                        ],
                    )
                else:
                    browser = _launch_browser(pw, browser_channel)
                try:
                    page = browser.new_page()
                    current_step = SCAN_STEPS["OPENING_GEM_PAGE"]
                    csrf = _capture_csrf_with_retry(page, run_id=run_id, scan_target_date=scan_target_date, retries=2)

                    new_candidate_ids = _run_with_browser(page, csrf)
                    database.update_gem_scan_run(
                        run_id,
                        current_step=SCAN_STEPS["SAVING_TENDERS"],
                        total_found=counters["total_found"],
                        new_found=counters["new_found"],
                        duplicates_count=counters["duplicates_count"],
                        skipped_wrong_start_date=counters["skipped_wrong_start_date"],
                        below_score_count=counters["below_score_count"],
                        pdf_failed_count=counters["pdf_failed_count"],
                        extraction_failed_count=counters["extraction_failed_count"],
                        evaluation_failed_count=counters["evaluation_failed_count"],
                        review_count=counters["review_count"],
                    )
                    _log_scan_step(run_id, SCAN_STEPS["DOWNLOADING_DOCUMENTS"], scan_target_date, counters=counters)
                    _process_new_candidates(page, set(new_candidate_ids), counters, error_messages, run_id=run_id)
                    if keyword_failures and (counters["total_found"] > 0 or counters["new_found"] > 0 or counters["approved_count"] > 0 or counters["review_count"] > 0 or counters["rejected_count"] > 0):
                        status = "PARTIAL_FAILED"
                    else:
                        status = "COMPLETED"
                    last_exc = None
                    break
                except Exception as e:
                    last_exc = e
                    current_step = SCAN_STEPS["OPENING_GEM_PAGE"]
                    remaining_edge_fallback = any(
                        (target == "msedge") or (isinstance(target, dict) and target.get("name") == "msedge-explicit")
                        for target in browser_attempts[browser_attempts.index(browser_target) + 1:]
                    )
                    can_retry = browser_label not in {"msedge", "msedge-explicit"} and remaining_edge_fallback and _should_retry_with_edge(e)
                    if can_retry:
                        print(
                            f"[gem_watcher] browser {browser_label} hit network access denied; retrying with Edge fallback. {e}"
                        )
                        continue
                    raise
                finally:
                    browser.close()
            if last_exc is not None and status != "COMPLETED":
                raise last_exc

    except _ScanCancelled:
        status = "CANCELLED"
        error_messages.append("Scan stopped by admin. Unprocessed candidates remain queued — re-run the same date to continue.")
    except GemBlockedError as e:
        status = "BLOCKED"
        error_messages.append(str(e))
        error_stack = traceback.format_exc()
        print(f"[gem_watcher] scan blocked at step={current_step}: {e}")
        print(error_stack)
    except Exception as e:
        status = "FAILED"
        error_messages.append(_friendly_scan_error(e))
        error_stack = traceback.format_exc()
        print(f"[gem_watcher] scan failed at step={current_step}: {type(e).__name__}: {e}")
        print(error_stack)
    finally:
        _scan_lock.release()
        _clear_cancel(run_id)

    if status == "FAILED" and not error_messages and counters["total_found"] == 0 and counters["new_found"] == 0:
        status = "COMPLETED"
        error_messages.append("No tenders found for selected date/keywords")
    elif status == "COMPLETED" and counters["total_found"] == 0 and counters["new_found"] == 0 and not error_messages:
        error_messages.append("No tenders found for selected date/keywords")
    elif status == "COMPLETED" and keyword_failures:
        status = "PARTIAL_FAILED"

    current_step = SCAN_STEPS["COMPLETED"] if status in {"COMPLETED", "PARTIAL_FAILED"} else current_step

    database.update_gem_scan_run(
        run_id,
        finished_at=datetime.now(),
        status=status,
        current_step=current_step,
        total_found=counters["total_found"],
        new_found=counters["new_found"],
        duplicates_count=counters["duplicates_count"],
        skipped_wrong_start_date=counters["skipped_wrong_start_date"],
        below_score_count=counters["below_score_count"],
        pdf_failed_count=counters["pdf_failed_count"],
        extraction_failed_count=counters["extraction_failed_count"],
        evaluation_failed_count=counters["evaluation_failed_count"],
        approved_count=counters["approved_count"],
        review_count=counters["review_count"],
        rejected_count=counters["rejected_count"],
        error_message="; ".join(error_messages)[:2000] if error_messages else None,
        error_stack=error_stack[:12000] if error_stack else None,
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
