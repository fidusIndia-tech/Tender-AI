import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import database
import ai_extractor
import doc_matcher
import doc_generator

HERE = Path(__file__).parent
UPLOADS_DIR     = HERE / "uploads"
EXTRACTIONS_DIR = HERE / "extractions"
COMPANY_DOCS_DIR = HERE / "company_docs"
GENERATED_DIR    = HERE / "generated"

for d in (UPLOADS_DIR, EXTRACTIONS_DIR, COMPANY_DOCS_DIR, GENERATED_DIR):
    d.mkdir(exist_ok=True)

app = FastAPI(title="AI Tender Management System")
database.init_db()

# ── SSO / Auth ─────────────────────────────────────────────────────────────────

_SSO_SECRET = os.environ.get("SSO_SECRET", "")
_PORTAL_URL = os.environ.get("PORTAL_URL", "https://practical-amazement-production-3539.up.railway.app")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[_PORTAL_URL, "http://localhost:3000", "http://localhost:8000"],
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


ALLOWED_PARTICIPATION_STATUSES = {"WON", "LOST", "FAILED", "FILED"}


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


# ── Tender Upload & Extraction ────────────────────────────────────────────────

@app.post("/api/tenders/upload")
async def upload_tender(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted")

    file_id = str(uuid.uuid4())
    file_bytes = await file.read()

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
    return {"id": tender_id}


@app.get("/api/tenders")
async def list_tenders():
    return database.list_tenders()


@app.get("/api/tenders/{tender_id}")
async def get_tender(tender_id: int):
    tender = database.get_tender(tender_id)
    if not tender:
        raise HTTPException(404, "Tender not found")
    return tender


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


@app.post("/api/admin/clear-tender-data", status_code=200)
async def clear_tender_data():
    """One-time endpoint to wipe all tender records and old upload files."""
    conn = database.get_db()
    conn.executescript("""
        DELETE FROM tender_prepared_documents;
        DELETE FROM tender_required_documents;
        DELETE FROM tender_items;
        DELETE FROM tenders;
    """)
    conn.commit()
    conn.close()

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
    )
    return {"id": tender_id}


@app.patch("/api/tenders/{tender_id}/status")
async def update_participation_status(tender_id: int, body: dict):
    status = body.get("status")
    if status not in ALLOWED_PARTICIPATION_STATUSES:
        raise HTTPException(400, f"Invalid status. Allowed: {', '.join(sorted(ALLOWED_PARTICIPATION_STATUSES))}")
    if not database.get_tender(tender_id):
        raise HTTPException(404, "Tender not found")
    database.update_tender_participation_status(tender_id, status)
    return {"id": tender_id, "participation_status": status}


# ── Company Profile ───────────────────────────────────────────────────────────

@app.get("/api/company/profile")
async def get_profile():
    return database.get_company_profile()


@app.put("/api/company/profile")
async def save_profile(payload: CompanyProfilePayload):
    database.upsert_company_profile(payload.model_dump())
    return {"message": "saved"}


@app.post("/api/company/profile/stamp")
async def upload_stamp(file: UploadFile = File(...)):
    safe = file.filename.replace(" ", "_")
    new_path = COMPANY_DOCS_DIR / f"stamp_{uuid.uuid4()}_{safe}"
    with open(new_path, "wb") as f:
        f.write(await file.read())
    profile = database.get_company_profile()
    # Delete old stamp file before replacing
    if profile.get("stamp_file_path"):
        old = Path(profile["stamp_file_path"])
        old.unlink(missing_ok=True)
    profile["stamp_file_path"] = str(new_path)
    database.upsert_company_profile(profile)
    return {"stamp_file_path": str(new_path)}


@app.post("/api/company/profile/signature")
async def upload_signature(file: UploadFile = File(...)):
    safe = file.filename.replace(" ", "_")
    new_path = COMPANY_DOCS_DIR / f"sig_{uuid.uuid4()}_{safe}"
    with open(new_path, "wb") as f:
        f.write(await file.read())
    profile = database.get_company_profile()
    # Delete old signature file before replacing
    if profile.get("signature_file_path"):
        old = Path(profile["signature_file_path"])
        old.unlink(missing_ok=True)
    profile["signature_file_path"] = str(new_path)
    database.upsert_company_profile(profile)
    return {"signature_file_path": str(new_path)}


