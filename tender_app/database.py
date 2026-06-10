import sqlite3
import os
import psycopg2
import psycopg2.extras
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "tenders.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ── PostgreSQL (uploaded_files only) ─────────────────────────────────────────

def get_pg_conn():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    return psycopg2.connect(url)


def init_uploaded_files_table():
    url = os.environ.get("DATABASE_URL")
    if not url:
        return  # no PG configured, skip silently
    try:
        conn = get_pg_conn()
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
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[PG] init_uploaded_files_table failed: {e}")


def save_uploaded_file(file_id, file_name, original_name, content_type, file_size, file_data, file_category="tender_pdf"):
    conn = get_pg_conn()
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
    conn = get_pg_conn()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT id, file_name, original_name, content_type, file_size, file_data FROM uploaded_files WHERE id=%s",
            (file_id,),
        )
        row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return dict(row)


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tenders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
        );

        CREATE TABLE IF NOT EXISTS tender_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tender_id INTEGER,
            part_number TEXT,
            item_description TEXT,
            quantity TEXT,
            FOREIGN KEY (tender_id) REFERENCES tenders(id)
        );

        CREATE TABLE IF NOT EXISTS tender_required_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tender_id INTEGER,
            label TEXT,
            FOREIGN KEY (tender_id) REFERENCES tenders(id)
        );

        CREATE TABLE IF NOT EXISTS company_profile (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
        );

        CREATE TABLE IF NOT EXISTS company_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_name TEXT,
            category TEXT,
            financial_year TEXT,
            brand_oem TEXT,
            file_path TEXT,
            tags TEXT,
            uploaded_at TEXT
        );

        CREATE TABLE IF NOT EXISTS tender_prepared_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tender_id INTEGER,
            required_document_label TEXT,
            document_name TEXT,
            source_type TEXT,
            source_document_id INTEGER,
            generated_file_path TEXT,
            status TEXT,
            remarks TEXT,
            recommended_action TEXT,
            created_at TEXT,
            FOREIGN KEY (tender_id) REFERENCES tenders(id)
        );
    """)
    conn.commit()

    # Migrate existing databases — add columns that may be missing
    migrations = [
        ("tenders",                    "gem_bidding_number",   "TEXT"),
        ("tenders",                    "participation_status", "TEXT DEFAULT NULL"),
        ("tenders",                    "make",                 "TEXT"),
        ("tenders",                    "tender_approx_value",  "TEXT"),
        ("tenders",                    "won_text",             "TEXT"),
        ("tenders",                    "lost_text",            "TEXT"),
        ("tenders",                    "participant_text",     "TEXT"),
        ("tender_prepared_documents",  "recommended_action",  "TEXT"),
    ]
    for table, col, definition in migrations:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
            conn.commit()
        except Exception:
            pass  # Column already exists

    conn.close()
    init_uploaded_files_table()


# ── Tenders ───────────────────────────────────────────────────────────────────

def save_tender(data, items, documents):
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute(
        """INSERT INTO tenders (
            gem_bidding_number, tender_number, date, bid_end_datetime, bid_opening_datetime,
            department_name, organization_name, office_name_location,
            total_quantity, make, tender_approx_value,
            won_text, lost_text, participant_text,
            uploaded_at, pdf_path, extraction_json_path, status, participation_status
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            data.get("gem_bidding_number"),
            data.get("tender_number"), data.get("date"),
            data.get("bid_end_datetime"), data.get("bid_opening_datetime"),
            data.get("department_name"), data.get("organization_name"),
            data.get("office_name_location"),
            data.get("total_quantity"), data.get("make"), data.get("tender_approx_value"),
            data.get("won_text"), data.get("lost_text"), data.get("participant_text"),
            now, data.get("pdf_path"), data.get("extraction_json_path"),
            "saved", None,
        ),
    )
    tender_id = c.lastrowid
    _insert_items(c, tender_id, items)
    _insert_docs(c, tender_id, documents)
    conn.commit()
    conn.close()
    return tender_id


