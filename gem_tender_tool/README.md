# GeM Tender Automation Tool (working prototype)

Turns a GeM bid PDF into a folder of ready-to-upload documents.

## Pipeline

```
tender.pdf ──► extract.py ──► structured fields + required-doc list
                                   │
                                   ▼
                            pipeline.py  ◄── company_profile.json
                                   │              doc_database/manifest.json
                ┌──────────────────┼───────────────────┐
                ▼                  ▼                   ▼
          static doc          template            manual / missing
          (copy as-is)     (auto-fill {{...}})   (added to checklist)
                └──────────────────┼───────────────────┘
                                   ▼
                    output/<BID_NO>/  (docs + checklist.md + summary.json)
```

## Files
- `extract.py` — parses the GeM PDF. Reads the bilingual key/value tables (keys on
  the English half of each label), the per-schedule item table, EMD amounts, and a
  rules engine that derives required documents from both the "Document required from
  seller" field and the buyer-added ATC clauses.
- `company_profile.json` — your bidder details, used to fill forms.
- `doc_database/manifest.json` — maps each `doc_key` to a source:
  `static` (copy a real file), `template` (fill placeholders), or `manual` (flag it).
- `doc_database/templates/*.docx` — fillable forms with `{{placeholder}}` tokens.
- `doc_database/static/*` — your real certificates, catalogues, datasheets.
- `fill_forms.py` — fills `{{...}}` tokens in docx (handles tokens split across runs).
- `pipeline.py` — orchestrates everything and writes the output folder.

## Run
```bash
python3 pipeline.py /path/to/GeM-Bidding-XXXX.pdf
```
Output lands in `output/<BID_NUMBER>/` with the produced docs, a `checklist.md`
(auto-produced / still-needed / unmatched), and `tender_summary.json`.

## How to extend
1. **Add a document to your database**: drop the file in `doc_database/static/` (or a
   template in `templates/`) and add an entry in `manifest.json` keyed by the `doc_key`
   the extractor emits. Run the extractor on a few tenders to see the keys it produces.
2. **Add a new required-doc rule**: append a row to `ATC_DOC_RULES` in `extract.py`
   (regex, doc_key, label, fillable?).
3. **Alias overlapping keys**: the "Document required" field and ATC clauses sometimes
   name the same doc with different keys (e.g. `oem_authorization_certificate` vs
   `oem_authorization`). Add an alias map so they resolve to one database entry.
4. **More form fields**: add `{{tokens}}` to templates; supply values in
   `company_profile.json` or in `fill_forms.build_context`.

## Production hardening (next steps)
- Replace JSON files with a small DB (SQLite/Postgres) for the document library and a
  tenders table; store one company profile per legal entity.
- Some buyer specs / BoQ / excel templates are *attachments* inside the bid (the
  "View File" links). Those are separate downloads from GeM, not in this PDF — fetch
  them via the GeM portal and feed paths into the manifest.
- Add a review UI before zipping (the manual items always need a human).
- PDF parsing is layout-dependent; keep a few sample bids as regression tests.

## GeM browser autofill

For production-friendly GeM search handoff, use the unpacked browser extension in
`chrome_extension/gem-bidplus-autofill/`.

The tender app opens GeM with a URL hash that carries the bid number, and the
extension reads that value in the user's browser to:

- switch to `Bid/RA Status`
- set `Exact Search`
- write the GeM bid number
- trigger search
