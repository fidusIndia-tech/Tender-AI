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
STATUS_BID_AVAILABLE = "BID_RESULT_AVAILABLE"
STATUS_RA_AVAILABLE = "RA_RESULT_AVAILABLE"
STATUS_BID_AND_RA_AVAILABLE = "BID_AND_RA_RESULT_AVAILABLE"
STATUS_FAILED = "FAILED_TO_CHECK"


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


def parse_gem_response(bid_number, gem_base_url, data):
    bid = str(bid_number or "").strip().upper()
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

    logging.info(
        "GeM docs num_found=%s returned_bids=%s returned_parents=%s matched=%s",
        num_found,
        returned_bids[:10],
        returned_parents[:10],
        bool(matched_doc),
    )

    if not matched_doc:
        raise GemNoDataFound("Exact tender was not found in GeM response.")

    bid_value = str(first_value(matched_doc.get("b_bid_number")) or "").strip().upper()
    parent_value = str(first_value(matched_doc.get("b_bid_number_parent")) or "").strip().upper()
    bid_parent_id = first_value(matched_doc.get("b_id_parent"))
    ra_id = first_value(matched_doc.get("id")) or first_value(matched_doc.get("b_id"))
    ra_match = GEM_RA_RE.search(bid_value or "")
    ra_number = ra_match.group(0).upper() if ra_match else None

    bid_result_available = bool(bid_parent_id)
    ra_result_available = bool(ra_number and ra_id)
    result_available = bid_result_available or ra_result_available

    if bid_result_available and ra_result_available:
        status = STATUS_BID_AND_RA_AVAILABLE
    elif bid_result_available:
        status = STATUS_BID_AVAILABLE
    elif ra_result_available:
        status = STATUS_RA_AVAILABLE
    else:
        status = STATUS_NOT_AVAILABLE

    return {
        "resultAvailable": result_available,
        "bidResultAvailable": bid_result_available,
        "raResultAvailable": ra_result_available,
        "raNumber": ra_number,
        "bidResultUrl": build_result_url(gem_base_url, bid_parent_id) if bid_result_available else None,
        "raResultUrl": build_result_url(gem_base_url, ra_id) if ra_result_available else None,
        "gemResultStatus": status,
        "gemPageStatus": str(first_value(matched_doc.get("b_status")) or first_value(matched_doc.get("status")) or "").strip() or None,
        "rawGemMatchedDoc": matched_doc,
        "debug": {
            "numFound": num_found,
            "returnedBidNumbers": returned_bids,
            "returnedParentBidNumbers": returned_parents,
            "b_bid_number": bid_value,
            "b_bid_number_parent": parent_value,
            "b_id_parent": bid_parent_id,
            "ra_id": ra_id,
        },
    }


def gem_payload(bid_number):
    return {
        "param": {"searchBid": bid_number, "searchType": "fullText"},
        "filter": {
            "bidStatusType": "bidrastatus",
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


def fetch_gem_bid_status(context, page, config, bid_number):
    gem_base_url = config["gem_base_url"]
    csrf = ensure_gem_page(context, page, gem_base_url)
    request_url = f"{gem_base_url}/all-bids-data"
    body = f"payload={urllib.parse.quote(json.dumps(gem_payload(bid_number), separators=(',', ':')))}&csrf_bd_gem_nk={urllib.parse.quote(csrf)}"

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
    logging.info("GeM response status=%s snippet=%s", status, text[:250])

    if status == 404:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = {}
        if parsed.get("code") == 404 and str(parsed.get("message", "")).lower() == "no data found":
            raise GemNoDataFound("Exact tender was not found in GeM response.")
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
            raw = fetch_gem_bid_status(context, page, config, bid_number)
            parsed = parse_gem_response(bid_number, config["gem_base_url"], raw)
            payload = {
                "bidNumber": bid_number,
                "checkedAt": utc_now_iso(),
                **{k: v for k, v in parsed.items() if k != "debug"},
            }
            post_json(config, f"/api/tenders/{tender_id}/ingest-gem-result", payload)
            logging.info(
                "ingested tender_id=%s bid=%s result_available=%s status=%s ra=%s",
                tender_id,
                bid_number,
                payload["resultAvailable"],
                payload["gemResultStatus"],
                payload.get("raNumber"),
            )
            return payload
        except GemNoDataFound as exc:
            payload = {
                "bidNumber": bid_number,
                "checkedAt": utc_now_iso(),
                "resultAvailable": False,
                "bidResultAvailable": False,
                "raResultAvailable": False,
                "gemResultStatus": STATUS_NOT_AVAILABLE,
                "bidResultUrl": None,
                "raResultUrl": None,
                "raNumber": None,
                "gemPageStatus": None,
                "rawGemMatchedDoc": None,
            }
            post_json(config, f"/api/tenders/{tender_id}/ingest-gem-result", payload)
            logging.info(
                "ingested tender_id=%s bid=%s result_available=False status=%s reason=%s",
                tender_id,
                bid_number,
                STATUS_NOT_AVAILABLE,
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
            raw = fetch_gem_bid_status(context, page, config, bid)
            parsed = parse_gem_response(bid, config["gem_base_url"], raw)
            print(json.dumps(parsed, indent=2, default=str))
        finally:
            context.close()


def main():
    parser = argparse.ArgumentParser(description="Local GeM Result Watcher Agent")
    parser.add_argument("--run-now", action="store_true", help="Run all pending tenders now")
    parser.add_argument("--test-bid", help="Test one GeM bid number without ingesting to Tender AI")
    args = parser.parse_args()

    config = load_config(require_api=not args.test_bid)
    setup_logging(config["log_level"])

    if args.test_bid:
        run_test_bid(config, args.test_bid)
        return
    run_pending(config)


if __name__ == "__main__":
    main()
