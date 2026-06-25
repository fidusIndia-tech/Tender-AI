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
                quantity TEXT,
                source_type TEXT DEFAULT 'extracted'
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
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tender_attachments (
                id SERIAL PRIMARY KEY,
                tender_id INTEGER REFERENCES tenders(id) ON DELETE CASCADE,
                original_file_name TEXT,
                content_type TEXT,
                file_size INTEGER,
                file_data BYTEA,
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        cur.execute("ALTER TABLE tenders ADD COLUMN IF NOT EXISTS remark TEXT")
        cur.execute("ALTER TABLE tender_items ADD COLUMN IF NOT EXISTS source_type TEXT DEFAULT 'extracted'")
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
        cur.execute("""
            CREATE TABLE IF NOT EXISTS company_capability_profile (
                id SERIAL PRIMARY KEY,
                year_established INTEGER,
                core_business TEXT,
                product_categories TEXT,
                brands_handled TEXT,
                industries_served TEXT,
                turnover_range TEXT,
                typical_tender_value_range TEXT,
                import_capability BOOLEAN DEFAULT FALSE,
                export_capability BOOLEAN DEFAULT FALSE,
                oem_support_available BOOLEAN DEFAULT FALSE,
                oem_authorizations TEXT,
                engineering_support BOOLEAN DEFAULT FALSE,
                installation_support BOOLEAN DEFAULT FALSE,
                gst_available BOOLEAN DEFAULT FALSE,
                pan_available BOOLEAN DEFAULT FALSE,
                msme_available BOOLEAN DEFAULT FALSE,
                itr_available BOOLEAN DEFAULT FALSE,
                bank_documents_available BOOLEAN DEFAULT FALSE,
                letterhead_available BOOLEAN DEFAULT FALSE,
                stamp_available BOOLEAN DEFAULT FALSE,
                signature_available BOOLEAN DEFAULT FALSE,
                psu_experience BOOLEAN DEFAULT FALSE,
                government_experience BOOLEAN DEFAULT FALSE,
                major_customers TEXT,
                past_orders_projects TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS gem_keywords (
                id SERIAL PRIMARY KEY,
                keyword TEXT NOT NULL UNIQUE,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_checked_at TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS gem_scan_runs (
                id SERIAL PRIMARY KEY,
                scan_target_date DATE NOT NULL,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                finished_at TIMESTAMP,
                status TEXT DEFAULT 'RUNNING',
                total_keywords INTEGER DEFAULT 0,
                total_found INTEGER DEFAULT 0,
                new_found INTEGER DEFAULT 0,
                skipped_wrong_start_date INTEGER DEFAULT 0,
                approved_count INTEGER DEFAULT 0,
                rejected_count INTEGER DEFAULT 0,
                error_message TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS gem_candidate_tenders (
                id SERIAL PRIMARY KEY,
                gem_bid_no TEXT NOT NULL UNIQUE,
                matched_keywords TEXT[] DEFAULT '{}',
                title TEXT,
                organisation TEXT,
                department TEXT,
                quantity TEXT,
                bid_start_date DATE,
                bid_end_date DATE,
                gem_detail_url TEXT,
                pdf_url TEXT,
                pdf_file_id TEXT,
                tender_id INTEGER REFERENCES tenders(id),
                evaluation_score INTEGER,
                evaluation_reason TEXT,
                evaluation_json JSONB,
                status TEXT DEFAULT 'FOUND',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tender_evaluations (
                id SERIAL PRIMARY KEY,
                candidate_id INTEGER REFERENCES gem_candidate_tenders(id) ON DELETE CASCADE,
                score INTEGER,
                rating_label TEXT,
                matched_brands TEXT,
                eligibility_status TEXT,
                rejection_reason TEXT,
                evaluation_json JSONB,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("SELECT COUNT(*) FROM gem_keywords")
        if cur.fetchone()[0] == 0:
            default_keywords = [
                "IFM", "PILZ", "Siemens", "SICK", "Omron", "Baumer",
                "Turck", "Balluff", "Pepperl+Fuchs", "ABB", "Schneider", "Keyence",
            ]
            for kw in default_keywords:
                cur.execute(
                    "INSERT INTO gem_keywords (keyword) VALUES (%s) ON CONFLICT (keyword) DO NOTHING",
                    (kw,),
                )
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
        _insert_items(cur, tender_id, items, source_type="extracted")
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
        _insert_items(cur, tender_id, items, source_type="extracted")
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
            """SELECT t.id, t.gem_bidding_number, t.tender_number, t.date, t.bid_end_datetime,
                      t.bid_opening_datetime, t.department_name, t.organization_name,
                      t.office_name_location, t.make, t.total_quantity, t.tender_approx_value,
                      t.participation_status, t.uploaded_at, t.won_text, t.lost_text,
                      t.participant_text, t.pdf_path, t.extraction_json_path, t.status,
                      t.filed_date, t.remark,
                      COALESCE(a.attachment_count, 0) AS attachment_count,
                      COALESCE(i.item_search_text, '') AS item_search_text,
                      COALESCE(d.required_document_search_text, '') AS required_document_search_text
               FROM tenders t
               LEFT JOIN (
                   SELECT tender_id, COUNT(*) AS attachment_count
                   FROM tender_attachments
                   GROUP BY tender_id
               ) a ON a.tender_id = t.id
               LEFT JOIN (
                   SELECT tender_id,
                          string_agg(COALESCE(part_number, '') || ' ' || COALESCE(item_description, '') || ' ' || COALESCE(quantity, ''), ' ') AS item_search_text
                   FROM tender_items
                   GROUP BY tender_id
               ) i ON i.tender_id = t.id
               LEFT JOIN (
                   SELECT tender_id,
                          string_agg(COALESCE(label, ''), ' ') AS required_document_search_text
                   FROM tender_required_documents
                   GROUP BY tender_id
               ) d ON d.tender_id = t.id
               ORDER BY t.uploaded_at DESC"""
        )
        rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# â”€â”€ Tender Attachments â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def save_tender_attachments(tender_id, files):
    conn = get_db()
    saved = []
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        for f in files:
            cur.execute(
                """INSERT INTO tender_attachments
                   (tender_id, original_file_name, content_type, file_size, file_data)
                   VALUES (%s, %s, %s, %s, %s)
                   RETURNING id, tender_id, original_file_name, content_type, file_size, uploaded_at""",
                (
                    tender_id,
                    f["original_file_name"],
                    f.get("content_type"),
                    f["file_size"],
                    psycopg2.Binary(f["file_data"]),
                ),
            )
            saved.append(dict(cur.fetchone()))
    conn.commit()
    conn.close()
    return saved


def list_tender_attachments(tender_id):
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT id, tender_id, original_file_name, content_type, file_size, uploaded_at
               FROM tender_attachments
               WHERE tender_id=%s
               ORDER BY uploaded_at DESC, id DESC""",
            (tender_id,),
        )
        rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_tender_attachment(attachment_id):
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT id, tender_id, original_file_name, content_type, file_size, file_data, uploaded_at
               FROM tender_attachments
               WHERE id=%s""",
            (attachment_id,),
        )
        row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def update_tender_record_fields(tender_id, won_text, lost_text, participant_text, remark=None):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE tenders SET won_text=%s, lost_text=%s, participant_text=%s, remark=%s WHERE id=%s",
            (won_text, lost_text, participant_text, remark, tender_id),
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
        cur.execute("DELETE FROM tender_attachments WHERE tender_id=%s", (tender_id,))
        cur.execute("DELETE FROM tender_prepared_documents WHERE tender_id=%s", (tender_id,))
        cur.execute("DELETE FROM tender_required_documents WHERE tender_id=%s", (tender_id,))
        cur.execute("DELETE FROM tender_items WHERE tender_id=%s", (tender_id,))
        cur.execute("DELETE FROM tenders WHERE id=%s", (tender_id,))
    conn.commit()
    conn.close()


def clear_tenders():
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM tender_attachments")
        cur.execute("DELETE FROM tender_prepared_documents")
        cur.execute("DELETE FROM tender_required_documents")
        cur.execute("DELETE FROM tender_items")
        cur.execute("DELETE FROM tenders")
    conn.commit()
    conn.close()


def _insert_items(cur, tender_id, items, source_type="extracted"):
    for item in items:
        cur.execute(
            """INSERT INTO tender_items (tender_id, part_number, item_description, quantity, source_type)
               VALUES (%s,%s,%s,%s,%s)""",
            (tender_id, item.get("part_number"), item.get("item_description"), item.get("quantity"), source_type),
        )


def update_tender_boq_items(tender_id, items):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM tender_items WHERE tender_id=%s", (tender_id,))
        _insert_items(cur, tender_id, items, source_type="manual")
    conn.commit()
    conn.close()


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

def _capability_years_experience(year_established):
    try:
        year = int(year_established)
        current_year = datetime.now().year
        return max(0, current_year - year) if 1800 <= year <= current_year else None
    except (TypeError, ValueError):
        return None


CAPABILITY_FIELDS = [
    "year_established",
    "core_business",
    "product_categories",
    "brands_handled",
    "industries_served",
    "turnover_range",
    "typical_tender_value_range",
    "import_capability",
    "export_capability",
    "oem_support_available",
    "oem_authorizations",
    "engineering_support",
    "installation_support",
    "gst_available",
    "pan_available",
    "msme_available",
    "itr_available",
    "bank_documents_available",
    "letterhead_available",
    "stamp_available",
    "signature_available",
    "psu_experience",
    "government_experience",
    "major_customers",
    "past_orders_projects",
]


def get_company_capability_profile():
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            f"SELECT id, {', '.join(CAPABILITY_FIELDS)}, updated_at FROM company_capability_profile LIMIT 1"
        )
        row = cur.fetchone()
    conn.close()
    data = dict(row) if row else {}
    if data.get("updated_at"):
        data["updated_at"] = data["updated_at"].isoformat()
    data["years_experience"] = _capability_years_experience(data.get("year_established"))
    return data


def upsert_company_capability_profile(data):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM company_capability_profile LIMIT 1")
        existing = cur.fetchone()
        values = [data.get(f) for f in CAPABILITY_FIELDS]
        if existing:
            set_parts = [f"{f}=%s" for f in CAPABILITY_FIELDS]
            cur.execute(
                f"UPDATE company_capability_profile SET {', '.join(set_parts)}, updated_at=CURRENT_TIMESTAMP WHERE id=%s",
                values + [existing[0]],
            )
        else:
            placeholders = ", ".join("%s" for _ in CAPABILITY_FIELDS)
            cur.execute(
                f"INSERT INTO company_capability_profile ({', '.join(CAPABILITY_FIELDS)}) VALUES ({placeholders})",
                values,
            )
    conn.commit()
    conn.close()


def get_company_profile_for_tender_evaluation():
    profile = get_company_profile()
    profile["capability_profile"] = get_company_capability_profile()
    return profile


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


# ── GeM Tender Watcher: Keywords ────────────────────────────────────────────────

def list_gem_keywords(active_only: bool = False):
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        query = "SELECT * FROM gem_keywords"
        if active_only:
            query += " WHERE is_active = TRUE"
        query += " ORDER BY keyword"
        cur.execute(query)
        rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_gem_keyword(keyword: str) -> int:
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO gem_keywords (keyword) VALUES (%s) RETURNING id",
            (keyword,),
        )
        keyword_id = cur.fetchone()[0]
    conn.commit()
    conn.close()
    return keyword_id


def update_gem_keyword(keyword_id: int, keyword=None, is_active=None):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT keyword, is_active FROM gem_keywords WHERE id=%s", (keyword_id,))
        existing = cur.fetchone()
        if not existing:
            conn.close()
            return None
        new_keyword = keyword if keyword is not None else existing[0]
        new_is_active = is_active if is_active is not None else existing[1]
        cur.execute(
            "UPDATE gem_keywords SET keyword=%s, is_active=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s",
            (new_keyword, new_is_active, keyword_id),
        )
    conn.commit()
    conn.close()
    return True


def delete_gem_keyword(keyword_id: int):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM gem_keywords WHERE id=%s", (keyword_id,))
    conn.commit()
    conn.close()


def touch_gem_keyword_checked(keyword_id: int):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("UPDATE gem_keywords SET last_checked_at=CURRENT_TIMESTAMP WHERE id=%s", (keyword_id,))
    conn.commit()
    conn.close()


# ── GeM Tender Watcher: Scan Runs ───────────────────────────────────────────────

def fail_stale_running_scans():
    """Mark any lingering RUNNING scan runs as FAILED. Called on app startup —
    a freshly started process has no in-flight scans, so any row still marked
    RUNNING is an orphan from a previous process that died mid-scan (e.g. a
    server restart). Without this, that stale row blocks all future scans."""
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE gem_scan_runs
               SET status='FAILED', finished_at=CURRENT_TIMESTAMP,
                   error_message=COALESCE(error_message,'') || ' [auto-failed: server restarted while running]'
               WHERE status='RUNNING'"""
        )
        n = cur.rowcount
    conn.commit()
    conn.close()
    if n:
        print(f"[DB] Cleared {n} stale RUNNING scan run(s) on startup")
    return n


