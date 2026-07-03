import argparse
import json
import logging
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

GEM_BID_RE = re.compile(r"\bGEM/\d{4}/B/\d+\b", re.I)
GEM_RA_RE = re.compile(r"\bGEM/\d{4}/R/\d+\b", re.I)

STATUS_PENDING = "PENDING"
STATUS_NOT_AVAILABLE = "NOT_AVAILABLE_YET"
STATUS_NOT_FOUND = "NOT_FOUND_ON_GEM"
STATUS_BID_AVAILABLE = "BID_RESULT_AVAILABLE"
STATUS_RA_CREATED = "RA_CREATED"
STATUS_RA_AVAILABLE = "RA_RESULT_AVAILABLE"
STATUS_BID_AND_RA_AVAILABLE = "BID_AND_RA_RESULT_AVAILABLE"
STATUS_FAILED = "FAILED_TO_CHECK"
STAGE_BID_RESULT = "BID_RESULT_AVAILABLE"
STAGE_BID_TECHNICAL = "BID_TECHNICAL_EVALUATION_AVAILABLE"
STAGE_BID_FINANCIAL = "BID_FINANCIAL_EVALUATION_AVAILABLE"
STAGE_RA_CREATED = "RA_CREATED"
STAGE_RA_TECHNICAL = "RA_TECHNICAL_EVALUATION_AVAILABLE"
STAGE_RA_FINANCIAL = "RA_FINANCIAL_EVALUATION_AVAILABLE"
OUR_COMPANY_ALIASES = [
    "FIDUS INDIA AUTOMATION PRIVATE LIMITED",
    "FIDUS INDIA AUTOMATION PVT LTD",
    "FIDUS",
]
GEM_STATUS_CODE_LABELS = {
    "0": "Not Evaluated",
    "1": "Technical Evaluation",
    "2": "Financial Evaluation",
    "3": "Bid Awarded",
}
RESULT_FILTER_TYPE = os.getenv("GEM_RESULT_FILTER_TYPE", "bidrastatus").strip() or "bidrastatus"
ONGOING_FILTER_TYPE = os.getenv("GEM_ONGOING_FILTER_TYPE", "bidra").strip() or "bidra"