def update_tender(tender_id, data, items, documents):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        """UPDATE tenders SET
            gem_bidding_number=?, tender_number=?, date=?, bid_end_datetime=?, bid_opening_datetime=?,
            department_name=?, organization_name=?, office_name_location=?,
            total_quantity=?, make=?, tender_approx_value=?,
            won_text=COALESCE(?,won_text), lost_text=COALESCE(?,lost_text), participant_text=COALESCE(?,participant_text),
            status=?, participation_status=COALESCE(?, participation_status)
        WHERE id=?""",
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
    c.execute("DELETE FROM tender_items WHERE tender_id=?", (tender_id,))
    c.execute("DELETE FROM tender_required_documents WHERE tender_id=?", (tender_id,))
    _insert_items(c, tender_id, items)
    _insert_docs(c, tender_id, documents)
    conn.commit()
    conn.close()


def get_tender(tender_id):
    conn = get_db()
    c = conn.cursor()
    row = c.execute("SELECT * FROM tenders WHERE id=?", (tender_id,)).fetchone()
    if not row:
        conn.close()
        return None
    tender = dict(row)
    tender["boq_items"] = [
        dict(r) for r in
        c.execute("SELECT * FROM tender_items WHERE tender_id=?", (tender_id,)).fetchall()
    ]
    tender["required_documents"] = [
        dict(r) for r in
        c.execute("SELECT * FROM tender_required_documents WHERE tender_id=?", (tender_id,)).fetchall()
    ]
    conn.close()
    return tender