def create_gem_scan_run(scan_target_date, total_keywords: int) -> int:
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO gem_scan_runs (scan_target_date, status, total_keywords)
               VALUES (%s, 'RUNNING', %s) RETURNING id""",
            (scan_target_date, total_keywords),
        )
        run_id = cur.fetchone()[0]
    conn.commit()
    conn.close()
    return run_id


def update_gem_scan_run(run_id: int, **fields):
    if not fields:
        return
    conn = get_db()
    with conn.cursor() as cur:
        set_parts = [f"{k}=%s" for k in fields]
        cur.execute(
            f"UPDATE gem_scan_runs SET {', '.join(set_parts)} WHERE id=%s",
            list(fields.values()) + [run_id],
        )
    conn.commit()
    conn.close()


def get_gem_scan_run(run_id: int):
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM gem_scan_runs WHERE id=%s", (run_id,))
        row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def list_gem_scan_runs(limit: int = 50):
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM gem_scan_runs ORDER BY started_at DESC LIMIT %s", (limit,))
        rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def is_gem_scan_running() -> bool:
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM gem_scan_runs WHERE status='RUNNING' LIMIT 1")
        row = cur.fetchone()
    conn.close()
    return row is not None


# ── GeM Tender Watcher: Candidate Tenders ───────────────────────────────────────

def upsert_gem_candidate(gem_bid_no: str, keyword: str, data: dict) -> int:
    """Insert a new candidate, or — if gem_bid_no already exists — merge the
    searched keyword into matched_keywords. For stale/unprocessed rows (no PDF
    saved yet), also refresh the latest GeM metadata/URLs so a re-scan can
    recover from earlier partial failures without disturbing already-processed
    tenders."""
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO gem_candidate_tenders (
                   gem_bid_no, matched_keywords, title, organisation, department,
                   quantity, bid_start_date, bid_end_date, gem_detail_url, pdf_url, status
               ) VALUES (%s, ARRAY[%s], %s, %s, %s, %s, %s, %s, %s, %s, 'QUEUED')
               ON CONFLICT (gem_bid_no) DO UPDATE SET
                   matched_keywords = (
                       SELECT ARRAY(SELECT DISTINCT unnest(gem_candidate_tenders.matched_keywords || EXCLUDED.matched_keywords))
                   ),
                   title = CASE
                       WHEN gem_candidate_tenders.pdf_file_id IS NULL THEN COALESCE(EXCLUDED.title, gem_candidate_tenders.title)
                       ELSE gem_candidate_tenders.title
                   END,
                   organisation = CASE
                       WHEN gem_candidate_tenders.pdf_file_id IS NULL THEN COALESCE(EXCLUDED.organisation, gem_candidate_tenders.organisation)
                       ELSE gem_candidate_tenders.organisation
                   END,
                   department = CASE
                       WHEN gem_candidate_tenders.pdf_file_id IS NULL THEN COALESCE(EXCLUDED.department, gem_candidate_tenders.department)
                       ELSE gem_candidate_tenders.department
                   END,
                   quantity = CASE
                       WHEN gem_candidate_tenders.pdf_file_id IS NULL THEN COALESCE(EXCLUDED.quantity, gem_candidate_tenders.quantity)
                       ELSE gem_candidate_tenders.quantity
                   END,
                   bid_start_date = CASE
                       WHEN gem_candidate_tenders.pdf_file_id IS NULL THEN COALESCE(EXCLUDED.bid_start_date, gem_candidate_tenders.bid_start_date)
                       ELSE gem_candidate_tenders.bid_start_date
                   END,
                   bid_end_date = CASE
                       WHEN gem_candidate_tenders.pdf_file_id IS NULL THEN COALESCE(EXCLUDED.bid_end_date, gem_candidate_tenders.bid_end_date)
                       ELSE gem_candidate_tenders.bid_end_date
                   END,
                   gem_detail_url = CASE
                       WHEN gem_candidate_tenders.pdf_file_id IS NULL THEN COALESCE(EXCLUDED.gem_detail_url, gem_candidate_tenders.gem_detail_url)
                       ELSE gem_candidate_tenders.gem_detail_url
                   END,
                   pdf_url = CASE
                       WHEN gem_candidate_tenders.pdf_file_id IS NULL THEN COALESCE(EXCLUDED.pdf_url, gem_candidate_tenders.pdf_url)
                       ELSE gem_candidate_tenders.pdf_url
                   END,
                   status = CASE
                       WHEN gem_candidate_tenders.pdf_file_id IS NULL
                            AND gem_candidate_tenders.status IN ('FOUND', 'ERROR', 'QUEUED')
                       THEN 'QUEUED'
                       ELSE gem_candidate_tenders.status
                   END,
                   evaluation_reason = CASE
                       WHEN gem_candidate_tenders.pdf_file_id IS NULL
                            AND gem_candidate_tenders.status IN ('FOUND', 'ERROR', 'QUEUED')
                       THEN NULL
                       ELSE gem_candidate_tenders.evaluation_reason
                   END,
                   updated_at = CURRENT_TIMESTAMP
               RETURNING id""",
            (
                gem_bid_no, keyword, data.get("title"), data.get("organisation"), data.get("department"),
                data.get("quantity"), data.get("bid_start_date"), data.get("bid_end_date"),
                data.get("gem_detail_url"), data.get("pdf_url"),
            ),
        )
        candidate_id = cur.fetchone()[0]
    conn.commit()
    conn.close()
    return candidate_id


