import json
import os
import re
import importlib.util
from pathlib import Path
import pdfplumber
from openai import OpenAI

# Pages are included only if they contain at least one of these keywords.
# This skips generic T&C, banning clauses, and legal boilerplate pages.
RELEVANT_KEYWORDS = [
    "bid number", "dated", "bid submission end", "bid end date",
    "bid opening", "department name", "organisation name", "organization name",
    "office name", "total quantity", "documents required from seller",
    "buyer added", "evaluation schedule", "boq", "bill of quantit",
    "item category", "pre-qualification", "pre qualification",
    "required document", "technical document", "atc", "quantity",
    "sl.", "s.no", "sr.no", "item description",
]

MAX_CHUNK_CHARS = 30_000  # ~7 500 tokens — well within gpt-4o limits
CRITICAL_TENDER_FIELDS = (
    "tender_number",
    "date",
    "bid_end_datetime",
    "bid_opening_datetime",
    "department_name",
    "organization_name",
    "office_name_location",
    "total_quantity",
)
MIN_CRITICAL_FIELDS_FOR_LOCAL_SUCCESS = int(os.environ.get("AI_EXTRACT_MIN_CRITICAL_FIELDS", "5"))
_GEM_EXTRACTOR_MODULE = None

EXTRACTION_PROMPT = """You are a tender data extraction system for Indian Government procurement (GeM — Government e-Marketplace) bids.

The input below is a subset of pages from a GeM tender PDF (text + table rows formatted as: cell1 | cell2).
Extract every field you can find in THIS chunk. Return null for fields not present in this chunk.

Return ONLY a valid JSON object. No explanation, no markdown fences, no preamble.

Required JSON structure:
{
  "tender_information": {
    "tender_number": "string or null",
    "date": "string or null",
    "bid_end_datetime": "string or null",
    "bid_opening_datetime": "string or null",
    "department_name": "string or null",
    "organization_name": "string or null",
    "office_name_location": "string or null",
    "total_quantity": "string or null",
    "make": "string or null",
    "tender_approx_value": "string or null"
  },
  "items": [
    {
      "part_number": "string or null",
      "item_description": "string or null",
      "quantity": "string or null"
    }
  ],
  "required_documents": [
    "Exact document name as written in the tender"
  ]
}

Field extraction rules — scan table rows (label | value) for these:
- tender_number: label "Bid Number" → value
- date: label "Dated" or "दिनांक" → value; keep original format e.g. 20-05-2026
- bid_end_datetime: label "Bid Submission End Date/Time" or "Bid End Date/Time" → value; e.g. 10-06-2026 11:00:00
- bid_opening_datetime: label "Bid Opening Date/Time" → value; e.g. 10-06-2026 11:30:00
- department_name: label "Department Name" → value
- organization_name: label "Organisation Name" or "Organization Name" → value
- office_name_location: label "Office Name and Location" or "Office" → value
- total_quantity: sum of all BOQ item quantities as a plain number string e.g. "80"
- make: from Item Category column or BOQ item descriptions, extract brand/manufacturer names (e.g. IFM, Siemens, Omron, Schneider, WIKA, Gulf); if multiple brands found, comma-separate them; null if not found
- tender_approx_value: look for labels "Estimated Value", "Tender Value", "Approximate Value", "Estimated Cost", "Contract Value", or "Bid Value" → extract the value; null if not found
- items: ALL line items from the Bill of Quantities; part_number is the model/order code
- required_documents: every document name listed as required or mandatory; plain strings only

Strict rules:
- Use JSON null for any field not found in this chunk; never hallucinate
- Do NOT extract consignee, EMD, financial year, or bid start date
- Do NOT add fields not listed above
- Keep all date/time values exactly as they appear in the document
- required_documents must be a flat list of strings, not objects

Tender Document Pages:
"""


# ── PDF extraction ──────────────────────────────────────────────────────────

