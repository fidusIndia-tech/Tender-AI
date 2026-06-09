import json
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


# ── Public entry point ──────────────────────────────────────────────────────

def process_pdf(pdf_path: str) -> dict:
    pages = extract_pages(pdf_path)
    relevant = filter_relevant_pages(pages)
    chunks = build_chunks(relevant)

    client = OpenAI()
    results = [extract_chunk(client, chunk) for chunk in chunks]
    return merge_results(results)
