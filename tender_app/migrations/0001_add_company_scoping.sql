-- =============================================================================
-- Migration 0001: Fidus360 company scoping (Phase 1)
-- =============================================================================
-- Purpose:
--   Make every tender / file / keyword / job / notification / agent belong to a
--   companyId, while keeping standalone mode working exactly as before by
--   assigning all existing data to a single "Default Company".
--
-- IMPORTANT:
--   * This file documents the schema change for review. It is idempotent and
--     mirrors what tender_app/database.py::_apply_company_scoping_migration()
--     applies at startup (called from init_db() against DATABASE_URL).
--   * In database.py every statement below that touches a PRE-EXISTING table is
--     additionally guarded by an existence check (to_regclass) and a SAVEPOINT,
--     so a missing optional table or an unexpected error is skipped with a log
--     line and can never abort init_db() or poison the transaction. This plain
--     SQL version has no such guards — run it only where the tables exist.
--   * DO NOT run this against the production Railway database by hand. Apply it
--     only to a local/staging copy. Production picks the change up on deploy via
--     init_db(), which is safe to re-run (every statement is IF NOT EXISTS /
--     backfill-then-constrain). No production migration runs on its own.
--
-- Postgres dialect (Railway Postgres, psycopg2).
-- =============================================================================

BEGIN;