def extract_pages(pdf_path: str) -> list:
    """Return one dict per page with combined text + table content."""
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            parts = []
            text = page.extract_text()
            if text:
                parts.append(text)
            for table in page.extract_tables():
                rows = [
                    " | ".join(str(cell or "").strip() for cell in row)
                    for row in table
                    if row and any(cell for cell in row)
                ]
                if rows:
                    parts.append("\n".join(rows))
            if parts:
                pages.append({"page_num": i, "content": "\n\n".join(parts)})
    return pages


def filter_relevant_pages(pages: list) -> list:
    """Keep only pages that contain at least one relevant keyword."""
    relevant = [
        p for p in pages
        if any(kw in p["content"].lower() for kw in RELEVANT_KEYWORDS)
    ]
    return relevant if relevant else pages  # fallback: use all pages


def build_chunks(pages: list, max_chars: int = MAX_CHUNK_CHARS) -> list:
    """Group filtered pages into chunks that stay under max_chars."""
    chunks, current, current_len = [], [], 0
    for page in pages:
        block = f"[Page {page['page_num']}]\n{page['content']}"
        if current and current_len + len(block) > max_chars:
            chunks.append("\n\n".join(current))
            current, current_len = [block], len(block)
        else:
            current.append(block)
            current_len += len(block)
    if current:
        chunks.append("\n\n".join(current))
    return chunks


# ── AI extraction ───────────────────────────────────────────────────────────

def extract_chunk(client: OpenAI, chunk_text: str) -> dict:
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": EXTRACTION_PROMPT + chunk_text}],
        response_format={"type": "json_object"},
        max_tokens=4096,
    )
    return json.loads(response.choices[0].message.content)


def _load_gem_extractor_module():
    global _GEM_EXTRACTOR_MODULE
    if _GEM_EXTRACTOR_MODULE is not None:
        return _GEM_EXTRACTOR_MODULE

    root = Path(__file__).resolve().parents[1]
    extractor_path = root / "gem_tender_tool" / "extract.py"
    if not extractor_path.exists():
        _GEM_EXTRACTOR_MODULE = None
        return None

    spec = importlib.util.spec_from_file_location("gem_structured_extract", extractor_path)
    if not spec or not spec.loader:
        _GEM_EXTRACTOR_MODULE = None
        return None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _GEM_EXTRACTOR_MODULE = module
    return module


def _deterministic_gem_extract(pdf_path: str) -> dict | None:
    module = _load_gem_extractor_module()
    if not module or not hasattr(module, "parse_tender"):
        return None

    try:
        parsed = module.parse_tender(pdf_path)
    except Exception:
        return None

    tender_number = parsed.get("bid_number") or parsed.get("tender_number")
    make = parsed.get("primary_product") or parsed.get("item_category") or parsed.get("boq_title")
    items = []
    for row in parsed.get("schedules") or []:
        item_text = row.get("item") or ""
        part_match = re.search(r"\b(?:model|order\s*code|ordercode)\s*[-: ]\s*([A-Z0-9./_-]+)", item_text, re.I)
        items.append({
            "part_number": part_match.group(1).strip() if part_match else None,
            "item_description": item_text or None,
            "quantity": row.get("quantity"),
        })

    required_documents = []
    for doc in parsed.get("required_documents") or []:
        label = doc.get("label") if isinstance(doc, dict) else str(doc)
        label = (label or "").strip()
        if label:
            required_documents.append(label)

    return {
        "tender_information": {
            "tender_number": tender_number,
            "date": parsed.get("bid_dated"),
            "bid_end_datetime": parsed.get("bid_end_datetime"),
            "bid_opening_datetime": parsed.get("bid_opening_datetime"),
            "department_name": parsed.get("department"),
            "organization_name": parsed.get("organisation"),
            "office_name_location": parsed.get("office"),
            "total_quantity": parsed.get("total_quantity"),
            "make": make,
            "tender_approx_value": None,
        },
        "items": items,
        "required_documents": required_documents,
        "extraction_mode": "deterministic_gem",
    }


