import base64
import io
import hashlib
import hmac
import json
import asyncio
import os
import re
import shutil
import tempfile
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional
from urllib.parse import quote, urlparse
from dotenv import load_dotenv
load_dotenv()

from cryptography.fernet import Fernet, InvalidToken

from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import database
import ai_extractor
import doc_matcher
import doc_generator
from evaluation import evaluate_tender_against_capability
from gem_watcher.routes import router as gem_watcher_router
from result_watcher import (
    check_tender_result,
    debug_gem_exact_result_search,
    ingest_gem_result,
    ingest_gem_result_error,
    ingest_gem_result_from_agent,
    list_pending_result_watcher_tenders,
    list_recheck_result_watcher_tenders,
    run_result_watcher_for_eligible_tenders,
    start_result_watcher_scheduler,
)

HERE = Path(__file__).parent
UPLOADS_DIR     = HERE / "uploads"
EXTRACTIONS_DIR = HERE / "extractions"
COMPANY_DOCS_DIR = HERE / "company_docs"
GENERATED_DIR    = HERE / "generated"
EXTENSION_BUNDLE_DIR = HERE / "extension_bundle"

for d in (UPLOADS_DIR, EXTRACTIONS_DIR, COMPANY_DOCS_DIR, GENERATED_DIR):
    d.mkdir(exist_ok=True)

app = FastAPI(title="AI Tender Management System")
database.init_db()
try:
    database.fail_stale_running_scans()
except Exception as e:
    print(f"[WARN] fail_stale_running_scans on startup: {type(e).__name__}: {e}")
try:
    start_result_watcher_scheduler()
except Exception as e:
    print(f"[WARN] result watcher scheduler startup failed: {type(e).__name__}: {e}")

# ── Portal Encryption (Fernet symmetric) ──────────────────────────────────────

def _init_fernet() -> Fernet:
    key_str = os.environ.get("PORTAL_ENCRYPTION_KEY", "").strip()
    if key_str:
        try:
            f = Fernet(key_str.encode())
            print("[INFO] PORTAL_ENCRYPTION_KEY loaded — portal passwords are persistent.")
            return f
        except Exception as e:
            print(f"[ERROR] PORTAL_ENCRYPTION_KEY is invalid ({type(e).__name__}) — falling back to ephemeral key.")
    generated = Fernet.generate_key()
    print("[WARN] PORTAL_ENCRYPTION_KEY not set — using ephemeral key. Portal passwords will be unreadable after restart.")
    print(f"[WARN] Fix: add the following to Railway environment variables:")
    print(f"[WARN]   PORTAL_ENCRYPTION_KEY={generated.decode()}")
    return Fernet(generated)

_fernet = _init_fernet()


def _encrypt_password(plain: str) -> str:
    if not plain:
        return ""
    return _fernet.encrypt(plain.encode()).decode()


def _decrypt_password(encrypted: str) -> str:
    if not encrypted:
        return ""
    return _fernet.decrypt(encrypted.encode()).decode()


def _require_admin(request: Request) -> dict:
    token = request.cookies.get("tender_session")
    if not token:
        raise HTTPException(401, "Not authenticated")
    payload = _decode_session(token)
    if payload.get("role", "employee") not in ("admin", "director"):
        raise HTTPException(403, "Access restricted to admin/director")
    return payload


def _require_watcher_or_admin(request: Request) -> dict:
    expected = os.environ.get("WATCHER_API_KEY", "").strip()
    auth = request.headers.get("Authorization", "")
    scheme, _, token = auth.partition(" ")
    if expected and scheme.lower() == "bearer" and hmac.compare_digest(token.strip(), expected):
        return {"role": "watcher", "sub": "local-agent"}
    return _require_admin(request)


def _normalize_portal_url(url: Optional[str]) -> Optional[str]:
    value = (url or "").strip()
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(422, "Portal URL must be a valid http/https URL")
    return value


def _parse_optional_datetime(value: Optional[str]):
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return value

# ── SSO / Auth ─────────────────────────────────────────────────────────────────

_SSO_SECRET = os.environ.get("SSO_SECRET", "")
_PORTAL_URL = os.environ.get("PORTAL_URL", "https://practical-amazement-production-3539.up.railway.app")
_SELF_URL   = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "https://tender-ai-production-5a7d.up.railway.app")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[_PORTAL_URL, "http://localhost:3000", "http://localhost:8000", "http://localhost:8001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _b64url_dec(s: str) -> bytes:
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s)


