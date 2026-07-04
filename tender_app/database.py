import os
import psycopg2
import psycopg2.extras
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

_UNSET = object()


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
                expand_sections_json JSONB,
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
        cur.execute("ALTER TABLE tenders ADD COLUMN IF NOT EXISTS ac_manager TEXT")
        cur.execute("ALTER TABLE tenders ADD COLUMN IF NOT EXISTS remark TEXT")
        cur.execute("ALTER TABLE tenders ADD COLUMN IF NOT EXISTS expand_sections_json JSONB")
        cur.execute("ALTER TABLE tenders ADD COLUMN IF NOT EXISTS result_available BOOLEAN DEFAULT FALSE")
        cur.execute("ALTER TABLE tenders ADD COLUMN IF NOT EXISTS bid_result_available BOOLEAN DEFAULT FALSE")
        cur.execute("ALTER TABLE tenders ADD COLUMN IF NOT EXISTS ra_created BOOLEAN DEFAULT FALSE")
        cur.execute("ALTER TABLE tenders ADD COLUMN IF NOT EXISTS ra_result_available BOOLEAN DEFAULT FALSE")
        cur.execute("ALTER TABLE tenders ADD COLUMN IF NOT EXISTS gem_result_status TEXT DEFAULT 'PENDING'")
        cur.execute("ALTER TABLE tenders ADD COLUMN IF NOT EXISTS gem_bid_number TEXT")
        cur.execute("ALTER TABLE tenders ADD COLUMN IF NOT EXISTS gem_internal_id TEXT")
        cur.execute("ALTER TABLE tenders ADD COLUMN IF NOT EXISTS result_declared BOOLEAN DEFAULT FALSE")
        cur.execute("ALTER TABLE tenders ADD COLUMN IF NOT EXISTS result_declared_at TIMESTAMP")
        cur.execute("ALTER TABLE tenders ADD COLUMN IF NOT EXISTS gem_result_url TEXT")
        cur.execute("ALTER TABLE tenders ADD COLUMN IF NOT EXISTS gem_ra_number TEXT")
        cur.execute("ALTER TABLE tenders ADD COLUMN IF NOT EXISTS gem_ra_url TEXT")
        cur.execute("ALTER TABLE tenders ADD COLUMN IF NOT EXISTS gem_ra_result_url TEXT")
        cur.execute("ALTER TABLE tenders ADD COLUMN IF NOT EXISTS ra_start_date TEXT")
        cur.execute("ALTER TABLE tenders ADD COLUMN IF NOT EXISTS ra_end_date TEXT")
        cur.execute("ALTER TABLE tenders ADD COLUMN IF NOT EXISTS gem_page_status TEXT")
        cur.execute("ALTER TABLE tenders ADD COLUMN IF NOT EXISTS last_result_checked_at TIMESTAMP")
        cur.execute("ALTER TABLE tenders ADD COLUMN IF NOT EXISTS notification_sent BOOLEAN DEFAULT FALSE")
        cur.execute("ALTER TABLE tenders ADD COLUMN IF NOT EXISTS ra_notified BOOLEAN DEFAULT FALSE")
        cur.execute("ALTER TABLE tenders ADD COLUMN IF NOT EXISTS result_check_error TEXT")
        cur.execute("ALTER TABLE tenders ADD COLUMN IF NOT EXISTS result_review_required BOOLEAN DEFAULT FALSE")
        cur.execute("ALTER TABLE tenders ADD COLUMN IF NOT EXISTS result_check_warning TEXT")
        cur.execute("ALTER TABLE tenders ADD COLUMN IF NOT EXISTS l1_seller_name TEXT")
        cur.execute("ALTER TABLE tenders ADD COLUMN IF NOT EXISTS our_company_rank TEXT")
        cur.execute("ALTER TABLE tenders ADD COLUMN IF NOT EXISTS our_company_status TEXT")
        cur.execute("ALTER TABLE tender_items ADD COLUMN IF NOT EXISTS source_type TEXT DEFAULT 'extracted'")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tender_notifications (
                id SERIAL PRIMARY KEY,
                tender_id INTEGER REFERENCES tenders(id) ON DELETE CASCADE,
                title TEXT,
                message TEXT,
                type TEXT DEFAULT 'RESULT_AVAILABLE',
                notification_type TEXT,
                is_read BOOLEAN DEFAULT FALSE,
                is_valid BOOLEAN DEFAULT TRUE,
                invalidated_at TIMESTAMP,
                invalidation_reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        try:
            cur.execute("ALTER TABLE tender_notifications ALTER COLUMN type SET DEFAULT 'RESULT_AVAILABLE'")
        except Exception:
            pass
        cur.execute("ALTER TABLE tender_notifications ADD COLUMN IF NOT EXISTS notification_type TEXT")
        cur.execute("ALTER TABLE tender_notifications ADD COLUMN IF NOT EXISTS is_valid BOOLEAN DEFAULT TRUE")
        cur.execute("ALTER TABLE tender_notifications ADD COLUMN IF NOT EXISTS invalidated_at TIMESTAMP")
        cur.execute("ALTER TABLE tender_notifications ADD COLUMN IF NOT EXISTS invalidation_reason TEXT")
        cur.execute("UPDATE tender_notifications SET notification_type=COALESCE(notification_type, type) WHERE notification_type IS NULL")
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS tender_notifications_unique_result_idx
            ON tender_notifications (tender_id, type)
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS result_watcher_run_logs (
                id SERIAL PRIMARY KEY,
                started_at TIMESTAMP,
                finished_at TIMESTAMP,
                total_pending INTEGER DEFAULT 0,
                checked INTEGER DEFAULT 0,
                results_found INTEGER DEFAULT 0,
                not_available INTEGER DEFAULT 0,
                failed INTEGER DEFAULT 0,
                skipped INTEGER DEFAULT 0,
                run_source TEXT DEFAULT 'LOCAL_AGENT',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS gem_result_check_history (
                id SERIAL PRIMARY KEY,
                tender_id INTEGER REFERENCES tenders(id) ON DELETE CASCADE,
                gem_bid_number TEXT,
                old_status TEXT,
                new_status TEXT,
                old_result_available BOOLEAN,
                new_result_available BOOLEAN,
                old_bid_result_available BOOLEAN,
                new_bid_result_available BOOLEAN,
                old_ra_created BOOLEAN,
                new_ra_created BOOLEAN,
                old_ra_result_available BOOLEAN,
                new_ra_result_available BOOLEAN,
                old_urls JSONB,
                new_urls JSONB,
                reason TEXT,
                confidence TEXT,
                raw_gem_response JSONB,
                checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                source TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tender_result_summary (
                id SERIAL PRIMARY KEY,
                tender_id INTEGER NOT NULL REFERENCES tenders(id) ON DELETE CASCADE,
                gem_bid_number TEXT,
                current_source_type TEXT,
                current_bid_or_ra_number TEXT,
                bid_result_available BOOLEAN DEFAULT FALSE,
                bid_technical_available BOOLEAN DEFAULT FALSE,
                bid_financial_available BOOLEAN DEFAULT FALSE,
                ra_created BOOLEAN DEFAULT FALSE,
                ra_number TEXT,
                ra_start_date TEXT,
                ra_end_date TEXT,
                ra_result_available BOOLEAN DEFAULT FALSE,
                ra_technical_available BOOLEAN DEFAULT FALSE,
                ra_financial_available BOOLEAN DEFAULT FALSE,
                current_stage TEXT DEFAULT 'NOT_CHECKED',
                our_company_participated BOOLEAN DEFAULT FALSE,
                our_company_technical_status TEXT,
                our_company_financial_rank TEXT,
                our_company_final_price TEXT,
                last_checked_at TIMESTAMP,
                last_successful_parse_at TIMESTAMP,
                parse_error TEXT,
                result_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (tender_id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tender_result_participants (
                id SERIAL PRIMARY KEY,
                tender_id INTEGER NOT NULL REFERENCES tenders(id) ON DELETE CASCADE,
                source_type TEXT NOT NULL,
                source_number TEXT,
                seller_name TEXT NOT NULL,
                offered_item TEXT,
                make TEXT,
                model TEXT,
                title TEXT,
                participated_on TEXT,
                mse_mii_status TEXT,
                status TEXT,
                raw_data JSONB,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (tender_id, source_type, source_number, seller_name)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tender_technical_evaluation (
                id SERIAL PRIMARY KEY,
                tender_id INTEGER NOT NULL REFERENCES tenders(id) ON DELETE CASCADE,
                source_type TEXT NOT NULL,
                source_number TEXT,
                seller_name TEXT NOT NULL,
                offered_item TEXT,
                make TEXT,
                model TEXT,
                title TEXT,
                participated_on TEXT,
                mse_mii_status TEXT,
                technical_status TEXT,
                raw_data JSONB,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (tender_id, source_type, source_number, seller_name)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tender_financial_evaluation (
                id SERIAL PRIMARY KEY,
                tender_id INTEGER NOT NULL REFERENCES tenders(id) ON DELETE CASCADE,
                source_type TEXT NOT NULL,
                source_number TEXT,
                seller_name TEXT NOT NULL,
                offered_item TEXT,
                total_price TEXT,
                rank TEXT,
                financial_status TEXT,
                raw_data JSONB,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (tender_id, source_type, source_number, seller_name)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tender_result_detail_history (
                id SERIAL PRIMARY KEY,
                tender_id INTEGER NOT NULL REFERENCES tenders(id) ON DELETE CASCADE,
                source_type TEXT,
                source_number TEXT,
                old_stage TEXT,
                new_stage TEXT,
                changes_detected JSONB,
                raw_summary JSONB,
                checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                error TEXT
            )
        """)
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
            CREATE TABLE IF NOT EXISTS tender_portals (
                id SERIAL PRIMARY KEY,
                portal_name TEXT NOT NULL,
                portal_url TEXT,
                login_id TEXT,
                encrypted_password TEXT,
                notes TEXT,
                status TEXT DEFAULT 'ACTIVE',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("ALTER TABLE tender_portals ADD COLUMN IF NOT EXISTS portal_url TEXT")
        cur.execute("ALTER TABLE tender_portals ADD COLUMN IF NOT EXISTS login_id TEXT")
        cur.execute("ALTER TABLE tender_portals ADD COLUMN IF NOT EXISTS encrypted_password TEXT")
        cur.execute("ALTER TABLE tender_portals ADD COLUMN IF NOT EXISTS notes TEXT")
        cur.execute("ALTER TABLE tender_portals ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'ACTIVE'")
        cur.execute("ALTER TABLE tender_portals ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        cur.execute("ALTER TABLE tender_portals ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        cur.execute("""
            INSERT INTO tender_portals (portal_name, portal_url, login_id, encrypted_password, notes, status, created_at, updated_at)
            SELECT gp.name, gp.url, gp.username, gp.password_encrypted, gp.notes, 'ACTIVE', gp.created_at, CURRENT_TIMESTAMP
            FROM government_portals gp
            WHERE NOT EXISTS (
                SELECT 1
                FROM tender_portals tp
                WHERE COALESCE(tp.portal_name, '') = COALESCE(gp.name, '')
                  AND COALESCE(tp.portal_url, '') = COALESCE(gp.url, '')
                  AND COALESCE(tp.login_id, '') = COALESCE(gp.username, '')
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
                duplicates_count INTEGER DEFAULT 0,
                below_score_count INTEGER DEFAULT 0,
                pdf_failed_count INTEGER DEFAULT 0,
                extraction_failed_count INTEGER DEFAULT 0,
                evaluation_failed_count INTEGER DEFAULT 0,
                approved_count INTEGER DEFAULT 0,
                review_count INTEGER DEFAULT 0,
                rejected_count INTEGER DEFAULT 0,
                current_step TEXT,
                error_message TEXT,
                error_stack TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS gem_candidate_tenders (
                id SERIAL PRIMARY KEY,
                gem_bid_no TEXT NOT NULL UNIQUE,
                matched_keywords TEXT[] DEFAULT '{}',
                matched_brands TEXT[] DEFAULT '{}',
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
                scan_status TEXT,
                extraction_status TEXT,
                extraction_confidence TEXT,
                extraction_error_message TEXT,
                skip_reason TEXT,
                duplicate_reason TEXT,
                pdf_error TEXT,
                extraction_error TEXT,
                evaluation_confidence TEXT,
                decision_reason TEXT,
                review_reason TEXT,
                rejection_reason TEXT,
                keyword_fit_score NUMERIC(4,1),
                keyword_fit_decision TEXT,
                keyword_pre_score NUMERIC(4,1),
                keyword_decision TEXT,
                matched_products TEXT[] DEFAULT '{}',
                matched_product_keywords TEXT[] DEFAULT '{}',
                negative_keywords TEXT[] DEFAULT '{}',
                negative_keywords_found TEXT[] DEFAULT '{}',
                keyword_context_type TEXT,
                keyword_fit_reason TEXT,
                keyword_evaluation_reason TEXT,
                evaluation_stage TEXT,
                requires_full_evaluation BOOLEAN,
                scan_run_id INTEGER REFERENCES gem_scan_runs(id) ON DELETE SET NULL,
                evaluation_score INTEGER,
                evaluation_reason TEXT,
                evaluation_json JSONB,
                status TEXT DEFAULT 'FOUND',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS gem_search_keywords (
                id SERIAL PRIMARY KEY,
                keyword TEXT NOT NULL UNIQUE,
                active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_scanned_at TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS gem_search_settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS gem_discovered_tenders (
                id SERIAL PRIMARY KEY,
                gem_bid_number TEXT NOT NULL UNIQUE,
                keyword_matched TEXT,
                raw_title TEXT,
                raw_organisation TEXT,
                raw_department TEXT,
                raw_quantity TEXT,
                bid_start_date TEXT,
                bid_end_date TEXT,
                gem_pdf_url TEXT,
                stored_pdf_file_id TEXT,
                raw_gem_data JSONB,
                extracted_data JSONB,
                evaluation_score NUMERIC,
                evaluation_decision TEXT,
                evaluation_reason TEXT,
                action_taken TEXT DEFAULT 'DISCOVERED',
                all_tender_id INTEGER REFERENCES tenders(id) ON DELETE SET NULL,
                source TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tender_evaluations (
                id SERIAL PRIMARY KEY,
                candidate_id INTEGER REFERENCES gem_candidate_tenders(id) ON DELETE CASCADE,
                score INTEGER,
                rating_label TEXT,
                matched_brands TEXT,
                matched_products TEXT,
                negative_keywords TEXT[] DEFAULT '{}',
                keyword_fit_score NUMERIC(4,1),
                keyword_fit_decision TEXT,
                keyword_fit_reason TEXT,
                evaluation_stage TEXT,
                eligibility_status TEXT,
                rejection_reason TEXT,
                evaluation_json JSONB,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        try:
            cur.execute(
                "ALTER TABLE gem_candidate_tenders ALTER COLUMN evaluation_score TYPE NUMERIC(4,1) USING evaluation_score::numeric"
            )
        except Exception:
            pass
        for ddl in [
            "ALTER TABLE gem_scan_runs ADD COLUMN IF NOT EXISTS review_count INTEGER DEFAULT 0",
            "ALTER TABLE gem_scan_runs ADD COLUMN IF NOT EXISTS duplicates_count INTEGER DEFAULT 0",
            "ALTER TABLE gem_scan_runs ADD COLUMN IF NOT EXISTS below_score_count INTEGER DEFAULT 0",
            "ALTER TABLE gem_scan_runs ADD COLUMN IF NOT EXISTS pdf_failed_count INTEGER DEFAULT 0",
            "ALTER TABLE gem_scan_runs ADD COLUMN IF NOT EXISTS extraction_failed_count INTEGER DEFAULT 0",
            "ALTER TABLE gem_scan_runs ADD COLUMN IF NOT EXISTS evaluation_failed_count INTEGER DEFAULT 0",
            "ALTER TABLE gem_scan_runs ADD COLUMN IF NOT EXISTS current_step TEXT",
            "ALTER TABLE gem_scan_runs ADD COLUMN IF NOT EXISTS error_stack TEXT",
            "ALTER TABLE gem_candidate_tenders ADD COLUMN IF NOT EXISTS matched_brands TEXT[] DEFAULT '{}'",
            "ALTER TABLE gem_candidate_tenders ADD COLUMN IF NOT EXISTS scan_status TEXT",
            "ALTER TABLE gem_candidate_tenders ADD COLUMN IF NOT EXISTS extraction_status TEXT",
            "ALTER TABLE gem_candidate_tenders ADD COLUMN IF NOT EXISTS extraction_confidence TEXT",
            "ALTER TABLE gem_candidate_tenders ADD COLUMN IF NOT EXISTS extraction_error_message TEXT",
            "ALTER TABLE gem_candidate_tenders ADD COLUMN IF NOT EXISTS skip_reason TEXT",
            "ALTER TABLE gem_candidate_tenders ADD COLUMN IF NOT EXISTS duplicate_reason TEXT",
            "ALTER TABLE gem_candidate_tenders ADD COLUMN IF NOT EXISTS pdf_error TEXT",
            "ALTER TABLE gem_candidate_tenders ADD COLUMN IF NOT EXISTS extraction_error TEXT",
            "ALTER TABLE gem_candidate_tenders ADD COLUMN IF NOT EXISTS evaluation_confidence TEXT",
            "ALTER TABLE gem_candidate_tenders ADD COLUMN IF NOT EXISTS decision_reason TEXT",
            "ALTER TABLE gem_candidate_tenders ADD COLUMN IF NOT EXISTS review_reason TEXT",
            "ALTER TABLE gem_candidate_tenders ADD COLUMN IF NOT EXISTS rejection_reason TEXT",
            "ALTER TABLE gem_candidate_tenders ADD COLUMN IF NOT EXISTS keyword_fit_score NUMERIC(4,1)",
            "ALTER TABLE gem_candidate_tenders ADD COLUMN IF NOT EXISTS keyword_fit_decision TEXT",
            "ALTER TABLE gem_candidate_tenders ADD COLUMN IF NOT EXISTS matched_products TEXT[] DEFAULT '{}'",
            "ALTER TABLE gem_candidate_tenders ADD COLUMN IF NOT EXISTS negative_keywords TEXT[] DEFAULT '{}'",
            "ALTER TABLE gem_candidate_tenders ADD COLUMN IF NOT EXISTS keyword_fit_reason TEXT",
            "ALTER TABLE gem_candidate_tenders ADD COLUMN IF NOT EXISTS evaluation_stage TEXT",
            "ALTER TABLE gem_candidate_tenders ADD COLUMN IF NOT EXISTS keyword_pre_score NUMERIC(4,1)",
            "ALTER TABLE gem_candidate_tenders ADD COLUMN IF NOT EXISTS keyword_decision TEXT",
            "ALTER TABLE gem_candidate_tenders ADD COLUMN IF NOT EXISTS matched_product_keywords TEXT[] DEFAULT '{}'",
            "ALTER TABLE gem_candidate_tenders ADD COLUMN IF NOT EXISTS negative_keywords_found TEXT[] DEFAULT '{}'",
            "ALTER TABLE gem_candidate_tenders ADD COLUMN IF NOT EXISTS keyword_context_type TEXT",
            "ALTER TABLE gem_candidate_tenders ADD COLUMN IF NOT EXISTS keyword_evaluation_reason TEXT",
            "ALTER TABLE gem_candidate_tenders ADD COLUMN IF NOT EXISTS requires_full_evaluation BOOLEAN",
            "ALTER TABLE gem_candidate_tenders ADD COLUMN IF NOT EXISTS scan_run_id INTEGER",
        ]:
            try:
                cur.execute(ddl)
            except Exception:
                pass
        for ddl in [
            "ALTER TABLE tender_evaluations ADD COLUMN IF NOT EXISTS matched_products TEXT",
            "ALTER TABLE tender_evaluations ADD COLUMN IF NOT EXISTS negative_keywords TEXT[] DEFAULT '{}'",
            "ALTER TABLE tender_evaluations ADD COLUMN IF NOT EXISTS keyword_fit_score NUMERIC(4,1)",
            "ALTER TABLE tender_evaluations ADD COLUMN IF NOT EXISTS keyword_fit_decision TEXT",
            "ALTER TABLE tender_evaluations ADD COLUMN IF NOT EXISTS keyword_fit_reason TEXT",
            "ALTER TABLE tender_evaluations ADD COLUMN IF NOT EXISTS evaluation_stage TEXT",
        ]:
            try:
                cur.execute(ddl)
            except Exception:
                pass
        try:
            cur.execute(
                "ALTER TABLE tender_evaluations ALTER COLUMN score TYPE NUMERIC(4,1) USING score::numeric"
            )
        except Exception:
            pass
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

def list_tender_portals(include_inactive: bool = True):
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if include_inactive:
            cur.execute(
                """
                SELECT id, portal_name, portal_url, login_id, notes, status, created_at, updated_at,
                       CASE WHEN COALESCE(encrypted_password, '') <> '' THEN TRUE ELSE FALSE END AS has_password
                FROM tender_portals
                ORDER BY LOWER(portal_name), id DESC
                """
            )
        else:
            cur.execute(
                """
                SELECT id, portal_name, portal_url, login_id, notes, status, created_at, updated_at,
                       CASE WHEN COALESCE(encrypted_password, '') <> '' THEN TRUE ELSE FALSE END AS has_password
                FROM tender_portals
                WHERE COALESCE(status, 'ACTIVE') <> 'INACTIVE'
                ORDER BY LOWER(portal_name), id DESC
                """
            )
        rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_tender_portal(portal_id: int):
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, portal_name, portal_url, login_id, notes, status, created_at, updated_at,
                   CASE WHEN COALESCE(encrypted_password, '') <> '' THEN TRUE ELSE FALSE END AS has_password
            FROM tender_portals
            WHERE id=%s
            """,
            (portal_id,),
        )
        row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_tender_portal_with_password(portal_id: int):
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, portal_name, portal_url, login_id, encrypted_password, notes, status, created_at, updated_at
            FROM tender_portals
            WHERE id=%s
            """,
            (portal_id,),
        )
        row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def create_tender_portal(portal_name: str, portal_url, login_id, encrypted_password: str, notes, status: str = "ACTIVE") -> int:
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tender_portals (portal_name, portal_url, login_id, encrypted_password, notes, status, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)
            RETURNING id
            """,
            (portal_name, portal_url, login_id, encrypted_password, notes, status),
        )
        portal_id = cur.fetchone()[0]
    conn.commit()
    conn.close()
    return portal_id


def update_tender_portal(portal_id: int, portal_name: str, portal_url, login_id, encrypted_password: str, notes, status: str):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE tender_portals
            SET portal_name=%s,
                portal_url=%s,
                login_id=%s,
                encrypted_password=%s,
                notes=%s,
                status=%s,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=%s
            """,
            (portal_name, portal_url, login_id, encrypted_password, notes, status, portal_id),
        )
    conn.commit()
    conn.close()


def set_tender_portal_status(portal_id: int, status: str):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE tender_portals SET status=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s",
            (status, portal_id),
        )
    conn.commit()
    conn.close()


def list_portals():
    rows = list_tender_portals(include_inactive=True)
    return [
        {
            "id": r["id"],
            "name": r.get("portal_name"),
            "url": r.get("portal_url"),
            "username": r.get("login_id"),
            "notes": r.get("notes"),
            "status": r.get("status"),
            "created_at": r.get("created_at"),
            "updated_at": r.get("updated_at"),
            "has_password": r.get("has_password"),
        }
        for r in rows
    ]


def get_portal(portal_id: int):
    row = get_tender_portal(portal_id)
    if not row:
        return None
    return {
        "id": row["id"],
        "name": row.get("portal_name"),
        "url": row.get("portal_url"),
        "username": row.get("login_id"),
        "notes": row.get("notes"),
        "status": row.get("status"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "has_password": row.get("has_password"),
    }


def get_portal_with_password(portal_id: int):
    row = get_tender_portal_with_password(portal_id)
    if not row:
        return None
    return {
        "id": row["id"],
        "name": row.get("portal_name"),
        "url": row.get("portal_url"),
        "username": row.get("login_id"),
        "password_encrypted": row.get("encrypted_password"),
        "notes": row.get("notes"),
        "status": row.get("status"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def create_portal(name: str, url, username, password_encrypted: str, notes) -> int:
    return create_tender_portal(name, url, username, password_encrypted, notes, "ACTIVE")


def update_portal(portal_id: int, name: str, url, username, password_encrypted: str, notes):
    existing = get_tender_portal_with_password(portal_id)
    status = existing.get("status", "ACTIVE") if existing else "ACTIVE"
    update_tender_portal(portal_id, name, url, username, password_encrypted, notes, status)


def delete_portal(portal_id: int):
    set_tender_portal_status(portal_id, "INACTIVE")


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
                won_text, lost_text, participant_text, expand_sections_json,
                uploaded_at, pdf_path, extraction_json_path, status, participation_status
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id""",
            (
                data.get("gem_bidding_number"),
                data.get("tender_number"), data.get("date"),
                data.get("bid_end_datetime"), data.get("bid_opening_datetime"),
                data.get("department_name"), data.get("organization_name"),
                data.get("office_name_location"),
                data.get("total_quantity"), data.get("make"), data.get("tender_approx_value"),
                data.get("won_text"), data.get("lost_text"), data.get("participant_text"),
                psycopg2.extras.Json(data.get("expand_sections_json")) if data.get("expand_sections_json") is not None else None,
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
                expand_sections_json=COALESCE(%s,expand_sections_json),
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
                psycopg2.extras.Json(data.get("expand_sections_json")) if data.get("expand_sections_json") is not None else None,
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
                      t.participant_text, t.expand_sections_json, t.pdf_path, t.extraction_json_path, t.status,
                      t.result_available, t.bid_result_available, t.ra_created, t.ra_result_available,
                      t.gem_result_status, t.gem_bid_number, t.gem_internal_id,
                      t.result_declared, t.result_declared_at, t.gem_result_url,
                      t.gem_ra_number, t.gem_ra_url, t.gem_ra_result_url, t.ra_start_date, t.ra_end_date, t.gem_page_status,
                      t.last_result_checked_at, t.notification_sent, t.ra_notified, t.result_check_error,
                      t.result_review_required, t.result_check_warning, t.l1_seller_name,
                      t.our_company_rank, t.our_company_status,
                      t.filed_date, t.ac_manager, t.remark,
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


def update_tender_record_fields(
    tender_id,
    won_text,
    lost_text,
    participant_text,
    ac_manager=None,
    remark=None,
    expand_sections_json=None,
):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE tenders
               SET won_text=%s,
                   lost_text=%s,
                   participant_text=%s,
                   ac_manager=%s,
                   remark=%s,
                   expand_sections_json=COALESCE(%s, expand_sections_json)
               WHERE id=%s""",
            (
                won_text,
                lost_text,
                participant_text,
                ac_manager,
                remark,
                psycopg2.extras.Json(expand_sections_json) if expand_sections_json is not None else None,
                tender_id,
            ),
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


def list_result_watch_eligible_tenders():
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT id, gem_bidding_number, tender_number, bid_end_datetime, gem_result_status,
                      result_available, bid_result_available, ra_created, ra_result_available,
                      gem_result_url, gem_ra_number, gem_ra_url, gem_ra_result_url, ra_start_date, ra_end_date, gem_page_status,
                      result_declared, notification_sent, ra_notified, last_result_checked_at, result_check_error,
                      result_review_required, result_check_warning,
                      organization_name, make
               FROM tenders
               ORDER BY uploaded_at DESC NULLS LAST, id DESC"""
        )
        rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_tender_result(
    tender_id,
    *,
    result_available=_UNSET,
    bid_result_available=_UNSET,
    ra_created=_UNSET,
    ra_result_available=_UNSET,
    gem_result_status=_UNSET,
    gem_bid_number=_UNSET,
    gem_internal_id=_UNSET,
    result_declared=_UNSET,
    result_declared_at=_UNSET,
    gem_result_url=_UNSET,
    gem_ra_number=_UNSET,
    gem_ra_url=_UNSET,
    gem_ra_result_url=_UNSET,
    ra_start_date=_UNSET,
    ra_end_date=_UNSET,
    gem_page_status=_UNSET,
    last_result_checked_at=_UNSET,
    notification_sent=_UNSET,
    ra_notified=_UNSET,
    result_check_error=_UNSET,
    result_review_required=_UNSET,
    result_check_warning=_UNSET,
    l1_seller_name=_UNSET,
    our_company_rank=_UNSET,
    our_company_status=_UNSET,
):
    updates = []
    values = []
    if result_available is not _UNSET:
        updates.append("result_available=%s")
        values.append(result_available)
    if bid_result_available is not _UNSET:
        updates.append("bid_result_available=%s")
        values.append(bid_result_available)
    if ra_created is not _UNSET:
        updates.append("ra_created=%s")
        values.append(ra_created)
    if ra_result_available is not _UNSET:
        updates.append("ra_result_available=%s")
        values.append(ra_result_available)
    if gem_result_status is not _UNSET:
        updates.append("gem_result_status=%s")
        values.append(gem_result_status)
    if gem_bid_number is not _UNSET:
        updates.append("gem_bid_number=%s")
        values.append(gem_bid_number)
    if gem_internal_id is not _UNSET:
        updates.append("gem_internal_id=%s")
        values.append(gem_internal_id)
    if result_declared is not _UNSET:
        updates.append("result_declared=%s")
        values.append(result_declared)
    if result_declared_at is not _UNSET:
        updates.append("result_declared_at=%s")
        values.append(result_declared_at)
    if gem_result_url is not _UNSET:
        updates.append("gem_result_url=%s")
        values.append(gem_result_url)
    if gem_ra_number is not _UNSET:
        updates.append("gem_ra_number=%s")
        values.append(gem_ra_number)
    if gem_ra_url is not _UNSET:
        updates.append("gem_ra_url=%s")
        values.append(gem_ra_url)
    if gem_ra_result_url is not _UNSET:
        updates.append("gem_ra_result_url=%s")
        values.append(gem_ra_result_url)
    if ra_start_date is not _UNSET:
        updates.append("ra_start_date=%s")
        values.append(ra_start_date)
    if ra_end_date is not _UNSET:
        updates.append("ra_end_date=%s")
        values.append(ra_end_date)
    if gem_page_status is not _UNSET:
        updates.append("gem_page_status=%s")
        values.append(gem_page_status)
    if last_result_checked_at is not _UNSET:
        updates.append("last_result_checked_at=%s")
        values.append(last_result_checked_at)
    if notification_sent is not _UNSET:
        updates.append("notification_sent=%s")
        values.append(notification_sent)
    if ra_notified is not _UNSET:
        updates.append("ra_notified=%s")
        values.append(ra_notified)
    if result_check_error is not _UNSET:
        updates.append("result_check_error=%s")
        values.append(result_check_error)
    if result_review_required is not _UNSET:
        updates.append("result_review_required=%s")
        values.append(result_review_required)
    if result_check_warning is not _UNSET:
        updates.append("result_check_warning=%s")
        values.append(result_check_warning)
    if l1_seller_name is not _UNSET:
        updates.append("l1_seller_name=%s")
        values.append(l1_seller_name)
    if our_company_rank is not _UNSET:
        updates.append("our_company_rank=%s")
        values.append(our_company_rank)
    if our_company_status is not _UNSET:
        updates.append("our_company_status=%s")
        values.append(our_company_status)
    if not updates:
        return

    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE tenders SET {', '.join(updates)} WHERE id=%s",
            (*values, tender_id),
        )
    conn.commit()
    conn.close()


def create_gem_result_check_history(
    tender_id,
    *,
    gem_bid_number=None,
    old_status=None,
    new_status=None,
    old_result_available=None,
    new_result_available=None,
    old_bid_result_available=None,
    new_bid_result_available=None,
    old_ra_created=None,
    new_ra_created=None,
    old_ra_result_available=None,
    new_ra_result_available=None,
    old_urls=None,
    new_urls=None,
    reason=None,
    confidence=None,
    raw_gem_response=None,
    checked_at=None,
    source=None,
):
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """INSERT INTO gem_result_check_history
               (tender_id, gem_bid_number, old_status, new_status,
                old_result_available, new_result_available,
                old_bid_result_available, new_bid_result_available,
                old_ra_created, new_ra_created,
                old_ra_result_available, new_ra_result_available,
                old_urls, new_urls, reason, confidence, raw_gem_response, checked_at, source)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, CURRENT_TIMESTAMP), %s)
               RETURNING id, tender_id, gem_bid_number, old_status, new_status, checked_at, source""",
            (
                tender_id,
                gem_bid_number,
                old_status,
                new_status,
                old_result_available,
                new_result_available,
                old_bid_result_available,
                new_bid_result_available,
                old_ra_created,
                new_ra_created,
                old_ra_result_available,
                new_ra_result_available,
                psycopg2.extras.Json(old_urls or {}),
                psycopg2.extras.Json(new_urls or {}),
                reason,
                confidence,
                psycopg2.extras.Json(raw_gem_response) if raw_gem_response is not None else None,
                checked_at,
                source,
            ),
        )
        row = cur.fetchone()
    conn.commit()
    conn.close()
    return dict(row)


def list_recent_gem_result_history(limit=100):
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT id, tender_id, gem_bid_number, old_status, new_status,
                      old_result_available, new_result_available,
                      old_bid_result_available, new_bid_result_available,
                      old_ra_created, new_ra_created,
                      old_ra_result_available, new_ra_result_available,
                      old_urls, new_urls, reason, confidence, raw_gem_response, checked_at, source
               FROM gem_result_check_history
               ORDER BY checked_at DESC, id DESC
               LIMIT %s""",
            (limit,),
        )
        rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_result_watcher_run_log(
    *,
    started_at=None,
    finished_at=None,
    total_pending=0,
    checked=0,
    results_found=0,
    not_available=0,
    failed=0,
    skipped=0,
    run_source="LOCAL_AGENT",
):
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """INSERT INTO result_watcher_run_logs
               (started_at, finished_at, total_pending, checked, results_found, not_available, failed, skipped, run_source)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id, started_at, finished_at, total_pending, checked, results_found, not_available, failed, skipped, run_source, created_at""",
            (started_at, finished_at, total_pending, checked, results_found, not_available, failed, skipped, run_source),
        )
        row = cur.fetchone()
    conn.commit()
    conn.close()
    return dict(row)


def get_result_watcher_summary():
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """DELETE FROM tender_notifications
               WHERE created_at::date < CURRENT_DATE"""
        )
        cur.execute(
            """SELECT id, started_at, finished_at, total_pending, checked, results_found, not_available, failed, skipped, run_source, created_at
               FROM result_watcher_run_logs
               ORDER BY COALESCE(finished_at, created_at) DESC, id DESC
               LIMIT 1"""
        )
        last_run = cur.fetchone()
        cur.execute(
            """SELECT COUNT(*) AS results_found_today
               FROM tender_notifications
               WHERE type IN ('RESULT_AVAILABLE', 'BID_RESULT_AVAILABLE', 'RA_CREATED', 'RA_RESULT_AVAILABLE')
                 AND is_valid = TRUE
                 AND created_at::date = CURRENT_DATE"""
        )
        today = cur.fetchone()
        cur.execute(
            """SELECT COUNT(*) AS unread_notifications_today
               FROM tender_notifications
               WHERE is_read = FALSE
                 AND is_valid = TRUE"""
        )
        unread = cur.fetchone()
    conn.commit()
    conn.close()
    return {
        "last_run": dict(last_run) if last_run else None,
        "results_found_today": int((today or {}).get("results_found_today") or 0),
        "unread_notifications_today": int((unread or {}).get("unread_notifications_today") or 0),
    }


def repair_confirmed_result_flags_from_notifications():
    """Notifications are alerts only and must not restore tender result state."""
    return 0


def create_tender_notification(tender_id, title, message, notification_type="RESULT_AVAILABLE"):
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """INSERT INTO tender_notifications (tender_id, title, message, type, notification_type, is_valid, invalidated_at, invalidation_reason)
               VALUES (%s, %s, %s, %s, %s, TRUE, NULL, NULL)
               ON CONFLICT (tender_id, type) DO UPDATE
               SET title = EXCLUDED.title,
                   message = EXCLUDED.message,
                   notification_type = EXCLUDED.notification_type,
                   is_valid = TRUE,
                   invalidated_at = NULL,
                   invalidation_reason = NULL
               RETURNING id, tender_id, title, message, type, notification_type, is_read, is_valid, invalidated_at, invalidation_reason, created_at""",
            (tender_id, title, message, notification_type, notification_type),
        )
        row = cur.fetchone()
    conn.commit()
    conn.close()
    return dict(row)


def list_tender_notifications(limit=50):
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """DELETE FROM tender_notifications
               WHERE created_at::date < CURRENT_DATE"""
        )
        cur.execute(
            """SELECT n.id, n.tender_id, n.title, n.message, n.type, n.notification_type, n.is_read, n.is_valid, n.invalidated_at, n.invalidation_reason, n.created_at,
                      t.gem_bidding_number, t.tender_number, t.gem_result_status, t.gem_result_url,
                      t.gem_ra_number, t.gem_ra_url, t.gem_ra_result_url, t.result_available, t.bid_result_available, t.ra_created, t.ra_result_available
                FROM tender_notifications n
                LEFT JOIN tenders t ON t.id = n.tender_id
               WHERE n.is_valid = TRUE
                ORDER BY n.created_at DESC, n.id DESC
                LIMIT %s""",
            (limit,),
        )
        rows = cur.fetchall()
    conn.commit()
    conn.close()
    return [dict(r) for r in rows]


def mark_tender_notification_read(notification_id):
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """UPDATE tender_notifications
               SET is_read=TRUE
               WHERE id=%s
               RETURNING id, tender_id, title, message, type, notification_type, is_read, is_valid, invalidated_at, invalidation_reason, created_at""",
            (notification_id,),
        )
        row = cur.fetchone()
    conn.commit()
    conn.close()
    return dict(row) if row else None


def invalidate_tender_notifications(tender_id, *, reason: str, notification_types=None):
    conn = get_db()
    with conn.cursor() as cur:
        if notification_types:
            cur.execute(
                """UPDATE tender_notifications
                   SET is_valid=FALSE,
                       invalidated_at=CURRENT_TIMESTAMP,
                       invalidation_reason=%s
                   WHERE tender_id=%s
                     AND is_valid=TRUE
                     AND COALESCE(notification_type, type) = ANY(%s)""",
                (reason, tender_id, list(notification_types)),
            )
        else:
            cur.execute(
                """UPDATE tender_notifications
                   SET is_valid=FALSE,
                       invalidated_at=CURRENT_TIMESTAMP,
                       invalidation_reason=%s
                   WHERE tender_id=%s
                     AND is_valid=TRUE""",
                (reason, tender_id),
            )
        invalidated = cur.rowcount or 0
    conn.commit()
    conn.close()
    return int(invalidated)


def _replace_result_detail_rows(cur, table_name: str, tender_id: int, source_type: str, source_number, rows: list[dict]):
    cur.execute(
        f"DELETE FROM {table_name} WHERE tender_id=%s AND source_type=%s AND COALESCE(source_number, '') = COALESCE(%s, '')",
        (tender_id, source_type, source_number),
    )
    if table_name == "tender_result_participants":
        sql = """
            INSERT INTO tender_result_participants
            (tender_id, source_type, source_number, seller_name, offered_item, make, model, title, participated_on, mse_mii_status, status, raw_data)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        for row in rows:
            cur.execute(
                sql,
                (
                    tender_id, source_type, source_number,
                    row.get("seller_name"), row.get("offered_item"), row.get("make"), row.get("model"),
                    row.get("title"), row.get("participated_on"), row.get("mse_mii_status"),
                    row.get("status"), psycopg2.extras.Json(row),
                ),
            )
    elif table_name == "tender_technical_evaluation":
        sql = """
            INSERT INTO tender_technical_evaluation
            (tender_id, source_type, source_number, seller_name, offered_item, make, model, title, participated_on, mse_mii_status, technical_status, raw_data)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        for row in rows:
            cur.execute(
                sql,
                (
                    tender_id, source_type, source_number,
                    row.get("seller_name"), row.get("offered_item"), row.get("make"), row.get("model"),
                    row.get("title"), row.get("participated_on"), row.get("mse_mii_status"),
                    row.get("technical_status"), psycopg2.extras.Json(row),
                ),
            )
    elif table_name == "tender_financial_evaluation":
        sql = """
            INSERT INTO tender_financial_evaluation
            (tender_id, source_type, source_number, seller_name, offered_item, total_price, rank, financial_status, raw_data)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        for row in rows:
            cur.execute(
                sql,
                (
                    tender_id, source_type, source_number,
                    row.get("seller_name"), row.get("offered_item"), row.get("total_price"),
                    row.get("rank"), row.get("financial_status"), psycopg2.extras.Json(row),
                ),
            )


def save_tender_result_details(
    tender_id: int,
    *,
    gem_bid_number=None,
    source_type=None,
    source_number=None,
    result_url=None,
    current_stage=None,
    participants=None,
    technical_evaluation=None,
    financial_evaluation=None,
    summary=None,
    checked_at=None,
    parse_error=None,
    changes_detected=None,
):
    participants = participants or []
    technical_evaluation = technical_evaluation or []
    financial_evaluation = financial_evaluation or []
    summary = summary or {}
    section_flags = changes_detected or {}
    has_detected_flags = any(
        key in section_flags
        for key in ("participants_detected", "technical_detected", "financial_detected")
    )
    replace_participants = bool(section_flags.get("participants_detected")) or (not has_detected_flags and bool(participants))
    replace_technical = bool(section_flags.get("technical_detected")) or (not has_detected_flags and bool(technical_evaluation))
    replace_financial = bool(section_flags.get("financial_detected")) or (not has_detected_flags and bool(financial_evaluation))
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, current_stage, current_source_type, current_bid_or_ra_number,
                   bid_result_available, bid_technical_available, bid_financial_available,
                   ra_created, ra_number, ra_result_available, ra_technical_available, ra_financial_available,
                   our_company_participated, our_company_technical_status, our_company_financial_rank, our_company_final_price
            FROM tender_result_summary
            WHERE tender_id=%s
            """,
            (tender_id,),
        )
        old_summary = dict(cur.fetchone() or {})
        checked_at_value = checked_at or datetime.now()
        cur.execute(
            """
            INSERT INTO tender_result_summary
            (tender_id, gem_bid_number, current_source_type, current_bid_or_ra_number,
             bid_result_available, bid_technical_available, bid_financial_available,
             ra_created, ra_number, ra_start_date, ra_end_date,
             ra_result_available, ra_technical_available, ra_financial_available,
             current_stage, our_company_participated, our_company_technical_status,
             our_company_financial_rank, our_company_final_price, last_checked_at,
             last_successful_parse_at, parse_error, result_url, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (tender_id) DO UPDATE
            SET gem_bid_number=EXCLUDED.gem_bid_number,
                current_source_type=EXCLUDED.current_source_type,
                current_bid_or_ra_number=EXCLUDED.current_bid_or_ra_number,
                bid_result_available=EXCLUDED.bid_result_available,
                bid_technical_available=EXCLUDED.bid_technical_available,
                bid_financial_available=EXCLUDED.bid_financial_available,
                ra_created=EXCLUDED.ra_created,
                ra_number=EXCLUDED.ra_number,
                ra_start_date=EXCLUDED.ra_start_date,
                ra_end_date=EXCLUDED.ra_end_date,
                ra_result_available=EXCLUDED.ra_result_available,
                ra_technical_available=EXCLUDED.ra_technical_available,
                ra_financial_available=EXCLUDED.ra_financial_available,
                current_stage=EXCLUDED.current_stage,
                our_company_participated=EXCLUDED.our_company_participated,
                our_company_technical_status=EXCLUDED.our_company_technical_status,
                our_company_financial_rank=EXCLUDED.our_company_financial_rank,
                our_company_final_price=EXCLUDED.our_company_final_price,
                last_checked_at=EXCLUDED.last_checked_at,
                last_successful_parse_at=EXCLUDED.last_successful_parse_at,
                parse_error=EXCLUDED.parse_error,
                result_url=EXCLUDED.result_url,
                updated_at=CURRENT_TIMESTAMP
            RETURNING *
            """,
            (
                tender_id,
                gem_bid_number,
                source_type,
                source_number,
                bool(summary.get("bidResultAvailable") or summary.get("bid_result_available")),
                bool(summary.get("bidTechnicalAvailable") or summary.get("bid_technical_available")),
                bool(summary.get("bidFinancialAvailable") or summary.get("bid_financial_available")),
                bool(summary.get("raCreated") or summary.get("ra_created")),
                summary.get("raNumber") or summary.get("ra_number") or source_number,
                summary.get("raStartDate") or summary.get("ra_start_date"),
                summary.get("raEndDate") or summary.get("ra_end_date"),
                bool(summary.get("raResultAvailable") or summary.get("ra_result_available")),
                bool(summary.get("raTechnicalAvailable") or summary.get("ra_technical_available")),
                bool(summary.get("raFinancialAvailable") or summary.get("ra_financial_available")),
                current_stage,
                bool(summary.get("ourCompanyParticipated") or summary.get("our_company_participated")),
                summary.get("ourCompanyTechnicalStatus") or summary.get("our_company_technical_status"),
                summary.get("ourCompanyFinancialRank") or summary.get("our_company_financial_rank"),
                summary.get("ourCompanyFinalPrice") or summary.get("our_company_final_price"),
                checked_at_value,
                checked_at_value if not parse_error else old_summary.get("last_successful_parse_at") or checked_at_value,
                parse_error,
                result_url,
            ),
        )
        saved_summary = dict(cur.fetchone() or {})
        if not parse_error:
            if replace_participants:
                _replace_result_detail_rows(cur, "tender_result_participants", tender_id, source_type or "BID", source_number, participants)
            if replace_technical:
                _replace_result_detail_rows(cur, "tender_technical_evaluation", tender_id, source_type or "BID", source_number, technical_evaluation)
            if replace_financial:
                _replace_result_detail_rows(cur, "tender_financial_evaluation", tender_id, source_type or "BID", source_number, financial_evaluation)
        cur.execute(
            """
            INSERT INTO tender_result_detail_history
            (tender_id, source_type, source_number, old_stage, new_stage, changes_detected, raw_summary, checked_at, error)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                tender_id,
                source_type,
                source_number,
                old_summary.get("current_stage"),
                current_stage,
                psycopg2.extras.Json(changes_detected or {}),
                psycopg2.extras.Json({
                    "summary": summary,
                    "participants_count": len(participants),
                    "technical_count": len(technical_evaluation),
                    "financial_count": len(financial_evaluation),
                    "result_url": result_url,
                }),
                checked_at_value,
                parse_error,
            ),
        )
    conn.commit()
    conn.close()
    saved_summary["success"] = True
    saved_summary["summary_saved"] = True
    saved_summary["participants_count"] = len(participants)
    saved_summary["technical_count"] = len(technical_evaluation)
    saved_summary["financial_count"] = len(financial_evaluation)
    saved_summary["participants_saved"] = len(participants) if (not parse_error and replace_participants) else 0
    saved_summary["technical_saved"] = len(technical_evaluation) if (not parse_error and replace_technical) else 0
    saved_summary["financial_saved"] = len(financial_evaluation) if (not parse_error and replace_financial) else 0
    return saved_summary


def get_tender_result_details(tender_id: int):
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM tender_result_summary WHERE tender_id=%s", (tender_id,))
        summary = dict(cur.fetchone() or {})
        cur.execute(
            "SELECT * FROM tender_result_participants WHERE tender_id=%s ORDER BY source_type, COALESCE(source_number,''), seller_name",
            (tender_id,),
        )
        participants = [dict(r) for r in cur.fetchall()]
        cur.execute(
            "SELECT * FROM tender_technical_evaluation WHERE tender_id=%s ORDER BY source_type, COALESCE(source_number,''), seller_name",
            (tender_id,),
        )
        technical = [dict(r) for r in cur.fetchall()]
        cur.execute(
            "SELECT * FROM tender_financial_evaluation WHERE tender_id=%s ORDER BY source_type, COALESCE(source_number,''), seller_name",
            (tender_id,),
        )
        financial = [dict(r) for r in cur.fetchall()]
        cur.execute(
            "SELECT * FROM tender_result_detail_history WHERE tender_id=%s ORDER BY checked_at DESC, id DESC LIMIT 25",
            (tender_id,),
        )
        history = [dict(r) for r in cur.fetchall()]
    conn.close()
    return {
        "summary": summary,
        "participants": participants,
        "technical_evaluation": technical,
        "financial_evaluation": financial,
        "technicalEvaluation": technical,
        "financialEvaluation": financial,
        "history": history,
    }


def find_tender_duplicate(gem_bidding_number, tender_number=None):
    """Return existing tender if the GeM bidding number already exists."""
    if not gem_bidding_number:
        return None
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT id FROM tenders WHERE gem_bidding_number=%s LIMIT 1",
            (gem_bidding_number,),
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


# Local GeM Search Agent helpers

def list_gem_search_keywords(include_inactive: bool = True):
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        where = "" if include_inactive else "WHERE active=TRUE"
        cur.execute(
            f"""
            SELECT k.*,
                   COALESCE(today.discovered_today, 0) AS new_tenders_found_today,
                   COALESCE(today.inserted_today, 0) AS inserted_today,
                   COALESCE(today.rejected_today, 0) AS rejected_today
            FROM gem_search_keywords k
            LEFT JOIN (
                SELECT keyword_matched,
                       COUNT(*) FILTER (WHERE created_at::date = CURRENT_DATE) AS discovered_today,
                       COUNT(*) FILTER (WHERE action_taken='INSERTED_TO_ALL_TENDERS' AND last_checked_at::date = CURRENT_DATE) AS inserted_today,
                       COUNT(*) FILTER (WHERE action_taken='REJECTED_NOT_SUITABLE' AND last_checked_at::date = CURRENT_DATE) AS rejected_today
                FROM gem_discovered_tenders
                GROUP BY keyword_matched
            ) today ON LOWER(today.keyword_matched)=LOWER(k.keyword)
            {where}
            ORDER BY active DESC, keyword ASC
            """
        )
        rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def upsert_gem_search_keyword(keyword: str):
    keyword = str(keyword or "").strip()
    if not keyword:
        raise ValueError("Keyword cannot be empty")
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """INSERT INTO gem_search_keywords (keyword, active)
               VALUES (%s, TRUE)
               ON CONFLICT (keyword) DO UPDATE SET active=TRUE, updated_at=CURRENT_TIMESTAMP
               RETURNING *""",
            (keyword,),
        )
        row = dict(cur.fetchone())
    conn.commit()
    conn.close()
    return row


def update_gem_search_keyword(keyword_id: int, *, keyword=_UNSET, active=_UNSET):
    updates = []
    values = []
    if keyword is not _UNSET:
        text = str(keyword or "").strip()
        if not text:
            raise ValueError("Keyword cannot be empty")
        updates.append("keyword=%s")
        values.append(text)
    if active is not _UNSET:
        updates.append("active=%s")
        values.append(bool(active))
    if not updates:
        return None
    updates.append("updated_at=CURRENT_TIMESTAMP")
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            f"UPDATE gem_search_keywords SET {', '.join(updates)} WHERE id=%s RETURNING *",
            values + [keyword_id],
        )
        row = cur.fetchone()
    conn.commit()
    conn.close()
    return dict(row) if row else None


def delete_gem_search_keyword(keyword_id: int):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM gem_search_keywords WHERE id=%s", (keyword_id,))
        deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted > 0


def touch_gem_search_keyword(keyword: str):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE gem_search_keywords SET last_scanned_at=CURRENT_TIMESTAMP WHERE LOWER(keyword)=LOWER(%s)",
            (keyword,),
        )
    conn.commit()
    conn.close()


def get_gem_search_settings():
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT key, value FROM gem_search_settings")
        rows = cur.fetchall()
    conn.close()
    settings = {row["key"]: row["value"] for row in rows}
    target_date = settings.get("scan_target_date") or datetime.now().strftime("%Y-%m-%d")
    return {
        "scanTargetDate": target_date,
        "scanDateFrom": settings.get("scan_date_from") or target_date,
        "scanDateTo": settings.get("scan_date_to") or target_date,
        "searchDateMode": settings.get("search_date_mode") or "date",
    }


def update_gem_search_settings(*, scan_target_date=_UNSET, scan_date_from=_UNSET, scan_date_to=_UNSET, search_date_mode=_UNSET):
    updates = {}
    if scan_target_date is not _UNSET:
        updates["scan_target_date"] = str(scan_target_date or "").strip()
    if scan_date_from is not _UNSET:
        updates["scan_date_from"] = str(scan_date_from or "").strip()
    if scan_date_to is not _UNSET:
        updates["scan_date_to"] = str(scan_date_to or "").strip()
    if search_date_mode is not _UNSET:
        updates["search_date_mode"] = str(search_date_mode or "date").strip() or "date"
    if not updates:
        return get_gem_search_settings()
    conn = get_db()
    with conn.cursor() as cur:
        for key, value in updates.items():
            cur.execute(
                """INSERT INTO gem_search_settings (key, value, updated_at)
                   VALUES (%s, %s, CURRENT_TIMESTAMP)
                   ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=CURRENT_TIMESTAMP""",
                (key, value),
            )
    conn.commit()
    conn.close()
    return get_gem_search_settings()


def get_discovered_tender_by_bid(gem_bid_number: str):
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM gem_discovered_tenders WHERE gem_bid_number=%s", (gem_bid_number,))
        row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def upsert_discovered_tender(payload: dict, *, action_taken="DISCOVERED"):
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """INSERT INTO gem_discovered_tenders (
                   gem_bid_number, keyword_matched, raw_title, raw_organisation, raw_department,
                   raw_quantity, bid_start_date, bid_end_date, gem_pdf_url, raw_gem_data,
                   source, action_taken, last_checked_at
               ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)
               ON CONFLICT (gem_bid_number) DO UPDATE SET
                   keyword_matched=COALESCE(EXCLUDED.keyword_matched, gem_discovered_tenders.keyword_matched),
                   raw_title=COALESCE(EXCLUDED.raw_title, gem_discovered_tenders.raw_title),
                   raw_organisation=COALESCE(EXCLUDED.raw_organisation, gem_discovered_tenders.raw_organisation),
                   raw_department=COALESCE(EXCLUDED.raw_department, gem_discovered_tenders.raw_department),
                   raw_quantity=COALESCE(EXCLUDED.raw_quantity, gem_discovered_tenders.raw_quantity),
                   bid_start_date=COALESCE(EXCLUDED.bid_start_date, gem_discovered_tenders.bid_start_date),
                   bid_end_date=COALESCE(EXCLUDED.bid_end_date, gem_discovered_tenders.bid_end_date),
                   gem_pdf_url=COALESCE(EXCLUDED.gem_pdf_url, gem_discovered_tenders.gem_pdf_url),
                   raw_gem_data=COALESCE(EXCLUDED.raw_gem_data, gem_discovered_tenders.raw_gem_data),
                   source=COALESCE(EXCLUDED.source, gem_discovered_tenders.source),
                   last_checked_at=CURRENT_TIMESTAMP
               RETURNING *""",
            (
                payload.get("gemBidNumber"),
                payload.get("keywordMatched"),
                payload.get("title"),
                payload.get("organisation"),
                payload.get("department"),
                payload.get("quantity"),
                payload.get("bidStartDate"),
                payload.get("bidEndDate"),
                payload.get("gemPdfUrl"),
                psycopg2.extras.Json(payload.get("rawGemData") or {}),
                payload.get("source") or "LOCAL_GEM_AGENT",
                action_taken,
            ),
        )
        row = dict(cur.fetchone())
    conn.commit()
    conn.close()
    return row


def update_discovered_tender(gem_bid_number: str, **fields):
    if not fields:
        return get_discovered_tender_by_bid(gem_bid_number)
    json_fields = {"raw_gem_data", "extracted_data"}
    set_parts = []
    values = []
    for key, value in fields.items():
        set_parts.append(f"{key}=%s")
        values.append(psycopg2.extras.Json(value) if key in json_fields else value)
    set_parts.append("last_checked_at=CURRENT_TIMESTAMP")
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            f"UPDATE gem_discovered_tenders SET {', '.join(set_parts)} WHERE gem_bid_number=%s RETURNING *",
            values + [gem_bid_number],
        )
        row = cur.fetchone()
    conn.commit()
    conn.close()
    return dict(row) if row else None


def list_gem_discovered_tenders(keyword=None, action_taken=None, inserted=None, date_from=None, date_to=None):
    where = []
    values = []
    if keyword:
        where.append("LOWER(keyword_matched)=LOWER(%s)")
        values.append(keyword)
    if action_taken:
        where.append("action_taken=%s")
        values.append(action_taken)
    if inserted is not None:
        where.append("all_tender_id IS NOT NULL" if inserted else "all_tender_id IS NULL")
    if date_from:
        where.append("created_at::date >= %s")
        values.append(date_from)
    if date_to:
        where.append("created_at::date <= %s")
        values.append(date_to)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            f"""SELECT * FROM gem_discovered_tenders
                {where_sql}
                ORDER BY last_checked_at DESC NULLS LAST, created_at DESC
                LIMIT 500""",
            values,
        )
        rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_gem_search_dashboard_stats():
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT
                   COUNT(*) FILTER (WHERE created_at::date=CURRENT_DATE) AS discovered_today,
                   COUNT(*) FILTER (WHERE action_taken='INSERTED_TO_ALL_TENDERS' AND last_checked_at::date=CURRENT_DATE) AS inserted_today,
                   COUNT(*) FILTER (WHERE action_taken='REJECTED_NOT_SUITABLE' AND last_checked_at::date=CURRENT_DATE) AS rejected_today,
                   COUNT(*) FILTER (WHERE action_taken='INSERTED_TO_ALL_TENDERS' AND last_checked_at::date=CURRENT_DATE) AS suitable_today
               FROM gem_discovered_tenders"""
        )
        row = dict(cur.fetchone() or {})
    conn.close()
    return row


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


def create_gem_keyword(keyword: str) -> dict:
    """Idempotent: if the keyword already exists (case-insensitive), return it
    and make sure it's active, instead of raising a duplicate-key error."""
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT id, is_active FROM gem_keywords WHERE LOWER(keyword) = LOWER(%s)", (keyword,))
        existing = cur.fetchone()
        if existing:
            keyword_id, is_active = existing
            if not is_active:
                cur.execute(
                    "UPDATE gem_keywords SET is_active=TRUE, updated_at=CURRENT_TIMESTAMP WHERE id=%s",
                    (keyword_id,),
                )
            conn.commit()
            conn.close()
            return {"id": keyword_id, "already_existed": True}
        cur.execute("INSERT INTO gem_keywords (keyword) VALUES (%s) RETURNING id", (keyword,))
        keyword_id = cur.fetchone()[0]
    conn.commit()
    conn.close()
    return {"id": keyword_id, "already_existed": False}


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
                   current_step=COALESCE(current_step, 'RUNNING'),
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
            """INSERT INTO gem_scan_runs (scan_target_date, status, total_keywords, current_step, error_message, error_stack)
               VALUES (%s, 'RUNNING', %s, 'STARTED', NULL, NULL) RETURNING id""",
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
        cur.execute(
            """SELECT *,
                      CASE
                          WHEN finished_at IS NOT NULL AND started_at IS NOT NULL
                          THEN EXTRACT(EPOCH FROM (finished_at - started_at))
                          ELSE NULL
                      END AS duration_seconds
               FROM gem_scan_runs
               WHERE id=%s""",
            (run_id,),
        )
        row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def list_gem_scan_runs(limit: int = 50):
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT *,
                      CASE
                          WHEN finished_at IS NOT NULL AND started_at IS NOT NULL
                          THEN EXTRACT(EPOCH FROM (finished_at - started_at))
                          ELSE NULL
                      END AS duration_seconds
               FROM gem_scan_runs
               ORDER BY started_at DESC
               LIMIT %s""",
            (limit,),
        )
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

def upsert_gem_candidate(gem_bid_no: str, keyword: str, data: dict, scan_run_id: int | None = None) -> dict:
    """Insert a new candidate, or — if gem_bid_no already exists — merge the
    searched keyword into matched_keywords. For stale/unprocessed rows (no PDF
    saved yet), also refresh the latest GeM metadata/URLs so a re-scan can
    recover from earlier partial failures without disturbing already-processed
    tenders."""
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT id, status, tender_id, pdf_file_id, scan_status
               FROM gem_candidate_tenders
               WHERE gem_bid_no=%s
               LIMIT 1""",
            (gem_bid_no,),
        )
        existing = cur.fetchone()
        cur.execute(
            """INSERT INTO gem_candidate_tenders (
                   gem_bid_no, matched_keywords, title, organisation, department,
                   quantity, bid_start_date, bid_end_date, gem_detail_url, pdf_url, status, scan_run_id
               ) VALUES (%s, ARRAY[%s], %s, %s, %s, %s, %s, %s, %s, %s, 'QUEUED', %s)
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
                   extraction_status = CASE
                       WHEN gem_candidate_tenders.pdf_file_id IS NULL
                            AND gem_candidate_tenders.status IN ('FOUND', 'ERROR', 'QUEUED', 'REJECTED', 'REVIEW')
                       THEN NULL
                       ELSE gem_candidate_tenders.extraction_status
                   END,
                   extraction_confidence = CASE
                       WHEN gem_candidate_tenders.pdf_file_id IS NULL
                            AND gem_candidate_tenders.status IN ('FOUND', 'ERROR', 'QUEUED', 'REJECTED', 'REVIEW')
                       THEN NULL
                       ELSE gem_candidate_tenders.extraction_confidence
                   END,
                   extraction_error_message = CASE
                       WHEN gem_candidate_tenders.pdf_file_id IS NULL
                            AND gem_candidate_tenders.status IN ('FOUND', 'ERROR', 'QUEUED', 'REJECTED', 'REVIEW')
                       THEN NULL
                       ELSE gem_candidate_tenders.extraction_error_message
                   END,
                   evaluation_confidence = CASE
                       WHEN gem_candidate_tenders.pdf_file_id IS NULL
                            AND gem_candidate_tenders.status IN ('FOUND', 'ERROR', 'QUEUED', 'REJECTED', 'REVIEW')
                       THEN NULL
                       ELSE gem_candidate_tenders.evaluation_confidence
                   END,
                   decision_reason = CASE
                       WHEN gem_candidate_tenders.pdf_file_id IS NULL
                            AND gem_candidate_tenders.status IN ('FOUND', 'ERROR', 'QUEUED', 'REJECTED', 'REVIEW')
                       THEN NULL
                       ELSE gem_candidate_tenders.decision_reason
                   END,
                   review_reason = CASE
                       WHEN gem_candidate_tenders.pdf_file_id IS NULL
                            AND gem_candidate_tenders.status IN ('FOUND', 'ERROR', 'QUEUED', 'REJECTED', 'REVIEW')
                       THEN NULL
                       ELSE gem_candidate_tenders.review_reason
                   END,
                   rejection_reason = CASE
                       WHEN gem_candidate_tenders.pdf_file_id IS NULL
                            AND gem_candidate_tenders.status IN ('FOUND', 'ERROR', 'QUEUED', 'REJECTED', 'REVIEW')
                       THEN NULL
                       ELSE gem_candidate_tenders.rejection_reason
                   END,
                   matched_brands = CASE
                       WHEN gem_candidate_tenders.pdf_file_id IS NULL
                            AND gem_candidate_tenders.status IN ('FOUND', 'ERROR', 'QUEUED', 'REJECTED', 'REVIEW')
                       THEN '{}'
                       ELSE gem_candidate_tenders.matched_brands
                   END,
                   keyword_fit_score = CASE
                       WHEN gem_candidate_tenders.pdf_file_id IS NULL
                            AND gem_candidate_tenders.status IN ('FOUND', 'ERROR', 'QUEUED', 'REJECTED', 'REVIEW')
                       THEN NULL
                       ELSE gem_candidate_tenders.keyword_fit_score
                   END,
                   keyword_fit_decision = CASE
                       WHEN gem_candidate_tenders.pdf_file_id IS NULL
                            AND gem_candidate_tenders.status IN ('FOUND', 'ERROR', 'QUEUED', 'REJECTED', 'REVIEW')
                       THEN NULL
                       ELSE gem_candidate_tenders.keyword_fit_decision
                   END,
                   keyword_pre_score = CASE
                       WHEN gem_candidate_tenders.pdf_file_id IS NULL
                            AND gem_candidate_tenders.status IN ('FOUND', 'ERROR', 'QUEUED', 'REJECTED', 'REVIEW')
                       THEN NULL
                       ELSE gem_candidate_tenders.keyword_pre_score
                   END,
                   keyword_decision = CASE
                       WHEN gem_candidate_tenders.pdf_file_id IS NULL
                            AND gem_candidate_tenders.status IN ('FOUND', 'ERROR', 'QUEUED', 'REJECTED', 'REVIEW')
                       THEN NULL
                       ELSE gem_candidate_tenders.keyword_decision
                   END,
                   matched_products = CASE
                       WHEN gem_candidate_tenders.pdf_file_id IS NULL
                            AND gem_candidate_tenders.status IN ('FOUND', 'ERROR', 'QUEUED', 'REJECTED', 'REVIEW')
                       THEN '{}'
                       ELSE gem_candidate_tenders.matched_products
                   END,
                   matched_product_keywords = CASE
                       WHEN gem_candidate_tenders.pdf_file_id IS NULL
                            AND gem_candidate_tenders.status IN ('FOUND', 'ERROR', 'QUEUED', 'REJECTED', 'REVIEW')
                       THEN '{}'
                       ELSE gem_candidate_tenders.matched_product_keywords
                   END,
                   negative_keywords = CASE
                       WHEN gem_candidate_tenders.pdf_file_id IS NULL
                            AND gem_candidate_tenders.status IN ('FOUND', 'ERROR', 'QUEUED', 'REJECTED', 'REVIEW')
                       THEN '{}'
                       ELSE gem_candidate_tenders.negative_keywords
                   END,
                   negative_keywords_found = CASE
                       WHEN gem_candidate_tenders.pdf_file_id IS NULL
                            AND gem_candidate_tenders.status IN ('FOUND', 'ERROR', 'QUEUED', 'REJECTED', 'REVIEW')
                       THEN '{}'
                       ELSE gem_candidate_tenders.negative_keywords_found
                   END,
                   keyword_context_type = CASE
                       WHEN gem_candidate_tenders.pdf_file_id IS NULL
                            AND gem_candidate_tenders.status IN ('FOUND', 'ERROR', 'QUEUED', 'REJECTED', 'REVIEW')
                       THEN NULL
                       ELSE gem_candidate_tenders.keyword_context_type
                   END,
                   keyword_evaluation_reason = CASE
                       WHEN gem_candidate_tenders.pdf_file_id IS NULL
                            AND gem_candidate_tenders.status IN ('FOUND', 'ERROR', 'QUEUED', 'REJECTED', 'REVIEW')
                       THEN NULL
                       ELSE gem_candidate_tenders.keyword_evaluation_reason
                   END,
                   keyword_fit_reason = CASE
                       WHEN gem_candidate_tenders.pdf_file_id IS NULL
                            AND gem_candidate_tenders.status IN ('FOUND', 'ERROR', 'QUEUED', 'REJECTED', 'REVIEW')
                       THEN NULL
                       ELSE gem_candidate_tenders.keyword_fit_reason
                   END,
                   evaluation_stage = CASE
                       WHEN gem_candidate_tenders.pdf_file_id IS NULL
                            AND gem_candidate_tenders.status IN ('FOUND', 'ERROR', 'QUEUED', 'REJECTED', 'REVIEW')
                       THEN NULL
                       ELSE gem_candidate_tenders.evaluation_stage
                   END,
                   requires_full_evaluation = CASE
                       WHEN gem_candidate_tenders.pdf_file_id IS NULL
                            AND gem_candidate_tenders.status IN ('FOUND', 'ERROR', 'QUEUED', 'REJECTED', 'REVIEW')
                       THEN NULL
                       ELSE gem_candidate_tenders.requires_full_evaluation
                   END,
                    evaluation_reason = CASE
                        WHEN gem_candidate_tenders.pdf_file_id IS NULL
                             AND gem_candidate_tenders.status IN ('FOUND', 'ERROR', 'QUEUED')
                        THEN NULL
                        ELSE gem_candidate_tenders.evaluation_reason
                    END,
                    scan_run_id = COALESCE(EXCLUDED.scan_run_id, gem_candidate_tenders.scan_run_id),
                    updated_at = CURRENT_TIMESTAMP
                RETURNING id""",
            (
                gem_bid_no, keyword, data.get("title"), data.get("organisation"), data.get("department"),
                data.get("quantity"), data.get("bid_start_date"), data.get("bid_end_date"),
                data.get("gem_detail_url"), data.get("pdf_url"), scan_run_id,
            ),
        )
        candidate_id = cur.fetchone()["id"]
    conn.commit()
    conn.close()
    return {
        "id": candidate_id,
        "is_new": existing is None,
        "is_duplicate": existing is not None,
        "existing_status": existing.get("status") if existing else None,
        "existing_tender_id": existing.get("tender_id") if existing else None,
        "existing_pdf_file_id": existing.get("pdf_file_id") if existing else None,
        "existing_scan_status": existing.get("scan_status") if existing else None,
    }


def get_gem_candidate(candidate_id: int):
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM gem_candidate_tenders WHERE id=%s", (candidate_id,))
        row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def list_gem_candidates(status: str = None, scan_run_id: int = None):
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if status and scan_run_id is not None:
            cur.execute(
                """SELECT * FROM gem_candidate_tenders
                   WHERE status=%s AND scan_run_id=%s
                   ORDER BY created_at DESC NULLS LAST, bid_start_date DESC NULLS LAST""",
                (status, scan_run_id),
            )
        elif status:
            cur.execute(
                """SELECT * FROM gem_candidate_tenders
                   WHERE status=%s
                   ORDER BY created_at DESC NULLS LAST, bid_start_date DESC NULLS LAST""",
                (status,),
            )
        elif scan_run_id is not None:
            cur.execute(
                """SELECT * FROM gem_candidate_tenders
                   WHERE scan_run_id=%s
                   ORDER BY created_at DESC NULLS LAST, bid_start_date DESC NULLS LAST""",
                (scan_run_id,),
            )
        else:
            cur.execute(
                """SELECT * FROM gem_candidate_tenders
                   ORDER BY created_at DESC NULLS LAST, bid_start_date DESC NULLS LAST"""
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


def set_gem_candidate_status(candidate_id: int, status: str, evaluation_reason: str | None = None):
    conn = get_db()
    with conn.cursor() as cur:
        if evaluation_reason is None:
            cur.execute(
                "UPDATE gem_candidate_tenders SET status=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s",
                (status, candidate_id),
            )
        else:
            review_reason = evaluation_reason if status == "REVIEW" else None
            rejection_reason = evaluation_reason if status == "REJECTED" else None
            cur.execute(
                """UPDATE gem_candidate_tenders
                   SET status=%s,
                       evaluation_reason=%s,
                       decision_reason=%s,
                       review_reason=%s,
                       rejection_reason=%s,
                       updated_at=CURRENT_TIMESTAMP
                   WHERE id=%s""",
                (status, evaluation_reason, evaluation_reason, review_reason, rejection_reason, candidate_id),
            )
    conn.commit()
    conn.close()


def delete_gem_candidate(candidate_id: int) -> bool:
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM gem_candidate_tenders WHERE id=%s", (candidate_id,))
        deleted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


# ── GeM Tender Watcher: Evaluations ─────────────────────────────────────────────

def save_gem_tender_evaluation(candidate_id: int, score, rating_label, matched_brands,
                                eligibility_status, rejection_reason, evaluation_json,
                                matched_products=None, negative_keywords=None,
                                keyword_fit_score=None, keyword_fit_decision=None,
                                keyword_fit_reason=None, evaluation_stage=None) -> int:
    matched_brands_text = ", ".join(matched_brands or []) if isinstance(matched_brands, (list, tuple)) else matched_brands
    matched_products_text = ", ".join(matched_products or []) if isinstance(matched_products, (list, tuple)) else matched_products
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO tender_evaluations (
                   candidate_id, score, rating_label, matched_brands, matched_products,
                   negative_keywords, keyword_fit_score, keyword_fit_decision,
                   keyword_fit_reason, evaluation_stage, eligibility_status,
                   rejection_reason, evaluation_json
               ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (
                candidate_id, score, rating_label, matched_brands_text,
                matched_products_text,
                negative_keywords or [],
                keyword_fit_score,
                keyword_fit_decision,
                keyword_fit_reason,
                evaluation_stage,
                eligibility_status, rejection_reason, psycopg2.extras.Json(evaluation_json),
            ),
        )
        eval_id = cur.fetchone()[0]
    conn.commit()
    conn.close()
    return eval_id
