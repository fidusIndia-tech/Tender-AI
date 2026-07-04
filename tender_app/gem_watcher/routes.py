from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

try:
    from .. import database
    from .scanner import (
        execute_scan,
        request_cancel,
        rerun_extraction_for_candidate,
        run_full_evaluation_for_candidate,
        start_scan,
    )
except ImportError:
    import database
    from gem_watcher.scanner import (
        execute_scan,
        request_cancel,
        rerun_extraction_for_candidate,
        run_full_evaluation_for_candidate,
        start_scan,
    )

router = APIRouter()


class KeywordCreate(BaseModel):
    keyword: str


class KeywordUpdate(BaseModel):
    keyword: Optional[str] = None
    is_active: Optional[bool] = None


class ScanRequest(BaseModel):
    scan_target_date: str  # YYYY-MM-DD
    keyword: Optional[str] = None  # one-shot ad-hoc keyword; falls back to saved keywords


class CandidateOverrideRequest(BaseModel):
    reason: Optional[str] = None


# ── Keywords ─────────────────────────────────────────────────────────────────

@router.get("/api/gem-watcher/keywords")
async def list_keywords():
    return database.list_gem_keywords()


@router.post("/api/gem-watcher/keywords", status_code=201)
async def create_keyword(payload: KeywordCreate):
    keyword = payload.keyword.strip()
    if not keyword:
        raise HTTPException(400, "Keyword cannot be empty")
    result = database.create_gem_keyword(keyword)
    return {"id": result["id"], "already_existed": result["already_existed"]}


@router.put("/api/gem-watcher/keywords/{keyword_id}")
async def update_keyword(keyword_id: int, payload: KeywordUpdate):
    updated = database.update_gem_keyword(keyword_id, keyword=payload.keyword, is_active=payload.is_active)
    if not updated:
        raise HTTPException(404, "Keyword not found")
    return {"status": "updated"}


@router.delete("/api/gem-watcher/keywords/{keyword_id}", status_code=204)
async def delete_keyword(keyword_id: int):
    database.delete_gem_keyword(keyword_id)


# ── Scan runs ────────────────────────────────────────────────────────────────

@router.post("/api/gem-watcher/scan")
async def run_scan_now(payload: ScanRequest, background_tasks: BackgroundTasks):
    try:
        run_id, scan_target_date = start_scan(payload.scan_target_date, keyword=payload.keyword)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    background_tasks.add_task(execute_scan, run_id, scan_target_date, payload.keyword)
    return {"run_id": run_id, "status": "RUNNING"}


@router.get("/api/gem-watcher/scan-runs")
async def list_scan_runs():
    return database.list_gem_scan_runs()


@router.get("/api/gem-watcher/scan-runs/{run_id}")
async def get_scan_run(run_id: int):
    run = database.get_gem_scan_run(run_id)
    if not run:
        raise HTTPException(404, "Scan run not found")
    return run


@router.post("/api/gem-watcher/scan-runs/{run_id}/cancel")
async def cancel_scan_run(run_id: int):
    run = database.get_gem_scan_run(run_id)
    if not run:
        raise HTTPException(404, "Scan run not found")
    if run["status"] != "RUNNING":
        return {"status": run["status"], "message": "Scan is not running"}
    request_cancel(run_id)
    return {"status": "CANCELLING", "message": "Stop requested — the scan will halt shortly"}


# ── Candidate tenders ────────────────────────────────────────────────────────

@router.get("/api/gem-candidates")
async def list_candidates(status: Optional[str] = None, run_id: Optional[int] = None):
    return database.list_gem_candidates(status=status, scan_run_id=run_id)


@router.get("/api/gem-candidates/{candidate_id}")
async def get_candidate(candidate_id: int):
    candidate = database.get_gem_candidate(candidate_id)
    if not candidate:
        raise HTTPException(404, "Candidate not found")
    return candidate


