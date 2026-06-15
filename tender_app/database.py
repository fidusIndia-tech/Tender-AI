import os
import psycopg2
import psycopg2.extras
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()


def get_db():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    return psycopg2.connect(url)


def init_db():
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS uploaded_files (
                id TEXT PRIMARY KEY,
                file_name TEXT,
                original_name TEXT,
                content_type TEXT,
                file_size INTEGER,
                file_data BYTEA,
                file_category TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tenders (
                id SERIAL PRIMARY KEY,
                gem_bidding_number TEXT,
                tender_number TEXT,
                date TEXT,
                bid_end_datetime TEXT,
                bid_opening_datetime TEXT,
                department_name TEXT,
                organization_name TEXT,
                office_name_location TEXT,
                total_quantity TEXT,
                make TEXT,
                tender_approx_value TEXT,
                won_text TEXT,
                lost_text TEXT,
                participant_text TEXT,
                uploaded_at TEXT,
                pdf_path TEXT,
                extraction_json_path TEXT,
                status TEXT DEFAULT 'extracted',
                participation_status TEXT DEFAULT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tender_items (
                id SERIAL PRIMARY KEY,
                tender_id INTEGER REFERENCES tenders(id),
                part_number TEXT,
                item_description TEXT,
                quantity TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tender_required_documents (
                id SERIAL PRIMARY KEY,
                tender_id INTEGER REFERENCES tenders(id),
                label TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS company_profile (
                id SERIAL PRIMARY KEY,
                company_name TEXT,
                address TEXT,
                gst_number TEXT,
                pan_number TEXT,
                msme_number TEXT,
                bank_name TEXT,
                account_number TEXT,
                ifsc_code TEXT,
                authorized_signatory_name TEXT,
                authorized_signatory_designation TEXT,
                email TEXT,
                phone TEXT,
                stamp_file_path TEXT,
                signature_file_path TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS company_documents (
                id SERIAL PRIMARY KEY,
                document_name TEXT,
                category TEXT,
                financial_year TEXT,
                brand_oem TEXT,
                file_path TEXT,
                tags TEXT,
                uploaded_at TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tender_prepared_documents (
                id SERIAL PRIMARY KEY,
                tender_id INTEGER REFERENCES tenders(id),
                required_document_label TEXT,
                document_name TEXT,
                source_type TEXT,
                source_document_id INTEGER,
                generated_file_path TEXT,
                status TEXT,
                remarks TEXT,
                recommended_action TEXT,
                created_at TEXT
            )
        """)
        # Persistent binary storage migrations
        cur.execute("ALTER TABLE company_profile ADD COLUMN IF NOT EXISTS stamp_data BYTEA")
        cur.execute("ALTER TABLE company_profile ADD COLUMN IF NOT EXISTS stamp_content_type TEXT")
        cur.execute("ALTER TABLE company_profile ADD COLUMN IF NOT EXISTS stamp_original_name TEXT")
        cur.execute("ALTER TABLE company_profile ADD COLUMN IF NOT EXISTS signature_data BYTEA")
        cur.execute("ALTER TABLE company_profile ADD COLUMN IF NOT EXISTS signature_content_type TEXT")
        cur.execute("ALTER TABLE company_profile ADD COLUMN IF NOT EXISTS signature_original_name TEXT")
        cur.execute("ALTER TABLE company_documents ADD COLUMN IF NOT EXISTS file_data BYTEA")
        cur.execute("ALTER TABLE company_documents ADD COLUMN IF NOT EXISTS content_type TEXT")
        cur.execute("ALTER TABLE company_documents ADD COLUMN IF NOT EXISTS original_name TEXT")
        cur.execute("ALTER TABLE company_documents ADD COLUMN IF NOT EXISTS file_size INTEGER")
        cur.execute("ALTER TABLE tender_prepared_documents ADD COLUMN IF NOT EXISTS generated_file_data BYTEA")
        cur.execute("ALTER TABLE tender_prepared_documents ADD COLUMN IF NOT EXISTS generated_file_name TEXT")
        cur.execute("ALTER TABLE tenders ADD COLUMN IF NOT EXISTS filed_date TEXT")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS government_portals (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                url TEXT,
                username TEXT,
                password_encrypted TEXT,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    conn.commit()
    conn.close()
    print("[DB] All PostgreSQL tables initialized")


# ── Government Portals ────────────────────────────────────────────────────────

def list_portals():
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT id, name, url, username, notes, created_at FROM government_portals ORDER BY name"
        )
        rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_portal(portal_id: int):
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT id, name, url, username, notes FROM government_portals WHERE id=%s",
            (portal_id,),
        )
        row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_portal_with_password(portal_id: int):
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT id, name, url, username, password_encrypted, notes FROM government_portals WHERE id=%s",
            (portal_id,),
        )
        row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def create_portal(name: str, url, username, password_encrypted: str, notes) -> int:
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO government_portals (name, url, username, password_encrypted, notes) VALUES (%s,%s,%s,%s,%s) RETURNING id",
            (name, url, username, password_encrypted, notes),
        )
        portal_id = cur.fetchone()[0]
    conn.commit()
    conn.close()
    return portal_id


def update_portal(portal_id: int, name: str, url, username, password_encrypted: str, notes):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE government_portals SET name=%s, url=%s, username=%s, password_encrypted=%s, notes=%s WHERE id=%s",
            (name, url, username, password_encrypted, notes, portal_id),
        )
    conn.commit()
    conn.close()


def delete_portal(portal_id: int):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM government_portals WHERE id=%s", (portal_id,))
    conn.commit()
    conn.close()


# ── Uploaded Files ────────────────────────────────────────────────────────────

def save_uploaded_file(file_id, file_name, original_name, content_type, file_size, file_data, file_category="tender_pdf"):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO uploaded_files
               (id, file_name, original_name, content_type, file_size, file_data, file_category)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (file_id, file_name, original_name, content_type, file_size, psycopg2.Binary(file_data), file_category),
        )
    conn.commit()
    conn.close()


def get_uploaded_file(file_id):
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT id, file_name, original_name, content_type, file_size, file_data FROM uploaded_files WHERE id=%s",
            (file_id,),
        )
        row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def list_uploaded_file_ids():
    """Return metadata for all uploaded tender PDFs (no binary data)."""
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT id, original_name, file_size, created_at FROM uploaded_files WHERE file_category='tender_pdf' ORDER BY created_at"
        )
        rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Tenders ───────────────────────────────────────────────────────────────────

def save_tender(data, items, documents):
    conn = get_db()
    with conn.cursor() as cur:
        now = datetime.now().isoformat()
        cur.execute(
            """INSERT INTO tenders (
                gem_bidding_number, tender_number, date, bid_end_datetime, bid_opening_datetime,
                department_name, organization_name, office_name_location,
                total_quantity, make, tender_approx_value,
                won_text, lost_text, participant_text,
                uploaded_at, pdf_path, extraction_json_path, status, participation_status
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id""",
            (
                data.get("gem_bidding_number"),
                data.get("tender_number"), data.get("date"),
                data.get("bid_end_datetime"), data.get("bid_opening_datetime"),
                data.get("department_name"), data.get("organization_name"),
                data.get("office_name_location"),
                data.get("total_quantity"), data.get("make"), data.get("tender_approx_value"),
                data.get("won_text"), data.get("lost_text"), data.get("participant_text"),
                now, data.get("pdf_path"), data.get("extraction_json_path"),
                "saved", "IN PROGRESS",
            ),
        )
        tender_id = cur.fetchone()[0]
        _insert_items(cur, tender_id, items)
        _insert_docs(cur, tender_id, documents)
    conn.commit()
    conn.close()
    return tender_id


def update_tender(tender_id, data, items, documents):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE tenders SET
                gem_bidding_number=%s, tender_number=%s, date=%s, bid_end_datetime=%s, bid_opening_datetime=%s,
                department_name=%s, organization_name=%s, office_name_location=%s,
                total_quantity=%s, make=%s, tender_approx_value=%s,
                won_text=COALESCE(%s,won_text), lost_text=COALESCE(%s,lost_text),
                participant_text=COALESCE(%s,participant_text),
                status=%s, participation_status=COALESCE(%s, participation_status)
            WHERE id=%s""",
            (
                data.get("gem_bidding_number"),
                data.get("tender_number"), data.get("date"),
                data.get("bid_end_datetime"), data.get("bid_opening_datetime"),
                data.get("department_name"), data.get("organization_name"),
                data.get("office_name_location"),
                data.get("total_quantity"), data.get("make"), data.get("tender_approx_value"),
                data.get("won_text"), data.get("lost_text"), data.get("participant_text"),
                "reviewed",
                data.get("participation_status"),
                tender_id,
            ),
        )
        cur.execute("DELETE FROM tender_items WHERE tender_id=%s", (tender_id,))
        cur.execute("DELETE FROM tender_required_documents WHERE tender_id=%s", (tender_id,))
        _insert_items(cur, tender_id, items)
        _insert_docs(cur, tender_id, documents)
    conn.commit()
    conn.close()


def get_tender(tender_id):
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM tenders WHERE id=%s", (tender_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return None
        tender = dict(row)
        cur.execute("SELECT * FROM tender_items WHERE tender_id=%s", (tender_id,))
        tender["boq_items"] = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT * FROM tender_required_documents WHERE tender_id=%s", (tender_id,))
        tender["required_documents"] = [dict(r) for r in cur.fetchall()]
    conn.close()
    return tender


def list_tenders():
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT id, gem_bidding_number, tender_number, organization_name, bid_end_datetime,
                      make, total_quantity, tender_approx_value, participation_status, uploaded_at,
                      won_text, lost_text, participant_text, pdf_path, filed_date
               FROM tenders ORDER BY uploaded_at DESC"""
        )
        rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_tender_record_fields(tender_id, won_text, lost_text, participant_text):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE tenders SET won_text=%s, lost_text=%s, participant_text=%s WHERE id=%s",
            (won_text, lost_text, participant_text, tender_id),
        )
    conn.commit()
    conn.close()


def update_tender_participation_status(tender_id, status):
    conn = get_db()
    filed_date = None
    with conn.cursor() as cur:
        if status == 'FILED':
            filed_date = datetime.now().strftime('%d-%m-%Y')
            cur.execute(
                "UPDATE tenders SET participation_status=%s, filed_date=%s WHERE id=%s",
                (status, filed_date, tender_id),
            )
        else:
            cur.execute(
                "UPDATE tenders SET participation_status=%s WHERE id=%s", (status, tender_id)
            )
    conn.commit()
    conn.close()
    return filed_date


def find_tender_duplicate(gem_bidding_number, tender_number):
    """Return existing tender if gem_bidding_number AND tender_number both match."""
    if not gem_bidding_number and not tender_number:
        return None
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT id FROM tenders WHERE gem_bidding_number=%s AND tender_number=%s LIMIT 1",
            (gem_bidding_number, tender_number),
        )
        row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def find_tender_by_pdf_path(pdf_path):
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT id FROM tenders WHERE pdf_path=%s LIMIT 1", (pdf_path,))
        row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def delete_tender(tender_id):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM tender_prepared_documents WHERE tender_id=%s", (tender_id,))
        cur.execute("DELETE FROM tender_required_documents WHERE tender_id=%s", (tender_id,))
        cur.execute("DELETE FROM tender_items WHERE tender_id=%s", (tender_id,))
        cur.execute("DELETE FROM tenders WHERE id=%s", (tender_id,))
    conn.commit()
    conn.close()


def clear_tenders():
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM tender_prepared_documents")
        cur.execute("DELETE FROM tender_required_documents")
        cur.execute("DELETE FROM tender_items")
        cur.execute("DELETE FROM tenders")
    conn.commit()
    conn.close()


def _insert_items(cur, tender_id, items):
    for item in items:
        cur.execute(
            """INSERT INTO tender_items (tender_id, part_number, item_description, quantity)
               VALUES (%s,%s,%s,%s)""",
            (tender_id, item.get("part_number"), item.get("item_description"), item.get("quantity")),
        )


def _insert_docs(cur, tender_id, documents):
    for doc in documents:
        label = doc.get("label") if isinstance(doc, dict) else doc
        cur.execute(
            "INSERT INTO tender_required_documents (tender_id, label) VALUES (%s,%s)",
            (tender_id, label),
        )


# ── Company Profile ───────────────────────────────────────────────────────────

def get_company_profile():
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        # Exclude binary columns (stamp_data, signature_data) — served via separate endpoints
        cur.execute("""
            SELECT id, company_name, address, gst_number, pan_number, msme_number,
                   bank_name, account_number, ifsc_code,
                   authorized_signatory_name, authorized_signatory_designation,
                   email, phone, stamp_file_path, signature_file_path,
                   stamp_original_name, stamp_content_type,
                   signature_original_name, signature_content_type,
                   (stamp_data IS NOT NULL) AS has_stamp,
                   (signature_data IS NOT NULL) AS has_signature
            FROM company_profile LIMIT 1
        """)
        row = cur.fetchone()
    conn.close()
    return dict(row) if row else {}


def upsert_company_profile(data):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM company_profile LIMIT 1")
        existing = cur.fetchone()
        text_fields = ["company_name", "address", "gst_number", "pan_number", "msme_number",
                       "bank_name", "account_number", "ifsc_code",
                       "authorized_signatory_name", "authorized_signatory_designation",
                       "email", "phone"]
        image_fields = ["stamp_file_path", "signature_file_path"]
        if existing:
            set_parts = [f"{f}=%s" for f in text_fields] + \
                        [f"{f}=COALESCE(%s,{f})" for f in image_fields]
            values = [data.get(f) for f in text_fields] + [data.get(f) for f in image_fields]
            cur.execute(
                f"UPDATE company_profile SET {', '.join(set_parts)} WHERE id=%s",
                values + [existing[0]],
            )
        else:
            all_fields = text_fields + image_fields
            values = [data.get(f) for f in all_fields]
            placeholders = ", ".join("%s" for _ in all_fields)
            cur.execute(
                f"INSERT INTO company_profile ({', '.join(all_fields)}) VALUES ({placeholders})",
                values,
            )
    conn.commit()
    conn.close()


def clear_profile_image_path(field):
    if field not in {"stamp_file_path", "signature_file_path"}:
        raise ValueError(f"Invalid field: {field}")
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(f"UPDATE company_profile SET {field}=NULL")
    conn.commit()
    conn.close()


# ── Company Documents ─────────────────────────────────────────────────────────

def list_company_documents():
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        # Exclude file_data (binary) — it's served via the /file endpoint
        cur.execute("""
            SELECT id, document_name, category, financial_year, brand_oem,
                   file_path, tags, uploaded_at, content_type, original_name, file_size,
                   (file_data IS NOT NULL) AS has_file
            FROM company_documents ORDER BY uploaded_at DESC
        """)
        rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_company_document(data, file_bytes=None):
    conn = get_db()
    with conn.cursor() as cur:
        now = datetime.now().isoformat()
        fd = psycopg2.Binary(file_bytes) if file_bytes else None
        file_size = len(file_bytes) if file_bytes else None
        cur.execute(
            """INSERT INTO company_documents
               (document_name, category, financial_year, brand_oem, file_path, tags, uploaded_at,
                file_data, content_type, original_name, file_size)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               RETURNING id""",
            (data.get("document_name"), data.get("category"), data.get("financial_year"),
             data.get("brand_oem"), data.get("file_path"), data.get("tags"), now,
             fd, data.get("content_type"), data.get("original_name"), file_size),
        )
        doc_id = cur.fetchone()[0]
    conn.commit()
    conn.close()
    return doc_id


def get_company_document(doc_id):
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM company_documents WHERE id=%s", (doc_id,))
        row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def delete_company_document(doc_id):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM company_documents WHERE id=%s", (doc_id,))
    conn.commit()
    conn.close()


# ── Tender Prepared Documents ─────────────────────────────────────────────────

def save_prepared_document(data):
    conn = get_db()
    with conn.cursor() as cur:
        now = datetime.now().isoformat()
        cur.execute(
            """INSERT INTO tender_prepared_documents
               (tender_id, required_document_label, document_name, source_type,
                source_document_id, generated_file_path, status, remarks, recommended_action, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               RETURNING id""",
            (data.get("tender_id"), data.get("required_document_label"),
             data.get("document_name"), data.get("source_type"),
             data.get("source_document_id"), data.get("generated_file_path"),
             data.get("status"), data.get("remarks"), data.get("recommended_action"), now),
        )
        doc_id = cur.fetchone()[0]
    conn.commit()
    conn.close()
    return doc_id


def list_prepared_documents(tender_id):
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM tender_prepared_documents WHERE tender_id=%s ORDER BY id",
            (tender_id,),
        )
        rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_prepared_document(doc_id):
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM tender_prepared_documents WHERE id=%s", (doc_id,))
        row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def update_prepared_document(doc_id, data):
    conn = get_db()
    with conn.cursor() as cur:
        fields = {k: v for k, v in data.items() if k not in ("id", "tender_id", "created_at")}
        if not fields:
            conn.close()
            return
        set_clause = ", ".join(f"{k}=%s" for k in fields)
        cur.execute(
            f"UPDATE tender_prepared_documents SET {set_clause} WHERE id=%s",
            list(fields.values()) + [doc_id],
        )
    conn.commit()
    conn.close()


def clear_prepared_documents(tender_id):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM tender_prepared_documents WHERE tender_id=%s", (tender_id,))
    conn.commit()
    conn.close()


def save_prepared_document_file(doc_id, file_data, file_name):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE tender_prepared_documents SET generated_file_data=%s, generated_file_name=%s WHERE id=%s",
            (psycopg2.Binary(file_data), file_name, doc_id),
        )
    conn.commit()
    conn.close()


def get_prepared_document_file(doc_id):
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT generated_file_data, generated_file_name FROM tender_prepared_documents WHERE id=%s",
            (doc_id,),
        )
        row = cur.fetchone()
    conn.close()
    if not row or not row["generated_file_data"]:
        return None
    return dict(row)


# ── Stamp / Signature binary storage ─────────────────────────────────────────

def _ensure_profile_row(cur):
    cur.execute("SELECT id FROM company_profile LIMIT 1")
    return cur.fetchone()


def save_stamp(data_bytes, content_type, original_name):
    conn = get_db()
    with conn.cursor() as cur:
        row = _ensure_profile_row(cur)
        if row:
            cur.execute(
                "UPDATE company_profile SET stamp_data=%s, stamp_content_type=%s, stamp_original_name=%s",
                (psycopg2.Binary(data_bytes), content_type, original_name),
            )
        else:
            cur.execute(
                "INSERT INTO company_profile (stamp_data, stamp_content_type, stamp_original_name) VALUES (%s,%s,%s)",
                (psycopg2.Binary(data_bytes), content_type, original_name),
            )
    conn.commit()
    conn.close()


def get_stamp():
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT stamp_data, stamp_content_type, stamp_original_name FROM company_profile LIMIT 1")
        row = cur.fetchone()
    conn.close()
    if not row or not row["stamp_data"]:
        return None
    return dict(row)


def clear_stamp():
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("UPDATE company_profile SET stamp_data=NULL, stamp_content_type=NULL, stamp_original_name=NULL")
    conn.commit()
    conn.close()


def save_signature(data_bytes, content_type, original_name):
    conn = get_db()
    with conn.cursor() as cur:
        row = _ensure_profile_row(cur)
        if row:
            cur.execute(
                "UPDATE company_profile SET signature_data=%s, signature_content_type=%s, signature_original_name=%s",
                (psycopg2.Binary(data_bytes), content_type, original_name),
            )
        else:
            cur.execute(
                "INSERT INTO company_profile (signature_data, signature_content_type, signature_original_name) VALUES (%s,%s,%s)",
                (psycopg2.Binary(data_bytes), content_type, original_name),
            )
    conn.commit()
    conn.close()


def get_signature():
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT signature_data, signature_content_type, signature_original_name FROM company_profile LIMIT 1")
        row = cur.fetchone()
    conn.close()
    if not row or not row["signature_data"]:
        return None
    return dict(row)


def clear_signature():
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("UPDATE company_profile SET signature_data=NULL, signature_content_type=NULL, signature_original_name=NULL")
    conn.commit()
    conn.close()


# ── Company Document binary storage ──────────────────────────────────────────

def get_company_document_file(doc_id):
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT file_data, content_type, original_name, file_path FROM company_documents WHERE id=%s",
                (doc_id,),
            )
            row = cur.fetchone()

        if not row:
            return None

        if row["file_data"]:
            return {
                "file_data": bytes(row["file_data"]),
                "content_type": row["content_type"] or "application/octet-stream",
                "original_name": row["original_name"] or "document",
            }

        # Migration: try to read bytes from old on-disk file_path
        file_path = row.get("file_path") or ""
        if file_path and os.path.exists(file_path):
            with open(file_path, "rb") as fh:
                file_bytes = fh.read()
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE company_documents SET file_data=%s, file_size=%s WHERE id=%s",
                    (psycopg2.Binary(file_bytes), len(file_bytes), doc_id),
                )
            conn.commit()
            print(f"[DB] Migrated company document {doc_id} bytes from disk into PostgreSQL")
            return {
                "file_data": file_bytes,
                "content_type": row["content_type"] or "application/octet-stream",
                "original_name": row["original_name"] or os.path.basename(file_path),
            }

        # Neither file_data nor a readable file_path — record exists but file is gone
        return {"missing": True}
    finally:
        conn.close()