def get_gem_candidate(candidate_id: int):
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM gem_candidate_tenders WHERE id=%s", (candidate_id,))
        row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def list_gem_candidates(status: str = None):
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if status:
            cur.execute(
                """SELECT * FROM gem_candidate_tenders
                   WHERE status=%s
                   ORDER BY bid_start_date DESC NULLS LAST, created_at DESC""",
                (status,),
            )
        else:
            cur.execute(
                """SELECT * FROM gem_candidate_tenders
                   ORDER BY bid_start_date DESC NULLS LAST, created_at DESC"""
            )
        rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_gem_candidates_found_without_pdf():
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM gem_candidate_tenders WHERE status='FOUND' AND pdf_file_id IS NULL")
        rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_gem_candidate(candidate_id: int, **fields):
    if not fields:
        return
    values = [psycopg2.extras.Json(v) if isinstance(v, dict) else v for v in fields.values()]
    conn = get_db()
    with conn.cursor() as cur:
        set_parts = [f"{k}=%s" for k in fields]
        cur.execute(
            f"UPDATE gem_candidate_tenders SET {', '.join(set_parts)}, updated_at=CURRENT_TIMESTAMP WHERE id=%s",
            values + [candidate_id],
        )
    conn.commit()
    conn.close()


# ── GeM Tender Watcher: Evaluations ─────────────────────────────────────────────

def save_gem_tender_evaluation(candidate_id: int, score, rating_label, matched_brands,
                                eligibility_status, rejection_reason, evaluation_json) -> int:
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO tender_evaluations (
                   candidate_id, score, rating_label, matched_brands,
                   eligibility_status, rejection_reason, evaluation_json
               ) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (
                candidate_id, score, rating_label, matched_brands,
                eligibility_status, rejection_reason, psycopg2.extras.Json(evaluation_json),
            ),
        )
        eval_id = cur.fetchone()[0]
    conn.commit()
    conn.close()
    return eval_id
