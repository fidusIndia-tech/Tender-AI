import os
import json
import random
import re
import threading
import time
import traceback
import urllib.parse
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pathlib import Path

import httpx
import database
from gem_watcher.scanner import (
    PLAYWRIGHT_BROWSER_CHANNEL,
    _detect_edge_executable,
    _launch_browser,
    _should_retry_with_edge,
)
from gem_bid_utils import getCanonicalGemBidNumber

RESULT_STATUS_PENDING = "PENDING"
RESULT_STATUS_NOT_AVAILABLE = "NOT_AVAILABLE_YET"
RESULT_STATUS_BID_AVAILABLE = "BID_RESULT_AVAILABLE"
RESULT_STATUS_RA_AVAILABLE = "RA_RESULT_AVAILABLE"
RESULT_STATUS_BID_AND_RA_AVAILABLE = "BID_AND_RA_RESULT_AVAILABLE"
RESULT_STATUS_FAILED = "FAILED_TO_CHECK"
GEM_RA_NUMBER_PATTERN = re.compile(r"\b(GEM/\d{4}/R/\d+)\b", re.I)
GEM_BID_NUMBER_TEXT_PATTERN = re.compile(r"\bGEM/\d{4}/B/\d+\b", re.I)
DEBUG_SCREENSHOT_DIR = Path(__file__).resolve().parent / "debug-screenshots"
DEBUG_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
GEM_ALL_BIDS_URL = "https://bidplus.gem.gov.in/all-bids"
GEM_ALL_BIDS_DATA_URL = "https://bidplus.gem.gov.in/all-bids-data"
GEM_NETWORK_USER_AGENT = os.environ.get(
    "GEM_RESULT_NETWORK_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
)
GEM_NETWORK_COOKIE_HEADER = os.environ.get("GEM_RESULT_NETWORK_COOKIES", "").strip()

DEFAULT_RESULT_URLS = [
    "https://bidplus.gem.gov.in/all-bids",
]
RESULT_SEARCH_URLS = [
    url.strip() for url in os.environ.get("GEM_RESULT_SEARCH_URLS", ",".join(DEFAULT_RESULT_URLS)).split(",") if url.strip()
]
CHECK_DELAY_MIN_SECONDS = float(os.environ.get("GEM_RESULT_CHECK_DELAY_MIN_SECONDS", "5"))
CHECK_DELAY_MAX_SECONDS = float(os.environ.get("GEM_RESULT_CHECK_DELAY_MAX_SECONDS", "10"))
PAGE_TIMEOUT_MS = int(os.environ.get("GEM_RESULT_PAGE_TIMEOUT_MS", "45000"))
RESULT_WATCHER_HEADLESS = os.environ.get("GEM_RESULT_WATCHER_HEADLESS", "true").strip().lower() not in {"0", "false", "no"}
SCHEDULE_HOURS = [
    int(part.strip()) for part in os.environ.get("GEM_RESULT_WATCHER_HOURS", "9,21").split(",") if part.strip()
]
SCHEDULE_TIMEZONE = os.environ.get("GEM_RESULT_WATCHER_TIMEZONE", "Asia/Kolkata").strip() or "Asia/Kolkata"
POLL_SECONDS = int(os.environ.get("GEM_RESULT_WATCHER_POLL_SECONDS", "60"))

_scheduler_started = False
_scheduler_lock = threading.Lock()
_watcher_last_slot = None


def _watcher_log(message: str):
    print(f"[result_watcher] {message}")


def _get_scheduler_timezone():
    try:
        return ZoneInfo(SCHEDULE_TIMEZONE)
    except ZoneInfoNotFoundError:
        fallback = timezone(timedelta(hours=5, minutes=30))
        _watcher_log(
            f"timezone '{SCHEDULE_TIMEZONE}' not available; falling back to fixed UTC+05:30 for local scheduling"
        )
        return fallback


def _normalize_space(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_for_compare(value) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _normalize_space(value).lower()).strip()