@app.get("/api/company/profile/stamp/file")
async def get_stamp_file():
    profile = database.get_company_profile()
    path = profile.get("stamp_file_path")
    if not path:
        raise HTTPException(404, "No stamp uploaded")
    p = Path(path)
    if not p.exists():
        raise HTTPException(404, "Stamp file not found on disk")
    return FileResponse(str(p), filename=p.name)


@app.get("/api/company/profile/signature/file")
async def get_signature_file():
    profile = database.get_company_profile()
    path = profile.get("signature_file_path")
    if not path:
        raise HTTPException(404, "No signature uploaded")
    p = Path(path)
    if not p.exists():
        raise HTTPException(404, "Signature file not found on disk")
    return FileResponse(str(p), filename=p.name)


@app.delete("/api/company/profile/stamp")
async def delete_stamp():
    profile = database.get_company_profile()
    if profile.get("stamp_file_path"):
        Path(profile["stamp_file_path"]).unlink(missing_ok=True)
    database.clear_profile_image_path("stamp_file_path")
    return {"message": "removed"}



@app.delete("/api/company/profile/signature")
async def delete_signature():
    profile = database.get_company_profile()
    if profile.get("signature_file_path"):
        Path(profile["signature_file_path"]).unlink(missing_ok=True)
    database.clear_profile_image_path("signature_file_path")
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
    safe = file.filename.replace(" ", "_")
    path = COMPANY_DOCS_DIR / f"{uuid.uuid4()}_{safe}"
    with open(path, "wb") as f:
        f.write(await file.read())
    doc_id = database.save_company_document({
        "document_name": document_name,
        "category": category,
        "financial_year": financial_year,
        "brand_oem": brand_oem,
        "file_path": str(path),
        "tags": tags,
    })
    return {"id": doc_id}


@app.delete("/api/company/documents/{doc_id}")
async def delete_company_doc(doc_id: int):
    doc = database.get_company_document(doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    if doc.get("file_path"):
        p = Path(doc["file_path"])
        if p.exists():
            p.unlink()
    database.delete_company_document(doc_id)
    return {"message": "deleted"}


@app.get("/api/company/documents/{doc_id}/file")
async def get_company_doc_file(doc_id: int):
    doc = database.get_company_document(doc_id)
    if not doc or not doc.get("file_path"):
        raise HTTPException(404, "File not found")
    p = Path(doc["file_path"])
    if not p.exists():
        raise HTTPException(404, "File not found on disk")
    return FileResponse(str(p), filename=p.name)


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

    out_dir = GENERATED_DIR / str(tender_id)
    out_dir.mkdir(exist_ok=True)
    safe_name = (prepared["document_name"] or "document").replace(" ", "_")
    out_path = str(out_dir / f"{doc_id}_{safe_name}.docx")

    try:
        doc_generator.generate_ai_document(
            prepared["document_name"], profile, tender, out_path
        )
    except Exception as e:
        raise HTTPException(500, f"Generation failed: {e}")

    database.update_prepared_document(doc_id, {
        "generated_file_path": out_path,
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
    out_dir = GENERATED_DIR / str(tender_id)
    out_dir.mkdir(exist_ok=True)
    safe = file.filename.replace(" ", "_")
    path = out_dir / f"{doc_id}_{safe}"
    with open(path, "wb") as f:
        f.write(await file.read())
    database.update_prepared_document(doc_id, {
        "generated_file_path": str(path),
        "status": "uploaded",
        "source_type": "manual_upload",
    })
    return database.get_prepared_document(doc_id)


@app.get("/api/tenders/{tender_id}/prepared-documents/{doc_id}/download")
async def download_prepared_doc(tender_id: int, doc_id: int):
    prepared = database.get_prepared_document(doc_id)
    if not prepared or prepared.get("tender_id") != tender_id:
        raise HTTPException(404, "Not found")
    if not prepared.get("generated_file_path"):
        raise HTTPException(404, "File not generated yet")
    p = Path(prepared["generated_file_path"])
    if not p.exists():
        raise HTTPException(404, "File not found on disk")
    return FileResponse(str(p), filename=p.name)


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


# ── Static SPA (must be last) ─────────────────────────────────────────────────

app.mount("/", StaticFiles(directory=str(HERE / "static"), html=True), name="static")