def _verify_sso_token(token: str) -> dict:
    if not _SSO_SECRET:
        raise HTTPException(500, "SSO_SECRET not configured on this service")
    try:
        h, p, sig = token.split(".")
        expected = hmac.new(_SSO_SECRET.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
        actual = _b64url_dec(sig)
        if not hmac.compare_digest(expected, actual):
            raise ValueError("bad signature")
        payload = json.loads(_b64url_dec(p))
        if payload.get("exp", 0) < time.time():
            raise ValueError("token expired")
        return payload
    except (ValueError, KeyError) as exc:
        raise HTTPException(401, str(exc))


def _make_session(username: str, role: str) -> str:
    hdr = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b"=").decode()
    pay = base64.urlsafe_b64encode(
        json.dumps({"sub": username, "role": role, "exp": int(time.time()) + 86400}).encode()
    ).rstrip(b"=").decode()
    secret = _SSO_SECRET.encode() or b"_local_fallback_"
    sig = base64.urlsafe_b64encode(
        hmac.new(secret, f"{hdr}.{pay}".encode(), hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    return f"{hdr}.{pay}.{sig}"


def _decode_session(token: str) -> dict:
    try:
        h, p, sig = token.split(".")
        secret = _SSO_SECRET.encode() or b"_local_fallback_"
        expected = hmac.new(secret, f"{h}.{p}".encode(), hashlib.sha256).digest()
        actual = _b64url_dec(sig)
        if not hmac.compare_digest(expected, actual):
            raise ValueError("bad sig")
        payload = json.loads(_b64url_dec(p))
        if payload.get("exp", 0) < time.time():
            raise ValueError("expired")
        return payload
    except Exception:
        raise HTTPException(401, "Not authenticated")


@app.get("/api/health")
async def health():
    key = os.environ.get("OPENAI_API_KEY", "")
    return {
        "status": "ok",
        "openai_key_set": bool(key),
        "openai_key_preview": (key[:8] + "...") if key else "NOT SET",
        "PORT": os.environ.get("PORT", "NOT SET"),
        "RAILWAY_ENVIRONMENT": os.environ.get("RAILWAY_ENVIRONMENT", "NOT SET"),
        "all_env_keys": [k for k in os.environ.keys()],
    }


@app.get("/api/extensions/gem-bidplus-autofill.zip")
async def download_gem_extension_zip():
    if not EXTENSION_BUNDLE_DIR.exists():
        raise HTTPException(404, "Extension bundle not found")

    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(EXTENSION_BUNDLE_DIR.rglob("*")):
            if path.is_file():
                zf.write(path, arcname=path.relative_to(EXTENSION_BUNDLE_DIR).as_posix())
    memory_file.seek(0)

    headers = {
        "Content-Disposition": 'attachment; filename="gem-bidplus-autofill.zip"'
    }
    return Response(
        content=memory_file.getvalue(),
        media_type="application/zip",
        headers=headers,
    )


# ── Pydantic Models ───────────────────────────────────────────────────────────

class BOQItem(BaseModel):
    id: Optional[int] = None
    tender_id: Optional[int] = None
    part_number: Optional[str] = None
    item_description: Optional[str] = None
    quantity: Optional[str] = None


class RequiredDocument(BaseModel):
    id: Optional[int] = None
    tender_id: Optional[int] = None
    label: Optional[str] = None


ALLOWED_PARTICIPATION_STATUSES = {
    "IN PROGRESS",
    "FILED",
    "QUALIFIED",
    "DISQUALIFIED",
    "WON",
    "LOST",
    "FAILED",
}


class TenderPayload(BaseModel):
    gem_bidding_number: Optional[str] = None
    tender_number: Optional[str] = None
    date: Optional[str] = None
    bid_end_datetime: Optional[str] = None
    bid_opening_datetime: Optional[str] = None
    department_name: Optional[str] = None
    organization_name: Optional[str] = None
    office_name_location: Optional[str] = None
    total_quantity: Optional[str] = None
    make: Optional[str] = None
    tender_approx_value: Optional[str] = None
    won_text: Optional[str] = None
    lost_text: Optional[str] = None
    participant_text: Optional[str] = None
    expand_sections_json: Optional[list[dict]] = None
    participation_status: Optional[str] = None
    pdf_path: Optional[str] = None
    extraction_json_path: Optional[str] = None
    boq_items: List[BOQItem] = []
    required_documents: List[RequiredDocument] = []


class CompanyProfilePayload(BaseModel):
    company_name: Optional[str] = None
    address: Optional[str] = None
    gst_number: Optional[str] = None
    pan_number: Optional[str] = None
    msme_number: Optional[str] = None
    bank_name: Optional[str] = None
    account_number: Optional[str] = None
    ifsc_code: Optional[str] = None
    authorized_signatory_name: Optional[str] = None
    authorized_signatory_designation: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None


class GemResultDebugPayload(BaseModel):
    bid_number: str


class GemResultIngestPayload(BaseModel):
    raw_response: Optional[dict] = None
    bidNumber: Optional[str] = None
    resultAvailable: Optional[bool] = None
    bidResultAvailable: Optional[bool] = None
    raCreated: Optional[bool] = None
    raResultAvailable: Optional[bool] = None
    raNumber: Optional[str] = None
    raUrl: Optional[str] = None
    raStartDate: Optional[str] = None
    raEndDate: Optional[str] = None
    bidResultUrl: Optional[str] = None
    raResultUrl: Optional[str] = None
    gemResultStatus: Optional[str] = None
    gemPageStatus: Optional[str] = None
    rawGemMatchedDoc: Optional[dict[str, Any]] = None
    checkedAt: Optional[str] = None
    resultCheckError: Optional[str] = None


class GemResultErrorPayload(BaseModel):
    bidNumber: Optional[str] = None
    error: str
    checkedAt: Optional[str] = None


class ResultWatcherRunLogPayload(BaseModel):
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    total_pending: int = 0
    checked: int = 0
    results_found: int = 0
    not_available: int = 0
    failed: int = 0
    skipped: int = 0
    run_source: str = "LOCAL_AGENT"


class CompanyCapabilityProfilePayload(BaseModel):
    year_established: Optional[int] = None
    core_business: Optional[str] = None
    product_categories: Optional[str] = None
    brands_handled: Optional[str] = None
    industries_served: Optional[str] = None
    turnover_range: Optional[str] = None
    typical_tender_value_range: Optional[str] = None
    import_capability: Optional[bool] = False
    export_capability: Optional[bool] = False
    oem_support_available: Optional[bool] = False
    oem_authorizations: Optional[str] = None
    engineering_support: Optional[bool] = False
    installation_support: Optional[bool] = False
    gst_available: Optional[bool] = False
    pan_available: Optional[bool] = False
    msme_available: Optional[bool] = False
    itr_available: Optional[bool] = False
    bank_documents_available: Optional[bool] = False
    letterhead_available: Optional[bool] = False
    stamp_available: Optional[bool] = False
    signature_available: Optional[bool] = False
    psu_experience: Optional[bool] = False
    government_experience: Optional[bool] = False
    major_customers: Optional[str] = None
    past_orders_projects: Optional[str] = None


# ── Tender Upload & Extraction ────────────────────────────────────────────────

@app.post("/api/tenders/upload")
async def upload_tender(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted")

    file_id = str(uuid.uuid4())
    file_bytes = await file.read()
    print(f"[UPLOAD] file received: {file.filename!r} size={len(file_bytes)} bytes file_id={file_id}")

    # Save to PostgreSQL permanently
    database.save_uploaded_file(
        file_id=file_id,
        file_name=f"{file_id}_{file.filename}",
        original_name=file.filename,
        content_type="application/pdf",
        file_size=len(file_bytes),
        file_data=file_bytes,
        file_category="tender_pdf",
    )
    print(f"[UPLOAD] saved to PostgreSQL: file_id={file_id} pdf_path=/files/{file_id}")

    # Write temp file for AI extraction only
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    try:
        tmp.write(file_bytes)
        tmp.flush()
        tmp.close()
        raw = ai_extractor.process_pdf(tmp.name)
    except Exception as e:
        raise HTTPException(500, f"AI extraction failed: {e}")
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    m = re.search(r"GeM-Bidding-(\d+)", file.filename, re.IGNORECASE)
    gem_bidding_number = m.group(1) if m else None

    ti = raw.get("tender_information", {})
    extracted = dict(ti)
    extracted["gem_bidding_number"] = gem_bidding_number

    # Duplicate check — only when both identifiers are available
    tender_number = extracted.get("tender_number")
    if gem_bidding_number and tender_number:
        duplicate = database.find_tender_duplicate(gem_bidding_number, tender_number)
        if duplicate:
            raise HTTPException(409, "This tender already exists.")

    extracted["boq_items"] = raw.get("items", [])
    docs = raw.get("required_documents", [])
    extracted["required_documents"] = [
        {"label": d} if isinstance(d, str) else d for d in docs
    ]
    extracted["pdf_path"] = f"/files/{file_id}"

    json_path = EXTRACTIONS_DIR / f"{file_id}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(raw, f, indent=2, ensure_ascii=False)
    extracted["extraction_json_path"] = str(json_path)

    return extracted


@app.post("/api/tenders/bulk-upload")
async def bulk_upload_tenders(files: List[UploadFile] = File(...)):
    results = []
    for file in files:
        filename = file.filename or "unknown.pdf"
        if not filename.lower().endswith(".pdf"):
            results.append({"filename": filename, "status": "failed", "error": "Not a PDF file"})
            continue

        file_id = str(uuid.uuid4())

        try:
            file_bytes = await file.read()
        except Exception as e:
            results.append({"filename": filename, "status": "failed", "error": f"Could not read file: {e}"})
            continue

        print(f"[BULK UPLOAD] file received: {filename!r} size={len(file_bytes)} bytes file_id={file_id}")

        # Save to PostgreSQL permanently
        try:
            database.save_uploaded_file(
                file_id=file_id,
                file_name=f"{file_id}_{filename}",
                original_name=filename,
                content_type="application/pdf",
                file_size=len(file_bytes),
                file_data=file_bytes,
                file_category="tender_pdf",
            )
            print(f"[BULK UPLOAD] saved to PostgreSQL: file_id={file_id} pdf_path=/files/{file_id}")
        except Exception as e:
            results.append({"filename": filename, "status": "failed", "error": f"Could not store file: {e}"})
            continue

        m = re.search(r"GeM-Bidding-(\d+)", filename, re.IGNORECASE)
        gem_bidding_number = m.group(1) if m else None

        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        try:
            tmp.write(file_bytes)
            tmp.flush()
            tmp.close()
            raw = ai_extractor.process_pdf(tmp.name)
        except Exception as e:
            results.append({"filename": filename, "status": "failed", "error": f"AI extraction failed: {e}"})
            continue
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

        ti = raw.get("tender_information", {})
        extracted = dict(ti)
        extracted["gem_bidding_number"] = gem_bidding_number
        tender_number = extracted.get("tender_number")

        if gem_bidding_number and tender_number:
            duplicate = database.find_tender_duplicate(gem_bidding_number, tender_number)
            if duplicate:
                results.append({"filename": filename, "status": "duplicate", "error": "Tender already exists"})
                continue

        boq_items = raw.get("items", [])
        docs = raw.get("required_documents", [])
        required_documents = [{"label": d} if isinstance(d, str) else d for d in docs]

        extracted["pdf_path"] = f"/files/{file_id}"
        json_path = EXTRACTIONS_DIR / f"{file_id}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2, ensure_ascii=False)
        extracted["extraction_json_path"] = str(json_path)

        try:
            tender_id = database.save_tender(
                {k: v for k, v in extracted.items() if k not in ("boq_items", "required_documents")},
                boq_items,
                required_documents,
            )
            print(f"[BULK UPLOAD] DB insert success: tender_id={tender_id} file={filename!r}")
            results.append({"filename": filename, "status": "completed", "tender_id": tender_id})
        except Exception as e:
            results.append({"filename": filename, "status": "failed", "error": f"Could not save tender: {e}"})

    return results


# ── Tender CRUD ───────────────────────────────────────────────────────────────

@app.post("/api/tenders", status_code=201)
async def create_tender(payload: TenderPayload):
    tender_id = database.save_tender(
        payload.model_dump(exclude={"boq_items", "required_documents"}),
        [i.model_dump() for i in payload.boq_items],
        [d.model_dump() for d in payload.required_documents],
    )
    print(f"[DB] tender saved: id={tender_id} pdf_path={payload.pdf_path!r} gem={payload.gem_bidding_number!r}")
    return {"id": tender_id}


@app.post("/api/tenders/evaluate-extracted")
async def evaluate_extracted_tender(payload: TenderPayload):
    tender = payload.model_dump()
    tender["boq_items"] = [i.model_dump() for i in payload.boq_items]
    tender["required_documents"] = [d.model_dump() for d in payload.required_documents]
    capability = database.get_company_capability_profile()
    return evaluate_tender_against_capability(tender, capability)


@app.get("/api/tenders")
async def list_tenders():
    return database.list_tenders()


@app.get("/api/tenders/{tender_id}")
async def get_tender(tender_id: int):
    tender = database.get_tender(tender_id)
    if not tender:
        raise HTTPException(404, "Tender not found")
    return tender


@app.patch("/api/tenders/{tender_id}/boq-items")
async def update_boq_items(tender_id: int, payload: List[BOQItem]):
    if not database.get_tender(tender_id):
        raise HTTPException(404, "Tender not found")
    database.update_tender_boq_items(tender_id, [i.model_dump() for i in payload])
    return {"id": tender_id}


@app.post("/api/tenders/{tender_id}/evaluate")
async def evaluate_tender(tender_id: int):
    tender = database.get_tender(tender_id)
    if not tender:
        raise HTTPException(404, "Tender not found")
    capability = database.get_company_capability_profile()
    return evaluate_tender_against_capability(tender, capability)


@app.put("/api/tenders/{tender_id}")
async def update_tender(tender_id: int, payload: TenderPayload):
    if not database.get_tender(tender_id):
        raise HTTPException(404, "Tender not found")
    database.update_tender(
        tender_id,
        payload.model_dump(exclude={"boq_items", "required_documents"}),
        [i.model_dump() for i in payload.boq_items],
        [d.model_dump() for d in payload.required_documents],
    )
    return {"id": tender_id}


@app.get("/files/{file_id}")
async def serve_uploaded_file(file_id: str):
    row = database.get_uploaded_file(file_id)
    if not row:
        raise HTTPException(404, "File not found")
    return Response(
        content=bytes(row["file_data"]),
        media_type=row["content_type"] or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{row["original_name"]}"'},
    )


@app.get("/api/tenders/{tender_id}/pdf")
async def get_tender_pdf(tender_id: int):
    tender = database.get_tender(tender_id)
    if not tender:
        raise HTTPException(404, "Tender not found")
    pdf_path = tender.get("pdf_path")
    if not pdf_path:
        raise HTTPException(404, "No PDF linked to this tender")
    # New-style path: /files/{file_id}
    if pdf_path.startswith("/files/"):
        return RedirectResponse(url=pdf_path)
    # Old-style local path — attempt disk fallback
    p = Path(pdf_path)
    if not p.exists():
        raise HTTPException(404, "PDF file not found")
    return FileResponse(str(p), media_type="application/pdf", filename=p.name)


@app.post("/api/tenders/{tender_id}/attachments")
async def upload_tender_attachments(tender_id: int, files: List[UploadFile] = File(...)):
    if not database.get_tender(tender_id):
        raise HTTPException(404, "Tender not found")
    if not files:
        raise HTTPException(400, "No files uploaded")

    attachment_rows = []
    for file in files:
        file_bytes = await file.read()
        attachment_rows.append({
            "original_file_name": file.filename or "attachment",
            "content_type": file.content_type or "application/octet-stream",
            "file_size": len(file_bytes),
            "file_data": file_bytes,
        })
    return database.save_tender_attachments(tender_id, attachment_rows)


@app.get("/api/tenders/{tender_id}/attachments")
async def list_tender_attachments(tender_id: int):
    if not database.get_tender(tender_id):
        raise HTTPException(404, "Tender not found")
    return database.list_tender_attachments(tender_id)


@app.get("/api/attachments/{attachment_id}/download")
async def download_tender_attachment(attachment_id: int):
    row = database.get_tender_attachment(attachment_id)
    if not row:
        raise HTTPException(404, "Attachment not found")
    filename = row.get("original_file_name") or "attachment"
    safe_name = filename.replace('"', "")
    encoded_name = quote(filename)
    return Response(
        content=bytes(row["file_data"]),
        media_type=row.get("content_type") or "application/octet-stream",
        headers={
            "Content-Disposition": f'inline; filename="{safe_name}"; filename*=UTF-8\'\'{encoded_name}'
        },
    )


@app.post("/api/admin/clear-tender-data", status_code=200)
async def clear_tender_data():
    """One-time endpoint to wipe all tender records and old upload files."""
    database.clear_tenders()

    # Remove files from uploads folder only (leave company_docs untouched)
    deleted_files = 0
    for f in UPLOADS_DIR.iterdir():
        try:
            f.unlink()
            deleted_files += 1
        except OSError:
            pass

    return {"message": "Cleared", "deleted_upload_files": deleted_files}


@app.delete("/api/tenders/{tender_id}", status_code=204)
async def delete_tender(tender_id: int):
    tender = database.get_tender(tender_id)
    if not tender:
        raise HTTPException(404, "Tender not found")
    # Remove uploaded PDF
    if tender.get("pdf_path"):
        p = Path(tender["pdf_path"])
        if p.exists():
            p.unlink()
    # Remove extraction JSON
    if tender.get("extraction_json_path"):
        p = Path(tender["extraction_json_path"])
        if p.exists():
            p.unlink()
    # Remove generated docs folder for this tender
    gen_dir = GENERATED_DIR / str(tender_id)
    if gen_dir.exists():
        import shutil
        shutil.rmtree(gen_dir, ignore_errors=True)
    database.delete_tender(tender_id)


@app.patch("/api/tenders/{tender_id}/record-fields")
async def update_record_fields(tender_id: int, body: dict):
    if not database.get_tender(tender_id):
        raise HTTPException(404, "Tender not found")
    database.update_tender_record_fields(
        tender_id,
        None,
        None,
        body.get("participant_text"),
        body.get("remark"),
        body.get("expand_sections_json"),
    )
    return {"id": tender_id}


@app.patch("/api/tenders/{tender_id}/status")
async def update_participation_status(tender_id: int, body: dict):
    status = body.get("status")
    if status not in ALLOWED_PARTICIPATION_STATUSES:
        raise HTTPException(400, f"Invalid status. Allowed: {', '.join(sorted(ALLOWED_PARTICIPATION_STATUSES))}")
    if not database.get_tender(tender_id):
        raise HTTPException(404, "Tender not found")
    filed_date = database.update_tender_participation_status(tender_id, status)
    return {"id": tender_id, "participation_status": status, "filed_date": filed_date}


@app.post("/api/tenders/{tender_id}/check-result")
async def check_tender_result_now(tender_id: int):
    if not database.get_tender(tender_id):
        raise HTTPException(404, "Tender not found")
    try:
        return await asyncio.to_thread(check_tender_result, tender_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/tenders/{tender_id}/ingest-gem-result")
async def ingest_gem_result_now(tender_id: int, payload: GemResultIngestPayload, request: Request):
    _require_watcher_or_admin(request)
    if not database.get_tender(tender_id):
        raise HTTPException(404, "Tender not found")
    try:
        data = payload.dict(exclude_none=True)
        if payload.raw_response is not None:
            return await asyncio.to_thread(ingest_gem_result, tender_id, payload.raw_response)
        return await asyncio.to_thread(ingest_gem_result_from_agent, tender_id, data)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/tenders/{tender_id}/ingest-gem-result-error")
async def ingest_gem_result_error_now(tender_id: int, payload: GemResultErrorPayload, request: Request):
    _require_watcher_or_admin(request)
    if not database.get_tender(tender_id):
        raise HTTPException(404, "Tender not found")
    try:
        return await asyncio.to_thread(ingest_gem_result_error, tender_id, payload.dict(exclude_none=True))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/result-watcher/run")
async def run_result_watcher(request: Request):
    _require_admin(request)
    return await asyncio.to_thread(run_result_watcher_for_eligible_tenders)


@app.get("/api/result-watcher/pending")
async def list_pending_result_watcher_items(request: Request):
    _require_watcher_or_admin(request)
    return await asyncio.to_thread(list_pending_result_watcher_tenders)


@app.get("/api/result-watcher/recheck-targets")
async def list_recheck_result_watcher_items(request: Request):
    _require_watcher_or_admin(request)
    return await asyncio.to_thread(list_recheck_result_watcher_tenders)


@app.post("/api/result-watcher/run-log")
async def create_result_watcher_run_log(payload: ResultWatcherRunLogPayload, request: Request):
    _require_watcher_or_admin(request)
    data = payload.dict()
    return await asyncio.to_thread(
        database.create_result_watcher_run_log,
        started_at=_parse_optional_datetime(data.get("started_at")),
        finished_at=_parse_optional_datetime(data.get("finished_at")),
        total_pending=data.get("total_pending", 0),
        checked=data.get("checked", 0),
        results_found=data.get("results_found", 0),
        not_available=data.get("not_available", 0),
        failed=data.get("failed", 0),
        skipped=data.get("skipped", 0),
        run_source=data.get("run_source") or "LOCAL_AGENT",
    )


@app.get("/api/result-watcher/summary")
async def get_result_watcher_summary():
    return await asyncio.to_thread(database.get_result_watcher_summary)


@app.get("/api/tender-notifications")
async def list_tender_notifications():
    return database.list_tender_notifications()


@app.patch("/api/tender-notifications/{notification_id}/read")
async def mark_tender_notification_read(notification_id: int):
    notification = database.mark_tender_notification_read(notification_id)
    if not notification:
        raise HTTPException(404, "Notification not found")
    return notification


@app.post("/api/gem-result-debug")
async def gem_result_debug(payload: GemResultDebugPayload):
    return await asyncio.to_thread(debug_gem_exact_result_search, payload.bid_number)


# ── Company Profile ───────────────────────────────────────────────────────────

@app.get("/api/company/profile")
async def get_profile():
    try:
        data = database.get_company_profile()
        # Use JSONResponse so encoding errors are caught here, not by Starlette
        return JSONResponse(content=data)
    except Exception as e:
        print(f"[ERROR] get_profile: {type(e).__name__}: {e}")
        return JSONResponse(content={"error": "Failed to load company profile"}, status_code=500)


@app.put("/api/company/profile")
async def save_profile(payload: CompanyProfilePayload):
    try:
        database.upsert_company_profile(payload.model_dump())
        return JSONResponse(content={"message": "saved"})
    except Exception as e:
        print(f"[ERROR] save_profile: {type(e).__name__}: {e}")
        return JSONResponse(content={"error": "Failed to save company profile"}, status_code=500)


@app.get("/api/company/capability-profile")
async def get_capability_profile():
    try:
        return JSONResponse(content=database.get_company_capability_profile())
    except Exception as e:
        print(f"[ERROR] get_capability_profile: {type(e).__name__}: {e}")
        return JSONResponse(content={"error": "Failed to load capability profile"}, status_code=500)


@app.put("/api/company/capability-profile")
async def save_capability_profile(payload: CompanyCapabilityProfilePayload):
    try:
        database.upsert_company_capability_profile(payload.model_dump())
        return JSONResponse(content={"message": "saved"})
    except Exception as e:
        print(f"[ERROR] save_capability_profile: {type(e).__name__}: {e}")
        return JSONResponse(content={"error": "Failed to save capability profile"}, status_code=500)


@app.get("/api/company/evaluation-profile")
async def get_company_evaluation_profile():
    try:
        return JSONResponse(content=database.get_company_profile_for_tender_evaluation())
    except Exception as e:
        print(f"[ERROR] get_company_evaluation_profile: {type(e).__name__}: {e}")
        return JSONResponse(content={"error": "Failed to load evaluation profile"}, status_code=500)


@app.post("/api/company/profile/stamp")
async def upload_stamp(file: UploadFile = File(...)):
    data = await file.read()
    database.save_stamp(data, file.content_type or "application/octet-stream", file.filename)
    return {"message": "stamp saved"}


@app.post("/api/company/profile/signature")
async def upload_signature(file: UploadFile = File(...)):
    data = await file.read()
    database.save_signature(data, file.content_type or "application/octet-stream", file.filename)
    return {"message": "signature saved"}


@app.get("/api/company/profile/stamp/file")
async def get_stamp_file():
    row = database.get_stamp()
    if not row:
        raise HTTPException(404, "No stamp uploaded")
    return Response(
        content=bytes(row["stamp_data"]),
        media_type=row["stamp_content_type"] or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{row["stamp_original_name"]}"'},
    )


@app.get("/api/company/profile/signature/file")
async def get_signature_file():
    row = database.get_signature()
    if not row:
        raise HTTPException(404, "No signature uploaded")
    return Response(
        content=bytes(row["signature_data"]),
        media_type=row["signature_content_type"] or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{row["signature_original_name"]}"'},
    )


@app.delete("/api/company/profile/stamp")
async def delete_stamp():
    database.clear_stamp()
    return {"message": "removed"}


@app.delete("/api/company/profile/signature")
async def delete_signature():
    database.clear_signature()
    return {"message": "removed"}


# ── Company Document Library ──────────────────────────────────────────────────

@app.get("/api/company/documents")
async def list_company_docs():
    return database.list_company_documents()


@app.post("/api/company/documents", status_code=201)
async def upload_company_doc(
    file: UploadFile = File(...),
    document_name: str = Form(...),
    category: str = Form(...),
    financial_year: Optional[str] = Form(None),
    brand_oem: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
):
    file_bytes = await file.read()
    doc_id = database.save_company_document(
        {
            "document_name": document_name,
            "category": category,
            "financial_year": financial_year,
            "brand_oem": brand_oem,
            "file_path": None,
            "tags": tags,
            "content_type": file.content_type or "application/octet-stream",
            "original_name": file.filename,
        },
        file_bytes=file_bytes,
    )
    return {"id": doc_id}


@app.delete("/api/company/documents/{doc_id}")
async def delete_company_doc(doc_id: int):
    doc = database.get_company_document(doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    database.delete_company_document(doc_id)
    return {"message": "deleted"}


@app.get("/api/company/documents/{doc_id}/file")
async def get_company_doc_file(doc_id: int, download: bool = False):
    row = database.get_company_document_file(doc_id)
    if not row:
        raise HTTPException(404, "Document not found")
    if row.get("missing"):
        raise HTTPException(410, "File missing — please re-upload this document")
    filename = (row["original_name"] or "document").replace('"', '')
    disposition = "attachment" if download else "inline"
    return Response(
        content=row["file_data"],
        media_type=row["content_type"] or "application/octet-stream",
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )


# ── Tender Prepared Documents (Phase 3) ──────────────────────────────────────

@app.post("/api/tenders/{tender_id}/prepare")
async def prepare_tender_docs(tender_id: int):
    tender = database.get_tender(tender_id)
    if not tender:
        raise HTTPException(404, "Tender not found")

    required_docs = tender.get("required_documents", [])
    if not required_docs:
        raise HTTPException(400, "This tender has no required documents extracted")

    company_docs = database.list_company_documents()
    database.clear_prepared_documents(tender_id)

    results = doc_matcher.match_all_documents(
        required_docs,
        company_docs,
        financial_year=None,
    )

    for r in results:
        r["tender_id"] = tender_id
        database.save_prepared_document(r)

    return database.list_prepared_documents(tender_id)


@app.get("/api/tenders/{tender_id}/prepared-documents")
async def list_prepared_docs(tender_id: int):
    return database.list_prepared_documents(tender_id)


@app.post("/api/tenders/{tender_id}/prepared-documents/{doc_id}/generate")
async def generate_prepared_doc(tender_id: int, doc_id: int):
    prepared = database.get_prepared_document(doc_id)
    if not prepared:
        raise HTTPException(404, "Prepared document not found")

    tender = database.get_tender(tender_id)
    profile = database.get_company_profile()

    # Write stamp/signature bytes to temp files so doc_generator can embed them
    tmp_files = []
    try:
        stamp_row = database.get_stamp()
        if stamp_row:
            sf = tempfile.NamedTemporaryFile(
                suffix=Path(stamp_row["stamp_original_name"] or "stamp.png").suffix or ".png",
                delete=False,
            )
            sf.write(bytes(stamp_row["stamp_data"]))
            sf.close()
            tmp_files.append(sf.name)
            profile["stamp_file_path"] = sf.name
        else:
            profile["stamp_file_path"] = None

        sig_row = database.get_signature()
        if sig_row:
            sf2 = tempfile.NamedTemporaryFile(
                suffix=Path(sig_row["signature_original_name"] or "sig.png").suffix or ".png",
                delete=False,
            )
            sf2.write(bytes(sig_row["signature_data"]))
            sf2.close()
            tmp_files.append(sf2.name)
            profile["signature_file_path"] = sf2.name
        else:
            profile["signature_file_path"] = None

        safe_name = (prepared["document_name"] or "document").replace(" ", "_")
        out_tmp = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
        out_tmp.close()
        tmp_files.append(out_tmp.name)

        try:
            doc_generator.generate_ai_document(
                prepared["document_name"], profile, tender, out_tmp.name
            )
        except Exception as e:
            raise HTTPException(500, f"Generation failed: {e}")

        with open(out_tmp.name, "rb") as fh:
            doc_bytes = fh.read()
    finally:
        for p in tmp_files:
            try:
                os.unlink(p)
            except OSError:
                pass

    file_name = f"{doc_id}_{safe_name}.docx"
    database.save_prepared_document_file(doc_id, doc_bytes, file_name)
    database.update_prepared_document(doc_id, {
        "generated_file_path": f"db:{doc_id}",
        "status": "generated",
    })
    return database.get_prepared_document(doc_id)


@app.post("/api/tenders/{tender_id}/prepared-documents/{doc_id}/approve")
async def approve_prepared_doc(tender_id: int, doc_id: int):
    prepared = database.get_prepared_document(doc_id)
    if not prepared or prepared.get("tender_id") != tender_id:
        raise HTTPException(404, "Not found")
    database.update_prepared_document(doc_id, {"status": "approved"})
    return database.get_prepared_document(doc_id)


@app.post("/api/tenders/{tender_id}/prepared-documents/{doc_id}/upload")
async def upload_missing_doc(tender_id: int, doc_id: int, file: UploadFile = File(...)):
    file_bytes = await file.read()
    safe = file.filename.replace(" ", "_")
    file_name = f"{doc_id}_{safe}"
    database.save_prepared_document_file(doc_id, file_bytes, file_name)
    database.update_prepared_document(doc_id, {
        "generated_file_path": f"db:{doc_id}",
        "status": "uploaded",
        "source_type": "manual_upload",
    })
    return database.get_prepared_document(doc_id)


@app.get("/api/tenders/{tender_id}/prepared-documents/{doc_id}/download")
async def download_prepared_doc(tender_id: int, doc_id: int):
    prepared = database.get_prepared_document(doc_id)
    if not prepared or prepared.get("tender_id") != tender_id:
        raise HTTPException(404, "Not found")
    row = database.get_prepared_document_file(doc_id)
    if not row:
        raise HTTPException(404, "File not found")
    file_name = row["generated_file_name"] or f"document_{doc_id}.docx"
    return Response(
        content=bytes(row["generated_file_data"]),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )


# ── Auth endpoints ────────────────────────────────────────────────────────────

@app.get("/api/auth/sso")
async def sso_login(token: str):
    """Portal redirects here with a signed SSO token after the user authenticates."""
    payload = _verify_sso_token(token)
    username = payload.get("sub", "")
    if not username:
        raise HTTPException(401, "Invalid token: missing sub")
    role = payload.get("role", "employee")
    session = _make_session(username, role)
    resp = RedirectResponse(url="/", status_code=302)
    resp.set_cookie("tender_session", session, httponly=True, samesite="none", secure=True, max_age=86400)
    return resp


@app.get("/dev-login")
async def dev_login(next: str = "/"):
    """Local development only — auto-login as admin when SSO_SECRET is not set."""
    if _SSO_SECRET:
        raise HTTPException(403, "Dev login is disabled in production")
    session = _make_session("dev-admin", "admin")
    # Only allow same-site relative paths to avoid open-redirect, and return the
    # user to the page they originally requested (e.g. /gem-watcher).
    target = next if next.startswith("/") and not next.startswith("//") else "/"
    resp = RedirectResponse(url=target, status_code=302)
    resp.set_cookie("tender_session", session, httponly=True, samesite="lax", secure=False, max_age=86400)
    return resp


@app.get("/api/auth/me")
async def me(request: Request):
    token = request.cookies.get("tender_session")
    if not token:
        raise HTTPException(401, "Not authenticated")
    payload = _decode_session(token)
    return {"username": payload["sub"], "role": payload.get("role", "employee")}


@app.post("/api/auth/logout")
async def logout(response: Response):
    response.delete_cookie("tender_session", samesite="none", secure=True)
    return {"status": "logged_out"}


# ── Data Recovery ────────────────────────────────────────────────────────────

@app.post("/api/admin/recover-tenders")
async def recover_tenders(limit: int = 3):
    """
    Re-extract and restore tender records from PDFs still in PostgreSQL.
    Processes at most `limit` files per call (default 3) to avoid timeouts.
    Call repeatedly until recovered=0.
    """
    files = database.list_uploaded_file_ids()
    print(f"[RECOVER] found {len(files)} uploaded PDF(s) in PostgreSQL")

    recovered, skipped, failed = 0, 0, []

    for meta in files:
        if recovered >= limit:
            break

        file_id = meta["id"]
        original_name = meta.get("original_name") or ""
        pdf_path = f"/files/{file_id}"

        if database.find_tender_by_pdf_path(pdf_path):
            print(f"[RECOVER] skip (already exists): {original_name!r} file_id={file_id}")
            skipped += 1
            continue

        # Fetch PDF binary and re-extract
        row = database.get_uploaded_file(file_id)
        if not row:
            failed.append({"file_id": file_id, "error": "File data missing from uploaded_files"})
            continue

        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        try:
            tmp.write(bytes(row["file_data"]))
            tmp.flush()
            tmp.close()
            print(f"[RECOVER] extracting: {original_name!r} file_id={file_id}")
            raw = ai_extractor.process_pdf(tmp.name)
        except Exception as e:
            failed.append({"file_id": file_id, "original_name": original_name, "error": str(e)})
            print(f"[RECOVER] extraction failed: {original_name!r} — {e}")
            continue
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

        m = re.search(r"GeM-Bidding-(\d+)", original_name, re.IGNORECASE)
        gem_bidding_number = m.group(1) if m else None

        ti = raw.get("tender_information", {})
        extracted = dict(ti)
        extracted["gem_bidding_number"] = gem_bidding_number
        extracted["pdf_path"] = pdf_path
        extracted["extraction_json_path"] = None

        boq_items = raw.get("items", [])
        docs = raw.get("required_documents", [])
        required_documents = [{"label": d} if isinstance(d, str) else d for d in docs]

        try:
            tender_id = database.save_tender(
                {k: v for k, v in extracted.items() if k not in ("boq_items", "required_documents")},
                boq_items,
                required_documents,
            )
            print(f"[RECOVER] restored: tender_id={tender_id} file={original_name!r}")
            recovered += 1
        except Exception as e:
            failed.append({"file_id": file_id, "original_name": original_name, "error": str(e)})
            print(f"[RECOVER] DB save failed: {original_name!r} — {e}")

    total_in_pg = len(files)
    already_done = skipped + recovered
    remaining = total_in_pg - already_done - len(failed)
    return {
        "recovered": recovered,
        "skipped": skipped,
        "failed": failed,
        "remaining": max(remaining, 0),
        "total_in_pg": total_in_pg,
    }


# ── Government Portals ────────────────────────────────────────────────────────

class TenderPortalCreate(BaseModel):
    portal_name: str
    portal_url: Optional[str] = None
    login_id: Optional[str] = None
    password: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[str] = "ACTIVE"


class TenderPortalUpdate(BaseModel):
    portal_name: Optional[str] = None
    portal_url: Optional[str] = None
    login_id: Optional[str] = None
    password: Optional[str] = None  # None = keep existing encrypted password
    notes: Optional[str] = None
    status: Optional[str] = None


def _serialize_tender_portal(row: dict) -> dict:
    return {
        "id": row["id"],
        "portal_name": row.get("portal_name"),
        "portal_url": row.get("portal_url"),
        "login_id": row.get("login_id"),
        "notes": row.get("notes"),
        "status": row.get("status") or "ACTIVE",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "has_password": bool(row.get("has_password")),
        "password_masked": "********" if row.get("has_password") else "",
    }


@app.get("/api/tender-portals")
async def list_tender_portals(request: Request):
    _require_admin(request)
    return [_serialize_tender_portal(row) for row in database.list_tender_portals(include_inactive=True)]


@app.post("/api/tender-portals", status_code=201)
async def create_tender_portal(portal: TenderPortalCreate, request: Request):
    _require_admin(request)
    portal_name = (portal.portal_name or "").strip()
    if not portal_name:
        raise HTTPException(422, "Portal name is required")
    status = (portal.status or "ACTIVE").strip().upper()
    if status not in ("ACTIVE", "INACTIVE"):
        raise HTTPException(422, "Status must be ACTIVE or INACTIVE")
    portal_id = database.create_tender_portal(
        portal_name=portal_name,
        portal_url=_normalize_portal_url(portal.portal_url),
        login_id=(portal.login_id or "").strip() or None,
        encrypted_password=_encrypt_password(portal.password or ""),
        notes=(portal.notes or "").strip() or None,
        status=status,
    )
    created = database.get_tender_portal(portal_id)
    return {"status": "created", "portal": _serialize_tender_portal(created)}


@app.put("/api/tender-portals/{portal_id}")
async def update_tender_portal(portal_id: int, portal: TenderPortalUpdate, request: Request):
    _require_admin(request)
    existing = database.get_tender_portal_with_password(portal_id)
    if not existing:
        raise HTTPException(404, "Tender portal not found")
    portal_name = (portal.portal_name if portal.portal_name is not None else existing.get("portal_name") or "").strip()
    if not portal_name:
        raise HTTPException(422, "Portal name is required")
    portal_url = _normalize_portal_url(portal.portal_url) if portal.portal_url is not None else existing.get("portal_url")
    login_id = (portal.login_id if portal.login_id is not None else existing.get("login_id") or "").strip() or None
    notes = (portal.notes if portal.notes is not None else existing.get("notes") or "").strip() or None
    status = (portal.status if portal.status is not None else existing.get("status") or "ACTIVE").strip().upper()
    if status not in ("ACTIVE", "INACTIVE"):
        raise HTTPException(422, "Status must be ACTIVE or INACTIVE")
    encrypted_password = existing.get("encrypted_password", "")
    if portal.password is not None:
        encrypted_password = _encrypt_password(portal.password or "")
    database.update_tender_portal(portal_id, portal_name, portal_url, login_id, encrypted_password, notes, status)
    updated = database.get_tender_portal(portal_id)
    return {"status": "updated", "portal": _serialize_tender_portal(updated)}


@app.post("/api/tender-portals/{portal_id}/reveal-password")
async def reveal_tender_portal_password(portal_id: int, request: Request):
    _require_admin(request)
    portal = database.get_tender_portal_with_password(portal_id)
    if not portal:
        raise HTTPException(404, "Tender portal not found")
    encrypted = portal.get("encrypted_password", "")
    if not encrypted:
        return {"password": ""}
    try:
        return {"password": _decrypt_password(encrypted)}
    except InvalidToken:
        print(f"[WARN] Tender portal {portal_id}: InvalidToken - password was encrypted with a different key")
        raise HTTPException(422, "Password must be re-entered because the encryption key changed")
    except Exception as e:
        print(f"[ERROR] Tender portal {portal_id}: decrypt failed - {type(e).__name__}: {e}")
        raise HTTPException(500, "Failed to decrypt password")


@app.patch("/api/tender-portals/{portal_id}/deactivate")
async def deactivate_tender_portal(portal_id: int, request: Request):
    _require_admin(request)
    existing = database.get_tender_portal(portal_id)
    if not existing:
        raise HTTPException(404, "Tender portal not found")
    database.set_tender_portal_status(portal_id, "INACTIVE")
    updated = database.get_tender_portal(portal_id)
    return {"status": "deactivated", "portal": _serialize_tender_portal(updated)}


@app.patch("/api/tender-portals/{portal_id}/activate")
async def activate_tender_portal(portal_id: int, request: Request):
    _require_admin(request)
    existing = database.get_tender_portal(portal_id)
    if not existing:
        raise HTTPException(404, "Tender portal not found")
    database.set_tender_portal_status(portal_id, "ACTIVE")
    updated = database.get_tender_portal(portal_id)
    return {"status": "activated", "portal": _serialize_tender_portal(updated)}


class PortalCreate(BaseModel):
    name: str
    url: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    notes: Optional[str] = None


class PortalUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    notes: Optional[str] = None


@app.get("/api/portals")
async def list_portals(request: Request):
    _require_admin(request)
    return database.list_portals()


@app.post("/api/portals", status_code=201)
async def create_portal(portal: PortalCreate, request: Request):
    _require_admin(request)
    payload = TenderPortalCreate(
        portal_name=portal.name,
        portal_url=portal.url,
        login_id=portal.username,
        password=portal.password,
        notes=portal.notes,
        status="ACTIVE",
    )
    result = await create_tender_portal(payload, request)
    return {"id": result["portal"]["id"], "status": "created"}


@app.put("/api/portals/{portal_id}")
async def update_portal(portal_id: int, portal: PortalUpdate, request: Request):
    _require_admin(request)
    payload = TenderPortalUpdate(
        portal_name=portal.name,
        portal_url=portal.url,
        login_id=portal.username,
        password=portal.password,
        notes=portal.notes,
    )
    await update_tender_portal(portal_id, payload, request)
    return {"status": "updated"}


@app.delete("/api/portals/{portal_id}", status_code=204)
async def delete_portal(portal_id: int, request: Request):
    _require_admin(request)
    if not database.get_portal(portal_id):
        raise HTTPException(404, "Portal not found")
    database.delete_portal(portal_id)


@app.post("/api/portals/{portal_id}/reveal")
async def reveal_portal_password(portal_id: int, request: Request):
    _require_admin(request)
    return await reveal_tender_portal_password(portal_id, request)


app.include_router(gem_watcher_router, dependencies=[Depends(_require_admin)])


@app.get("/gem-watcher")
async def gem_watcher_page():
    return FileResponse(str(HERE / "static" / "index.html"))


@app.get("/gem-candidates")
async def gem_candidates_page():
    return FileResponse(str(HERE / "static" / "index.html"))


@app.get("/tender-portals")
async def tender_portals_page():
    return FileResponse(str(HERE / "static" / "index.html"))


@app.get("/gem-result-debug")
async def gem_result_debug_page():
    return FileResponse(str(HERE / "static" / "index.html"))


# ── Static SPA (must be last) ─────────────────────────────────────────────────

app.mount("/", StaticFiles(directory=str(HERE / "static"), html=True), name="static")