def _first(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _extract_gem_ra_number(*values) -> str | None:
    for value in values:
        text = _normalize_space(value)
        if not text:
            continue
        match = GEM_RA_NUMBER_PATTERN.search(text)
        if match:
            return match.group(1).upper()
    return None


def _parse_bid_end_datetime(value):
    text = _normalize_space(value)
    if not text:
        return None
    for fmt in (
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d-%m-%Y",
        "%Y-%m-%d",
        "%d/%m/%Y",
    ):
        try:
            return datetime.strptime(text[: len(fmt)], fmt)
        except ValueError:
            continue
    match = re.search(r"(\d{2})-(\d{2})-(\d{4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?", text)
    if match:
        hh = int(match.group(4) or 0)
        mm = int(match.group(5) or 0)
        ss = int(match.group(6) or 0)
        return datetime(int(match.group(3)), int(match.group(2)), int(match.group(1)), hh, mm, ss)
    return None


def _is_tender_eligible(tender: dict, now: datetime | None = None) -> bool:
    if tender.get("result_available") or tender.get("result_declared"):
        return False
    if not getCanonicalGemBidNumber(tender):
        return False
    bid_end_dt = _parse_bid_end_datetime(tender.get("bid_end_datetime"))
    if bid_end_dt is None:
        return False
    current = now or datetime.now()
    return bid_end_dt < current


def _status_from_text(text: str) -> str | None:
    return None


def _extract_l1_seller_name(text: str) -> str | None:
    for line in str(text or "").splitlines():
        clean = _normalize_space(line)
        if not clean:
            continue
        match = re.search(r"\bL1\b\s*[:\-]?\s*(.+)", clean, re.I)
        if match:
            return _normalize_space(match.group(1))[:300]
        match = re.search(r"lowest\s+1\s+seller\s*[:\-]?\s*(.+)", clean, re.I)
        if match:
            return _normalize_space(match.group(1))[:300]
    return None


def _extract_company_rank_and_status(text: str, company_name: str | None) -> tuple[str | None, str | None]:
    normalized_company = _normalize_for_compare(company_name)
    if not normalized_company:
        return None, None
    for line in str(text or "").splitlines():
        clean = _normalize_space(line)
        if not clean:
            continue
        normalized_line = _normalize_for_compare(clean)
        if normalized_company not in normalized_line:
            continue
        rank_match = re.search(r"\b(L[1-9])\b", clean, re.I)
        rank = rank_match.group(1).upper() if rank_match else None
        status = None
        if re.search(r"\bawarded\b|\bwinner\b|\bsuccessful\b", clean, re.I):
            status = "AWARDED"
        elif rank:
            status = rank
        elif re.search(r"\bqualified\b|\btechnically responsive\b", clean, re.I):
            status = "QUALIFIED"
        return rank, status
    return None, None


def _extract_result_page_text(page) -> str:
    texts = []
    try:
        texts.append(page.locator("body").inner_text(timeout=5000))
    except Exception:
        pass
    try:
        main_text = page.evaluate(
            """() => Array.from(document.querySelectorAll('main, section, article, table, div'))
                .map(el => (el.innerText || '').trim())
                .filter(Boolean)
                .slice(0, 20)
                .join('\\n')"""
        )
        if main_text:
            texts.append(main_text)
    except Exception:
        pass
    return "\n".join(t for t in texts if t).strip()


def _iter_scopes(page):
    scopes = [page]
    try:
        scopes.extend([frame for frame in page.frames if frame != page.main_frame])
    except Exception:
        pass
    return scopes


def _scope_name(scope) -> str:
    try:
        return getattr(scope, "url", None) or getattr(scope, "name", None) or "page"
    except Exception:
        return "page"


def _scope_text(scope) -> str:
    try:
        return scope.locator("body").inner_text(timeout=5000)
    except Exception:
        pass
    try:
        return scope.evaluate(
            """() => Array.from(document.querySelectorAll('main, section, article, table, div'))
                .map(el => (el.innerText || '').trim())
                .filter(Boolean)
                .slice(0, 20)
                .join('\\n')"""
        ) or ""
    except Exception:
        return ""


def _is_bid_listing_page(page) -> bool:
    text = _normalize_for_compare(_extract_result_page_text(page))
    if ("bid listing" in text) or ("advance search" in text) or ("showing" in text and "records" in text):
        return True
    return False


def _ensure_bid_listing_page(page) -> bool:
    if _is_bid_listing_page(page):
        return True

    # GeM sometimes lands on the portal homepage even when /all-bids was opened.
    # In that state the "Bids List" / "List of Bids" nav item is present and
    # takes us to the actual searchable listing page.
    bids_list_selectors = [
        "a:has-text('Bids List')",
        "a:has-text('List of Bids')",
        "text='Bids List'",
        "text='List of Bids'",
    ]
    for selector in bids_list_selectors:
        try:
            link = page.locator(selector).first
            if link.count() == 0:
                continue
            link.click(timeout=4000)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                page.wait_for_timeout(2500)
            if _is_bid_listing_page(page):
                return True
        except Exception:
            continue

    navigation_selectors = [
        "a[href*='all-bids']",
        "a[href*='bidlists']",
        "a:has-text('Bids List')",
        "a:has-text('Bid Listing')",
    ]
    for selector in navigation_selectors:
        try:
            link = page.locator(selector).first
            if link.count() == 0:
                continue
            link.click(timeout=4000)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                page.wait_for_timeout(2500)
            if _is_bid_listing_page(page):
                return True
        except Exception:
            continue
    return _is_bid_listing_page(page)


def _page_text_has_phrase(text: str, phrase: str) -> bool:
    return _normalize_for_compare(phrase) in _normalize_for_compare(text)


def _safe_debug_screenshot_name(bid_number: str) -> str:
    normalized = _normalize_space(bid_number).upper()
    normalized = normalized.replace("/", "-")
    normalized = re.sub(r"[^A-Z0-9\-]+", "-", normalized)
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-")
    return normalized or "unknown-bid"


def _save_debug_screenshot(page, bid_number: str, step: str) -> str | None:
    try:
        filename = f"result-check-{_safe_debug_screenshot_name(bid_number)}-{step}.png"
        path = DEBUG_SCREENSHOT_DIR / filename
        page.screenshot(path=str(path), full_page=True)
        return str(path)
    except Exception as exc:
        _watcher_log(f"screenshot failed bid={bid_number} step={step} error={type(exc).__name__}: {exc}")
        return None


def _standardize_result_payload(result: dict | None) -> dict:
    payload = dict(result or {})
    gem_result_status = payload.get("gem_result_status") or payload.get("status") or RESULT_STATUS_FAILED
    payload["gem_result_status"] = gem_result_status
    payload["status"] = gem_result_status
    payload["result_available"] = bool(payload.get("result_available", False))
    payload["bid_result_available"] = bool(payload.get("bid_result_available", False))
    payload["ra_result_available"] = bool(payload.get("ra_result_available", False))
    payload.setdefault("gem_result_url", None)
    payload.setdefault("gem_ra_result_url", None)
    payload.setdefault("gem_ra_number", None)
    payload.setdefault("reason", "")
    payload.setdefault("failure_details", [])
    return payload


def _load_cookie_header_into_client(client: httpx.Client, cookie_header: str):
    for part in (cookie_header or "").split(";"):
        piece = part.strip()
        if not piece or "=" not in piece:
            continue
        key, value = piece.split("=", 1)
        client.cookies.set(key.strip(), value.strip(), domain="bidplus.gem.gov.in")


def _collect_string_values(value, acc: list[str]):
    if value is None:
        return
    if isinstance(value, str):
        acc.append(value)
        return
    if isinstance(value, dict):
        for item in value.values():
            _collect_string_values(item, acc)
        return
    if isinstance(value, list):
        for item in value:
            _collect_string_values(item, acc)


def _extract_urls_from_text(text: str, label: str) -> str | None:
    patterns = [
        rf'href=["\']([^"\']+)["\'][^>]*>\s*{re.escape(label)}\s*<',
        rf'{re.escape(label)}.*?href=["\']([^"\']+)["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I | re.S)
        if match:
            return match.group(1)
    return None


def _build_bid_status_payload(bid_number: str) -> str:
    body_json = {
        "param": {"searchBid": bid_number, "searchType": "fullText"},
        "filter": {
            "bidStatusType": "bidrastatus",
            "byType": "all",
            "highBidValue": "",
            "byEndDate": {"from": "", "to": ""},
            "sort": "Bid-End-Date-Latest",
        },
    }
    return "payload=" + urllib.parse.quote(json.dumps(body_json, separators=(",", ":"))) 


def _extract_doc_field_values(docs: list[dict], field_name: str) -> list[str]:
    values: list[str] = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        raw_value = doc.get(field_name)
        if isinstance(raw_value, list):
            for item in raw_value:
                text = _normalize_space(item)
                if text:
                    values.append(text)
        else:
            text = _normalize_space(raw_value)
            if text:
                values.append(text)
    return values


def _build_gem_bid_result_url(doc_id) -> str | None:
    value = _normalize_space(_first(doc_id))
    if not value:
        return None
    return None


def _build_gem_search_url(identifier: str | None) -> str | None:
    value = _normalize_space(identifier).upper()
    if not value:
        return None
    return f"{GEM_ALL_BIDS_URL}#bidrastatus-search-{urllib.parse.quote(value, safe='')}"


def _build_gem_ra_result_url(doc_id) -> str | None:
    value = _normalize_space(_first(doc_id))
    if not value:
        return None
    return None


def debug_gem_exact_result_search(bid_number: str) -> dict:
    canonical_bid_number = _normalize_space(bid_number).upper()
    if not GEM_BID_NUMBER_TEXT_PATTERN.fullmatch(canonical_bid_number):
        return {
            "request_url": GEM_ALL_BIDS_DATA_URL,
            "request_method": "POST",
            "request_payload": None,
            "searched_bid_number": canonical_bid_number,
            "docs_count": 0,
            "num_found": 0,
            "all_returned_b_bid_number_values": [],
            "all_returned_b_bid_number_parent_values": [],
            "searched_bid_found": False,
            "matched_doc_json": None,
            "exact_search_filter_applied": False,
            "reason": "Invalid GeM Bid Number. Expected format GEM/YYYY/B/NUMBER.",
        }

    payload_json = {
        "param": {"searchBid": canonical_bid_number, "searchType": "fullText"},
        "filter": {
            "bidStatusType": "bidrastatus",
            "byType": "all",
            "highBidValue": "",
            "byEndDate": {"from": "", "to": ""},
            "sort": "Bid-End-Date-Latest",
        },
    }
    request_payload = {
        "payload": payload_json,
        "csrf_bd_gem_nk": "<dynamic csrf token from csrf_gem_cookie>",
    }

    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": "https://bidplus.gem.gov.in",
        "Pragma": "no-cache",
        "Referer": GEM_ALL_BIDS_URL,
        "User-Agent": GEM_NETWORK_USER_AGENT,
        "X-Requested-With": "XMLHttpRequest",
    }

    with httpx.Client(follow_redirects=True, timeout=45.0, headers={"User-Agent": GEM_NETWORK_USER_AGENT}) as client:
        if GEM_NETWORK_COOKIE_HEADER:
            _load_cookie_header_into_client(client, GEM_NETWORK_COOKIE_HEADER)

        landing = client.get(
            GEM_ALL_BIDS_URL,
            headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
        )
        csrf_token = client.cookies.get("csrf_gem_cookie")
        if not csrf_token:
            return {
                "request_url": GEM_ALL_BIDS_DATA_URL,
                "request_method": "POST",
                "request_payload": request_payload,
                "searched_bid_number": canonical_bid_number,
                "docs_count": 0,
                "num_found": 0,
                "all_returned_b_bid_number_values": [],
                "all_returned_b_bid_number_parent_values": [],
                "searched_bid_found": False,
                "matched_doc_json": None,
                "exact_search_filter_applied": False,
                "reason": "csrf_gem_cookie not found after opening GeM all-bids page.",
                "landing_url": str(landing.url),
                "landing_snippet": _normalize_space(landing.text)[:1500],
            }

        body = _build_bid_status_payload(canonical_bid_number) + "&csrf_bd_gem_nk=" + csrf_token
        response = client.post(GEM_ALL_BIDS_DATA_URL, headers=headers, content=body)
        response_text = response.text
        base_result = {
            "request_url": GEM_ALL_BIDS_DATA_URL,
            "request_method": "POST",
            "request_payload": request_payload | {"csrf_bd_gem_nk": csrf_token},
            "searched_bid_number": canonical_bid_number,
            "landing_url": str(landing.url),
            "response_status": response.status_code,
            "response_snippet": _normalize_space(response_text)[:3000],
        }
        if response.status_code != 200:
            return {
                **base_result,
                "docs_count": 0,
                "num_found": 0,
                "all_returned_b_bid_number_values": [],
                "all_returned_b_bid_number_parent_values": [],
                "searched_bid_found": False,
                "matched_doc_json": None,
                "exact_search_filter_applied": False,
                "reason": f"GeM exact search request failed with HTTP {response.status_code}.",
            }

        try:
            data = response.json()
        except Exception as exc:
            return {
                **base_result,
                "docs_count": 0,
                "num_found": 0,
                "all_returned_b_bid_number_values": [],
                "all_returned_b_bid_number_parent_values": [],
                "searched_bid_found": False,
                "matched_doc_json": None,
                "exact_search_filter_applied": False,
                "reason": f"GeM exact search returned non-JSON data ({type(exc).__name__}).",
            }

        inner = (data.get("response") or {}).get("response") or {}
        docs = inner.get("docs") or []
        num_found = int(inner.get("numFound") or 0)
        bid_numbers = [value.upper() for value in _extract_doc_field_values(docs, "b_bid_number")]
        parent_bid_numbers = [value.upper() for value in _extract_doc_field_values(docs, "b_bid_number_parent")]

        matched_doc = None
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            doc_bid_numbers = [value.upper() for value in _extract_doc_field_values([doc], "b_bid_number")]
            doc_parent_numbers = [value.upper() for value in _extract_doc_field_values([doc], "b_bid_number_parent")]
            if canonical_bid_number in doc_bid_numbers or canonical_bid_number in doc_parent_numbers:
                matched_doc = doc
                break

        searched_bid_found = matched_doc is not None
        exact_search_filter_applied = searched_bid_found
        reason = "Exact searched bid found in GeM response."
        if not searched_bid_found:
            if num_found > max(len(docs), 10):
                reason = "Exact search filter not applied."
            elif num_found == 0:
                reason = "GeM returned zero documents for this bid number."
            else:
                reason = "Searched bid number was not present in returned GeM documents."

        return {
            **base_result,
            "docs_count": len(docs),
            "num_found": num_found,
            "all_returned_b_bid_number_values": bid_numbers,
            "all_returned_b_bid_number_parent_values": parent_bid_numbers,
            "searched_bid_found": searched_bid_found,
            "matched_doc_json": matched_doc,
            "exact_search_filter_applied": exact_search_filter_applied,
            "reason": reason,
            "raw_response": data,
        }


def parse_gem_result_response(canonical_bid_number: str, data: dict) -> dict:
    """Turn a raw GeM all-bids-data response into a standardized result payload.

    Shared by the server-side network check and the browser-extension ingest path
    so both use identical bid/RA availability logic.
    """
    canonical_bid_number = _normalize_space(canonical_bid_number).upper()
    debug = {
        "method": "network",
        "endpoint": GEM_ALL_BIDS_DATA_URL,
        "searched_bid_number": canonical_bid_number,
        "matched_doc_found": False,
        "b_bid_number": None,
        "b_bid_number_parent": None,
        "b_id_parent": None,
        "ra_id": None,
        "bid_found": False,
        "view_bid_results_found": False,
        "view_ra_results_found": False,
        "final_result_available": False,
        "final_gem_result_status": None,
        "bid_result_url": None,
        "ra_result_url": None,
    }

    inner = (data.get("response") or {}).get("response") or {}
    docs = inner.get("docs") or []
    values = []
    _collect_string_values(data, values)
    joined_text = "\n".join(values)

    matched_doc = None
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        doc_bid_numbers = [value.upper() for value in _extract_doc_field_values([doc], "b_bid_number")]
        doc_parent_numbers = [value.upper() for value in _extract_doc_field_values([doc], "b_bid_number_parent")]
        if canonical_bid_number in doc_bid_numbers or canonical_bid_number in doc_parent_numbers:
            matched_doc = doc
            break

    matched_doc_found = matched_doc is not None
    bid_value = _normalize_space(_first((matched_doc or {}).get("b_bid_number"))).upper() or None
    parent_bid_value = _normalize_space(_first((matched_doc or {}).get("b_bid_number_parent"))).upper() or None
    bid_parent_id = _normalize_space(_first((matched_doc or {}).get("b_id_parent"))) or None
    ra_id = _normalize_space(_first((matched_doc or {}).get("id") or (matched_doc or {}).get("b_id"))) or None
    inferred_ra_number = _extract_gem_ra_number(bid_value, joined_text)
    parent_matches = parent_bid_value == canonical_bid_number
    direct_matches = bid_value == canonical_bid_number
    ra_pattern_match = bool(inferred_ra_number and bid_value and "/R/" in bid_value.upper())

    bid_result_available = bool(bid_parent_id)
    ra_result_available = bool(ra_id and (ra_pattern_match or parent_matches or direct_matches))
    result_available = bool(matched_doc_found and (bid_result_available or ra_result_available))

    if bid_result_available and ra_result_available:
        status = RESULT_STATUS_BID_AND_RA_AVAILABLE
        reason = "Matched GeM result document contains both original bid id and RA id."
    elif bid_result_available:
        status = RESULT_STATUS_BID_AVAILABLE
        reason = "Matched GeM result document contains original bid result id."
    elif ra_result_available:
        status = RESULT_STATUS_RA_AVAILABLE
        reason = "Matched GeM result document contains RA result id."
    elif matched_doc_found:
        status = RESULT_STATUS_NOT_AVAILABLE
        reason = "Exact GeM document matched, but no usable bid/RA result ids were returned."
    else:
        status = RESULT_STATUS_NOT_AVAILABLE
        reason = "Searched bid number was not present in GeM result documents."

    gem_result_url = _build_gem_search_url(canonical_bid_number) if bid_result_available else None
    gem_ra_result_url = _build_gem_search_url(inferred_ra_number or bid_value) if ra_result_available else None

    debug["matched_doc_found"] = matched_doc_found
    debug["b_bid_number"] = bid_value
    debug["b_bid_number_parent"] = parent_bid_value
    debug["b_id_parent"] = bid_parent_id
    debug["ra_id"] = ra_id
    debug["bid_found"] = matched_doc_found
    debug["view_bid_results_found"] = bid_result_available
    debug["view_ra_results_found"] = ra_result_available
    debug["final_result_available"] = result_available
    debug["final_gem_result_status"] = status
    debug["bid_result_url"] = gem_result_url
    debug["ra_result_url"] = gem_ra_result_url

    return {
        "card_found": matched_doc_found,
        "matched_doc_found": matched_doc_found,
        "matched_doc_json": matched_doc,
        "bid_result_available": bid_result_available,
        "ra_result_available": ra_result_available,
        "result_available": result_available,
        "gem_result_url": gem_result_url,
        "gem_ra_result_url": gem_ra_result_url,
        "gem_ra_number": inferred_ra_number,
        "status": status,
        "gem_result_status": status,
        "reason": reason,
        "page_text_snippet": _normalize_space(joined_text)[:3000],
        "opened_url": GEM_ALL_BIDS_DATA_URL,
        "failure_details": [],
        "network_debug": debug,
        "b_bid_number": bid_value,
        "b_bid_number_parent": parent_bid_value,
        "b_id_parent": bid_parent_id,
        "ra_id": ra_id,
    }


def checkGemResultByNetwork(bid_number: str):
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": "https://bidplus.gem.gov.in",
        "Pragma": "no-cache",
        "Referer": GEM_ALL_BIDS_URL,
        "User-Agent": GEM_NETWORK_USER_AGENT,
        "X-Requested-With": "XMLHttpRequest",
    }
    canonical_bid_number = _normalize_space(bid_number).upper()
    debug = {
        "method": "network",
        "endpoint": GEM_ALL_BIDS_DATA_URL,
        "csrf_found": False,
        "response_status": None,
        "response_snippet": None,
        "searched_bid_number": canonical_bid_number,
        "matched_doc_found": False,
        "b_bid_number": None,
        "b_bid_number_parent": None,
        "b_id_parent": None,
        "ra_id": None,
        "bid_found": False,
        "view_bid_results_found": False,
        "view_ra_results_found": False,
        "final_result_available": False,
        "final_gem_result_status": None,
        "bid_result_url": None,
        "ra_result_url": None,
    }

    with httpx.Client(follow_redirects=True, timeout=45.0, headers={"User-Agent": GEM_NETWORK_USER_AGENT}) as client:
        if GEM_NETWORK_COOKIE_HEADER:
            _load_cookie_header_into_client(client, GEM_NETWORK_COOKIE_HEADER)

        landing = client.get(GEM_ALL_BIDS_URL, headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})
        csrf_token = client.cookies.get("csrf_gem_cookie")
        debug["csrf_found"] = bool(csrf_token)
        if not csrf_token:
            return {
                "card_found": False,
                "bid_result_available": False,
                "ra_result_available": False,
                "result_available": False,
                "gem_result_url": None,
                "gem_ra_result_url": None,
                "gem_ra_number": None,
                "status": RESULT_STATUS_FAILED,
                "reason": "GeM network search failed: csrf_gem_cookie not found after opening all-bids.",
                "page_text_snippet": _normalize_space(landing.text)[:3000],
                "opened_url": GEM_ALL_BIDS_URL,
                "failure_details": [],
                "network_debug": debug,
            }

        body = _build_bid_status_payload(canonical_bid_number) + "&csrf_bd_gem_nk=" + csrf_token
        response = client.post(GEM_ALL_BIDS_DATA_URL, headers=headers, content=body)
        debug["response_status"] = response.status_code
        raw_text = response.text
        debug["response_snippet"] = _normalize_space(raw_text)[:3000]
        if response.status_code != 200:
            return {
                "card_found": False,
                "bid_result_available": False,
                "ra_result_available": False,
                "result_available": False,
                "gem_result_url": None,
                "gem_ra_result_url": None,
                "gem_ra_number": None,
                "status": RESULT_STATUS_FAILED,
                "reason": f"GeM network search failed: HTTP {response.status_code}",
                "page_text_snippet": debug["response_snippet"],
                "opened_url": GEM_ALL_BIDS_DATA_URL,
                "failure_details": [f"HTTP {response.status_code}"],
                "network_debug": debug,
            }

        try:
            data = response.json()
        except Exception as exc:
            return {
                "card_found": False,
                "bid_result_available": False,
                "ra_result_available": False,
                "result_available": False,
                "gem_result_url": None,
                "gem_ra_result_url": None,
                "gem_ra_number": None,
                "status": RESULT_STATUS_FAILED,
                "reason": f"GeM network search failed: invalid JSON response ({type(exc).__name__}).",
                "page_text_snippet": debug["response_snippet"],
                "opened_url": GEM_ALL_BIDS_DATA_URL,
                "failure_details": [str(exc)],
                "network_debug": debug,
            }

        return parse_gem_result_response(canonical_bid_number, data)


