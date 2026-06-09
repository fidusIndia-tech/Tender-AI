"""
extract.py — Parse a GeM bid PDF into structured data.

GeM bid PDFs are laid out as 2-column [label, value] tables. The Hindi half of
each label is often garbled (font/cid encoding), but the English half after the
"/" and all values extract cleanly, so we key on the English label fragment.

Output: a dict with header fields, EMD schedule, evaluation schedules,
and a normalised list of required documents.
"""

import re
import json
import pdfplumber


# ---- label -> canonical field name -------------------------------------------------
# we match on a lowercased substring of the English part of each row's label cell.
FIELD_LABELS = {
    "bid end date":            "bid_end_datetime",
    "bid opening":             "bid_opening_datetime",
    "bid offer":               "bid_validity",
    "state name":              "ministry",
    "department name":         "department",
    "organisation name":       "organisation",
    "office name":             "office",
    "total quantity":          "total_quantity",
    "item category":           "item_category",
    "boq title":               "boq_title",
    "type of bid":             "bid_type",
    "primary product category":"primary_product",
    "evaluation method":       "evaluation_method",
    "document required from seller": "documents_required_raw",
    "epbg percentage":         "epbg_percentage",
    "duration of epbg":        "epbg_duration_months",
}


def _clean(text: str) -> str:
    """Strip cid garbage and collapse whitespace."""
    if text is None:
        return ""
    text = re.sub(r"\(cid:\d+\)", "", text)
    text = text.replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip()


def _english_label(label: str) -> str:
    """Return the lowercased English portion of a bilingual label.

    The label is '<hindi>/<english>'; English can itself contain '/'
    (e.g. 'Date/Time'), so we take everything from the first ASCII letter."""
    label = _clean(label)
    m = re.search(r"[A-Za-z].*$", label)
    return (m.group(0) if m else label).lower().strip()


def extract_rows(pdf):
    """Yield every (label, value) pair from all key/value tables in the PDF."""
    for page in pdf.pages:
        for table in page.extract_tables() or []:
            for row in table:
                if not row or len(row) < 2:
                    continue
                label, value = row[0], row[1]
                if label and value:
                    yield label, value


def extract_bid_number(pdf):
    """Bid number + dated appear in running text, not the table."""
    text = _clean(pdf.pages[0].extract_text() or "")
    num = re.search(r"GEM/\d{4}/B/\d+", text)
    dated = re.search(r"Dated:\s*([\d-]+)", text)
    return (num.group(0) if num else None,
            dated.group(1) if dated else None)


def extract_schedules(pdf):
    """Pull the per-schedule item/quantity table from the evaluation section."""
    schedules = []
    for page in pdf.pages:
        for table in page.extract_tables() or []:
            for row in table:
                if not row:
                    continue
                first = _clean(row[0])
                m = re.match(r"Schedule\s+(\d+)$", first)
                if m and len(row) >= 3:
                    schedules.append({
                        "schedule": int(m.group(1)),
                        "item": _clean(row[1]),
                        "quantity": _clean(row[2]),
                    })
    return schedules


def extract_emd(pdf):
    """EMD amounts per schedule + ePBG details."""
    emd = {}
    for label, value in extract_rows(pdf):
        lbl = _clean(label).lower()
        if "emd amount" in lbl:
            m = re.search(r"schedule\s+(\d+)", lbl)
            if m:
                emd[f"schedule_{m.group(1)}"] = _clean(value)
    return emd


# ---- required-documents rules engine -----------------------------------------------
# GeM tenders express doc requirements in two places:
#   (1) the "Document required from seller" field (comma list)
#   (2) buyer-added ATC clauses in the free text (clause 2.x etc.)
# We normalise both into a checklist with a stable doc_key the database can match on.

ATC_DOC_RULES = [
    # (regex searched in full text, doc_key, human label, fillable form?)
    (r"OEM Authorization",                 "oem_authorization",      "OEM Authorization Certificate", False),
    (r"Annexure-?II",                      "one_bid_declaration",    "One Bid Per Bidder declaration (Annexure-II)", True),
    (r"One bid per bidder",                "one_bid_declaration",    "One Bid Per Bidder declaration (Annexure-II)", True),
    (r"Pre-?Qualification Criteria",       "pqc_document",           "Pre-Qualification Criteria proof (auth cert OR PO+COC)", False),
    (r"Catalogues?\s*/\s*Data\s*Sheets",   "technical_datasheet",    "Technical catalogues / datasheets / manuals", False),
    (r"affix bidder.?s signature",         "signed_buyer_spec",      "Buyer specification signed & stamped", False),
    (r"Udyam",                             "mse_declaration",        "MSE: Udyam cert + manufacturer declaration (if claiming)", True),
    (r"Compliance of BoQ",                 "boq_compliance",         "Compliance of BoQ specification + supporting docs", True),
    (r"Compliance to Bo[qQ]",              "boq_compliance",         "Compliance of BoQ specification + supporting docs", True),
    (r"Warranty",                          "warranty_certificate",   "Warranty certificate (at delivery)", False),
    (r"HSN code and GST",                  "hsn_gst_excel",          "HSN code & GST % excel upload", True),
    (r"Annexure-?F",                       "annexure_f",             "Annexure-F payment document", True),
]


def extract_required_docs(pdf):
    full_text = " ".join(_clean(p.extract_text() or "") for p in pdf.pages)
    docs = {}

    # (1) explicit "Document required from seller" field
    for label, value in extract_rows(pdf):
        if "document required from seller" in _english_label(label):
            field_val = _clean(value).split("*")[0]  # drop trailing exemption note
            for part in re.split(r",(?![^()]*\))", field_val):
                part = part.strip()
                if part:
                    key = re.sub(r"[^a-z0-9]+", "_", part.lower()).strip("_")
                    docs[key] = {"doc_key": key, "label": part,
                                 "source": "Document required field", "fillable": None}

    # (2) ATC clause rules
    for pattern, key, label, fillable in ATC_DOC_RULES:
        if re.search(pattern, full_text, re.IGNORECASE):
            docs[key] = {"doc_key": key, "label": label,
                         "source": "ATC clause", "fillable": fillable}

    return list(docs.values())


def parse_tender(pdf_path):
    result = {"source_pdf": pdf_path}
    with pdfplumber.open(pdf_path) as pdf:
        # header fields
        for label, value in extract_rows(pdf):
            eng = _english_label(label)
            for needle, field in FIELD_LABELS.items():
                if needle in eng and field not in result:
                    result[field] = _clean(value)

        num, dated = extract_bid_number(pdf)
        result["bid_number"] = num
        result["bid_dated"] = dated
        result["schedules"] = extract_schedules(pdf)
        result["emd"] = extract_emd(pdf)
        result["required_documents"] = extract_required_docs(pdf)
    return result


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/uploads/GeM-Bidding-9318524_IFM.pdf"
    data = parse_tender(path)
    print(json.dumps(data, indent=2, ensure_ascii=False))
