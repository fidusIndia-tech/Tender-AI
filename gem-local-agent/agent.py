import argparse
import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

GEM_ALL_BIDS_URL = "https://bidplus.gem.gov.in/all-bids"
GEM_DATA_URL = "https://bidplus.gem.gov.in/all-bids-data"
GEM_PDF_URL_TEMPLATE = "https://bidplus.gem.gov.in/showbidDocument/{doc_id}"
GEM_RA_PDF_URL_TEMPLATE = "https://bidplus.gem.gov.in/showradocumentPdf/{doc_id}"
GEM_SORT = "Bid-Start-Date-Latest"
PAGE_SIZE = 10


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(LOG_DIR / "agent.log", encoding="utf-8"),
        ],
    )


def load_env_file(path):
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def first(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value


def norm_space(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def parse_gem_date(value):
    text = norm_space(first(value))
    if not text:
        return None
    iso_match = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if iso_match:
        return f"{iso_match.group(1)}-{iso_match.group(2)}-{iso_match.group(3)}"
    dmy_match = re.search(r"(\d{2})-(\d{2})-(\d{4})", text)
    if dmy_match:
        return f"{dmy_match.group(3)}-{dmy_match.group(2)}-{dmy_match.group(1)}"
    return text or None


def keyword_has_strict_match(keyword, parsed):
    kw = norm_space(keyword)
    if not kw:
        return False
    haystack = " ".join(
        norm_space(parsed.get(k))
        for k in ("title", "organisation", "department", "gemBidNumber")
    )
    if len(kw) <= 4 and re.fullmatch(r"[A-Za-z0-9]+", kw):
        return re.search(rf"(?<![A-Za-z0-9]){re.escape(kw)}(?![A-Za-z0-9])", haystack, re.I) is not None
    return kw.lower() in haystack.lower()


class Config:
    def __init__(self):
        self.base_url = os.getenv("TENDER_AI_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
        self.api_key = os.getenv("LOCAL_AGENT_API_KEY", "").strip()
        self.profile_dir = str(ROOT / os.getenv("BROWSER_PROFILE_DIR", ".browser-profile"))
        self.headless = env_bool("PLAYWRIGHT_HEADLESS", False)
        self.browser_channel = os.getenv("PLAYWRIGHT_BROWSER_CHANNEL", "msedge").strip() or None
        self.max_results = max(1, int(os.getenv("MAX_RESULTS_PER_KEYWORD", "50")))
        self.keyword_delay = float(os.getenv("KEYWORD_DELAY_SECONDS", "2"))
        self.page_delay = float(os.getenv("PAGE_DELAY_SECONDS", "0.5"))
        self.search_date_mode = os.getenv("SEARCH_DATE_MODE", "today").strip().lower()
        self.target_date = os.getenv("SEARCH_TARGET_DATE", "").strip() or date.today().isoformat()
        self.date_from = os.getenv("SEARCH_DATE_FROM", "").strip() or self.target_date
        self.date_to = os.getenv("SEARCH_DATE_TO", "").strip() or self.date_from
        self.dry_run = env_bool("DRY_RUN", True)


def auth_headers(config):
    if not config.api_key:
        raise RuntimeError("LOCAL_AGENT_API_KEY is missing in .env")
    return {"Authorization": f"Bearer {config.api_key}"}


def fetch_keywords(config):
    url = f"{config.base_url}/api/gem-search/keywords"
    req = urllib.request.Request(url, headers=auth_headers(config), method="GET")
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_search_config(config):
    url = f"{config.base_url}/api/gem-search/config"
    req = urllib.request.Request(url, headers=auth_headers(config), method="GET")
    with urllib.request.urlopen(req, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))
    scan_date = (data.get("scanTargetDate") or "").strip()
    date_from = (data.get("scanDateFrom") or "").strip()
    date_to = (data.get("scanDateTo") or "").strip()
    mode = (data.get("searchDateMode") or "date").strip().lower()
    if scan_date:
        config.target_date = scan_date
    if date_from:
        config.date_from = date_from
    if date_to:
        config.date_to = date_to
    if mode in {"date", "range", "all"}:
        config.search_date_mode = mode
    return data


def post_discovered_tender(config, payload, dry_run):
    if dry_run:
        logging.info("DRY RUN discovery: %s", json.dumps(payload, ensure_ascii=False))
        return {"dryRun": True, "action": "DRY_RUN_NOT_SENT"}
    url = f"{config.base_url}/api/gem-search/discovered-tender"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{url}?dryRun=false",
        data=body,
        headers={**auth_headers(config), "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as response:
        return json.loads(response.read().decode("utf-8"))


def capture_csrf(context, page):
    page.goto(GEM_ALL_BIDS_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1200)
    for cookie in context.cookies():
        if cookie.get("name") == "csrf_gem_cookie":
            return cookie.get("value") or ""
    logging.warning("csrf_gem_cookie not found; GeM may require opening the page once in headed mode.")
    return ""


def fetch_bids(page, keyword, csrf, page_no):
    body_json = {
        "param": {"searchBid": keyword, "searchType": "fullText"},
        "filter": {
            "bidStatusType": "ongoing_bids",
            "byType": "all",
            "highBidValue": "",
            "byEndDate": {"from": "", "to": ""},
            "sort": GEM_SORT,
        },
    }
    if page_no > 1:
        body_json = {"page": page_no, **body_json}
    body = "payload=" + urllib.parse.quote(json.dumps(body_json, separators=(",", ":"))) + "&csrf_bd_gem_nk=" + urllib.parse.quote(csrf or "")
    response = page.request.post(
        GEM_DATA_URL,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
        },
        fail_on_status_code=False,
    )
    if response.status != 200:
        raise RuntimeError(f"GeM all-bids-data failed HTTP {response.status}: {response.text()[:250]}")
    data = response.json()
    inner = (data.get("response") or {}).get("response") or {}
    return inner.get("docs") or [], int(inner.get("numFound") or 0)


def doc_to_payload(doc, keyword):
    bid_no = first(doc.get("b_bid_number"))
    if not bid_no:
        return None

    parent_bid_no = first(doc.get("b_bid_number_parent"))
    parent_id = first(doc.get("b_id_parent"))
    doc_id = first(doc.get("id")) or first(doc.get("b_id"))
    is_ra = "/R/" in str(bid_no).upper() or first(doc.get("b_bid_to_ra")) == 1

    if is_ra and parent_bid_no and parent_id:
        gem_bid_number = str(parent_bid_no).strip()
        pdf_url = GEM_PDF_URL_TEMPLATE.format(doc_id=parent_id)
    elif is_ra and doc_id:
        gem_bid_number = str(bid_no).strip()
        pdf_url = GEM_RA_PDF_URL_TEMPLATE.format(doc_id=doc_id)
    else:
        gem_bid_number = str(bid_no).strip()
        pdf_url = GEM_PDF_URL_TEMPLATE.format(doc_id=doc_id) if doc_id else None

    quantity = first(doc.get("b_total_quantity"))
    return {
        "gemBidNumber": gem_bid_number,
        "title": first(doc.get("b_category_name")) or first(doc.get("bd_category_name")),
        "organisation": first(doc.get("ba_official_details_minName")),
        "department": first(doc.get("ba_official_details_deptName")),
        "quantity": str(quantity) if quantity is not None else None,
        "bidStartDate": parse_gem_date(doc.get("final_start_date_sort") or doc.get("final_start_date")),
        "bidEndDate": parse_gem_date(doc.get("final_end_date_sort") or doc.get("final_end_date")),
        "keywordMatched": keyword,
        "gemPdfUrl": pdf_url,
        "source": "LOCAL_GEM_AGENT",
        "rawGemData": doc,
    }


def search_keyword(page, keyword, csrf, config):
    target_date = config.target_date or date.today().isoformat()
    date_from = config.date_from or target_date
    date_to = config.date_to or date_from
    results = []
    seen = set()
    total = None
    page_no = 1

    while len(results) < config.max_results:
        docs, num_found = fetch_bids(page, keyword, csrf, page_no)
        if total is None:
            total = num_found
        if not docs:
            break

        for doc in docs:
            payload = doc_to_payload(doc, keyword)
            if not payload:
                continue
            bid_start = payload.get("bidStartDate")
            if config.search_date_mode in {"today", "date"} and bid_start != target_date:
                continue
            if config.search_date_mode == "range" and (not bid_start or bid_start < date_from or bid_start > date_to):
                continue
            if not keyword_has_strict_match(keyword, payload):
                continue
            bid_no = payload["gemBidNumber"]
            if bid_no in seen:
                continue
            seen.add(bid_no)
            results.append(payload)
            if len(results) >= config.max_results:
                break

        if page_no * PAGE_SIZE >= total:
            break
        page_no += 1
        time.sleep(config.page_delay)

    logging.info("keyword=%s target_date=%s date_from=%s date_to=%s mode=%s total_gem=%s matched=%s", keyword, target_date, date_from, date_to, config.search_date_mode, total or 0, len(results))
    return results


def run_keywords(config, keywords, dry_run):
    from playwright.sync_api import sync_playwright

    summary = {"keywords": len(keywords), "discovered": 0, "sent": 0, "failed": 0}
    with sync_playwright() as pw:
        launch_kwargs = {"headless": config.headless}
        if config.browser_channel:
            launch_kwargs["channel"] = config.browser_channel
        context = pw.chromium.launch_persistent_context(config.profile_dir, **launch_kwargs)
        page = context.new_page()
        try:
            csrf = capture_csrf(context, page)
            for item in keywords:
                keyword = item["keyword"] if isinstance(item, dict) else str(item)
                logging.info("searching keyword=%s", keyword)
                try:
                    discoveries = search_keyword(page, keyword, csrf, config)
                    summary["discovered"] += len(discoveries)
                    for payload in discoveries:
                        result = post_discovered_tender(config, payload, dry_run)
                        summary["sent"] += 0 if dry_run else 1
                        logging.info("bid=%s backend_action=%s", payload["gemBidNumber"], result.get("action"))
                except Exception:
                    summary["failed"] += 1
                    logging.exception("keyword failed: %s", keyword)
                time.sleep(config.keyword_delay)
        finally:
            context.close()
    return summary


def select_keywords_for_run(config, args):
    if args.test_keyword:
        if not args.date:
            fetched_config = fetch_search_config(config)
            logging.info("loaded backend scan config=%s", fetched_config)
        return [{"keyword": args.test_keyword}]

    keywords = fetch_keywords(config)
    if not args.date:
        fetched_config = fetch_search_config(config)
        logging.info("loaded backend scan config=%s", fetched_config)
    if not keywords:
        logging.info("No active keywords returned by Tender AI.")
    return keywords


def run_once(config, args, dry_run):
    keywords = select_keywords_for_run(config, args)
    if not keywords:
        return {"keywords": 0, "discovered": 0, "sent": 0, "failed": 0}
    summary = run_keywords(config, keywords, dry_run=dry_run)
    logging.info("summary=%s", summary)
    print(json.dumps(summary, indent=2))
    return summary


def main():
    load_env_file(ROOT / ".env")
    setup_logging()
    parser = argparse.ArgumentParser(description="Local GeM Tender Search Agent")
    parser.add_argument("--test-keyword", help="Search one keyword only")
    parser.add_argument("--search-new-tenders", action="store_true", help="Fetch active keywords from Tender AI and search them")
    parser.add_argument("--run-all", action="store_true", help="Alias for --search-new-tenders")
    parser.add_argument("--dry-run", action="store_true", help="Print discoveries without sending to Tender AI")
    parser.add_argument("--date", help="Override scan date for this run, format YYYY-MM-DD")
    parser.add_argument("--loop", action="store_true", help="Keep running automatically on this PC")
    parser.add_argument("--interval-minutes", type=float, default=float(os.getenv("LOOP_INTERVAL_MINUTES", "30")), help="Minutes between automatic runs in --loop mode")
    args = parser.parse_args()

    config = Config()
    if args.date:
        datetime.strptime(args.date, "%Y-%m-%d")
        config.target_date = args.date
        config.search_date_mode = "date"
    dry_run = args.dry_run or config.dry_run

    if not (args.test_keyword or args.search_new_tenders or args.run_all or args.loop):
        parser.error("Use --test-keyword Siemens, --search-new-tenders, --run-all, or --loop")

    if args.loop:
        interval_seconds = max(60, int(args.interval_minutes * 60))
        logging.info("starting loop mode interval_minutes=%s dry_run=%s", args.interval_minutes, dry_run)
        while True:
            try:
                run_once(config, args, dry_run=dry_run)
            except KeyboardInterrupt:
                logging.info("loop stopped by user")
                raise
            except Exception:
                logging.exception("loop run failed")
            logging.info("sleeping %s seconds before next run", interval_seconds)
            time.sleep(interval_seconds)
    else:
        run_once(config, args, dry_run=dry_run)


if __name__ == "__main__":
    main()