def _find_matching_link(page, bid_number: str):
    target = _normalize_for_compare(bid_number)
    try:
        links = page.evaluate(
            """() => Array.from(document.querySelectorAll('a[href]')).map(a => ({
                href: a.href,
                text: (a.innerText || a.textContent || '').trim()
            }))"""
        )
    except Exception:
        return None
    for link in links or []:
        href = link.get("href") or ""
        text = link.get("text") or ""
        haystack = _normalize_for_compare(f"{text} {href}")
        if target and target in haystack:
            return link
    return None


def _find_matching_card(page, bid_number: str):
    target = _normalize_for_compare(bid_number)
    row_selectors = [".card", ".panel", ".list-group-item", "tr", "div"]
    for scope in _iter_scopes(page):
        for row_selector in row_selectors:
            rows = scope.locator(row_selector)
            try:
                count = min(rows.count(), 120)
            except Exception:
                continue
            for idx in range(count):
                row = rows.nth(idx)
                try:
                    row_text = row.inner_text(timeout=1500)
                except Exception:
                    continue
                normalized = _normalize_for_compare(row_text)
                if normalized and target in normalized:
                    return row, row_text, _scope_name(scope)
    return None, None, None


def _find_bid_container_by_text(page, bid_number: str):
    targets = {
        _normalize_space(bid_number).upper(),
        _normalize_space(bid_number).replace("%2F", "/").upper(),
    }
    for scope in _iter_scopes(page):
        try:
            candidates = scope.locator("div, tr, li, section, article").filter(has_text=bid_number)
            count = min(candidates.count(), 40)
            for idx in range(count):
                candidate = candidates.nth(idx)
                try:
                    text = candidate.inner_text(timeout=1500)
                except Exception:
                    continue
                normalized = _normalize_space(text).upper()
                if any(target in normalized for target in targets):
                    return candidate, text, _scope_name(scope)
        except Exception:
            pass
        try:
            locator = scope.get_by_text(bid_number, exact=False).first
            if locator.count() > 0:
                text = locator.inner_text(timeout=1500)
                return locator, text, _scope_name(scope)
        except Exception:
            pass
    return None, None, None