@router.post("/api/gem-candidates/{candidate_id}/approve")
async def approve_candidate(candidate_id: int):
    candidate = database.get_gem_candidate(candidate_id)
    if not candidate:
        raise HTTPException(404, "Candidate not found")
    if candidate.get("status") in {"APPROVED", "SENT_TO_ALL_TENDERS"}:
        return {"status": candidate.get("status"), "tender_id": candidate.get("tender_id")}
    if not candidate.get("pdf_file_id"):
        raise HTTPException(400, "Candidate has no downloaded PDF yet — cannot approve")

    import json
    import os
    import tempfile
    from pathlib import Path
    import ai_extractor

    file_id = candidate["pdf_file_id"]
    raw = {}

    # Re-extract from the stored PDF so the approved tender gets the SAME full
    # information a manual upload produces (all tender fields, BOQ items,
    # required documents) — not just whatever was cached earlier.
    file_row = database.get_uploaded_file(file_id)
    if file_row and file_row.get("file_data"):
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        try:
            tmp.write(bytes(file_row["file_data"]))
            tmp.flush()
            tmp.close()
            raw = ai_extractor.process_pdf(tmp.name)
            # Refresh the cached extraction JSON to match.
            extraction_path = Path(__file__).resolve().parent.parent / "extractions" / f"{file_id}.json"
            with open(extraction_path, "w", encoding="utf-8") as f:
                json.dump(raw, f, indent=2, ensure_ascii=False)
        except Exception:
            raw = {}
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    if not raw:
        # Fall back to the extraction JSON saved during the scan.
        extraction_path = Path(__file__).resolve().parent.parent / "extractions" / f"{file_id}.json"
        if extraction_path.exists():
            with open(extraction_path, "r", encoding="utf-8") as f:
                raw = json.load(f)

    ti = raw.get("tender_information", {})
    tender_data = dict(ti)
    tender_data["gem_bidding_number"] = candidate["gem_bid_no"]
    tender_data.setdefault("tender_number", candidate["gem_bid_no"])
    tender_data["organization_name"] = tender_data.get("organization_name") or candidate.get("organisation")
    tender_data["department_name"] = tender_data.get("department_name") or candidate.get("department")
    tender_data["total_quantity"] = tender_data.get("total_quantity") or candidate.get("quantity")
    tender_data["pdf_path"] = f"/files/{candidate['pdf_file_id']}"
    boq_items = raw.get("items", [])
    docs = raw.get("required_documents", [])
    required_documents = [{"label": d} if isinstance(d, str) else d for d in docs]

    duplicate = database.find_tender_duplicate(tender_data["gem_bidding_number"], tender_data["tender_number"])
    if duplicate:
        database.update_gem_candidate(
            candidate_id, status="SENT_TO_ALL_TENDERS", scan_status="DUPLICATE", tender_id=duplicate["id"],
            duplicate_reason="GeM bid number already exists in All Tenders.",
            evaluation_reason=(candidate.get("evaluation_reason") or "") + " (manually approved; already exists in All Tenders)",
        )
        return {"status": "SENT_TO_ALL_TENDERS", "tender_id": duplicate["id"]}

    tender_id = database.save_tender(tender_data, boq_items, required_documents)
    database.update_gem_candidate(
        candidate_id, status="SENT_TO_ALL_TENDERS", scan_status="SENT_TO_ALL_TENDERS", tender_id=tender_id,
        evaluation_reason=(candidate.get("evaluation_reason") or "") + " (manually approved by admin despite score below threshold)",
    )
    return {"status": "SENT_TO_ALL_TENDERS", "tender_id": tender_id}


@router.post("/api/gem-candidates/{candidate_id}/review")
async def send_candidate_to_review(candidate_id: int, payload: CandidateOverrideRequest | None = None):
    candidate = database.get_gem_candidate(candidate_id)
    if not candidate:
        raise HTTPException(404, "Candidate not found")
    if candidate.get("status") in {"APPROVED", "SENT_TO_ALL_TENDERS"} and candidate.get("tender_id"):
        raise HTTPException(400, "This tender is already in All Tenders. Keep it approved there or handle it manually.")
    reason = (payload.reason if payload else None) or candidate.get("evaluation_reason") or "Moved to review by admin"
    database.update_gem_candidate(candidate_id, scan_status="REVIEW")
    database.set_gem_candidate_status(candidate_id, "REVIEW", reason)
    return {"status": "REVIEW"}


@router.post("/api/gem-candidates/{candidate_id}/reject")
async def reject_candidate(candidate_id: int, payload: CandidateOverrideRequest | None = None):
    candidate = database.get_gem_candidate(candidate_id)
    if not candidate:
        raise HTTPException(404, "Candidate not found")
    if candidate.get("status") in {"APPROVED", "SENT_TO_ALL_TENDERS"} and candidate.get("tender_id"):
        raise HTTPException(400, "This tender is already in All Tenders. Keep it approved there or handle it manually.")
    reason = (payload.reason if payload else None) or candidate.get("evaluation_reason") or "Rejected by admin"
    database.update_gem_candidate(candidate_id, scan_status="REJECTED")
    database.set_gem_candidate_status(candidate_id, "REJECTED", reason)
    return {"status": "REJECTED"}


@router.post("/api/gem-candidates/{candidate_id}/full-evaluate")
async def full_evaluate_candidate(candidate_id: int):
    candidate = database.get_gem_candidate(candidate_id)
    if not candidate:
        raise HTTPException(404, "Candidate not found")
    if not candidate.get("pdf_file_id"):
        raise HTTPException(400, "Candidate has no downloaded PDF yet")
    outcome = run_full_evaluation_for_candidate(candidate_id)
    return {"status": outcome}


@router.post("/api/gem-candidates/{candidate_id}/rerun-extraction")
async def rerun_candidate_extraction(candidate_id: int):
    candidate = database.get_gem_candidate(candidate_id)
    if not candidate:
        raise HTTPException(404, "Candidate not found")
    if not candidate.get("pdf_file_id"):
        raise HTTPException(400, "Candidate has no downloaded PDF yet")
    outcome = rerun_extraction_for_candidate(candidate_id)
    return {"status": outcome}


@router.delete("/api/gem-candidates/{candidate_id}", status_code=204)
async def delete_candidate(candidate_id: int):
    candidate = database.get_gem_candidate(candidate_id)
    if not candidate:
        raise HTTPException(404, "Candidate not found")
    if candidate.get("status") in {"APPROVED", "SENT_TO_ALL_TENDERS"} and candidate.get("tender_id"):
        raise HTTPException(400, "Candidate already exists in All Tenders and cannot be deleted here")
    deleted = database.delete_gem_candidate(candidate_id)
    if not deleted:
        raise HTTPException(404, "Candidate not found")