def merge_results(results: list) -> dict:
    """
    Merge partial results from multiple chunks.
    - Scalar fields in tender_information: first non-null value wins.
    - items / required_documents: deduplicated union across all chunks.
    """
    merged = {
        "tender_information": {
            "tender_number": None,
            "date": None,
            "bid_end_datetime": None,
            "bid_opening_datetime": None,
            "department_name": None,
            "organization_name": None,
            "office_name_location": None,
            "total_quantity": None,
            "make": None,
            "tender_approx_value": None,
        },
        "items": [],
        "required_documents": [],
    }

    seen_items = set()
    seen_docs = set()

    for result in results:
        ti = result.get("tender_information") or {}
        for key in merged["tender_information"]:
            if merged["tender_information"][key] is None and ti.get(key):
                merged["tender_information"][key] = ti[key]

        for item in result.get("items") or []:
            dedup_key = (
                (item.get("part_number") or "").strip().lower(),
                (item.get("item_description") or "").strip().lower()[:60],
            )
            if dedup_key not in seen_items:
                seen_items.add(dedup_key)
                merged["items"].append(item)

        for doc in result.get("required_documents") or []:
            if isinstance(doc, str) and doc.strip():
                norm = doc.strip().lower()
                if norm not in seen_docs:
                    seen_docs.add(norm)
                    merged["required_documents"].append(doc.strip())

    return merged


def _merge_prefer_first(results: list) -> dict:
    merged = {
        "tender_information": {
            "tender_number": None,
            "date": None,
            "bid_end_datetime": None,
            "bid_opening_datetime": None,
            "department_name": None,
            "organization_name": None,
            "office_name_location": None,
            "total_quantity": None,
            "make": None,
            "tender_approx_value": None,
        },
        "items": [],
        "required_documents": [],
    }

    seen_items = set()
    seen_docs = set()
    modes = []

    for result in results:
        if not result:
            continue

        mode = result.get("extraction_mode")
        if mode:
            modes.append(mode)

        ti = result.get("tender_information") or {}
        for key in merged["tender_information"]:
            if merged["tender_information"][key] is None and ti.get(key):
                merged["tender_information"][key] = ti[key]

        for item in result.get("items") or []:
            dedup_key = (
                (item.get("part_number") or "").strip().lower(),
                (item.get("item_description") or "").strip().lower()[:80],
                (item.get("quantity") or "").strip().lower(),
            )
            if dedup_key not in seen_items:
                seen_items.add(dedup_key)
                merged["items"].append(item)

        for doc in result.get("required_documents") or []:
            label = doc if isinstance(doc, str) else doc.get("label")
            label = (label or "").strip()
            if label:
                norm = label.lower()
                if norm not in seen_docs:
                    seen_docs.add(norm)
                    merged["required_documents"].append(label)

    if modes:
        merged["extraction_mode"] = "+".join(modes)
    return merged


def _critical_field_count(result: dict) -> int:
    ti = (result or {}).get("tender_information") or {}
    return sum(1 for field in CRITICAL_TENDER_FIELDS if ti.get(field))


def _is_local_extraction_good_enough(result: dict) -> bool:
    if not result:
        return False
    return _critical_field_count(result) >= MIN_CRITICAL_FIELDS_FOR_LOCAL_SUCCESS and (
        bool(result.get("items")) or bool(result.get("required_documents"))
    )


# ── Public entry point ──────────────────────────────────────────────────────

def _first_match(text: str, patterns: list) -> str | None:
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if m:
            return " ".join((m.group(1) or "").split()) or None
    return None


