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
        "profile_dir": str((ROOT / os.getenv("BROWSER_PROFILE_DIR", ".browser-profile")).resolve()),
        "log_level": os.getenv("LOG_LEVEL", "INFO"),
    }


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
            parts.extend(str(item).strip() for item in raw if str(item or "").strip())
        else:
            text = str(raw or "").strip()
            if text:
                parts.append(text)
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
    bid_result_available = bool(is_direct_bid and result_direct_doc_id and is_evaluated_status_text(result_status_text))

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
            "reason": result_status_text or ongoing_status_text,
        },
    }


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


def check_one_tender(context, page, config, tender):
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
                **{k: v for k, v in parsed.items() if k != "debug"},
            }
            post_json(config, f"/api/tenders/{tender_id}/ingest-gem-result", payload)
            debug = parsed.get("debug") or {}
            logging.info(
                "ingested tender_id=%s searched_bid=%s matched_doc_id=%s b_bid_number=%s b_bid_number_parent=%s "
                "is_direct_bid=%s is_ra_doc=%s bid_result_available=%s ra_created=%s ra_result_available=%s "
                "status=%s reason=%s",
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
                payload["gemResultStatus"],
                payload.get("gemPageStatus") or payload.get("resultCheckError"),
            )
            return payload
        except GemNoDataFound as exc:
            payload = {
                "bidNumber": bid_number,
                "checkedAt": utc_now_iso(),
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
            }
            post_json(config, f"/api/tenders/{tender_id}/ingest-gem-result", payload)
            logging.info(
                "ingested tender_id=%s bid=%s result_available=False status=%s reason=%s",
                tender_id,
                bid_number,
                STATUS_NOT_FOUND,
                exc,
            )
            return payload
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
        context = p.chromium.launch_persistent_context(
            config["profile_dir"],
            headless=config["headless"],
            viewport={"width": 1366, "height": 900},
        )
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
            context.close()

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


def run_recheck_and_fix_statuses(config):
    started_at = utc_now_iso()
    pending = fetch_recheck_tenders(config)
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
        context = p.chromium.launch_persistent_context(
            config["profile_dir"],
            headless=config["headless"],
            viewport={"width": 1366, "height": 900},
        )
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
            context.close()

    finished_at = utc_now_iso()
    post_json(
        config,
        "/api/result-watcher/run-log",
        {
            "started_at": started_at,
            "finished_at": finished_at,
            "run_source": "LOCAL_AGENT_RECHECK",
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
        context = p.chromium.launch_persistent_context(
            config["profile_dir"],
            headless=config["headless"],
            viewport={"width": 1366, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            result_raw = fetch_gem_bid_status(context, page, config, bid, RESULT_FILTER_TYPE)
            ongoing_raw = fetch_gem_bid_status(context, page, config, bid, ONGOING_FILTER_TYPE)
            parsed = parse_gem_response(bid, config["gem_base_url"], result_raw, ongoing_raw)
            print(json.dumps(parsed, indent=2, default=str))
        finally:
            context.close()


def main():
    parser = argparse.ArgumentParser(description="Local GeM Result Watcher Agent")
    parser.add_argument("--run-now", action="store_true", help="Run all pending tenders now")
    parser.add_argument("--test-bid", help="Test one GeM bid number without ingesting to Tender AI")
    parser.add_argument("--recheck-and-fix-statuses", action="store_true", help="Recheck all ended tenders and fix old result statuses")
    args = parser.parse_args()

    config = load_config(require_api=not args.test_bid)
    setup_logging(config["log_level"])

    if args.test_bid:
        run_test_bid(config, args.test_bid)
        return
    if args.recheck_and_fix_statuses:
        run_recheck_and_fix_statuses(config)
        return
    run_pending(config)


if __name__ == "__main__":
    main()
