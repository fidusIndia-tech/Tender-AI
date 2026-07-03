import importlib.util
from pathlib import Path

from tender_app.result_watcher import (
    RESULT_STATUS_BID_AVAILABLE,
    RESULT_STATUS_NOT_AVAILABLE,
    RESULT_STATUS_NOT_FOUND,
    RESULT_STATUS_RA_AVAILABLE,
    RESULT_STATUS_RA_CREATED,
    _resolve_safe_result,
    parse_gem_result_response,
)


WATCHER_PATH = Path(__file__).resolve().parents[1] / "gem-result-watcher-agent" / "watcher.py"
WATCHER_SPEC = importlib.util.spec_from_file_location("gem_result_watcher_agent_watcher", WATCHER_PATH)
WATCHER_MODULE = importlib.util.module_from_spec(WATCHER_SPEC)
assert WATCHER_SPEC and WATCHER_SPEC.loader
WATCHER_SPEC.loader.exec_module(WATCHER_MODULE)


def test_direct_bid_result_available():
    result_data = {
        "response": {
            "response": {
                "docs": [
                    {
                        "b_bid_number": ["GEM/2026/B/7587284"],
                        "b_bid_number_parent": [""],
                        "id": ["9420932"],
                        "status": ["1"],
                    }
                ],
                "numFound": 1,
            }
        }
    }
    parsed = parse_gem_result_response("GEM/2026/B/7587284", result_data, None)
    assert parsed["bid_result_available"] is True
    assert parsed["result_available"] is True
    assert parsed["ra_created"] is False
    assert parsed["ra_result_available"] is False
    assert parsed["gem_result_status"] == RESULT_STATUS_BID_AVAILABLE


def test_ra_created_without_ra_result():
    result_data = {
        "response": {"response": {"docs": [], "numFound": 0}}
    }
    ongoing_data = {
        "response": {
            "response": {
                "docs": [
                    {
                        "b_bid_number": ["GEM/2026/R/1111111"],
                        "b_bid_number_parent": ["GEM/2026/B/7587284"],
                        "id": ["123"],
                        "status": ["0"],
                        "final_start_date_sort": ["2026-07-01"],
                        "final_end_date_sort": ["2026-07-05"],
                    }
                ],
                "numFound": 1,
            }
        }
    }
    parsed = parse_gem_result_response("GEM/2026/B/7587284", result_data, ongoing_data)
    assert parsed["ra_created"] is True
    assert parsed["ra_result_available"] is False
    assert parsed["gem_result_status"] == RESULT_STATUS_RA_CREATED


def test_ra_result_available():
    result_data = {
        "response": {"response": {"docs": [], "numFound": 0}}
    }
    ongoing_data = {
        "response": {
            "response": {
                "docs": [
                    {
                        "b_bid_number": ["GEM/2026/R/2222222"],
                        "b_bid_number_parent": ["GEM/2026/B/7587284"],
                        "id": ["124"],
                        "status": ["2"],
                    }
                ],
                "numFound": 1,
            }
        }
    }
    parsed = parse_gem_result_response("GEM/2026/B/7587284", result_data, ongoing_data)
    assert parsed["ra_result_available"] is True
    assert parsed["gem_result_status"] == RESULT_STATUS_RA_AVAILABLE


def test_not_available_only_when_exact_tender_found():
    result_data = {
        "response": {
            "response": {
                "docs": [
                    {
                        "b_bid_number": ["GEM/2026/B/7587284"],
                        "b_bid_number_parent": [""],
                        "id": ["125"],
                        "status": ["0"],
                    }
                ],
                "numFound": 1,
            }
        }
    }
    parsed = parse_gem_result_response("GEM/2026/B/7587284", result_data, None)
    assert parsed["gem_result_status"] == RESULT_STATUS_NOT_AVAILABLE
    assert parsed["result_available"] is False


def test_not_found_does_not_crash():
    parsed = parse_gem_result_response(
        "GEM/2026/B/7587284",
        {"_gem_not_found": True},
        {"_gem_not_found": True},
    )
    assert parsed["gem_result_status"] == RESULT_STATUS_NOT_FOUND
    assert parsed["result_available"] is False


def test_existing_available_status_is_not_downgraded_without_force():
    existing = {
        "gem_result_status": RESULT_STATUS_BID_AVAILABLE,
        "result_available": True,
        "bid_result_available": True,
        "ra_created": False,
        "ra_result_available": False,
        "gem_result_url": "https://example.com/bid/123",
    }
    new_result = {
        "gem_result_status": RESULT_STATUS_NOT_AVAILABLE,
        "result_available": False,
        "bid_result_available": False,
        "ra_created": False,
        "ra_result_available": False,
        "reason": "No result visible now.",
    }
    resolved = _resolve_safe_result(existing, new_result, force_downgrade=False)
    assert resolved["gem_result_status"] == RESULT_STATUS_BID_AVAILABLE
    assert resolved["result_review_required"] is True
    assert resolved["downgrade_blocked"] is True


def test_compute_current_stage_uses_detected_section_flags():
    stage = WATCHER_MODULE.compute_current_stage(
        "BID",
        RESULT_STATUS_BID_AVAILABLE,
        {
            "technicalEvaluation": [],
            "financialEvaluation": [],
            "detectedSections": {"technical": True, "financial": False},
        },
    )
    assert stage == WATCHER_MODULE.STAGE_BID_TECHNICAL


def test_compute_current_stage_prefers_ra_financial():
    stage = WATCHER_MODULE.compute_current_stage(
        "RA",
        RESULT_STATUS_RA_AVAILABLE,
        {
            "technicalEvaluation": [],
            "financialEvaluation": [{"seller_name": "FIDUS"}],
            "detectedSections": {"technical": True, "financial": True},
        },
    )
    assert stage == WATCHER_MODULE.STAGE_RA_FINANCIAL