def _local_extract_from_text(text: str) -> dict:
    compact = re.sub(r"[ \t]+", " ", text)
    tender_number = _first_match(compact, [
        r"Bid\s*Number\s*[:|\-]?\s*([A-Z0-9/.-]+)",
        r"(GEM/\d{4}/B/\d+)",
    ])
    bid_end = _first_match(compact, [
        r"Bid\s*(?:Submission\s*)?End\s*Date/?Time\s*[:|\-]?\s*([0-9]{1,2}[-/][0-9]{1,2}[-/][0-9]{2,4}\s+[0-9:.]{4,8})",
        r"Bid\s*End\s*Date/?Time\s*[:|\-]?\s*([0-9]{1,2}[-/][0-9]{1,2}[-/][0-9]{2,4}\s+[0-9:.]{4,8})",
    ])
    bid_open = _first_match(compact, [
        r"Bid\s*Opening\s*Date/?Time\s*[:|\-]?\s*([0-9]{1,2}[-/][0-9]{1,2}[-/][0-9]{2,4}\s+[0-9:.]{4,8})",
    ])
    date = _first_match(compact, [r"Dated\s*[:|\-]?\s*([0-9]{1,2}[-/][0-9]{1,2}[-/][0-9]{2,4})"])
    department = _first_match(compact, [r"Department\s*Name\s*[:|\-]?\s*([^\n\r|]+)"])
    organisation = _first_match(compact, [r"Organi[sz]ation\s*Name\s*[:|\-]?\s*([^\n\r|]+)"])
    office = _first_match(compact, [
        r"Office\s*Name\s*(?:and|&)?\s*Location\s*[:|\-]?\s*([^\n\r|]+)",
        r"Office\s*Name\s*[:|\-]?\s*([^\n\r|]+)",
    ])
    qty = _first_match(compact, [r"Total\s*Quantity\s*[:|\-]?\s*([0-9,]+)", r"Quantity\s*[:|\-]?\s*([0-9,]+)"])
    value = _first_match(compact, [
        r"(?:Estimated|Tender|Approximate|Contract|Bid)\s*(?:Value|Cost)?\s*[:|\-]?\s*(?:Rs\.?|INR)?\s*([0-9,]+(?:\.[0-9]+)?)",
    ])
    item_category = _first_match(compact, [r"Item\s*Category\s*[:|\-]?\s*([^\n\r|]+)"])
    docs = []
    for label in ("GST", "PAN", "MSME", "ITR", "Bank", "OEM Authorization", "Authorization", "Experience"):
        if re.search(r"\b" + re.escape(label) + r"\b", compact, re.IGNORECASE):
            docs.append(label)

    return {
        "tender_information": {
            "tender_number": tender_number,
            "date": date,
            "bid_end_datetime": bid_end,
            "bid_opening_datetime": bid_open,
            "department_name": department,
            "organization_name": organisation,
            "office_name_location": office,
            "total_quantity": qty,
            "make": item_category,
            "tender_approx_value": value,
        },
        "items": [{"part_number": None, "item_description": item_category, "quantity": qty}] if item_category or qty else [],
        "required_documents": docs,
        "extraction_mode": "local_fallback",
    }


def process_pdf(pdf_path: str) -> dict:
    pages = extract_pages(pdf_path)
    full_text = "\n\n".join(p["content"] for p in pages)
    relevant = filter_relevant_pages(pages)
    chunks = build_chunks(relevant)
    deterministic = _deterministic_gem_extract(pdf_path)
    local = _local_extract_from_text(full_text)
    local_first = _merge_prefer_first([deterministic, local])

    if _is_local_extraction_good_enough(local_first):
        local_first["extraction_mode"] = local_first.get("extraction_mode") or "local_only"
        return local_first

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key or api_key.strip() in {"", "YOUR_OPENAI_KEY_HERE"}:
        return local_first
    client = OpenAI(api_key=api_key)
    try:
        results = [extract_chunk(client, chunk) for chunk in chunks]
        llm_merged = merge_results(results)
        llm_merged["extraction_mode"] = "llm_full"
        combined = _merge_prefer_first([local_first, llm_merged])
        combined["extraction_mode"] = "hybrid_local_llm"
        return combined
    except Exception:
        return local_first