-- 1. Companies registry + the single default company -------------------------
CREATE TABLE IF NOT EXISTS companies (
    id         SERIAL PRIMARY KEY,
    name       TEXT NOT NULL,
    slug       TEXT NOT NULL UNIQUE,
    status     TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO companies (name, slug, status)
VALUES ('Default Company', 'default-company', 'ACTIVE')
ON CONFLICT (slug) DO NOTHING;

-- 2. Per-company local GeM agents --------------------------------------------
--    The agent authenticates with a bearer token; the backend hashes it,
--    finds the matching row here, and derives companyId from it. The agent
--    never sends companyId itself.
CREATE TABLE IF NOT EXISTS local_agents (
    id                SERIAL PRIMARY KEY,
    company_id        INTEGER NOT NULL REFERENCES companies(id),
    agent_name        TEXT,
    token_hash        TEXT NOT NULL UNIQUE,
    status            TEXT NOT NULL DEFAULT 'ACTIVE',
    last_heartbeat_at TIMESTAMP,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS local_agents_company_idx ON local_agents (company_id);

-- 3. Add company_id to scoped tables, backfill to the default company, then
--    default + NOT NULL so any code path that does not pass a company_id keeps
--    behaving exactly like today.
--
--    NOTE: `:default_company_id` below stands for
--          (SELECT id FROM companies WHERE slug = 'default-company').
--          database.py resolves it to the concrete integer before issuing the
--          ALTER ... SET DEFAULT statements (SET DEFAULT requires a literal).
--
--    Root tables (assigned directly to the default company):
--      tenders, uploaded_files, gem_search_keywords,
--      gem_discovered_tenders, gem_candidate_tenders, gem_scan_runs,
--      gem_run_requests, company_profile, company_capability_profile
--
--    NOTE: gem_keywords is deliberately NOT scoped. It is a GLOBAL master/seed
--    list of suggested keywords (globally unique on `keyword`, seeded with
--    defaults) and keeps its ON CONFLICT (keyword) upsert. The per-company
--    saved keyword table is gem_search_keywords (scoped below).
--
--    Example for one root table (repeat for each):
--      ALTER TABLE tenders ADD COLUMN IF NOT EXISTS company_id INTEGER REFERENCES companies(id);
--      UPDATE tenders SET company_id = :default_company_id WHERE company_id IS NULL;
--      ALTER TABLE tenders ALTER COLUMN company_id SET DEFAULT :default_company_id;
--      ALTER TABLE tenders ALTER COLUMN company_id SET NOT NULL;
--      CREATE INDEX IF NOT EXISTS tenders_company_idx ON tenders (company_id);

-- (gem_keywords is intentionally omitted here — it is a global master/seed table.)
ALTER TABLE tenders                    ADD COLUMN IF NOT EXISTS company_id INTEGER REFERENCES companies(id);
ALTER TABLE uploaded_files             ADD COLUMN IF NOT EXISTS company_id INTEGER REFERENCES companies(id);
ALTER TABLE gem_search_keywords        ADD COLUMN IF NOT EXISTS company_id INTEGER REFERENCES companies(id);
ALTER TABLE gem_discovered_tenders     ADD COLUMN IF NOT EXISTS company_id INTEGER REFERENCES companies(id);
ALTER TABLE gem_candidate_tenders      ADD COLUMN IF NOT EXISTS company_id INTEGER REFERENCES companies(id);
ALTER TABLE gem_scan_runs              ADD COLUMN IF NOT EXISTS company_id INTEGER REFERENCES companies(id);
ALTER TABLE gem_run_requests           ADD COLUMN IF NOT EXISTS company_id INTEGER REFERENCES companies(id);
ALTER TABLE company_profile            ADD COLUMN IF NOT EXISTS company_id INTEGER REFERENCES companies(id);
ALTER TABLE company_capability_profile ADD COLUMN IF NOT EXISTS company_id INTEGER REFERENCES companies(id);

-- Backfill every existing root row to the default company.
UPDATE tenders                    t SET company_id = c.id FROM companies c WHERE c.slug='default-company' AND t.company_id IS NULL;
UPDATE uploaded_files             t SET company_id = c.id FROM companies c WHERE c.slug='default-company' AND t.company_id IS NULL;
UPDATE gem_search_keywords        t SET company_id = c.id FROM companies c WHERE c.slug='default-company' AND t.company_id IS NULL;
UPDATE gem_discovered_tenders     t SET company_id = c.id FROM companies c WHERE c.slug='default-company' AND t.company_id IS NULL;
UPDATE gem_candidate_tenders      t SET company_id = c.id FROM companies c WHERE c.slug='default-company' AND t.company_id IS NULL;
UPDATE gem_scan_runs              t SET company_id = c.id FROM companies c WHERE c.slug='default-company' AND t.company_id IS NULL;
UPDATE gem_run_requests           t SET company_id = c.id FROM companies c WHERE c.slug='default-company' AND t.company_id IS NULL;
UPDATE company_profile            t SET company_id = c.id FROM companies c WHERE c.slug='default-company' AND t.company_id IS NULL;
UPDATE company_capability_profile t SET company_id = c.id FROM companies c WHERE c.slug='default-company' AND t.company_id IS NULL;

-- Child tables inherit company_id from their parent (fallback = default company):
--   tender_evaluations       -> gem_candidate_tenders(candidate_id)
--   gem_result_check_history -> tenders(tender_id)
--   tender_notifications     -> tenders(tender_id)
ALTER TABLE tender_evaluations       ADD COLUMN IF NOT EXISTS company_id INTEGER REFERENCES companies(id);
ALTER TABLE gem_result_check_history ADD COLUMN IF NOT EXISTS company_id INTEGER REFERENCES companies(id);
ALTER TABLE tender_notifications     ADD COLUMN IF NOT EXISTS company_id INTEGER REFERENCES companies(id);

UPDATE tender_evaluations e
   SET company_id = COALESCE((SELECT p.company_id FROM gem_candidate_tenders p WHERE p.id = e.candidate_id),
                             (SELECT id FROM companies WHERE slug='default-company'))
 WHERE e.company_id IS NULL;
UPDATE gem_result_check_history h
   SET company_id = COALESCE((SELECT p.company_id FROM tenders p WHERE p.id = h.tender_id),
                             (SELECT id FROM companies WHERE slug='default-company'))
 WHERE h.company_id IS NULL;
UPDATE tender_notifications n
   SET company_id = COALESCE((SELECT p.company_id FROM tenders p WHERE p.id = n.tender_id),
                             (SELECT id FROM companies WHERE slug='default-company'))
 WHERE n.company_id IS NULL;

-- After backfill, database.py issues (per table):
--   ALTER TABLE <t> ALTER COLUMN company_id SET DEFAULT <default_company_id literal>;
--   ALTER TABLE <t> ALTER COLUMN company_id SET NOT NULL;
--   CREATE INDEX IF NOT EXISTS <t>_company_idx ON <t> (company_id);

-- 4. Duplicate checks become company-wise ------------------------------------
--    The same GeM bid / keyword may exist for different companies. Replace the
--    old global UNIQUE keys with (company_id, ...) composite unique indexes
--    (these back the ON CONFLICT (company_id, ...) upserts in database.py).
CREATE UNIQUE INDEX IF NOT EXISTS gem_search_keywords_company_keyword_key ON gem_search_keywords (company_id, keyword);
ALTER TABLE gem_search_keywords    DROP CONSTRAINT IF EXISTS gem_search_keywords_keyword_key;

CREATE UNIQUE INDEX IF NOT EXISTS gem_discovered_tenders_company_bid_key  ON gem_discovered_tenders (company_id, gem_bid_number);
ALTER TABLE gem_discovered_tenders DROP CONSTRAINT IF EXISTS gem_discovered_tenders_gem_bid_number_key;

CREATE UNIQUE INDEX IF NOT EXISTS gem_candidate_tenders_company_bid_key   ON gem_candidate_tenders (company_id, gem_bid_no);
ALTER TABLE gem_candidate_tenders  DROP CONSTRAINT IF EXISTS gem_candidate_tenders_gem_bid_no_key;

COMMIT;

-- Rollback note (manual, staging only): drop the columns/indexes/tables above.
-- Since the change is additive with a default, the app also runs unchanged if
-- the columns are simply left in place.