def _find_action_control(row, label: str):
    locators = [
        row.get_by_role("button", name=label),
        row.get_by_role("link", name=label),
        row.locator(f"button:has-text('{label}')"),
        row.locator(f"a:has-text('{label}')"),
        row.locator(f"[role='button']:has-text('{label}')"),
        row.locator(f"input[value='{label}']"),
    ]
    for locator in locators:
        try:
            if locator.count() > 0:
                return locator.first
        except Exception:
            continue
    return None


def _find_action_control_in_page(page, label: str):
    for scope in _iter_scopes(page):
        locators = [
            scope.get_by_role("button", name=label),
            scope.get_by_role("link", name=label),
            scope.locator(f"button:has-text('{label}')"),
            scope.locator(f"a:has-text('{label}')"),
            scope.locator(f"[role='button']:has-text('{label}')"),
            scope.locator(f"input[value='{label}']"),
        ]
        for locator in locators:
            try:
                if locator.count() > 0:
                    return locator.first
            except Exception:
                continue
    return None


def _open_popup_or_click(locator, page):
    try:
        with page.expect_popup(timeout=5000) as popup_info:
            locator.click(timeout=4000)
        popup = popup_info.value
        popup.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        return popup
    except Exception:
        locator.click(timeout=4000)
        page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        return page