def list_tenders():
    conn = get_db()
    rows = conn.execute(
        """SELECT id, gem_bidding_number, tender_number, organization_name, bid_end_datetime,
                  make, total_quantity, participation_status, uploaded_at,
                  won_text, lost_text, participant_text, pdf_path
           FROM tenders ORDER BY uploaded_at DESC"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_tender_record_fields(tender_id, won_text, lost_text, participant_text):
    conn = get_db()
    conn.execute(
        "UPDATE tenders SET won_text=?, lost_text=?, participant_text=? WHERE id=?",
        (won_text, lost_text, participant_text, tender_id),
    )
    conn.commit()
    conn.close()


def update_tender_participation_status(tender_id, status):
    conn = get_db()
    conn.execute(
        "UPDATE tenders SET participation_status=? WHERE id=?", (status, tender_id)
    )
    conn.commit()
    conn.close()


def find_tender_duplicate(gem_bidding_number, tender_number):
    """Return existing tender if gem_bidding_number AND tender_number both match."""
    if not gem_bidding_number and not tender_number:
        return None
    conn = get_db()
    row = conn.execute(
        """SELECT id FROM tenders
           WHERE gem_bidding_number=? AND tender_number=?
           LIMIT 1""",
        (gem_bidding_number, tender_number),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_tender(tender_id):
    conn = get_db()
    conn.executescript(f"""
        DELETE FROM tender_prepared_documents WHERE tender_id={tender_id};
        DELETE FROM tender_required_documents  WHERE tender_id={tender_id};
        DELETE FROM tender_items               WHERE tender_id={tender_id};
        DELETE FROM tenders                    WHERE id={tender_id};
    """)
    conn.commit()
    conn.close()


def _insert_items(c, tender_id, items):
    for item in items:
        c.execute(
            """INSERT INTO tender_items (tender_id, part_number, item_description, quantity)
               VALUES (?,?,?,?)""",
            (tender_id, item.get("part_number"), item.get("item_description"), item.get("quantity")),
        )


def _insert_docs(c, tender_id, documents):
    for doc in documents:
        label = doc.get("label") if isinstance(doc, dict) else doc
        c.execute(
            "INSERT INTO tender_required_documents (tender_id, label) VALUES (?,?)",
            (tender_id, label),
        )


# ── Company Profile ───────────────────────────────────────────────────────────

def get_company_profile():
    conn = get_db()
    row = conn.execute("SELECT * FROM company_profile LIMIT 1").fetchone()
    conn.close()
    return dict(row) if row else {}


def upsert_company_profile(data):
    conn = get_db()
    c = conn.cursor()
    existing = c.execute("SELECT id FROM company_profile LIMIT 1").fetchone()
    text_fields = ["company_name", "address", "gst_number", "pan_number", "msme_number",
                   "bank_name", "account_number", "ifsc_code",
                   "authorized_signatory_name", "authorized_signatory_designation",
                   "email", "phone"]
    image_fields = ["stamp_file_path", "signature_file_path"]
    if existing:
        # Text fields overwrite; image fields use COALESCE so a text-only save never clears them.
        set_parts = [f"{f}=?" for f in text_fields] + \
                    [f"{f}=COALESCE(?,{f})" for f in image_fields]
        values = [data.get(f) for f in text_fields] + [data.get(f) for f in image_fields]
        c.execute(f"UPDATE company_profile SET {', '.join(set_parts)} WHERE id=?",
                  values + [existing["id"]])
    else:
        all_fields = text_fields + image_fields
        values = [data.get(f) for f in all_fields]
        placeholders = ", ".join("?" for _ in all_fields)
        c.execute(
            f"INSERT INTO company_profile ({', '.join(all_fields)}) VALUES ({placeholders})", values
        )
    conn.commit()
    conn.close()


def clear_profile_image_path(field):
    """Explicitly NULL out a profile image column (stamp or signature removal)."""
    if field not in {"stamp_file_path", "signature_file_path"}:
        raise ValueError(f"Invalid field: {field}")
    conn = get_db()
    conn.execute(f"UPDATE company_profile SET {field}=NULL")
    conn.commit()
    conn.close()


# ── Company Documents ─────────────────────────────────────────────────────────

def list_company_documents():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM company_documents ORDER BY uploaded_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_company_document(data):
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute(
        """INSERT INTO company_documents
           (document_name, category, financial_year, brand_oem, file_path, tags, uploaded_at)
           VALUES (?,?,?,?,?,?,?)""",
        (data.get("document_name"), data.get("category"), data.get("financial_year"),
         data.get("brand_oem"), data.get("file_path"), data.get("tags"), now),
    )
    doc_id = c.lastrowid
    conn.commit()
    conn.close()
    return doc_id


def get_company_document(doc_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM company_documents WHERE id=?", (doc_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_company_document(doc_id):
    conn = get_db()
    conn.execute("DELETE FROM company_documents WHERE id=?", (doc_id,))
    conn.commit()
    conn.close()


# ── Tender Prepared Documents ─────────────────────────────────────────────────

def save_prepared_document(data):
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute(
        """INSERT INTO tender_prepared_documents
           (tender_id, required_document_label, document_name, source_type,
            source_document_id, generated_file_path, status, remarks, recommended_action, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (data.get("tender_id"), data.get("required_document_label"),
         data.get("document_name"), data.get("source_type"),
         data.get("source_document_id"), data.get("generated_file_path"),
         data.get("status"), data.get("remarks"), data.get("recommended_action"), now),
    )
    doc_id = c.lastrowid
    conn.commit()
    conn.close()
    return doc_id


def list_prepared_documents(tender_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM tender_prepared_documents WHERE tender_id=? ORDER BY id",
        (tender_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_prepared_document(doc_id):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM tender_prepared_documents WHERE id=?", (doc_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def update_prepared_document(doc_id, data):
    conn = get_db()
    c = conn.cursor()
    fields = {k: v for k, v in data.items() if k not in ("id", "tender_id", "created_at")}
    if not fields:
        conn.close()
        return
    set_clause = ", ".join(f"{k}=?" for k in fields)
    c.execute(
        f"UPDATE tender_prepared_documents SET {set_clause} WHERE id=?",
        list(fields.values()) + [doc_id]
    )
    conn.commit()
    conn.close()


def clear_prepared_documents(tender_id):
    conn = get_db()
    conn.execute("DELETE FROM tender_prepared_documents WHERE tender_id=?", (tender_id,))
    conn.commit()
    conn.close()
