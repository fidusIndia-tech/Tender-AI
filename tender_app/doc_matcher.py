"""
doc_matcher.py — Classify tender required documents into four categories:
  ai_generated      — AI can generate a declaration/letter automatically
  company_library   — Matched from the company document library
  oem_required      — Must be obtained from OEM / Manufacturer
  library_required  — Government-issued cert; must be uploaded from company records
  missing           — Other documents requiring manual upload
"""

from difflib import SequenceMatcher

# ── Category keyword map for library matching ──────────────────────────────
CATEGORY_KEYWORDS = {
    "GST Certificate":   ["gst", "gstin", "goods and service"],
    "PAN Certificate":   ["pan", "permanent account"],
    "MSME Certificate":  ["msme", "udyam", "micro small"],
    "ITR":               ["itr", "income tax return"],
    "Cancelled Cheque":  ["cancelled cheque", "cancel cheque", "cheque"],
    "Bank Document":     ["bank details", "bank document", "bank account"],
    "Aadhaar Card":      ["aadhaar", "aadhar", "kyc", "identity proof"],
    "OEM Authorization": ["oem authorization", "oem certificate",
                          "manufacturer authorization", "authorization certificate",
                          "oem auth", "make authorization", "dealer authorization"],
    "Catalogue":         ["catalogue", "catalog", "datasheet", "data sheet",
                          "brochure", "manual", "technical manual"],
    "Trade License":     ["trade license"],
}

# ── AI-generatable: declarations, letters, undertakings ──────────────────────
AI_GENERATABLE = [
    # Generic declaration/undertaking types
    "declaration", "undertaking", "self declaration", "self-declaration",
    "bid declaration", "bidder declaration", "manufacturer declaration",
    "seller declaration", "msme declaration", "make in india declaration",
    "make in india", "bank details declaration",
    "authorized signatory declaration", "authorised signatory declaration",
    # Financial / technical capacity declarations
    "experience criteria", "experience declaration",
    "past performance", "performance declaration",
    "bidder turnover", "turnover declaration", "annual turnover declaration",
    "financial capacity", "technical capacity", "bid capacity",
    # Standard bid documents
    "cover letter", "bid submission letter", "bid letter",
    "no deviation", "no deviation certificate",
    "compliance sheet", "compliance statement",
    "company information sheet",
    "acceptance of tender", "acceptance of terms",
    "technical compliance", "technical compliance statement",
    "one bid", "one bid per bidder",
    "integrity pact", "non-blacklisting", "non blacklisting",
    "certificate of compliance",
    "declaration as per clause 29", "clause 29", "gem gtc",
    "atc compliance", "atc declaration",
]

# Subset: AI generates declaration only — supporting proof still needed
DECLARATION_WITH_PROOF_REQUIRED = [
    "experience criteria", "past performance", "bidder turnover",
    "annual turnover", "financial capacity", "technical capacity",
    "performance declaration", "experience declaration",
]

# ── OEM / Manufacturer-sourced (cannot generate) ──────────────────────────
OEM_REQUIRED = [
    "oem authorization", "oem certificate", "oem auth",
    "manufacturer authorization", "authorization certificate",
    "make authorization", "dealer authorization",
    "oem annual turnover", "oem turnover", "manufacturer turnover",
    "oem financial", "authorized dealer",
]

# ── Government-issued / company records (upload from library) ─────────────
LIBRARY_REQUIRED = [
    "gst certificate", "gstin", "gst registration",
    "pan card", "pan certificate",
    "msme certificate", "udyam certificate", "udyam registration",
    "cancelled cheque", "cancel cheque",
    "income tax return", "itr",
    "aadhaar", "aadhar",
    "trade license",
    "incorporation certificate", "registration certificate",
]

# ── Third-party / external documents ─────────────────────────────────────
THIRD_PARTY_REQUIRED = [
    "purchase order", "work order",
    "completion certificate", "experience certificate", "performance certificate",
    "datasheet", "data sheet",
    "catalogue", "catalog", "brochure",
    "technical manual", "manual",
]


def _fuzzy(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _find_library_match(label: str, company_documents: list, financial_year: str = None):
    lower = label.lower()
    for doc in company_documents:
        category = (doc.get("category") or "").strip()
        doc_name  = (doc.get("document_name") or "").lower()
        tags      = (doc.get("tags") or "").lower()

        kws = CATEGORY_KEYWORDS.get(category, [])
        if kws and any(kw in lower for kw in kws):
            if category == "ITR" and financial_year:
                doc_fy = (doc.get("financial_year") or "")
                if financial_year not in doc_fy and doc_fy not in financial_year:
                    continue
            return doc

        if _fuzzy(lower, doc_name) >= 0.65:
            return doc

        if tags:
            for tag in tags.split(","):
                if tag.strip() and tag.strip() in lower:
                    return doc
    return None


def _classify(label: str):
    """
    Returns (source_type, status, recommended_action, remarks)
    source_type: ai_generated | oem_required | library_required | missing
    status:      can_generate | missing
    """
    lower = label.lower()

    # 1. OEM-required
    if any(kw in lower for kw in OEM_REQUIRED):
        action = "Request from OEM / Manufacturer"
        if "turnover" in lower or "financial" in lower:
            action = "Request Annual Turnover Certificate from OEM"
        return "oem_required", "missing", action, None

    # 2. Government-issued library docs
    if any(kw in lower for kw in LIBRARY_REQUIRED):
        return "library_required", "missing", "Upload from Company Library", None

    # 3. Third-party docs
    if any(kw in lower for kw in THIRD_PARTY_REQUIRED):
        if any(kw in lower for kw in ["datasheet","data sheet","catalogue","catalog","brochure","manual"]):
            return "missing", "missing", "Download from manufacturer website", None
        if any(kw in lower for kw in ["purchase order","work order"]):
            return "missing", "missing", "Attach copy of relevant Purchase Order", None
        if any(kw in lower for kw in ["completion certificate","experience certificate","performance certificate"]):
            return "missing", "missing", "Obtain from end customer / client", None
        return "missing", "missing", "Upload manually", None

    # 4. AI-generatable
    if any(kw in lower for kw in AI_GENERATABLE):
        needs_proof = any(kw in lower for kw in DECLARATION_WITH_PROOF_REQUIRED)
        remarks = (
            "AI will generate a declaration — upload supporting proof (PO / CA certificate) separately"
            if needs_proof else None
        )
        return "ai_generated", "can_generate", "Generate automatically", remarks

    # 5. Fallback
    return "missing", "missing", "Upload manually", None


def match_all_documents(required_docs: list, company_documents: list, financial_year: str = None) -> list:
    """
    For each required document label, determine source_type, status, recommended_action.
    Company library match always takes priority over AI generation.
    Returns list of dicts ready for tender_prepared_documents table.
    """
    results = []
    for doc in required_docs:
        label = (doc.get("label") or "").strip()
        if not label:
            continue

        result = {
            "required_document_label": label,
            "document_name": label,
            "source_type": None,
            "source_document_id": None,
            "generated_file_path": None,
            "status": None,
            "remarks": None,
            "recommended_action": None,
        }

        # Priority 1: check company library
        match = _find_library_match(label, company_documents, financial_year)
        if match:
            result.update({
                "source_type": "company_library",
                "source_document_id": match["id"],
                "document_name": match["document_name"],
                "status": "matched",
                "recommended_action": "View",
            })
        else:
            source_type, status, recommended_action, remarks = _classify(label)
            result.update({
                "source_type": source_type,
                "status": status,
                "recommended_action": recommended_action,
                "remarks": remarks,
            })

        results.append(result)
    return results