def _open_button_and_capture_url(page, container, button_text: str):
    try:
        button = _find_action_control(container, button_text)
        if button is None:
            return None
        opened_page = _open_popup_or_click(button, page)
        opened_page.wait_for_timeout(1500)
        url = opened_page.url
        text = _extract_result_page_text(opened_page)
        if opened_page is page:
            try:
                page.go_back(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
                page.wait_for_timeout(1000)
            except Exception:
                pass
        else:
            try:
                opened_page.close()
            except Exception:
                pass
        return {"url": url, "text": text}
    except Exception:
        return None


def _detect_result_availability(page, bid_number: str):
    page_text = "\n".join(
        text for text in (_scope_text(scope) for scope in _iter_scopes(page)) if text
    )
    page_text_normalized = _normalize_for_compare(page_text)
    page_has_bid_number = _normalize_for_compare(bid_number) in page_text_normalized
    row, row_text, scope_name = _find_matching_card(page, bid_number)
    if not row:
        row, row_text, scope_name = _find_bid_container_by_text(page, bid_number)

    bid_button_found = (
        _page_text_has_phrase(page_text, "View Bid Results")
        or _page_text_has_phrase(page_text, "View Bid Result")
    )
    ra_button_found = (
        _page_text_has_phrase(page_text, "View RA Results")
        or _page_text_has_phrase(page_text, "View RA Result")
    )

    bid_result = _open_button_and_capture_url(page, row, "View Bid Results") if bid_button_found and row else None
    if not bid_result and bid_button_found:
        bid_result = _open_button_and_capture_url(page, page, "View Bid Results")
    if (not bid_result and bid_button_found) or ra_button_found:
        row, row_text, scope_name = _find_bid_container_by_text(page, bid_number)
    ra_result = _open_button_and_capture_url(page, row, "View RA Results") if row and ra_button_found else None
    if not ra_result and ra_button_found:
        ra_result = _open_button_and_capture_url(page, page, "View RA Results")

    result_available = bool(bid_button_found or ra_button_found)
    if bid_button_found and ra_button_found:
        status = RESULT_STATUS_BID_AND_RA_AVAILABLE
    elif bid_button_found:
        status = RESULT_STATUS_BID_AVAILABLE
    elif ra_button_found:
        status = RESULT_STATUS_RA_AVAILABLE
    else:
        status = RESULT_STATUS_NOT_AVAILABLE

    if result_available:
        if bid_button_found and ra_button_found:
            reason = "View Bid Results and View RA Results found on GeM page"
        elif bid_button_found:
            reason = "View Bid Results found on GeM page"
        else:
            reason = "View RA Results found on GeM page"
    elif page_has_bid_number:
        reason = "Tender card found but no result buttons were visible"
    elif page_has_bid_number:
        reason = "Bid number was visible in page text but no result buttons were found"
    else:
        reason = "Bid number and result buttons were not found on the loaded GeM page"

    return {
        "card_found": page_has_bid_number,
        "matched_card_text": _normalize_space(row_text)[:500],
        "matched_scope": scope_name,
        "bid_result_available": bid_button_found,
        "ra_result_available": ra_button_found,
        "result_available": result_available,
        "gem_result_url": bid_result["url"] if bid_result else None,
        "gem_ra_result_url": ra_result["url"] if ra_result else None,
        "gem_ra_number": _extract_gem_ra_number(row_text, bid_result["text"] if bid_result else None, ra_result["text"] if ra_result else None),
        "status": status,
        "reason": reason,
        "page_text_snippet": _normalize_space(page_text)[:1000],
        "page_has_bid_number": page_has_bid_number,
    }


def _find_bid_ra_status_section(page):
    selectors = [
        "text='Bid/RA Status'",
        "a:has-text('Bid/RA Status')",
        "button:has-text('Bid/RA Status')",
        "[role='tab']:has-text('Bid/RA Status')",
    ]
    for selector in selectors:
        try:
            node = page.locator(selector).first
            if node.count() == 0:
                continue
            section = node.locator("xpath=ancestor::*[self::div or self::section or self::form][1]")
            if section.count() > 0:
                return section.first
        except Exception:
            continue
    return None


def _select_exact_search(section):
    try:
        dropdown = section.locator("select").first
        if dropdown.count() == 0:
            return False
        options = dropdown.locator("option")
        option_count = options.count()
        for idx in range(option_count):
            option = options.nth(idx)
            text = _normalize_space(option.inner_text())
            value = option.get_attribute("value")
            if re.search(r"exact", text, re.I):
                if value:
                    dropdown.select_option(value=value)
                else:
                    dropdown.select_option(label=text)
                return True
    except Exception:
        return False
    return False


def _submit_bid_search(section, bid_number: str):
    debug = {
        "exact_search_selected": False,
        "input_filled": False,
        "search_button_clicked": False,
    }
    debug["exact_search_selected"] = _select_exact_search(section)
    if not debug["exact_search_selected"]:
        return debug

    search_input = None
    selectors = [
        "input[type='search']",
        "input[placeholder*='Search' i]",
        "input[aria-label*='Search' i]",
        "input[name*='search' i]",
        "input[name*='bid' i]",
        "input[placeholder*='Bid' i]",
        "input.form-control",
    ]
    for selector in selectors:
        locator = section.locator(selector).first
        try:
            if locator.count() == 0:
                continue
            locator.wait_for(state="visible", timeout=2500)
            input_type = (locator.get_attribute("type") or "").lower()
            placeholder = (locator.get_attribute("placeholder") or "").lower()
            name_attr = (locator.get_attribute("name") or "").lower()
            if input_type in {"hidden", "date"}:
                continue
            if any(token in f"{placeholder} {name_attr}" for token in ("from", "to", "date")):
                continue
            search_input = locator
            break
        except Exception:
            continue
    if search_input is None:
        return debug

    try:
        search_input.click(timeout=2500)
        search_input.fill("")
        search_input.fill(bid_number)
        debug["input_filled"] = True
    except Exception:
        return debug

    button_selectors = [
        "button[aria-label*='Search' i]",
        "button[title*='Search' i]",
        "button[type='submit']",
        "button:has(svg)",
        "button:has-text('Search')",
        "a:has(svg)",
        "input[type='submit']",
    ]
    for selector in button_selectors:
        try:
            button = section.locator(selector).first
            if button.count() == 0:
                continue
            button.click(timeout=2500)
            debug["search_button_clicked"] = True
            return debug
        except Exception:
            continue
    return debug


def _wait_for_bid_search_result(page, bid_number: str):
    target = _normalize_for_compare(bid_number)
    for _ in range(12):
        try:
            raw_text = _extract_result_page_text(page)
            text = _normalize_for_compare(raw_text)
        except Exception:
            text = ""
            raw_text = ""
        if target and target in text:
            return True, raw_text
        if "showing 1 1 records" in text or "no records found" in text:
            return False, raw_text
        page.wait_for_timeout(1000)
    try:
        return False, _extract_result_page_text(page)
    except Exception:
        return False, ""


def _open_bid_ra_status_view(page) -> bool:
    selectors = [
        "a:has-text('Bid/RA Status')",
        "button:has-text('Bid/RA Status')",
        "[role='tab']:has-text('Bid/RA Status')",
        "text='Bid/RA Status'",
    ]
    for selector in selectors:
        try:
            control = page.locator(selector).first
            if control.count() == 0:
                continue
            control.click(timeout=4000)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                page.wait_for_timeout(2500)
            return True
        except Exception:
            continue
    return False


def _open_result_page(page, bid_number: str):
    failure_details = []
    for url_template in RESULT_SEARCH_URLS:
        url = url_template
        try:
            _watcher_log(f"opening search page url={url} bid={bid_number}")
            page.goto(url, wait_until="load", timeout=PAGE_TIMEOUT_MS)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                page.wait_for_timeout(2500)
            open_screenshot = _save_debug_screenshot(page, bid_number, "01-page-open")
            listing_ready = _ensure_bid_listing_page(page)
            _watcher_log(f"listing-page-check bid={bid_number} opened_url={url} listing_ready={listing_ready} current_url={page.url!r}")
            if not listing_ready:
                _watcher_log(f"listing-page-missing bid={bid_number} current_page_snippet={_normalize_space(_extract_result_page_text(page))[:400]!r}")
            bid_ra_status_opened = _open_bid_ra_status_view(page)
            _watcher_log(f"bid-ra-status-open bid={bid_number} opened={bid_ra_status_opened} current_url={page.url!r}")
            bid_ra_screenshot = _save_debug_screenshot(page, bid_number, "02-bid-ra-status")
            if not bid_ra_status_opened:
                return {
                    "card_found": False,
                    "bid_result_available": False,
                    "ra_result_available": False,
                    "result_available": False,
                    "gem_result_url": None,
                    "gem_ra_result_url": None,
                    "gem_ra_number": None,
                    "status": RESULT_STATUS_FAILED,
                    "reason": "Could not open Bid/RA Status tab. Search was not executed.",
                    "page_text_snippet": _normalize_space(_extract_result_page_text(page))[:3000],
                    "opened_url": url,
                    "screenshot_path": bid_ra_screenshot or open_screenshot,
                    "page_has_bid_number": False,
                    "failure_details": failure_details,
                    "bid_ra_status_tab_clicked": False,
                    "exact_search_selected": False,
                    "input_filled": False,
                    "search_button_clicked": False,
                }
            if "exact search" not in _normalize_for_compare(_extract_result_page_text(page)):
                return {
                    "card_found": False,
                    "bid_result_available": False,
                    "ra_result_available": False,
                    "result_available": False,
                    "gem_result_url": None,
                    "gem_ra_result_url": None,
                    "gem_ra_number": None,
                    "status": RESULT_STATUS_FAILED,
                    "reason": "GeM Bid/RA Status page did not load. Search happened on wrong page/input.",
                    "page_text_snippet": _normalize_space(_extract_result_page_text(page))[:3000],
                    "opened_url": url,
                    "screenshot_path": bid_ra_screenshot or open_screenshot,
                    "page_has_bid_number": False,
                    "failure_details": failure_details,
                    "bid_ra_status_tab_clicked": True,
                    "exact_search_selected": False,
                    "input_filled": False,
                    "search_button_clicked": False,
                }
            section = _find_bid_ra_status_section(page)
            if section is None:
                return {
                    "card_found": False,
                    "bid_result_available": False,
                    "ra_result_available": False,
                    "result_available": False,
                    "gem_result_url": None,
                    "gem_ra_number": None,
                    "gem_ra_result_url": None,
                    "status": RESULT_STATUS_FAILED,
                    "reason": "GeM Bid/RA Status page did not load. Search happened on wrong page/input.",
                    "page_text_snippet": _normalize_space(_extract_result_page_text(page))[:3000],
                    "opened_url": url,
                    "screenshot_path": bid_ra_screenshot or open_screenshot,
                    "page_has_bid_number": False,
                    "failure_details": failure_details,
                    "bid_ra_status_tab_clicked": True,
                    "exact_search_selected": False,
                    "input_filled": False,
                    "search_button_clicked": False,
                }
            search_debug = _submit_bid_search(section, bid_number)
            exact_screenshot = _save_debug_screenshot(page, bid_number, "03-exact-search")
            if not search_debug.get("exact_search_selected"):
                return {
                    "card_found": False,
                    "bid_result_available": False,
                    "ra_result_available": False,
                    "result_available": False,
                    "gem_result_url": None,
                    "gem_ra_result_url": None,
                    "gem_ra_number": None,
                    "status": RESULT_STATUS_FAILED,
                    "reason": "Could not select Exact Search. Search was not executed.",
                    "page_text_snippet": _normalize_space(_extract_result_page_text(page))[:3000],
                    "opened_url": url,
                    "screenshot_path": exact_screenshot or bid_ra_screenshot or open_screenshot,
                    "page_has_bid_number": False,
                    "failure_details": failure_details,
                    "bid_ra_status_tab_clicked": True,
                    "exact_search_selected": False,
                    "input_filled": False,
                    "search_button_clicked": False,
                }
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                page.wait_for_timeout(3500)
            bid_found_after_search, body_text_after_search = _wait_for_bid_search_result(page, bid_number)
            body_text_snippet = _normalize_space(body_text_after_search)[:3000]
            search_screenshot = _save_debug_screenshot(page, bid_number, "04-search-result")
            view_bid_results_found = (
                "view bid results" in _normalize_for_compare(body_text_after_search)
                or "view bid result" in _normalize_for_compare(body_text_after_search)
            )
            view_ra_results_found = (
                "view ra results" in _normalize_for_compare(body_text_after_search)
                or "view ra result" in _normalize_for_compare(body_text_after_search)
            )
            _watcher_log(
                "manual-search-debug "
                f"bid={bid_number} "
                f"bid_ra_status_tab_clicked={bid_ra_status_opened} "
                f"exact_search_selected={search_debug.get('exact_search_selected')} "
                f"input_filled={search_debug.get('input_filled')} "
                f"search_button_clicked={search_debug.get('search_button_clicked')} "
                f"final_bid_number_found={bid_found_after_search} "
                f"view_bid_results_found={view_bid_results_found} "
                f"view_ra_results_found={view_ra_results_found} "
                f"body_text_after_search={body_text_snippet!r}"
            )
            try:
                screenshot_path = search_screenshot or exact_screenshot or bid_ra_screenshot or open_screenshot
            except Exception:
                screenshot_path = None
            result = _detect_result_availability(page, bid_number)
            result["opened_url"] = url
            result["screenshot_path"] = screenshot_path
            result["failure_details"] = failure_details
            result["bid_ra_status_tab_clicked"] = bid_ra_status_opened
            result["exact_search_selected"] = search_debug.get("exact_search_selected")
            result["input_filled"] = search_debug.get("input_filled")
            result["search_button_clicked"] = search_debug.get("search_button_clicked")
            result["page_text_snippet"] = body_text_snippet
            result["card_found"] = bid_found_after_search
            result["bid_result_available"] = view_bid_results_found
            result["ra_result_available"] = view_ra_results_found
            result["result_available"] = bool(view_bid_results_found or view_ra_results_found)
            if view_bid_results_found and view_ra_results_found:
                result["status"] = RESULT_STATUS_BID_AND_RA_AVAILABLE
                result["reason"] = "View Bid Results and View RA Results found on GeM page"
            elif view_bid_results_found:
                result["status"] = RESULT_STATUS_BID_AVAILABLE
                result["reason"] = "View Bid Results found on GeM page"
            elif view_ra_results_found:
                result["status"] = RESULT_STATUS_RA_AVAILABLE
                result["reason"] = "View RA Results found on GeM page"
            elif bid_found_after_search:
                result["status"] = RESULT_STATUS_NOT_AVAILABLE
                result["reason"] = "Tender card found but no result buttons were visible"
            else:
                result["status"] = RESULT_STATUS_NOT_AVAILABLE
                result["reason"] = "Bid number and result buttons were not found on the loaded GeM page"
            if (not bid_found_after_search) and ("gem" not in _normalize_for_compare(body_text_after_search) or "bid ra status" not in _normalize_for_compare(body_text_after_search)):
                result["status"] = RESULT_STATUS_FAILED
                result["reason"] = "GeM Bid/RA Status page did not load. Search happened on wrong page/input."
            return result
        except Exception as exc:
            detail = f"{url} -> {type(exc).__name__}: {exc}"
            failure_details.append(detail)
            _watcher_log(f"search page failed bid={bid_number} {detail}")
    return {
        "card_found": False,
        "bid_result_available": False,
        "ra_result_available": False,
        "result_available": False,
        "gem_result_url": None,
        "gem_ra_result_url": None,
        "gem_ra_number": None,
        "status": RESULT_STATUS_FAILED,
        "reason": "Unable to open or inspect the GeM result page with any supported URL",
        "page_text_snippet": None,
        "opened_url": None,
        "screenshot_path": None,
        "page_has_bid_number": False,
        "failure_details": failure_details,
    }


def _open_result_with_browser(page, tender: dict, company_name: str | None):
    bid_number = getCanonicalGemBidNumber(tender)
    result = _open_result_page(page, bid_number)
    return {
        "card_found": result["card_found"],
        "matched_card_text": result.get("matched_card_text"),
        "matched_scope": result.get("matched_scope"),
        "result_available": result["result_available"],
        "bid_result_available": result["bid_result_available"],
        "ra_result_available": result["ra_result_available"],
        "gem_result_status": result["status"],
        "gem_result_url": result["gem_result_url"],
        "gem_ra_number": result["gem_ra_number"],
        "gem_ra_result_url": result["gem_ra_result_url"],
        "opened_url": result.get("opened_url"),
        "screenshot_path": result.get("screenshot_path"),
        "page_text_snippet": result.get("page_text_snippet"),
        "reason": result.get("reason"),
        "page_has_bid_number": result.get("page_has_bid_number"),
        "failure_details": result.get("failure_details") or [],
        "bid_ra_status_tab_clicked": result.get("bid_ra_status_tab_clicked"),
        "exact_search_selected": result.get("exact_search_selected"),
        "input_filled": result.get("input_filled"),
        "search_button_clicked": result.get("search_button_clicked"),
        "l1_seller_name": None,
        "our_company_rank": None,
        "our_company_status": None,
    }


def _run_with_playwright(tender: dict, company_name: str | None):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser_attempts = []
        detected_edge_path = _detect_edge_executable() if os.name == "nt" else None
        if PLAYWRIGHT_BROWSER_CHANNEL:
            browser_attempts.append(PLAYWRIGHT_BROWSER_CHANNEL)
        else:
            browser_attempts.append(None)
            if os.name == "nt":
                browser_attempts.append("msedge")
                if detected_edge_path:
                    browser_attempts.append({"name": "msedge-explicit", "executable_path": detected_edge_path})

        last_exc = None
        for browser_target in browser_attempts:
            browser_label = browser_target if isinstance(browser_target, str) else "chromium"
            browser = None
            try:
                if isinstance(browser_target, dict) and browser_target.get("executable_path"):
                    browser = pw.chromium.launch(
                        headless=True,
                        executable_path=browser_target["executable_path"],
                        args=[
                            "--disable-features=RendererCodeIntegrity",
                            "--no-sandbox",
                            "--disable-setuid-sandbox",
                            "--disable-dev-shm-usage",
                        ],
                    )
                else:
                    browser = _launch_browser(pw, browser_target if isinstance(browser_target, str) else None)
                page = browser.new_page()
                return _open_result_with_browser(page, tender, company_name)
            except Exception as exc:
                last_exc = exc
                if browser_label != "msedge" and _should_retry_with_edge(exc):
                    _watcher_log(f"retrying with Edge fallback for bid={getCanonicalGemBidNumber(tender) or tender.get('gem_bidding_number')}")
                    continue
            finally:
                if browser is not None:
                    browser.close()
        if last_exc is not None:
            raise last_exc
    return None


def ingest_gem_result(tender_id: int, raw_response: dict):
    """Apply a GeM all-bids-data response that was fetched by the browser extension.

    The extension runs on the user's own IP (which GeM allows), fetches the raw
    JSON, and posts it here so the server never has to reach GeM directly.
    """
    tender = database.get_tender(tender_id)
    if not tender:
        raise ValueError("Tender not found")
    bid_number = getCanonicalGemBidNumber(tender)
    if not bid_number:
        raise ValueError("Valid GeM Bid Number not found. Please check extraction.")
    if not isinstance(raw_response, dict):
        raise ValueError("Invalid GeM response payload from extension.")
    result = parse_gem_result_response(bid_number, raw_response)
    return check_tender_result(tender_id, precomputed_result=result)


def check_tender_result(tender_id: int, precomputed_result: dict | None = None):
    tender = database.get_tender(tender_id)
    if not tender:
        raise ValueError("Tender not found")

    bid_number = getCanonicalGemBidNumber(tender)
    _watcher_log(
        "check-click "
        f"tender_id={tender_id} "
        f"gem_bidding_no={tender.get('gem_bidding_no') or tender.get('gem_bidding_number')!r} "
        f"tender_number={tender.get('tender_number')!r} "
        f"final_bid_number={bid_number!r}"
    )
    if not bid_number:
        raise ValueError("Valid GeM Bid Number not found. Please check extraction.")

    now = datetime.now()
    debug = {
        "tender_id": tender_id,
        "gem_bidding_no": tender.get("gem_bidding_no") or tender.get("gem_bidding_number"),
        "tender_number": tender.get("tender_number"),
        "final_bid_number": bid_number,
        "opened_url": None,
        "screenshot_path": None,
        "card_found": False,
        "view_bid_results_found": False,
        "view_ra_results_found": False,
        "page_text_snippet": None,
        "reason": None,
        "status": None,
        "failure_details": [],
        "bid_ra_status_tab_clicked": None,
        "exact_search_selected": None,
        "input_filled": None,
        "search_button_clicked": None,
        "network_debug": None,
        "matched_doc_found": False,
        "b_bid_number": None,
        "b_bid_number_parent": None,
        "b_id_parent": None,
        "ra_id": None,
        "bid_result_url": None,
        "ra_result_url": None,
    }
    if precomputed_result is None and not _is_tender_eligible(tender, now=now):
        _watcher_log(f"pending bid={bid_number} reason=not-eligible")
        database.update_tender_result(
            tender_id,
            gem_result_status=tender.get("gem_result_status") or RESULT_STATUS_PENDING,
            last_result_checked_at=now,
        )
        updated = database.get_tender(tender_id)
        debug["status"] = updated.get("gem_result_status")
        debug["reason"] = "Tender was not eligible for checking because bid end date has not passed or result was already declared"
        return {
            "tender_id": tender_id,
            "status": updated.get("gem_result_status"),
            "gem_result_status": updated.get("gem_result_status"),
            "result_found": False,
            "tender": updated,
            "debug": debug,
        }

    company_name = (database.get_company_profile() or {}).get("company_name")
    existing_available = bool(tender.get("result_available"))

    try:
        if precomputed_result is not None:
            _watcher_log(f"applying browser-supplied result bid={bid_number} tender_id={tender_id}")
            result = _standardize_result_payload(precomputed_result)
        else:
            _watcher_log(f"checking bid={bid_number} tender_id={tender_id}")
            try:
                result = _standardize_result_payload(checkGemResultByNetwork(bid_number))
            except Exception as network_exc:
                _watcher_log(f"network-check-exception bid={bid_number} {type(network_exc).__name__}: {network_exc}")
                result = _standardize_result_payload({
                    "status": RESULT_STATUS_FAILED,
                    "reason": f"GeM network check could not connect: {network_exc}",
                    "failure_details": [f"{type(network_exc).__name__}: {network_exc}"],
                })
            if result.get("gem_result_status") == RESULT_STATUS_FAILED:
                _watcher_log(
                    f"network-check-failed bid={bid_number} reason={result.get('reason')!r} "
                    f"failure_details={result.get('failure_details')!r}; falling back to Playwright"
                )
                result = _standardize_result_payload(_run_with_playwright(tender, company_name))

        _watcher_log(
            "check-result "
            f"tender_id={tender_id} "
            f"bid_number={bid_number!r} "
            f"opened_url={result.get('opened_url')!r} "
            f"card_found={result.get('card_found')} "
            f"matched_card={result.get('matched_card_text')!r} "
            f"matched_scope={result.get('matched_scope')!r} "
            f"view_bid_results_found={result.get('bid_result_available')} "
            f"view_ra_results_found={result.get('ra_result_available')} "
            f"bid_ra_status_tab_clicked={result.get('bid_ra_status_tab_clicked')} "
            f"exact_search_selected={result.get('exact_search_selected')} "
            f"input_filled={result.get('input_filled')} "
            f"search_button_clicked={result.get('search_button_clicked')} "
            f"network_debug={result.get('network_debug')!r} "
            f"page_text_snippet={result.get('page_text_snippet')!r} "
            f"ra_number={result.get('gem_ra_number')!r} "
            f"final_status_saved={result.get('gem_result_status')} "
            f"reason={result.get('reason')!r} "
            f"failure_details={result.get('failure_details')!r}"
        )
        debug.update(
            {
                "opened_url": result.get("opened_url"),
                "screenshot_path": result.get("screenshot_path"),
                "card_found": result.get("card_found"),
                "view_bid_results_found": result.get("bid_result_available"),
                "view_ra_results_found": result.get("ra_result_available"),
                "page_text_snippet": result.get("page_text_snippet"),
                "reason": result.get("reason"),
                "status": result.get("gem_result_status"),
                "failure_details": result.get("failure_details") or [],
                "matched_scope": result.get("matched_scope"),
                "bid_ra_status_tab_clicked": result.get("bid_ra_status_tab_clicked"),
                "exact_search_selected": result.get("exact_search_selected"),
                "input_filled": result.get("input_filled"),
                "search_button_clicked": result.get("search_button_clicked"),
                "network_debug": result.get("network_debug"),
                "matched_doc_found": result.get("matched_doc_found"),
                "b_bid_number": result.get("b_bid_number"),
                "b_bid_number_parent": result.get("b_bid_number_parent"),
                "b_id_parent": result.get("b_id_parent"),
                "ra_id": result.get("ra_id"),
                "bid_result_url": result.get("gem_result_url"),
                "ra_result_url": result.get("gem_ra_result_url"),
            }
        )

        database.update_tender_result(
            tender_id,
            result_available=result["result_available"],
            bid_result_available=result["bid_result_available"],
            ra_result_available=result["ra_result_available"],
            gem_result_status=result["gem_result_status"],
            gem_bid_number=bid_number,
            gem_internal_id=_normalize_space(tender.get("gem_bidding_number")) or None,
            gem_result_url=result["gem_result_url"],
            gem_ra_number=result.get("gem_ra_number"),
            gem_ra_result_url=result.get("gem_ra_result_url"),
            last_result_checked_at=now,
            l1_seller_name=result.get("l1_seller_name"),
            our_company_rank=result.get("our_company_rank"),
            our_company_status=result.get("our_company_status"),
            result_declared=result["result_available"],
            result_declared_at=(tender.get("result_declared_at") or now) if result["result_available"] else tender.get("result_declared_at"),
        )

        notification = None
        notification_created = False
        if result["result_available"] and (not existing_available or not tender.get("notification_sent")):
            if result["bid_result_available"] and result["ra_result_available"]:
                title = f"Bid and RA results available for {bid_number}"
                message = f"Bid and RA results available for {bid_number}."
            elif result["bid_result_available"]:
                title = f"Bid result available for {bid_number}"
                message = f"Bid result available for {bid_number}."
            else:
                title = f"RA result available for {bid_number}"
                message = f"RA result available for {bid_number}."
            notification = database.create_tender_notification(tender_id, title, message, notification_type="RESULT_AVAILABLE")
            database.update_tender_result(tender_id, notification_sent=True)
            notification_created = True
            _watcher_log(f"result-found bid={bid_number} status={result['gem_result_status']}")
        else:
            _watcher_log(
                f"result-updated bid={bid_number} status={result['gem_result_status']} reason={result.get('reason')!r}"
            )

        _watcher_log(
            f"notification tender_id={tender_id} bid_number={bid_number!r} created={notification_created}"
        )

        updated = database.get_tender(tender_id)
        return {
            "tender_id": tender_id,
            "status": result.get("gem_result_status", RESULT_STATUS_FAILED),
            "gem_result_status": result.get("gem_result_status", RESULT_STATUS_FAILED),
            "result_found": result["result_available"],
            "notification": notification,
            "tender": updated,
            "debug": debug,
        }
    except Exception as exc:
        _watcher_log(
            "failed "
            f"tender_id={tender_id} "
            f"gem_bidding_no={tender.get('gem_bidding_no') or tender.get('gem_bidding_number')!r} "
            f"tender_number={tender.get('tender_number')!r} "
            f"final_bid_number={bid_number!r} "
            f"error={type(exc).__name__}: {exc}"
        )
        _watcher_log(traceback.format_exc())
        database.update_tender_result(
            tender_id,
            gem_result_status=RESULT_STATUS_FAILED,
            last_result_checked_at=now,
        )
        updated = database.get_tender(tender_id)
        debug.update(
            {
                "status": RESULT_STATUS_FAILED,
                "reason": str(exc),
                "failure_details": [str(exc)],
            }
        )
        return {
            "tender_id": tender_id,
            "status": RESULT_STATUS_FAILED,
            "gem_result_status": RESULT_STATUS_FAILED,
            "result_found": False,
            "error": str(exc),
            "tender": updated,
            "debug": debug,
        }


def run_result_watcher_for_eligible_tenders():
    eligible = [t for t in database.list_result_watch_eligible_tenders() if _is_tender_eligible(t)]
    summary = {
        "checked": 0,
        "pending": 0,
        "results_found": 0,
        "failed": 0,
        "items": [],
    }
    if not eligible:
        _watcher_log("no eligible tenders to check")
        return summary

    for index, tender in enumerate(eligible):
        try:
            result = check_tender_result(tender["id"])
            summary["checked"] += 1
            summary["items"].append(result)
            if result["status"] == RESULT_STATUS_FAILED:
                summary["failed"] += 1
            elif result["result_found"]:
                summary["results_found"] += 1
            else:
                summary["pending"] += 1
        except Exception as exc:
            summary["checked"] += 1
            summary["failed"] += 1
            summary["items"].append({"tender_id": tender["id"], "status": RESULT_STATUS_FAILED, "error": str(exc)})
            _watcher_log(f"failed tender_id={tender['id']} error={type(exc).__name__}: {exc}")
        if index < len(eligible) - 1:
            time.sleep(random.uniform(CHECK_DELAY_MIN_SECONDS, CHECK_DELAY_MAX_SECONDS))
    return summary


def _scheduler_loop():
    global _watcher_last_slot
    tz = _get_scheduler_timezone()
    while True:
        try:
            now = datetime.now(tz)
            due_hour = next((hour for hour in sorted(SCHEDULE_HOURS) if now.hour == hour and now.minute < 10), None)
            slot_key = f"{now.date().isoformat()}-{due_hour}"
            if due_hour is not None and _watcher_last_slot != slot_key:
                _watcher_last_slot = slot_key
                _watcher_log(f"scheduled-run starting slot={slot_key}")
                run_result_watcher_for_eligible_tenders()
        except Exception as exc:
            _watcher_log(f"scheduler error={type(exc).__name__}: {exc}")
        time.sleep(max(30, POLL_SECONDS))


def start_result_watcher_scheduler():
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        thread = threading.Thread(target=_scheduler_loop, name="tender-result-watcher", daemon=True)
        thread.start()
        _scheduler_started = True
        _watcher_log(f"scheduler started hours={SCHEDULE_HOURS} timezone={SCHEDULE_TIMEZONE}")