class GemNoDataFound(Exception):
    """Raised when GeM responds cleanly but the exact bid is not present."""


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def setup_logging(level):
    log_file = LOG_DIR / f"watcher-{datetime.now().strftime('%Y-%m-%d')}.log"
    logging.basicConfig(
        level=getattr(logging, str(level or "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def load_config(require_api=True):
    load_dotenv(ROOT / ".env")
    base_url = os.getenv("TENDER_AI_BASE_URL", "").strip().rstrip("/")
    api_key = os.getenv("WATCHER_API_KEY", "").strip()
    if require_api and not base_url:
        raise SystemExit("TENDER_AI_BASE_URL is required in .env")
    if require_api and not api_key:
        raise SystemExit("WATCHER_API_KEY is required in .env")
    return {
        "base_url": base_url,
        "api_key": api_key,
        "gem_base_url": os.getenv("GEM_BASE_URL", "https://bidplus.gem.gov.in").strip().rstrip("/"),
        "delay": float(os.getenv("CHECK_DELAY_SECONDS", "7") or "7"),
        "max_tenders": int(os.getenv("MAX_TENDERS_PER_RUN", "0") or "0"),
        "headless": os.getenv("PLAYWRIGHT_HEADLESS", "false").strip().lower() in {"1", "true", "yes"},
        "use_persistent_profile": os.getenv("USE_PERSISTENT_PROFILE", "false").strip().lower() in {"1", "true", "yes"},
        "profile_dir": str((ROOT / os.getenv("BROWSER_PROFILE_DIR", ".browser-profile")).resolve()),
        "browser_channel": os.getenv("BROWSER_CHANNEL", "msedge").strip(),
        "log_level": os.getenv("LOG_LEVEL", "INFO"),
    }


def launch_browser_context(playwright, config):
    profile_dir = config["profile_dir"]
    headless = config["headless"]
    use_persistent_profile = config.get("use_persistent_profile", False)
    browser_channel = config.get("browser_channel") or ""
    viewport = {"width": 1366, "height": 900}
    browser = None
    context = None
    launch_mode = "persistent"

    if use_persistent_profile:
        try:
            context = playwright.chromium.launch_persistent_context(
                profile_dir,
                headless=headless,
                viewport=viewport,
            )
            logging.info("Playwright browser launched using persistent profile: %s", profile_dir)
            return browser, context, launch_mode
        except Exception as exc:
            logging.warning(
                "Persistent Playwright profile launch failed for %s: %s. Falling back to a fresh browser context.",
                profile_dir,
                exc,
            )

    launch_attempts = []
    if browser_channel:
        launch_attempts.append(("channel", {"channel": browser_channel, "headless": headless}))
    launch_attempts.append(("bundled", {"headless": headless}))

    last_error = None
    for attempt_name, launch_kwargs in launch_attempts:
        try:
            browser = playwright.chromium.launch(**launch_kwargs)
            context = browser.new_context(viewport=viewport)
            launch_mode = f"ephemeral-{attempt_name}"
            if attempt_name == "channel":
                logging.info(
                    "Playwright browser launched using installed channel '%s' with a fresh ephemeral context.",
                    browser_channel,
                )
            else:
                logging.info("Playwright browser launched using bundled Chromium with a fresh ephemeral context.")
            return browser, context, launch_mode
        except Exception as exc:
            last_error = exc
            logging.warning("Ephemeral Playwright launch using %s failed: %s", attempt_name, exc)

    raise RuntimeError(
        "Playwright could not launch any supported browser. "
        f"Attempted persistent_profile={use_persistent_profile}, channel='{browser_channel or 'none'}', bundled Chromium."
    ) from last_error


def close_browser_context(browser, context):
    close_errors = []
    if context is not None:
        try:
            context.close()
        except Exception as exc:
            close_errors.append(f"context.close failed: {exc}")
    if browser is not None:
        try:
            browser.close()
        except Exception as exc:
            close_errors.append(f"browser.close failed: {exc}")
    for item in close_errors:
        logging.warning(item)


def auth_headers(config):
    return {
        "Authorization": f"Bearer {config['api_key']}",
        "Accept": "application/json",
    }


def request_json(method, url, headers=None, payload=None, timeout=45):
    body = None
    final_headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        final_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=final_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
            return response.status, text, json.loads(text) if text else None
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        return exc.code, text, None


def post_json(config, path, payload):
    url = f"{config['base_url']}{path}"
    status, text, data = request_json("POST", url, auth_headers(config), payload=payload)
    if status < 200 or status >= 300:
        raise RuntimeError(f"{path} failed HTTP {status}: {text[:500]}")
    return data


def fetch_pending_tenders(config):
    status, text, data = request_json("GET", f"{config['base_url']}/api/result-watcher/pending", auth_headers(config))
    if status < 200 or status >= 300:
        if status == 404 and "Application not found" in text:
            raise RuntimeError(
                "Tender AI base URL is wrong or the Railway app is not deployed. "
                f"Current TENDER_AI_BASE_URL={config['base_url']}. "
                "Set it to your live Tender AI app URL."
            )
        raise RuntimeError(f"pending fetch failed HTTP {status}: {text[:500]}")
    return data if isinstance(data, list) else []


def fetch_recheck_tenders(config):
    status, text, data = request_json("GET", f"{config['base_url']}/api/result-watcher/recheck-targets", auth_headers(config))
    if status < 200 or status >= 300:
        raise RuntimeError(f"recheck targets fetch failed HTTP {status}: {text[:500]}")
    return data if isinstance(data, list) else []


def first_value(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value


def values_for(doc, key):
    raw = doc.get(key)
    if isinstance(raw, list):
        return [str(item).strip().upper() for item in raw if str(item or "").strip()]
    text = str(raw or "").strip().upper()
    return [text] if text else []


def normalize_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_compare(value):
    return re.sub(r"[^a-z0-9]+", " ", normalize_text(value).lower()).strip()


def build_result_url(gem_base_url, internal_id):
    value = str(first_value(internal_id) or "").strip()
    if not value:
        return None
    return f"{gem_base_url}/bidding/bid/getBidResultView/{value}"


def extract_status_text(doc):
    parts = []
    for key in ("b_status", "status", "status_text", "evaluation_status"):
        raw = (doc or {}).get(key)
        if isinstance(raw, list):
            for item in raw:
                text = str(item or "").strip()
                if text:
                    parts.append(GEM_STATUS_CODE_LABELS.get(text) or text)
        else:
            text = str(raw or "").strip()
            if text:
                parts.append(GEM_STATUS_CODE_LABELS.get(text) or text)
    return " | ".join(parts)


def is_evaluated_status_text(text):
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    return any(
        phrase in normalized
        for phrase in (
            "technical evaluation",
            "financial evaluation",
            "evaluated",
            "bid awarded",
            "awarded",
            "contract",
        )
    )


def find_matching_doc(data, bid):
    inner = ((data or {}).get("response") or {}).get("response") or {}
    docs = inner.get("docs") or []
    num_found = int(inner.get("numFound") or len(docs) or 0)
    returned_bids = []
    returned_parents = []
    matched_doc = None
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        doc_bids = values_for(doc, "b_bid_number")
        doc_parents = values_for(doc, "b_bid_number_parent")
        returned_bids.extend(doc_bids)
        returned_parents.extend(doc_parents)
        if bid in doc_bids or bid in doc_parents:
            matched_doc = doc
            break
    return matched_doc, returned_bids, returned_parents, num_found


def parse_gem_response(bid_number, gem_base_url, result_data, ongoing_data=None):
    bid = str(bid_number or "").strip().upper()
    result_doc, returned_bids, returned_parents, num_found = find_matching_doc(result_data, bid)
    ongoing_doc, ongoing_returned_bids, ongoing_returned_parents, ongoing_num_found = find_matching_doc(ongoing_data, bid)
    matched_doc = result_doc or ongoing_doc

    logging.info(
        "GeM docs result_filter=%s num_found=%s ongoing_filter=%s ongoing_num_found=%s matched=%s",
        RESULT_FILTER_TYPE,
        num_found,
        ONGOING_FILTER_TYPE,
        ongoing_num_found,
        bool(matched_doc),
    )

    if not matched_doc:
        result_not_found = bool((result_data or {}).get("_gem_not_found"))
        ongoing_not_found = bool((ongoing_data or {}).get("_gem_not_found"))
        return {
            "resultAvailable": False,
            "bidResultAvailable": False,
            "raCreated": False,
            "raResultAvailable": False,
            "raNumber": None,
            "raUrl": None,
            "raStartDate": None,
            "raEndDate": None,
            "bidResultUrl": None,
            "raResultUrl": None,
            "gemResultStatus": STATUS_NOT_FOUND if result_not_found and (ongoing_not_found or ongoing_data is None) else STATUS_NOT_AVAILABLE,
            "gemPageStatus": None,
            "resultCheckError": "GeM returned 404 No data found" if result_not_found and (ongoing_not_found or ongoing_data is None) else "Exact tender was not matchable in selected GeM filters.",
            "rawGemMatchedDoc": None,
            "debug": {
                "numFound": num_found,
                "returnedBidNumbers": returned_bids,
                "returnedParentBidNumbers": returned_parents,
                "ongoingReturnedBidNumbers": ongoing_returned_bids,
                "ongoingReturnedParentBidNumbers": ongoing_returned_parents,
                "matched_doc_id": None,
                "b_bid_number": None,
                "b_bid_number_parent": None,
                "b_id_parent": None,
                "ra_id": None,
                "is_direct_bid": False,
                "is_ra_doc": False,
                "result_filter_type": RESULT_FILTER_TYPE,
                "ongoing_filter_type": ONGOING_FILTER_TYPE,
            },
        }

    result_bid_value = str(first_value((result_doc or {}).get("b_bid_number")) or "").strip().upper()
    result_parent_value = str(first_value((result_doc or {}).get("b_bid_number_parent")) or "").strip().upper()
    result_direct_doc_id = first_value((result_doc or {}).get("id")) or first_value((result_doc or {}).get("b_id"))
    bid_parent_id = first_value((result_doc or {}).get("b_id_parent"))
    result_status_text = extract_status_text(result_doc)
    is_direct_bid = bool(result_bid_value == bid and not result_parent_value)
    bid_result_available = bool(
        is_direct_bid
        and result_direct_doc_id
        and is_evaluated_status_text(result_status_text)
    )

    ongoing_bid_value = str(first_value((ongoing_doc or {}).get("b_bid_number")) or "").strip().upper()
    ongoing_parent_value = str(first_value((ongoing_doc or {}).get("b_bid_number_parent")) or "").strip().upper()
    ongoing_doc_id = first_value((ongoing_doc or {}).get("id")) or first_value((ongoing_doc or {}).get("b_id"))
    ra_match = GEM_RA_RE.search(ongoing_bid_value or "")
    ra_number = ra_match.group(0).upper() if ra_match else None
    is_ra_doc = bool(ongoing_parent_value == bid and ra_number and "/R/" in (ongoing_bid_value or ""))
    ra_created = is_ra_doc
    ra_id = ongoing_doc_id if is_ra_doc else None
    ongoing_status_text = extract_status_text(ongoing_doc)
    ra_result_available = bool(is_ra_doc and ra_id and is_evaluated_status_text(ongoing_status_text))
    if is_ra_doc and bid_parent_id:
        bid_result_available = True
    result_available = bool(bid_result_available or ra_result_available)

    if bid_result_available and ra_result_available:
        status = STATUS_BID_AND_RA_AVAILABLE
    elif ra_result_available:
        status = STATUS_RA_AVAILABLE
    elif ra_created:
        status = STATUS_RA_CREATED
    elif bid_result_available:
        status = STATUS_BID_AVAILABLE
    else:
        status = STATUS_NOT_AVAILABLE

    bid_result_url = None
    if is_direct_bid and bid_result_available:
        bid_result_url = build_result_url(gem_base_url, result_direct_doc_id)
    elif bid_result_available:
        bid_result_url = build_result_url(gem_base_url, bid_parent_id)

    ra_url = build_result_url(gem_base_url, ra_id) if ra_created else None
    ra_result_url = build_result_url(gem_base_url, ra_id) if ra_result_available else None

    return {
        "resultAvailable": result_available,
        "bidResultAvailable": bid_result_available,
        "raCreated": ra_created,
        "raResultAvailable": ra_result_available,
        "raNumber": ra_number,
        "raUrl": ra_url,
        "raStartDate": str(first_value(matched_doc.get("final_start_date_sort")) or "").strip() or None,
        "raEndDate": str(first_value(matched_doc.get("final_end_date_sort")) or "").strip() or None,
        "bidResultUrl": bid_result_url,
        "raResultUrl": ra_result_url,
        "gemResultStatus": status,
        "gemPageStatus": result_status_text or ongoing_status_text or None,
        "resultCheckError": None,
        "rawGemMatchedDoc": matched_doc,
        "confidence": "high" if result_available else ("medium" if matched_doc else "low"),
        "reason": (
            "Original bid result is available and the RA document is also evaluated."
            if status == STATUS_BID_AND_RA_AVAILABLE
            else "RA document matched and its status indicates evaluation/result availability."
            if status == STATUS_RA_AVAILABLE
            else "RA document exists for this bid, but the RA result is not available yet."
            if status == STATUS_RA_CREATED
            else "Direct original bid result is available from the matched GeM document."
            if status == STATUS_BID_AVAILABLE
            else "Exact GeM document matched, but no confirmed result and no RA were available."
        ),
        "debug": {
            "numFound": num_found,
            "returnedBidNumbers": returned_bids,
            "returnedParentBidNumbers": returned_parents,
            "ongoingReturnedBidNumbers": ongoing_returned_bids,
            "ongoingReturnedParentBidNumbers": ongoing_returned_parents,
            "matched_doc_id": result_direct_doc_id or ongoing_doc_id,
            "b_bid_number": result_bid_value or ongoing_bid_value,
            "b_bid_number_parent": result_parent_value or ongoing_parent_value,
            "b_id_parent": bid_parent_id,
            "ra_id": ra_id,
            "is_direct_bid": is_direct_bid,
            "is_ra_doc": is_ra_doc,
            "bid_result_available": bid_result_available,
            "ra_created": ra_created,
            "ra_result_available": ra_result_available,
            "final_gem_result_status": status,
            "result_filter_type": RESULT_FILTER_TYPE,
            "ongoing_filter_type": ONGOING_FILTER_TYPE,
            "reason": (
                "Original bid result is available and the RA document is also evaluated."
                if status == STATUS_BID_AND_RA_AVAILABLE
                else "RA document matched and its status indicates evaluation/result availability."
                if status == STATUS_RA_AVAILABLE
                else "RA document exists for this bid, but the RA result is not available yet."
                if status == STATUS_RA_CREATED
                else "Direct original bid result is available from the matched GeM document."
                if status == STATUS_BID_AVAILABLE
                else "Exact GeM document matched, but no confirmed result and no RA were available."
            ),
            "doc_status_text": result_status_text or ongoing_status_text or None,
        },
    }


def extract_result_tables(page):
    script = """
    () => {
      function clean(v) { return String(v || '').replace(/\\s+/g, ' ').trim(); }
      function nearestHeading(table) {
        let node = table.previousElementSibling;
        let steps = 0;
        while (node && steps < 8) {
          const text = clean(node.innerText || node.textContent || '');
          if (text) return text;
          node = node.previousElementSibling;
          steps += 1;
        }
        return '';
      }
      return Array.from(document.querySelectorAll('table')).map((table, index) => {
        const rows = Array.from(table.querySelectorAll('tr')).map(tr =>
          Array.from(tr.querySelectorAll('th,td')).map(td => clean(td.innerText || td.textContent || ''))
        ).filter(row => row.some(Boolean));
        return {
          index,
          heading: nearestHeading(table),
          rows,
        };
      }).filter(item => item.rows.length);
    }
    """
    return page.evaluate(script)


def classify_table_section(table):
    context = normalize_compare(table.get("heading"))
    joined = normalize_compare(" ".join(" ".join(row) for row in table.get("rows") or []))
    blob = f"{context} {joined}".strip()
    if "technical evaluation" in blob:
        return "technical"
    if "financial evaluation" in blob or "price" in blob or "rank" in blob:
        return "financial"
    if "participant" in blob or "seller name" in blob or "participated on" in blob:
        return "participants"
    return ""


def canonical_row_dict(headers, row):
    data = {}
    for idx, header in enumerate(headers):
        key = normalize_compare(header)
        data[key] = row[idx] if idx < len(row) else ""
    return data


def map_participant_row(row_dict):
    return {
        "seller_name": row_dict.get("seller name") or row_dict.get("seller") or row_dict.get("bidder name"),
        "offered_item": row_dict.get("offered item") or row_dict.get("item") or row_dict.get("items"),
        "make": row_dict.get("make"),
        "model": row_dict.get("model"),
        "title": row_dict.get("title") or row_dict.get("product title"),
        "participated_on": row_dict.get("participated on") or row_dict.get("participation date"),
        "mse_mii_status": row_dict.get("mse mii status") or row_dict.get("mii status") or row_dict.get("mse status"),
        "status": row_dict.get("qualification status") or row_dict.get("status"),
    }


def map_technical_row(row_dict):
    return {
        "seller_name": row_dict.get("seller name") or row_dict.get("seller") or row_dict.get("bidder name"),
        "offered_item": row_dict.get("offered item") or row_dict.get("item") or row_dict.get("items"),
        "make": row_dict.get("make"),
        "model": row_dict.get("model"),
        "title": row_dict.get("title") or row_dict.get("product title"),
        "participated_on": row_dict.get("participated on") or row_dict.get("participation date"),
        "mse_mii_status": row_dict.get("mse mii status") or row_dict.get("mii status") or row_dict.get("mse status"),
        "technical_status": row_dict.get("status") or row_dict.get("technical status") or row_dict.get("qualification status"),
    }


def map_financial_row(row_dict):
    return {
        "seller_name": row_dict.get("seller name") or row_dict.get("seller") or row_dict.get("bidder name"),
        "offered_item": row_dict.get("offered item") or row_dict.get("item") or row_dict.get("items"),
        "total_price": row_dict.get("total price") or row_dict.get("price") or row_dict.get("quoted price"),
        "rank": row_dict.get("rank") or row_dict.get("l1 rank") or row_dict.get("status"),
        "financial_status": row_dict.get("financial status") or row_dict.get("status"),
    }


def find_our_company(participants, technical, financial):
    result = {
        "ourCompanyParticipated": False,
        "ourCompanyTechnicalStatus": None,
        "ourCompanyFinancialRank": None,
        "ourCompanyFinalPrice": None,
    }
    aliases = [normalize_compare(alias) for alias in OUR_COMPANY_ALIASES]
    def matches(name):
        normalized = normalize_compare(name)
        return bool(normalized and any(alias in normalized for alias in aliases))
    for row in participants:
        if matches(row.get("seller_name")):
            result["ourCompanyParticipated"] = True
            break
    for row in technical:
        if matches(row.get("seller_name")):
            result["ourCompanyParticipated"] = True
            result["ourCompanyTechnicalStatus"] = row.get("technical_status")
            break
    for row in financial:
        if matches(row.get("seller_name")):
            result["ourCompanyParticipated"] = True
            result["ourCompanyFinancialRank"] = row.get("rank")
            result["ourCompanyFinalPrice"] = row.get("total_price")
            break
    return result


def parse_result_page_details(page):
    tables = extract_result_tables(page)
    participants = []
    technical = []
    financial = []
    detected_sections = {
        "participants": False,
        "technical": False,
        "financial": False,
    }
    for table in tables:
        rows = table.get("rows") or []
        if len(rows) < 2:
            continue
        section = classify_table_section(table)
        if not section:
            continue
        detected_sections[section] = True
        headers = rows[0]
        for row in rows[1:]:
            row_dict = canonical_row_dict(headers, row)
            if section == "participants":
                participants.append(map_participant_row(row_dict) | {"raw_data": row_dict})
            elif section == "technical":
                technical.append(map_technical_row(row_dict) | {"raw_data": row_dict})
            elif section == "financial":
                financial.append(map_financial_row(row_dict) | {"raw_data": row_dict})
    return {
        "participants": [row for row in participants if normalize_text(row.get("seller_name"))],
        "technicalEvaluation": [row for row in technical if normalize_text(row.get("seller_name"))],
        "financialEvaluation": [row for row in financial if normalize_text(row.get("seller_name"))],
        "detectedSections": detected_sections,
    }


def compute_current_stage(source_type, parsed_status, details):
    detected_sections = details.get("detectedSections") or {}
    technical_rows = details.get("technicalEvaluation") or []
    financial_rows = details.get("financialEvaluation") or []
    technical_available = bool(detected_sections.get("technical") or technical_rows)
    financial_available = bool(detected_sections.get("financial") or financial_rows)
    if source_type == "RA":
        if financial_available:
            return STAGE_RA_FINANCIAL
        if technical_available:
            return STAGE_RA_TECHNICAL
        return STAGE_RA_CREATED
    if financial_available:
        return STAGE_BID_FINANCIAL
    if technical_available:
        return STAGE_BID_TECHNICAL
    if parsed_status in {STATUS_BID_AVAILABLE, STATUS_BID_AND_RA_AVAILABLE}:
        return STAGE_BID_RESULT
    return STATUS_NOT_AVAILABLE


def open_and_parse_result_details(page, url):
    if not url:
        return {"participants": [], "technicalEvaluation": [], "financialEvaluation": [], "parseError": "Missing result URL"}
    page.goto(url, wait_until="load", timeout=45000)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        page.wait_for_timeout(2500)
    details = parse_result_page_details(page)
    details["pageUrl"] = page.url
    return details


def gem_payload(bid_number, bid_status_type):
    return {
        "param": {"searchBid": bid_number, "searchType": "fullText"},
        "filter": {
            "bidStatusType": bid_status_type,
            "byType": "all",
            "highBidValue": "",
            "byEndDate": {"from": "", "to": ""},
            "sort": "Bid-End-Date-Latest",
        },
    }


def ensure_gem_page(context, page, gem_base_url):
    all_bids_url = f"{gem_base_url}/all-bids"
    if not page.url.startswith(all_bids_url):
        page.goto(all_bids_url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1500)
    cookies = context.cookies(gem_base_url)
    csrf = next((cookie.get("value") for cookie in cookies if cookie.get("name") == "csrf_gem_cookie"), "")
    if not csrf:
        logging.warning("csrf_gem_cookie not found; open GeM in the headed browser once if requests fail")
    return csrf


def fetch_gem_bid_status(context, page, config, bid_number, bid_status_type):
    gem_base_url = config["gem_base_url"]
    csrf = ensure_gem_page(context, page, gem_base_url)
    request_url = f"{gem_base_url}/all-bids-data"
    body = f"payload={urllib.parse.quote(json.dumps(gem_payload(bid_number, bid_status_type), separators=(',', ':')))}&csrf_bd_gem_nk={urllib.parse.quote(csrf)}"

    script = """
    async ({ requestUrl, body }) => {
      const response = await fetch(requestUrl, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Accept': 'application/json, text/javascript, */*; q=0.01',
          'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
          'X-Requested-With': 'XMLHttpRequest'
        },
        body
      });
      const text = await response.text();
      return { status: response.status, text };
    }
    """
    result = page.evaluate(script, {"requestUrl": request_url, "body": body})
    status = int(result.get("status") or 0)
    text = result.get("text") or ""
    logging.info("GeM response filter=%s status=%s snippet=%s", bid_status_type, status, text[:250])

    if status == 404:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = {}
        if parsed.get("code") == 404 and str(parsed.get("message", "")).lower() == "no data found":
            return {"_gem_not_found": True, "raw_text": text}
    if status < 200 or status >= 300:
        raise RuntimeError(f"GeM request failed HTTP {status}: {text[:500]}")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"GeM returned non-JSON response: {text[:500]}") from exc


def check_one_tender(context, page, config, tender, *, dry_run=False, force_downgrade=False, source="LOCAL_AGENT"):
    tender_id = tender.get("id")
    bid_number = str(tender.get("bidNumber") or "").strip().upper()
    if not tender_id or not GEM_BID_RE.fullmatch(bid_number):
        raise ValueError("Pending tender is missing a valid GeM bid number.")

    logging.info("checking tender_id=%s bid=%s", tender_id, bid_number)
    last_error = None
    for attempt in range(1, 4):
        try:
            result_raw = fetch_gem_bid_status(context, page, config, bid_number, RESULT_FILTER_TYPE)
            ongoing_raw = fetch_gem_bid_status(context, page, config, bid_number, ONGOING_FILTER_TYPE)
            parsed = parse_gem_response(bid_number, config["gem_base_url"], result_raw, ongoing_raw)
            payload = {
                "bidNumber": bid_number,
                "checkedAt": utc_now_iso(),
                "dryRun": dry_run,
                "forceDowngrade": force_downgrade,
                "source": source,
                "rawGemResponse": {
                    "result": result_raw,
                    "ongoing": ongoing_raw,
                },
                **{k: v for k, v in parsed.items() if k != "debug"},
            }
            response = post_json(config, f"/api/tenders/{tender_id}/ingest-gem-result", payload)
            debug = parsed.get("debug") or {}
            logging.info(
                "ingested tender_id=%s searched_bid=%s matched_doc_id=%s b_bid_number=%s b_bid_number_parent=%s "
                "is_direct_bid=%s is_ra_doc=%s bid_result_available=%s ra_created=%s ra_result_available=%s "
                "status=%s reason=%s dry_run=%s downgrade_blocked=%s",
                tender_id,
                bid_number,
                debug.get("matched_doc_id"),
                debug.get("b_bid_number"),
                debug.get("b_bid_number_parent"),
                debug.get("is_direct_bid"),
                debug.get("is_ra_doc"),
                payload["bidResultAvailable"],
                payload.get("raCreated"),
                payload["raResultAvailable"],
                response.get("gem_result_status") if isinstance(response, dict) else payload["gemResultStatus"],
                payload.get("gemPageStatus") or payload.get("resultCheckError"),
                dry_run,
                bool((response or {}).get("downgrade_blocked")) if isinstance(response, dict) else False,
            )
            return response if isinstance(response, dict) else payload
        except GemNoDataFound as exc:
            payload = {
                "bidNumber": bid_number,
                "checkedAt": utc_now_iso(),
                "dryRun": dry_run,
                "forceDowngrade": force_downgrade,
                "source": source,
                "resultAvailable": False,
                "bidResultAvailable": False,
                "raCreated": False,
                "raResultAvailable": False,
                "gemResultStatus": STATUS_NOT_FOUND,
                "bidResultUrl": None,
                "raUrl": None,
                "raResultUrl": None,
                "raNumber": None,
                "raStartDate": None,
                "raEndDate": None,
                "gemPageStatus": None,
                "resultCheckError": str(exc),
                "rawGemMatchedDoc": None,
                "rawGemResponse": {"result": {"_gem_not_found": True}, "ongoing": None},
            }
            response = post_json(config, f"/api/tenders/{tender_id}/ingest-gem-result", payload)
            logging.info(
                "ingested tender_id=%s bid=%s result_available=False status=%s reason=%s",
                tender_id,
                bid_number,
                STATUS_NOT_FOUND,
                exc,
            )
            return response if isinstance(response, dict) else payload
        except Exception as exc:
            last_error = exc
            logging.warning("attempt %s failed tender_id=%s bid=%s error=%s", attempt, tender_id, bid_number, exc)
            if attempt < 3:
                time.sleep(2 * attempt)
    raise last_error


def ingest_error(config, tender, error):
    tender_id = tender.get("id")
    bid_number = str(tender.get("bidNumber") or "").strip().upper()
    post_json(
        config,
        f"/api/tenders/{tender_id}/ingest-gem-result-error",
        {
            "bidNumber": bid_number,
            "error": str(error),
            "checkedAt": utc_now_iso(),
        },
    )


def check_one_tender_result_details(context, page, config, tender):
    tender_id = tender.get("id")
    bid_number = str(tender.get("bidNumber") or "").strip().upper()
    if not tender_id or not GEM_BID_RE.fullmatch(bid_number):
        raise ValueError("Tender is missing a valid GeM bid number.")
    result_raw = fetch_gem_bid_status(context, page, config, bid_number, RESULT_FILTER_TYPE)
    ongoing_raw = fetch_gem_bid_status(context, page, config, bid_number, ONGOING_FILTER_TYPE)
    parsed = parse_gem_response(bid_number, config["gem_base_url"], result_raw, ongoing_raw)
    source_type = "RA" if parsed.get("raCreated") else "BID"
    source_number = parsed.get("raNumber") if source_type == "RA" else bid_number
    result_url = (
        parsed.get("raResultUrl")
        or parsed.get("raUrl")
        or parsed.get("bidResultUrl")
    )
    details = {"participants": [], "technicalEvaluation": [], "financialEvaluation": [], "parseError": None}
    if result_url:
        try:
            details = open_and_parse_result_details(page, result_url)
            if (
                (parsed.get("bidResultAvailable") or parsed.get("raCreated") or parsed.get("raResultAvailable"))
                and not any((details.get("detectedSections") or {}).values())
            ):
                details["parseError"] = "Result page opened but no result sections were detected."
        except Exception as exc:
            details = {
                "participants": [],
                "technicalEvaluation": [],
                "financialEvaluation": [],
                "detectedSections": {},
                "parseError": str(exc),
            }
    stage = compute_current_stage(source_type, parsed.get("gemResultStatus"), details)
    ours = find_our_company(
        details.get("participants") or [],
        details.get("technicalEvaluation") or [],
        details.get("financialEvaluation") or [],
    )
    summary = {
        "bidResultAvailable": bool(parsed.get("bidResultAvailable")),
        "bidTechnicalAvailable": bool(source_type == "BID" and ((details.get("detectedSections") or {}).get("technical") or details.get("technicalEvaluation"))),
        "bidFinancialAvailable": bool(source_type == "BID" and ((details.get("detectedSections") or {}).get("financial") or details.get("financialEvaluation"))),
        "raCreated": bool(parsed.get("raCreated")),
        "raNumber": parsed.get("raNumber"),
        "raStartDate": parsed.get("raStartDate"),
        "raEndDate": parsed.get("raEndDate"),
        "raResultAvailable": bool(parsed.get("raResultAvailable")),
        "raTechnicalAvailable": bool(source_type == "RA" and ((details.get("detectedSections") or {}).get("technical") or details.get("technicalEvaluation"))),
        "raFinancialAvailable": bool(source_type == "RA" and ((details.get("detectedSections") or {}).get("financial") or details.get("financialEvaluation"))),
        **ours,
    }
    payload = {
        "gemBidNumber": bid_number,
        "sourceType": source_type,
        "sourceNumber": source_number,
        "resultUrl": result_url,
        "currentStage": stage,
        "participants": details.get("participants") or [],
        "technicalEvaluation": details.get("technicalEvaluation") or [],
        "financialEvaluation": details.get("financialEvaluation") or [],
        "summary": summary,
        "checkedAt": utc_now_iso(),
        "parseError": details.get("parseError"),
        "changesDetected": {
            "participants_count": len(details.get("participants") or []),
            "technical_count": len(details.get("technicalEvaluation") or []),
            "financial_count": len(details.get("financialEvaluation") or []),
            "participants_detected": bool((details.get("detectedSections") or {}).get("participants")),
            "technical_detected": bool((details.get("detectedSections") or {}).get("technical")),
            "financial_detected": bool((details.get("detectedSections") or {}).get("financial")),
        },
    }
    response = post_json(config, f"/api/tenders/{tender_id}/ingest-gem-result-details", payload)
    logging.info(
        "result-details tender_id=%s bid=%s ra=%s current_source=%s participants=%s technical=%s financial=%s our_found=%s our_tech=%s our_rank=%s stage=%s parse_error=%s",
        tender_id,
        bid_number,
        parsed.get("raNumber"),
        source_type,
        len(payload["participants"]),
        len(payload["technicalEvaluation"]),
        len(payload["financialEvaluation"]),
        summary.get("ourCompanyParticipated"),
        summary.get("ourCompanyTechnicalStatus"),
        summary.get("ourCompanyFinancialRank"),
        stage,
        payload.get("parseError"),
    )
    return response if isinstance(response, dict) else payload


def run_pending(config):
    started_at = utc_now_iso()
    pending = fetch_pending_tenders(config)
    if config["max_tenders"] > 0:
        pending = pending[: config["max_tenders"]]

    summary = {
        "total_pending": len(pending),
        "checked": 0,
        "results_found": 0,
        "not_available": 0,
        "failed": 0,
        "skipped": 0,
    }

    with sync_playwright() as p:
        browser, context, _launch_mode = launch_browser_context(p, config)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            for index, tender in enumerate(pending):
                try:
                    result = check_one_tender(context, page, config, tender)
                    summary["checked"] += 1
                    if result.get("resultAvailable"):
                        summary["results_found"] += 1
                    else:
                        summary["not_available"] += 1
                except Exception as exc:
                    summary["checked"] += 1
                    summary["failed"] += 1
                    logging.exception("failed tender_id=%s bid=%s", tender.get("id"), tender.get("bidNumber"))
                    try:
                        ingest_error(config, tender, exc)
                    except Exception:
                        logging.exception("failed to ingest error for tender_id=%s", tender.get("id"))

                if index < len(pending) - 1:
                    delay = max(0, config["delay"] + random.uniform(-2, 2))
                    time.sleep(delay)
        finally:
            close_browser_context(browser, context)

    finished_at = utc_now_iso()
    post_json(
        config,
        "/api/result-watcher/run-log",
        {
            "started_at": started_at,
            "finished_at": finished_at,
            "run_source": "LOCAL_AGENT",
            **summary,
        },
    )
    logging.info("run summary=%s", summary)
    return summary


def run_result_detail_checks(config):
    started_at = utc_now_iso()
    pending = fetch_recheck_tenders(config)
    if config["max_tenders"] > 0:
        pending = pending[: config["max_tenders"]]
    summary = {
        "total_tenders": len(pending),
        "checked": 0,
        "failed": 0,
        "participants_rows": 0,
        "technical_rows": 0,
        "financial_rows": 0,
    }
    with sync_playwright() as p:
        browser, context, _launch_mode = launch_browser_context(p, config)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            for index, tender in enumerate(pending):
                try:
                    result = check_one_tender_result_details(context, page, config, tender)
                    summary["checked"] += 1
                    summary["participants_rows"] += int(result.get("participants_count") or 0)
                    summary["technical_rows"] += int(result.get("technical_count") or 0)
                    summary["financial_rows"] += int(result.get("financial_count") or 0)
                except Exception:
                    summary["checked"] += 1
                    summary["failed"] += 1
                    logging.exception("failed result details tender_id=%s bid=%s", tender.get("id"), tender.get("bidNumber"))
                if index < len(pending) - 1:
                    delay = max(0, config["delay"] + random.uniform(-2, 2))
                    time.sleep(delay)
        finally:
            close_browser_context(browser, context)
    finished_at = utc_now_iso()
    post_json(
        config,
        "/api/result-watcher/run-log",
        {
            "started_at": started_at,
            "finished_at": finished_at,
            "run_source": "LOCAL_AGENT_RESULT_DETAILS",
            "checked": summary["checked"],
            "failed": summary["failed"],
            "total_pending": summary["total_tenders"],
        },
    )
    logging.info("result details summary=%s", summary)
    return summary


def run_recheck_and_fix_statuses(config, *, dry_run=False, force_downgrade=False, repair_only=False):
    started_at = utc_now_iso()
    pending = fetch_recheck_tenders(config)
    if config["max_tenders"] > 0:
        pending = pending[: config["max_tenders"]]

    summary = {
        "total_tenders": len(pending),
        "checked": 0,
        "results_found": 0,
        "confirmed_bid_results": 0,
        "confirmed_ra_created": 0,
        "confirmed_ra_results": 0,
        "downgraded_blocked": 0,
        "not_available": 0,
        "not_found_on_gem": 0,
        "failed": 0,
        "review_required": 0,
        "false_positive_notifications_invalidated": 0,
        "db_verified_success": 0,
        "db_verified_failed": 0,
        "skipped": 0,
    }

    with sync_playwright() as p:
        browser, context, _launch_mode = launch_browser_context(p, config)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            for index, tender in enumerate(pending):
                try:
                    result = check_one_tender(
                        context,
                        page,
                        config,
                        tender,
                        dry_run=dry_run,
                        force_downgrade=force_downgrade,
                        source="LOCAL_AGENT_REPAIR" if repair_only else "LOCAL_AGENT_RECHECK",
                    )
                    summary["checked"] += 1
                    effective_status = str(result.get("gem_result_status") or result.get("gemResultStatus") or "").upper()
                    if result.get("result_found") or result.get("resultAvailable"):
                        summary["results_found"] += 1
                    if effective_status == STATUS_BID_AVAILABLE:
                        summary["confirmed_bid_results"] += 1
                    elif effective_status == STATUS_RA_CREATED:
                        summary["confirmed_ra_created"] += 1
                    elif effective_status in {STATUS_RA_AVAILABLE, STATUS_BID_AND_RA_AVAILABLE}:
                        summary["confirmed_ra_results"] += 1
                    elif effective_status == STATUS_NOT_FOUND:
                        summary["not_found_on_gem"] += 1
                    else:
                        summary["not_available"] += 1
                    if result.get("downgrade_blocked"):
                        summary["downgraded_blocked"] += 1
                    if result.get("review_required"):
                        summary["review_required"] += 1
                    if result.get("db_verified") is True:
                        summary["db_verified_success"] += 1
                    elif result.get("db_verified") is False:
                        summary["db_verified_failed"] += 1
                    summary["false_positive_notifications_invalidated"] = summary.get("false_positive_notifications_invalidated", 0) + int(result.get("invalidated_notifications") or 0)
                except Exception as exc:
                    summary["checked"] += 1
                    summary["failed"] += 1
                    logging.exception("failed tender_id=%s bid=%s", tender.get("id"), tender.get("bidNumber"))
                    try:
                        ingest_error(config, tender, exc)
                    except Exception:
                        logging.exception("failed to ingest error for tender_id=%s", tender.get("id"))

                if index < len(pending) - 1:
                    delay = max(0, config["delay"] + random.uniform(-2, 2))
                    time.sleep(delay)
        finally:
            close_browser_context(browser, context)

    finished_at = utc_now_iso()
    post_json(
        config,
        "/api/result-watcher/run-log",
        {
            "started_at": started_at,
            "finished_at": finished_at,
            "run_source": "LOCAL_AGENT_REPAIR" if repair_only else ("LOCAL_AGENT_RECHECK_DRY_RUN" if dry_run else "LOCAL_AGENT_RECHECK"),
            **summary,
        },
    )
    logging.info("recheck summary=%s", summary)
    return summary


def run_test_bid(config, bid_number):
    bid = str(bid_number or "").strip().upper()
    if not GEM_BID_RE.fullmatch(bid):
        raise SystemExit("Use a valid bid number like GEM/2026/B/7586698")
    with sync_playwright() as p:
        browser, context, _launch_mode = launch_browser_context(p, config)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            result_raw = fetch_gem_bid_status(context, page, config, bid, RESULT_FILTER_TYPE)
            ongoing_raw = fetch_gem_bid_status(context, page, config, bid, ONGOING_FILTER_TYPE)
            parsed = parse_gem_response(bid, config["gem_base_url"], result_raw, ongoing_raw)
            print(json.dumps(parsed, indent=2, default=str))
        finally:
            close_browser_context(browser, context)


def run_test_result_url(config, result_url):
    with sync_playwright() as p:
        browser, context, _launch_mode = launch_browser_context(p, config)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            details = open_and_parse_result_details(page, result_url)
            print(json.dumps(details, indent=2, default=str))
        finally:
            close_browser_context(browser, context)


def main():
    parser = argparse.ArgumentParser(description="Local GeM Result Watcher Agent")
    parser.add_argument("--run-now", action="store_true", help="Run all pending tenders now")
    parser.add_argument("--check-results", action="store_true", help="Run the basic result status checker for pending tenders")
    parser.add_argument("--check-result-details", action="store_true", help="Open result pages and ingest participants/technical/financial details")
    parser.add_argument("--test-bid", help="Test one GeM bid number without ingesting to Tender AI")
    parser.add_argument("--test-result-url", help="Test one GeM result URL and print parsed tables")
    parser.add_argument("--recheck-and-fix-statuses", action="store_true", help="Recheck all ended tenders and fix old result statuses")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without saving tender rows")
    parser.add_argument("--apply", action="store_true", help="Apply repair updates to the database")
    parser.add_argument("--force-downgrade", action="store_true", help="Allow downgrading previously available result states")
    parser.add_argument("--repair-result-statuses", action="store_true", help="Prioritize suspicious result regressions for repair")
    args = parser.parse_args()

    config = load_config(require_api=not (args.test_bid or args.test_result_url))
    setup_logging(config["log_level"])

    if args.test_result_url:
        run_test_result_url(config, args.test_result_url)
        return
    if args.test_bid:
        run_test_bid(config, args.test_bid)
        return
    if args.check_result_details:
        run_result_detail_checks(config)
        return
    if args.repair_result_statuses:
        run_recheck_and_fix_statuses(
            config,
            dry_run=(not args.apply) or args.dry_run,
            force_downgrade=args.force_downgrade,
            repair_only=True,
        )
        return
    if args.recheck_and_fix_statuses:
        run_recheck_and_fix_statuses(
            config,
            dry_run=(not args.apply) if not args.dry_run else True,
            force_downgrade=args.force_downgrade,
        )
        return
    if args.check_results:
        run_pending(config)
        return
    run_pending(config)


if __name__ == "__main__":
    main()
