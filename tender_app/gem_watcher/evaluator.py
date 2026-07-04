"""GeM candidate evaluation.

This module keeps the GeM watcher pipeline strict:
- deterministic hard filters reject obviously irrelevant tenders early
- borderline tenders are routed to REVIEW instead of polluting All Tenders
- the LLM is used only for survivors so cost stays controlled
"""
import json
import os
import re
from datetime import date, datetime

try:
    from ..evaluation import _money_to_number, _range_contains, evaluate_tender_against_capability
except ImportError:
    from evaluation import _money_to_number, _range_contains, evaluate_tender_against_capability

MIN_DEADLINE_DAYS = int(os.environ.get("GEM_MIN_DEADLINE_DAYS", "3"))
AUTO_APPROVE_THRESHOLD = float(os.environ.get("GEM_AUTO_APPROVE_SCORE_THRESHOLD", "8"))
REVIEW_THRESHOLD = float(os.environ.get("GEM_REVIEW_SCORE_THRESHOLD", "6"))
LLM_MIN_RULE_SCORE = float(os.environ.get("GEM_LLM_MIN_RULE_SCORE", "6"))
STRICT_LISTING_GATE = os.environ.get("GEM_STRICT_LISTING_GATE", "true").strip().lower() != "false"
KEYWORD_EVAL_MODEL = os.environ.get("GEM_KEYWORD_EVAL_MODEL", "gpt-4o-mini")
KEYWORD_CONTEXT_WORDS = int(os.environ.get("GEM_KEYWORD_CONTEXT_WORDS", "300"))
EVAL_MODEL = os.environ.get("GEM_EVAL_MODEL", "gpt-4o-mini")
MAX_TENDER_TEXT_CHARS = 40_000

AUTOMATION_PRODUCT_HINTS = [
    "sensor", "proximity sensor", "photoelectric sensor", "encoder", "plc", "hmi",
    "vfd", "drive", "servo", "relay", "contactor", "mcb", "switchgear",
    "instrumentation", "transmitter", "pressure gauge", "control panel",
    "automation", "industrial electrical", "industrial electricals",
    "limit switch", "safety relay", "scada", "io module", "breaker",
    "temperature controller", "actuator", "solenoid", "cable gland",
]

EXCLUDED_SCOPE_PATTERNS = {
    "civil_work": [r"\bcivil\b", r"\bconstruction\b", r"\bbuilding\b", r"\broad\b", r"\bdrain\b"],
    "manpower": [r"\bmanpower\b", r"\boutsourcing\b", r"\bdeployment of staff\b", r"\boperator supply\b", r"\blabou?r\b", r"\blabou?r contract\b"],
    "security_service": [r"\bsecurity service\b", r"\bsecurity guard\b", r"\bsecurity manpower\b"],
    "cleaning": [r"\bcleaning\b", r"\bhousekeeping\b", r"\bsanitation\b"],
    "furniture": [r"\bfurniture\b", r"\bchair\b", r"\btable\b", r"\balmirah\b"],
    "stationery": [r"\bstationery\b", r"\bpen\b", r"\bpaper\b", r"\bnotebook\b"],
    "it_hardware": [r"\blaptop\b", r"\bcomputer\b", r"\bprinter\b", r"\bserver\b", r"\bdesktop\b"],
    "medical_equipment": [r"\bmedical\b", r"\bhospital\b", r"\bpatient\b", r"\bdiagnostic\b"],
    "software_license": [r"\bsoftware\b", r"\blicen[cs]e\b", r"\bsubscription\b", r"\bsaas\b"],
    "vehicle": [r"\bvehicle\b", r"\bcar\b", r"\btruck\b", r"\btractor\b", r"\btyre\b"],
    "catering": [r"\bcatering\b", r"\bmeal\b", r"\bfood\b", r"\bvegetable\b", r"\bfruit\b", r"\brice\b"],
    "consultancy_only": [r"\bconsultancy\b", r"\bconsulting\b"],
    "training_only": [r"\btraining\b", r"\bworkshop\b", r"\bseminar\b"],
}

AMC_PATTERNS = [r"\bamc\b", r"\bannual maintenance\b", r"\bcmc\b", r"\bmaintenance contract\b", r"\brepair and maintenance\b", r"\brepair only\b", r"\bmaintenance only\b"]
OEM_ONLY_PATTERNS = [r"\boem only\b", r"\bonly oem\b", r"\bonly manufacturer\b", r"\boem or authorized\b", r"\bmanufacturer or authorized\b"]
REFERENCE_ONLY_PATTERNS = [
    r"\bpast performance\b", r"\bpast experience\b", r"\boem authorization\b",
    r"\bauthorization certificate\b", r"\bcompliance\b", r"\bequivalent\b",
]
KEYWORD_CONTEXT_TYPES = [
    "PRIMARY_REQUIRED_BRAND",
    "ACCEPTED_EQUIVALENT_BRAND",
    "PRODUCT_SPECIFICATION_MATCH",
    "REFERENCE_ONLY",
    "EXISTING_SYSTEM_ONLY",
    "SERVICE_OR_AMC_ONLY",
    "UNRELATED_MENTION",
]

KEYWORD_PRE_EVAL_MODE = os.environ.get("GEM_KEYWORD_PRE_EVAL_MODE", "rule").strip().lower()

BRAND_PRODUCT_FIT_MATRIX = {
    "ifm": ["sensor", "proximity sensor", "photoelectric sensor", "pressure sensor", "flow sensor", "level sensor"],
    "sick": ["sensor", "photoelectric sensor", "safety sensor", "encoder", "scanner"],
    "baumer": ["sensor", "encoder", "pressure sensor", "level sensor"],
    "turck": ["sensor", "io module", "connector", "interface module"],
    "balluff": ["sensor", "proximity sensor", "rfid", "io module"],
    "pepperl+fuchs": ["sensor", "proximity sensor", "intrinsic safety", "barrier"],
    "keyence": ["sensor", "vision system", "laser sensor", "measurement sensor"],
    "abb": ["vfd", "drive", "switchgear", "mccb", "contactor", "relay", "plc", "control panel"],
    "schneider": ["plc", "hmi", "vfd", "mccb", "mcb", "contactor", "relay", "switchgear", "control panel"],
    "siemens": ["plc", "hmi", "vfd", "drive", "automation", "control panel"],
    "omron": ["plc", "relay", "sensor", "hmi", "automation"],
}

BRAND_ALIAS_MAP = {
    "abb": ["abb"],
    "ifm": ["ifm"],
    "schneider": ["schneider", "schinider", "schneider electric"],
    "siemens": ["siemens", "siemen"],
    "omron": ["omron"],
    "sick": ["sick"],
    "baumer": ["baumer"],
    "turck": ["turck"],
    "balluff": ["balluff"],
    "pepperl+fuchs": ["pepperl+fuchs", "pepperl fuchs", "pepperl-fuchs"],
    "keyence": ["keyence"],
}

KEYWORD_USEFUL_PRODUCTS = [
    "sensor", "proximity sensor", "photoelectric sensor", "pressure sensor",
    "flow sensor", "level sensor", "plc", "hmi", "vfd", "drive",
    "variable frequency drive", "instrumentation", "control panel",
    "automation panel", "switchgear", "relay", "contactor", "mccb", "mcb",
    "io module", "i/o module", "smps", "encoder", "limit switch",
    "safety relay", "industrial ethernet", "power supply",
    "industrial automation",
]


def _clean_text(value) -> str:
    return " ".join(str(value or "").split()).strip()


def _clean_list(values, limit: int = 6) -> list[str]:
    if not values:
        return []
    if not isinstance(values, list):
        values = [values]
    out = []
    seen = set()
    for value in values:
        text = _clean_text(value)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _build_reason(summary: str, strengths=None, risks=None, hard_failures=None, manual_checks=None) -> str:
    parts = []
    summary = _clean_text(summary)
    if summary:
        parts.append(summary)
    strengths = _clean_list(strengths, limit=3)
    risks = _clean_list(risks, limit=3)
    hard_failures = _clean_list(hard_failures, limit=4)
    manual_checks = _clean_list(manual_checks, limit=3)
    if strengths:
        parts.append("Why bid: " + "; ".join(strengths))
    if risks:
        parts.append("Risks: " + "; ".join(risks))
    if hard_failures:
        parts.append("Why not bid: " + "; ".join(hard_failures))
    if manual_checks:
        parts.append("Review before bid: " + "; ".join(manual_checks))
    return " ".join(parts).strip() or "Evaluated."


def _parse_date(value):
    if isinstance(value, date):
        return value
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def _yes_no(v):
    return "Yes" if v else "No"


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").lower()).strip()


def _split_profile_terms(value: str) -> list[str]:
    return [t.strip() for t in re.split(r"[,;\n/|]+", str(value or "")) if t.strip()]


def _has_term(text: str, term: str) -> bool:
    text = _norm(text)
    term = _norm(term)
    if not text or not term:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])"
    return re.search(pattern, text) is not None


def _has_any_pattern(text: str, patterns: list[str]) -> bool:
    text = _norm(text)
    return any(re.search(p, text, re.I) for p in patterns)


def _normalize_keyword_text(value: str) -> str:
    text = str(value or "").lower()
    text = text.replace("&", " and ").replace("+", " plus ").replace("/", " ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _normalized_aliases(alias_map: dict[str, list[str]]) -> dict[str, list[str]]:
    out = {}
    for canonical, aliases in alias_map.items():
        values = [_normalize_keyword_text(canonical)]
        values.extend(_normalize_keyword_text(alias) for alias in aliases)
        deduped = []
        seen = set()
        for value in values:
            if value and value not in seen:
                seen.add(value)
                deduped.append(value)
        out[canonical] = deduped
    return out


_NORMALIZED_BRAND_ALIASES = _normalized_aliases(BRAND_ALIAS_MAP)


def _canonical_brand_map(capability: dict) -> dict[str, list[str]]:
    canonical_map = dict(_NORMALIZED_BRAND_ALIASES)
    for brand in _split_profile_terms(capability.get("brands_handled")):
        normalized = _normalize_keyword_text(brand)
        if not normalized:
            continue
        if normalized in canonical_map:
            aliases = canonical_map[normalized]
            if normalized not in aliases:
                aliases.append(normalized)
        else:
            canonical_map[normalized] = [normalized]
    return canonical_map


def _build_product_terms(capability: dict) -> list[str]:
    terms = []
    seen = set()
    matrix_terms = [term for terms_list in BRAND_PRODUCT_FIT_MATRIX.values() for term in terms_list]
    for raw in _split_profile_terms(capability.get("product_categories")) + KEYWORD_USEFUL_PRODUCTS + matrix_terms:
        norm = _normalize_keyword_text(raw)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        terms.append(norm)
    return terms


def _canonical_brand_name(raw: str) -> str:
    normalized = _normalize_keyword_text(raw)
    for canonical, aliases in _NORMALIZED_BRAND_ALIASES.items():
        if normalized == canonical or normalized in aliases:
            return canonical
    return normalized


def _brand_product_same_context(
    contexts: list[dict],
    matched_brands: list[str],
    matched_products: list[str],
) -> tuple[bool, list[str]]:
    brand_map = _NORMALIZED_BRAND_ALIASES
    product_terms = {_normalize_keyword_text(term) for term in matched_products if _normalize_keyword_text(term)}
    context_hits = []
    for context in contexts or []:
        text = context.get("text") or ""
        normalized_text = _normalize_keyword_text(text)
        if not normalized_text:
            continue
        context_brands = []
        for brand in matched_brands or []:
            canonical = _canonical_brand_name(brand)
            aliases = brand_map.get(canonical, [canonical])
            if any(_context_has_term(normalized_text, alias) for alias in aliases):
                context_brands.append(canonical)
        if not context_brands:
            continue
        context_products = {term for term in product_terms if _context_has_term(normalized_text, term)}
        for brand in context_brands:
            expected = {
                _normalize_keyword_text(term)
                for term in BRAND_PRODUCT_FIT_MATRIX.get(brand, [])
                if _normalize_keyword_text(term)
            }
            if context_products and (not expected or context_products.intersection(expected)):
                context_hits.append(_clean_text(text))
                break
    return bool(context_hits), _clean_list(context_hits, limit=4)


def _context_has_term(text: str, normalized_term: str) -> bool:
    if not text or not normalized_term:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(normalized_term) + r"(?![a-z0-9])"
    return re.search(pattern, _normalize_keyword_text(text)) is not None


def _extract_delivery_days(text: str):
    text = _norm(text)
    patterns = [
        r"delivery period[^.\n:]{0,50}?(\d{1,3})\s*day",
        r"within\s+(\d{1,3})\s*day",
        r"delivery[^.\n]{0,40}?(\d{1,3})\s*day",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                return None
    return None


def _extract_negative_keywords(text: str) -> list[str]:
    normalized = _norm(text)
    found = []
    label_map = {
        "civil_work": "civil/construction",
        "manpower": "manpower/labour",
        "security_service": "security",
        "cleaning": "housekeeping/cleaning",
        "furniture": "furniture",
        "stationery": "stationery",
        "it_hardware": "laptop/computer/printer",
        "medical_equipment": "medical equipment",
        "software_license": "software/license",
        "vehicle": "vehicle",
        "catering": "catering/food/vegetable",
        "consultancy_only": "consultancy only",
        "training_only": "training only",
    }
    for key, patterns in EXCLUDED_SCOPE_PATTERNS.items():
        if any(re.search(p, normalized, re.I) for p in patterns):
            found.append(label_map.get(key, key))
    if _has_any_pattern(normalized, AMC_PATTERNS):
        found.append("service/amc")
    return _clean_list(found, limit=8)


def _keyword_line_hits(text: str, keywords: list[str]) -> list[str]:
    if not text:
        return []
    hits = []
    for line in text.splitlines():
        compact = _clean_text(line)
        if not compact:
            continue
        if any(_has_term(compact, kw) for kw in keywords):
            hits.append(compact)
        if len(hits) >= 12:
            break
    return hits


def _keyword_context_snippets(text: str, keywords: list[str], window_words: int = KEYWORD_CONTEXT_WORDS) -> list[dict]:
    if not text:
        return []
    snippets = []
    words = re.findall(r"\S+", text)
    if not words:
        return []
    lower_words = [w.lower() for w in words]
    for keyword in keywords:
        kw_parts = [p for p in re.findall(r"\S+", keyword.lower()) if p]
        if not kw_parts:
            continue
        matches = 0
        for idx in range(0, max(0, len(lower_words) - len(kw_parts) + 1)):
            if lower_words[idx:idx + len(kw_parts)] == kw_parts:
                start = max(0, idx - window_words)
                end = min(len(words), idx + len(kw_parts) + window_words)
                snippets.append({
                    "keyword": keyword,
                    "snippet": " ".join(words[start:end]),
                })
                matches += 1
                if matches >= 2:
                    break
        if len(snippets) >= 8:
            break
    return snippets


def _extract_context_sections(candidate: dict, tender: dict, tender_text: str) -> list[dict]:
    contexts = []

    def add(kind: str, text: str):
        compact = _clean_text(text)
        if compact:
            contexts.append({"kind": kind, "text": compact})

    add("title", candidate.get("title") or tender.get("title"))
    add("item_category", tender.get("item_category") or tender.get("primary_product"))
    add("product_name", tender.get("product_name") or tender.get("title") or candidate.get("title"))
    add("make_brand", tender.get("make"))

    for row in tender.get("boq_items") or []:
        add("boq_row", f"{row.get('part_number') or ''} {row.get('item_description') or ''} {row.get('quantity') or ''}")

    for line in (tender.get("technical_specifications") or [])[:10]:
        add("specification", line)

    for line in _keyword_line_hits(tender_text or "", _clean_list(candidate.get("matched_keywords") or [], limit=8)):
        add("keyword_line", line)

    for snippet in _keyword_context_snippets(tender_text or "", _clean_list(candidate.get("matched_keywords") or [], limit=8), window_words=KEYWORD_CONTEXT_WORDS):
        add("keyword_snippet", snippet.get("snippet"))

    return contexts


def _find_context_brands(context_text: str, brand_map: dict[str, list[str]]) -> list[str]:
    normalized = _normalize_keyword_text(context_text)
    matched = []
    for canonical, aliases in brand_map.items():
        if any(_context_has_term(normalized, alias) for alias in aliases):
            matched.append(canonical)
    return matched


def _find_context_products(context_text: str, product_terms: list[str]) -> list[str]:
    normalized = _normalize_keyword_text(context_text)
    matched = []
    for term in product_terms:
        if _context_has_term(normalized, term):
            matched.append(term)
    return matched


def _build_keyword_evidence_packet(candidate: dict, tender: dict, capability: dict, tender_text: str) -> dict:
    matched_keywords = _clean_list(candidate.get("matched_keywords") or [], limit=8)
    local_blocks = _collect_text_blocks(tender, tender_text)
    brand_map = _canonical_brand_map(capability)
    product_terms = _build_product_terms(capability)
    contexts = _extract_context_sections(candidate, tender, tender_text)
    matched_brands = _clean_list(
        [brand for context in contexts for brand in _find_context_brands(context["text"], brand_map)],
        limit=8,
    )
    matched_products = _clean_list(
        [term for context in contexts for term in _find_context_products(context["text"], product_terms)],
        limit=10,
    )
    negative_keywords = _extract_negative_keywords(local_blocks["combined_text"])
    evidence_lines = _keyword_line_hits(
        "\n".join([
            tender_text or "",
            local_blocks["items_text"],
            "\n".join(tender.get("technical_specifications") or []),
        ]),
        matched_keywords,
    )
    snippets = _keyword_context_snippets(tender_text or "", matched_keywords, window_words=KEYWORD_CONTEXT_WORDS)
    return {
        "gem_bid_number": candidate.get("gem_bid_no"),
        "tender_title": candidate.get("title") or tender.get("title"),
        "item_category": tender.get("item_category") or tender.get("primary_product") or tender.get("make"),
        "buyer_organization": tender.get("organization_name") or candidate.get("organisation"),
        "matched_keywords": matched_keywords,
        "matched_brands": matched_brands,
        "make_brand": tender.get("make"),
        "product_name": tender.get("title") or tender.get("item_category") or candidate.get("title"),
        "quantity": tender.get("total_quantity") or candidate.get("quantity"),
        "matched_product_keywords": matched_products,
        "negative_keywords_found": negative_keywords,
        "keyword_snippets": snippets,
        "keyword_lines": evidence_lines,
        "short_description_text": _clean_text(" ".join((tender.get("technical_specifications") or [])[:6])),
        "boq_item_rows": [_clean_text(f"{row.get('part_number') or ''} {row.get('item_description') or ''} {row.get('quantity') or ''}") for row in (tender.get("boq_items") or [])[:10] if _clean_text(f"{row.get('part_number') or ''} {row.get('item_description') or ''} {row.get('quantity') or ''}")],
        "contexts": contexts[:24],
    }


def _extract_required_experience_years(text: str):
    text = _norm(text)
    m = re.search(r"(?:minimum|required).{0,40}?experience.{0,20}?(\d{1,2})\s*year", text, re.I)
    if not m:
        m = re.search(r"(\d{1,2})\s*year.{0,20}?experience", text, re.I)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def _extract_turnover_requirement(text: str):
    text = str(text or "")
    patterns = [
        r"(minimum average annual turnover[^.\n:]{0,80}?\d[\d,]*(?:\.\d+)?\s*(?:crore|cr|lakh|lac)?)",
        r"(turnover[^.\n:]{0,80}?\d[\d,]*(?:\.\d+)?\s*(?:crore|cr|lakh|lac)?)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            snippet = _clean_text(m.group(1))
            return snippet, _money_to_number(snippet)
    return None, None


def _capability_is_oem(capability: dict) -> bool:
    core = _norm(capability.get("core_business"))
    return "manufacturer" in core or re.search(r"(?<![a-z0-9])oem(?![a-z0-9])", core) is not None


def _build_profile_summary(c: dict) -> str:
    years = c.get("years_experience")
    docs_available = [name for name, flag in {
        "GST": c.get("gst_available"),
        "PAN": c.get("pan_available"),
        "MSME": c.get("msme_available"),
        "ITR": c.get("itr_available"),
        "Bank documents": c.get("bank_documents_available"),
        "Letterhead": c.get("letterhead_available"),
        "Stamp": c.get("stamp_available"),
        "Signature": c.get("signature_available"),
    }.items() if flag]
    return (
        f"Core business: {c.get('core_business') or 'N/A'}\n"
        f"Product categories handled: {c.get('product_categories') or 'N/A'}\n"
        f"Brands handled: {c.get('brands_handled') or 'N/A'}\n"
        f"Industries served: {c.get('industries_served') or 'N/A'}\n"
        f"Years in business: {years if years is not None else 'N/A'} "
        f"(established {c.get('year_established') or 'N/A'})\n"
        f"Annual turnover range: {c.get('turnover_range') or 'N/A'}\n"
        f"Typical tender value range handled: {c.get('typical_tender_value_range') or 'N/A'}\n"
        f"OEM support available: {_yes_no(c.get('oem_support_available'))}\n"
        f"OEM authorizations held: {c.get('oem_authorizations') or 'None stated'}\n"
        f"Engineering support: {_yes_no(c.get('engineering_support'))}; "
        f"Installation support: {_yes_no(c.get('installation_support'))}\n"
        f"Government experience: {_yes_no(c.get('government_experience'))}; "
        f"PSU experience: {_yes_no(c.get('psu_experience'))}\n"
        f"Documents available: {', '.join(docs_available) or 'None'}\n"
        f"Major customers: {c.get('major_customers') or 'N/A'}\n"
        f"Past orders/projects: {c.get('past_orders_projects') or 'N/A'}"
    )


def _collect_text_blocks(tender: dict, tender_text: str) -> dict:
    items = tender.get("boq_items") or []
    docs = tender.get("required_documents") or []
    title = _clean_text(tender.get("title") or tender.get("make"))
    item_lines = [
        _clean_text(f"{i.get('part_number') or ''} {i.get('item_description') or ''} {i.get('quantity') or ''}")
        for i in items
    ]
    docs_text = ", ".join(
        _clean_text(d.get("label") if isinstance(d, dict) else d)
        for d in docs
    )
    header_text = " ".join([
        tender.get("gem_bidding_number") or "",
        tender.get("tender_number") or "",
        tender.get("organization_name") or "",
        tender.get("department_name") or "",
        tender.get("office_name_location") or "",
        tender.get("make") or "",
        tender.get("total_quantity") or "",
        tender.get("tender_approx_value") or "",
        tender.get("item_category") or "",
        tender.get("primary_product") or "",
        title,
    ])
    combined = "\n".join([
        header_text,
        "\n".join(item_lines),
        docs_text,
        tender_text or "",
    ])
    return {
        "title": title,
        "header_text": header_text,
        "items_text": "\n".join(item_lines),
        "docs_text": docs_text,
        "combined_text": combined,
    }


def _classify_business_category(text: str) -> tuple[str, str | None]:
    normalized = _norm(text)
    for category, patterns in EXCLUDED_SCOPE_PATTERNS.items():
        if any(re.search(p, normalized, re.I) for p in patterns):
            return category, "reject_scope"
    if _has_any_pattern(normalized, AMC_PATTERNS):
        if any(token in normalized for token in AUTOMATION_PRODUCT_HINTS):
            return "automation_amc_service", None
        return "amc_only", "reject_scope"
    if any(token in normalized for token in AUTOMATION_PRODUCT_HINTS):
        return "industrial_automation", None
    if any(w in normalized for w in ["electrical", "switch", "panel", "control", "instrument"]):
        return "industrial_electrical", None
    return "unclassified", None


def _classify_brand_context(capability: dict, blocks: dict) -> tuple[list[str], str]:
    brands = [b for b in _split_profile_terms(capability.get("brands_handled")) if b]
    if not brands:
        return [], "UNRELATED_MENTION"

    title_items_text = " ".join([blocks["title"], blocks["items_text"], blocks["header_text"]])
    docs_text = blocks["docs_text"]
    combined = blocks["combined_text"]
    matched = [b for b in brands if _has_term(combined, b)]
    if not matched:
        return [], "UNRELATED_MENTION"

    strong_hits = [b for b in matched if _has_term(title_items_text, b)]
    if strong_hits:
        if re.search(r"\bor equivalent\b", _norm(title_items_text), re.I):
            return strong_hits, "ACCEPTED_EQUIVALENT_BRAND"
        return strong_hits, "PRIMARY_REQUIRED_BRAND"

    if any(_has_term(docs_text, b) for b in matched) or _has_any_pattern(combined, REFERENCE_ONLY_PATTERNS):
        return matched, "REFERENCE_ONLY"
    return matched, "UNRELATED_MENTION"


def _match_products(capability: dict, blocks: dict) -> list[str]:
    profile_terms = _split_profile_terms(capability.get("product_categories")) + AUTOMATION_PRODUCT_HINTS
    combined = _norm(blocks["combined_text"])
    matched = []
    seen = set()
    for term in profile_terms:
        term = _clean_text(term)
        if not term:
            continue
        key = term.lower()
        if key in seen:
            continue
        if _has_term(combined, term):
            matched.append(term)
            seen.add(key)
    return matched[:8]


def _missing_documents(capability: dict, tender: dict, combined_text: str) -> list[str]:
    docs = {
        "GST": capability.get("gst_available"),
        "PAN": capability.get("pan_available"),
        "MSME": capability.get("msme_available"),
        "ITR": capability.get("itr_available"),
        "Bank": capability.get("bank_documents_available"),
        "Letterhead": capability.get("letterhead_available"),
        "Stamp": capability.get("stamp_available"),
        "Signature": capability.get("signature_available"),
        "OEM Authorization": bool(capability.get("oem_support_available") or capability.get("oem_authorizations")),
    }
    required_docs = tender.get("required_documents") or []
    required_text = " ".join(
        _clean_text(d.get("label") if isinstance(d, dict) else d)
        for d in required_docs
    )
    if combined_text:
        required_text += " " + combined_text
    missing = []
    for name, available in docs.items():
        if re.search(re.escape(name), required_text, re.I) and not available:
            missing.append(name)
    return _clean_list(missing, limit=6)


def _profit_potential_label(amount, qty_text, matched_products: list[str]) -> str:
    qty_num = None
    qty_match = re.search(r"\d[\d,]*", str(qty_text or ""))
    if qty_match:
        try:
            qty_num = int(qty_match.group(0).replace(",", ""))
        except ValueError:
            qty_num = None
    if amount and amount >= 1_000_000:
        return "HIGH"
    if qty_num and qty_num >= 20 and matched_products:
        return "MEDIUM"
    if matched_products:
        return "MEDIUM"
    return "LOW"


def _risk_level(hard_failures: list[str], score: float) -> str:
    if hard_failures:
        return "HIGH"
    if score >= 8:
        return "LOW"
    if score >= 6:
        return "MEDIUM"
    return "HIGH"


def _decision_from_score(score: float, hard_failures: list[str]) -> str:
    if hard_failures:
        return "REJECT"
    if score >= AUTO_APPROVE_THRESHOLD:
        return "APPROVE"
    if score >= REVIEW_THRESHOLD:
        return "REVIEW"
    return "REJECT"


def _safe_final_decision(
    score: float,
    hard_negative_reasons: list[str],
    matched_brands: list[str] | None = None,
    matched_products: list[str] | None = None,
    brand_product_same_context: bool = False,
    extraction_status: str = "SUCCESS",
    extraction_confidence: str = "HIGH",
) -> str:
    matched_brands = matched_brands or []
    matched_products = matched_products or []
    if extraction_status != "SUCCESS" or extraction_confidence == "LOW":
        return "REVIEW"
    if hard_negative_reasons and (brand_product_same_context or (matched_brands and matched_products)):
        return "REVIEW"
    if brand_product_same_context and not hard_negative_reasons:
        return "APPROVE" if score >= 7 else "REVIEW"
    if matched_brands and matched_products and not hard_negative_reasons:
        return "APPROVE" if score >= 7 else "REVIEW"
    if score >= 7:
        return "APPROVE"
    if score >= 4:
        return "REVIEW"
    if hard_negative_reasons and not matched_products:
        return "REJECT"
    return "REVIEW"


def _decision_safety_reason(
    original_decision: str,
    final_decision: str,
    matched_brands: list[str],
    matched_products: list[str],
    hard_negative_reasons: list[str],
    brand_product_same_context: bool,
) -> str | None:
    if final_decision == original_decision:
        return None
    if final_decision == "REVIEW" and matched_brands and matched_products and not hard_negative_reasons:
        return "Tender contains useful brand and useful automation product, so it cannot be auto-rejected without hard negative evidence."
    if final_decision == "APPROVE" and brand_product_same_context and not hard_negative_reasons:
        return "Useful brand and automation product appear in the same tender context, so the tender qualifies for approval."
    if final_decision == "REVIEW" and brand_product_same_context:
        return "Useful brand and automation product appear in the same tender context, so the tender requires manual review instead of rejection."
    if final_decision == "REVIEW" and not hard_negative_reasons:
        return "No hard negative evidence was found, so the tender should be reviewed instead of auto-rejected."
    return None


def _weighted_local_eval(tender: dict, capability: dict, bid_end_date=None, tender_text: str = None) -> dict:
    base = evaluate_tender_against_capability(tender, capability)
    blocks = _collect_text_blocks(tender, tender_text or "")
    combined_text = blocks["combined_text"]
    matched_brands, brand_context = _classify_brand_context(capability, blocks)
    matched_products = _match_products(capability, blocks)
    contexts = _extract_context_sections(
        {"title": tender.get("title"), "matched_keywords": matched_brands},
        tender,
        tender_text or "",
    )
    brand_product_same_context, same_context_examples = _brand_product_same_context(contexts, matched_brands, matched_products)
    business_category, category_gate = _classify_business_category(combined_text)
    missing_documents = _missing_documents(capability, tender, combined_text)
    strong_brand_signal = bool(matched_brands) and brand_context in {"PRIMARY_REQUIRED_BRAND", "ACCEPTED_EQUIVALENT_BRAND"}

    hard_failures = []
    risks = []
    strengths = []
    manual_checks = []

    if category_gate == "reject_scope" and not (matched_products or strong_brand_signal):
        hard_failures.append(f"Tender scope is outside industrial automation ({business_category.replace('_', ' ')}).")

    automation_related = business_category in {"industrial_automation", "industrial_electrical", "automation_amc_service"}
    if not matched_products and not strong_brand_signal and not automation_related and brand_context == "UNRELATED_MENTION":
        hard_failures.append("Tender product/category does not match industrial automation business.")

    oem_required = bool(re.search(r"\boem authorization\b|\bauthori[sz]ation certificate\b|\bmaf\b", combined_text, re.I))
    oem_available = bool(capability.get("oem_support_available") or capability.get("oem_authorizations"))
    is_oem_company = _capability_is_oem(capability)
    if _has_any_pattern(combined_text, OEM_ONLY_PATTERNS) and not is_oem_company and not oem_available:
        risks.append("Tender appears OEM/manufacturer-only. Verify whether authorized supply is accepted.")
        manual_checks.append("OEM/manufacturer restriction needs manual verification before bidding.")
    if oem_required and not oem_available:
        risks.append("OEM authorization appears required, but availability is not confirmed in the profile.")
        manual_checks.append("Verify OEM authorization/MAF requirement manually.")

    turnover_text, turnover_required_amount = _extract_turnover_requirement(combined_text)
    turnover_met = True
    if turnover_required_amount is not None:
        turnover_fit = _range_contains(capability.get("turnover_range"), turnover_required_amount)
        turnover_met = turnover_fit is not False
        if turnover_fit is False:
            risks.append(f"Turnover requirement appears high: {turnover_text}.")
            manual_checks.append(f"Verify turnover requirement manually: {turnover_text}.")
        elif turnover_fit is None:
            manual_checks.append(f"Verify turnover requirement manually: {turnover_text}.")

    required_experience_years = _extract_required_experience_years(combined_text)
    experience_met = True
    company_years = capability.get("years_experience")
    if required_experience_years and company_years is not None and company_years < required_experience_years:
        experience_met = False
        risks.append(
            f"Past experience requirement appears to need {required_experience_years}+ year(s), profile shows {company_years}."
        )
        manual_checks.append("Experience mismatch should be reviewed manually before rejecting.")
    elif required_experience_years and company_years is None:
        manual_checks.append(f"Tender asks for about {required_experience_years}+ year(s) experience; profile year is missing.")

    bed = _parse_date(bid_end_date or tender.get("bid_end_datetime") or tender.get("bid_end_date"))
    days_remaining = None
    if bed:
        days_remaining = (bed - date.today()).days
        if days_remaining < MIN_DEADLINE_DAYS:
            risks.append(f"Tender closing date is too near ({days_remaining} day(s) remaining).")
            manual_checks.append("Deadline is near. Review manual bidding feasibility urgently.")
        elif days_remaining <= MIN_DEADLINE_DAYS + 2:
            risks.append(f"Bid deadline is tight ({days_remaining} day(s) remaining).")

    delivery_days = _extract_delivery_days(combined_text)
    if delivery_days is not None:
        if delivery_days <= 3:
            risks.append(f"Delivery period appears extremely aggressive ({delivery_days} day(s)).")
            manual_checks.append("Delivery timeline needs manual feasibility review.")
        elif delivery_days <= 7 and (matched_products or automation_related):
            risks.append(f"Delivery period is very aggressive ({delivery_days} day(s)).")

    if brand_context == "REFERENCE_ONLY" and not matched_products:
        risks.append("Keyword/brand may be reference-only, not the actual required product.")
        manual_checks.append("Verify whether the brand is required supply scope or only compliance/reference text.")

    if missing_documents:
        risks.append("Missing/uncertain documents: " + ", ".join(missing_documents))

    product_score = 0.0
    if matched_products:
        product_score = 2.0
        strengths.append("Product/category aligns with industrial automation profile.")
    elif automation_related or matched_brands:
        product_score = 1.0
        risks.append("Only partial product/category alignment was detected.")

    brand_score = {
        "PRIMARY_REQUIRED_BRAND": 2.0,
        "ACCEPTED_EQUIVALENT_BRAND": 1.5,
        "REFERENCE_ONLY": 0.5,
        "UNRELATED_MENTION": 0.0,
    }.get(brand_context, 0.0)
    if matched_brands and brand_score >= 1.5:
        strengths.append("Required/acceptable brand appears in tender scope: " + ", ".join(matched_brands[:4]))
    elif matched_brands:
        risks.append("Brand appears, but likely only as reference/compliance context.")

    technical_score = 0.0
    if matched_products and (capability.get("engineering_support") or capability.get("installation_support")):
        technical_score = 2.0
        strengths.append("Company profile indicates engineering/installation capability for this scope.")
    elif matched_products or automation_related:
        technical_score = 1.0
    elif business_category == "automation_amc_service":
        technical_score = 0.5

    eligibility_score = 1.5
    if not turnover_met or not experience_met or oem_required and not oem_available:
        eligibility_score = 0.0
    elif required_experience_years or turnover_text:
        eligibility_score = 1.0

    amount = _money_to_number(tender.get("tender_approx_value"))
    profit_potential = _profit_potential_label(amount, tender.get("total_quantity"), matched_products)
    commercial_score = {"HIGH": 1.0, "MEDIUM": 0.7, "LOW": 0.3}.get(profit_potential, 0.5)

    if missing_documents:
        document_score = 0.2 if len(missing_documents) <= 2 else 0.0
    else:
        document_score = 1.0 if (tender.get("required_documents") or []) else 0.6

    deadline_location_score = 0.5
    if days_remaining is not None and days_remaining <= MIN_DEADLINE_DAYS + 2:
        deadline_location_score = 0.2
    if delivery_days is not None and delivery_days <= 7:
        deadline_location_score = min(deadline_location_score, 0.2)

    score_breakdown = {
        "product_category_match": round(product_score, 1),
        "brand_match": round(brand_score, 1),
        "technical_match": round(technical_score, 1),
        "eligibility_match": round(eligibility_score, 1),
        "commercial_value": round(commercial_score, 1),
        "document_availability": round(document_score, 1),
        "deadline_location": round(deadline_location_score, 1),
    }
    total_score = round(sum(score_breakdown.values()), 1)

    hard_negative_reasons = []
    for msg in hard_failures:
        if any(term in msg.lower() for term in ["outside industrial automation", "does not match industrial automation business"]):
            hard_negative_reasons.append(msg)

    if hard_negative_reasons and (matched_products or strong_brand_signal):
        manual_checks.append("Automation signals were found, so review manually before final rejection.")

    if hard_negative_reasons:
        total_score = min(total_score, 3.9 if total_score < 4 else 5.9)

    original_decision = _decision_from_score(total_score, hard_negative_reasons)
    decision = _safe_final_decision(
        total_score,
        hard_negative_reasons,
        matched_brands=matched_brands,
        matched_products=matched_products,
        brand_product_same_context=brand_product_same_context,
    )
    safety_reason = _decision_safety_reason(
        original_decision,
        decision,
        matched_brands,
        matched_products,
        hard_negative_reasons,
        brand_product_same_context,
    )
    if decision == "APPROVE":
        strengths.append("Overall fit is strong enough for direct bidding.")
    elif decision == "REVIEW":
        manual_checks.append("Tender has some fit but needs commercial/manual review before bidding.")
    else:
        risks.append("Tender does not clear the qualification gate for main pipeline.")

    summary = {
        "APPROVE": "Tender fits the industrial-automation profile and can be pushed to All Tenders.",
        "REVIEW": "Tender has some relevance but needs manual review before bidding.",
        "REJECT": "Tender should not be pursued because the scope or eligibility is not a fit.",
    }[decision]

    evaluation_json = {
        "method": "rule_based",
        "decision": decision,
        "recommended_action": decision,
        "score_breakdown": score_breakdown,
        "matched_brands": matched_brands,
        "matched_products": matched_products,
        "brand_product_same_context": brand_product_same_context,
        "brand_product_same_context_examples": same_context_examples,
        "brand_context": brand_context,
        "business_category": business_category,
        "eligibility_status": "APPROVED" if decision == "APPROVE" else "REVIEW" if decision == "REVIEW" else "REJECTED",
        "risk_level": _risk_level(hard_failures, total_score),
        "profit_potential": profit_potential,
        "missing_documents": missing_documents,
        "rejection_reason": "; ".join(_clean_list(hard_negative_reasons, limit=5)) if hard_negative_reasons else None,
        "review_reason": "; ".join(_clean_list(manual_checks, limit=5)) if decision == "REVIEW" else None,
        "hard_negative_found": bool(hard_negative_reasons),
        "hard_negative_reasons": _clean_list(hard_negative_reasons, limit=6),
        "original_ai_decision": original_decision,
        "final_decision_after_safety_layer": decision,
        "decision_reason": safety_reason or summary,
        "evaluation_confidence": "LOW" if manual_checks else "MEDIUM" if risks else "HIGH",
        "ai_explanation": summary,
        "hard_failures": _clean_list(hard_failures, limit=6),
        "strengths": _clean_list(strengths, limit=5),
        "risks": _clean_list(risks, limit=5),
        "manual_checks_needed": _clean_list(manual_checks, limit=5),
        "extracted_fields": {
            "gem_bid_number": tender.get("gem_bidding_number"),
            "tender_number": tender.get("tender_number"),
            "start_date": tender.get("date") or tender.get("bid_start_date"),
            "bid_end_date": tender.get("bid_end_datetime") or tender.get("bid_end_date"),
            "buyer_organization": tender.get("organization_name"),
            "item_category": tender.get("item_category"),
            "product_name": tender.get("title") or tender.get("make"),
            "make_brand": tender.get("make"),
            "quantity": tender.get("total_quantity"),
            "delivery_location": tender.get("office_name_location"),
            "eligibility_criteria": turnover_text or "",
            "turnover_requirement": turnover_text,
            "experience_requirement": f"{required_experience_years} year(s)" if required_experience_years else None,
            "oem_authorization_requirement": oem_required,
            "emd": tender.get("emd"),
            "delivery_period_days": delivery_days,
            "technical_specifications": tender.get("technical_specifications"),
        },
        "base_checks": base.get("checks", []),
    }
    final_reason = _build_reason(summary, strengths=strengths, risks=risks, hard_failures=hard_failures, manual_checks=manual_checks)
    if safety_reason:
        final_reason = f"{safety_reason} {final_reason}".strip()
    return {
        "score": total_score,
        "rating_label": "STRONG_FIT" if total_score >= 8 else "MODERATE_FIT" if total_score >= 6 else "WEAK_FIT",
        "matched_brands": ", ".join(matched_brands),
        "eligibility_status": evaluation_json["eligibility_status"],
        "evaluation_confidence": evaluation_json["evaluation_confidence"],
        "reason": final_reason,
        "evaluation_json": evaluation_json,
    }


EVAL_SYSTEM_PROMPT = """You are a meticulous tender bid-evaluation analyst for an Indian industrial-automation supplier bidding on GeM tenders.

Judge whether the tender is genuinely relevant for an industrial automation reseller / system integrator.
Be strict about false positives. A keyword hit alone is never enough.

Return ONLY valid JSON with this structure:
{
  "criteria": {
    "product_category_match": {"score": 0, "justification": ""},
    "brand_match": {"score": 0, "justification": ""},
    "technical_match": {"score": 0, "justification": ""},
    "eligibility_match": {"score": 0, "justification": ""},
    "commercial_value": {"score": 0, "justification": ""},
    "document_availability": {"score": 0, "justification": ""},
    "deadline_location": {"score": 0, "justification": ""}
  },
  "flags": {
    "product_related": true,
    "oem_authorization_required": false,
    "oem_authorization_available": false,
    "turnover_eligibility_met": true,
    "experience_eligibility_met": true,
    "deadline_feasible": true
  },
  "brand_context": "PRIMARY_REQUIRED_BRAND",
  "matched_brands": [],
  "matched_products": [],
  "business_category": "industrial_automation",
  "risk_level": "LOW",
  "profit_potential": "MEDIUM",
  "missing_documents": [],
  "hard_failures": [],
  "strengths": [],
  "risks": [],
  "manual_checks_needed": [],
  "overall_score": 0,
  "recommendation": "APPROVE",
  "summary": ""
}

Scoring weights:
- product_category_match: 0 to 2
- brand_match: 0 to 2
- technical_match: 0 to 2
- eligibility_match: 0 to 1.5
- commercial_value: 0 to 1
- document_availability: 0 to 1
- deadline_location: 0 to 0.5

Use these brand contexts only:
- PRIMARY_REQUIRED_BRAND
- ACCEPTED_EQUIVALENT_BRAND
- REFERENCE_ONLY
- UNRELATED_MENTION

Do not reject only because information is missing or not extracted.
Missing OEM/turnover/experience details should reduce confidence and move the tender to REVIEW, not REJECT.
Reject only when there is clear negative scope evidence or the tender is clearly unrelated to industrial automation.
If a useful brand and useful product category match, prefer APPROVE or REVIEW.
Return JSON only."""


def _llm_evaluate(tender: dict, capability: dict, tender_text: str, local_eval: dict) -> dict:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key or api_key.strip() in {"", "YOUR_OPENAI_KEY_HERE"}:
        raise RuntimeError("OPENAI_API_KEY not configured")

    from openai import OpenAI

    items = tender.get("boq_items") or []
    item_lines = "\n".join(
        f"- {i.get('part_number') or ''} {i.get('item_description') or ''} (qty {i.get('quantity') or ''})".strip()
        for i in items[:60]
    )
    req_docs = ", ".join(
        (d.get("label") if isinstance(d, dict) else str(d))
        for d in (tender.get("required_documents") or [])
    )
    local_json = local_eval.get("evaluation_json") or {}

    tender_block = (
        f"GeM Bid Number: {tender.get('gem_bidding_number') or tender.get('tender_number') or 'N/A'}\n"
        f"Title/Items category: {tender.get('title') or tender.get('make') or 'N/A'}\n"
        f"Organisation: {tender.get('organization_name') or 'N/A'}\n"
        f"Department: {tender.get('department_name') or 'N/A'}\n"
        f"Total quantity: {tender.get('total_quantity') or 'N/A'}\n"
        f"Estimated value: {tender.get('tender_approx_value') or 'N/A'}\n"
        f"Bid end date/time: {tender.get('bid_end_datetime') or 'N/A'}\n"
        f"Required documents (extracted): {req_docs or 'None extracted'}\n"
        f"Line items:\n{item_lines or 'None extracted'}\n\n"
        f"Local qualification result before AI:\n{json.dumps(local_json, ensure_ascii=False)}\n\n"
        f"Tender document text (relevant pages):\n{(tender_text or '')[:MAX_TENDER_TEXT_CHARS]}"
    )
    user_content = (
        "=== COMPANY CAPABILITY PROFILE ===\n"
        + _build_profile_summary(capability)
        + "\n\n=== TENDER ===\n"
        + tender_block
    )

    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=EVAL_MODEL,
        messages=[
            {"role": "system", "content": EVAL_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        response_format={"type": "json_object"},
        temperature=0,
        max_tokens=2200,
    )
    return json.loads(response.choices[0].message.content)


def _finalize_from_llm(llm: dict, local_eval: dict) -> dict:
    local_json = (local_eval.get("evaluation_json") or {}).copy()
    local_hard_failures = _clean_list(local_json.get("hard_failures"))
    local_strengths = _clean_list(local_json.get("strengths"))
    local_risks = _clean_list(local_json.get("risks"))
    local_manual_checks = _clean_list(local_json.get("manual_checks_needed"))
    local_missing_docs = _clean_list(local_json.get("missing_documents"))

    try:
        score = round(float(llm.get("overall_score", local_eval.get("score", 0))), 1)
    except (TypeError, ValueError):
        score = float(local_eval.get("score", 0) or 0)
    score = max(0.0, min(10.0, score))

    flags = llm.get("flags", {}) or {}
    matched_brands = _clean_list(llm.get("matched_brands")) or _clean_list(local_json.get("matched_brands"))
    matched_products = _clean_list(llm.get("matched_products")) or _clean_list(local_json.get("matched_products"))
    brand_product_same_context = bool(llm.get("brand_product_same_context"))
    if not brand_product_same_context:
        brand_product_same_context = bool(local_json.get("brand_product_same_context"))
    hard_failures = _clean_list(local_hard_failures + _clean_list(llm.get("hard_failures")))
    strengths = _clean_list(local_strengths + _clean_list(llm.get("strengths")))
    risks = _clean_list(local_risks + _clean_list(llm.get("risks")))
    manual_checks = _clean_list(local_manual_checks + _clean_list(llm.get("manual_checks_needed")))
    missing_documents = _clean_list(local_missing_docs + _clean_list(llm.get("missing_documents")))

    if flags.get("product_related") is False:
        hard_failures.append("Tender product/category does not match industrial automation business.")
    if flags.get("oem_authorization_required") and not flags.get("oem_authorization_available"):
        manual_checks.append("OEM authorization requirement is unclear or unavailable; review manually.")
    if flags.get("turnover_eligibility_met") is False:
        manual_checks.append("Turnover eligibility may not be met; review manually.")
    if flags.get("experience_eligibility_met") is False:
        manual_checks.append("Past experience eligibility may not be met; review manually.")
    if flags.get("deadline_feasible") is False:
        manual_checks.append("Deadline or delivery timeline looks risky; review manually.")

    hard_negative_reasons = []
    for msg in hard_failures:
        if "does not match industrial automation business" in msg.lower():
            hard_negative_reasons.append(msg)

    if hard_negative_reasons:
        score = min(score, 3.9 if score < 4 else 5.9)

    original_decision = _clean_text(llm.get("recommendation")) or _decision_from_score(score, hard_negative_reasons)
    decision = _safe_final_decision(
        score,
        hard_negative_reasons,
        matched_brands=matched_brands,
        matched_products=matched_products,
        brand_product_same_context=brand_product_same_context,
    )
    safety_reason = _decision_safety_reason(
        original_decision,
        decision,
        matched_brands,
        matched_products,
        hard_negative_reasons,
        brand_product_same_context,
    )
    summary = _clean_text(llm.get("summary")) or (
        "AI evaluation confirmed the tender is suitable for bidding."
        if decision == "APPROVE"
        else "AI evaluation suggests manual review before bidding."
        if decision == "REVIEW"
        else "AI evaluation found the tender unsuitable for bidding."
    )

    evaluation_json = dict(local_json)
    evaluation_json.update({
        "method": "llm",
        "llm": llm,
        "decision": decision,
        "recommended_action": decision,
        "matched_brands": matched_brands,
        "matched_products": matched_products,
        "brand_product_same_context": brand_product_same_context,
        "brand_product_same_context_examples": _clean_list(llm.get("brand_product_same_context_examples")) or _clean_list(local_json.get("brand_product_same_context_examples")),
        "brand_context": llm.get("brand_context") or local_json.get("brand_context"),
        "business_category": llm.get("business_category") or local_json.get("business_category"),
        "eligibility_status": "APPROVED" if decision == "APPROVE" else "REVIEW" if decision == "REVIEW" else "REJECTED",
        "risk_level": llm.get("risk_level") or _risk_level(hard_failures, score),
        "profit_potential": llm.get("profit_potential") or local_json.get("profit_potential"),
        "missing_documents": missing_documents,
        "rejection_reason": "; ".join(_clean_list(hard_negative_reasons, limit=5)) if hard_negative_reasons else None,
        "review_reason": "; ".join(_clean_list(manual_checks, limit=5)) if decision == "REVIEW" else None,
        "hard_negative_found": bool(hard_negative_reasons),
        "hard_negative_reasons": _clean_list(hard_negative_reasons, limit=6),
        "original_ai_decision": original_decision,
        "final_decision_after_safety_layer": decision,
        "decision_reason": safety_reason or summary,
        "hard_failures": _clean_list(hard_failures, limit=6),
        "strengths": _clean_list(strengths, limit=5),
        "risks": _clean_list(risks, limit=5),
        "manual_checks_needed": _clean_list(manual_checks, limit=5),
        "ai_explanation": summary,
        "confidence": _clean_text(llm.get("confidence")) or ("LOW" if manual_checks else "MEDIUM" if risks else "HIGH"),
        "evaluation_confidence": _clean_text(llm.get("confidence")) or ("LOW" if manual_checks else "MEDIUM" if risks else "HIGH"),
    })

    final_reason = _build_reason(summary, strengths=strengths, risks=risks, hard_failures=hard_failures, manual_checks=manual_checks)
    if safety_reason:
        final_reason = f"{safety_reason} {final_reason}".strip()
    return {
        "score": score,
        "rating_label": "STRONG_FIT" if score >= 8 else "MODERATE_FIT" if score >= 6 else "WEAK_FIT",
        "matched_brands": ", ".join(matched_brands),
        "eligibility_status": evaluation_json["eligibility_status"],
        "evaluation_confidence": evaluation_json["evaluation_confidence"],
        "reason": final_reason,
        "evaluation_json": evaluation_json,
    }


def prequalify_gem_listing(candidate: dict, capability: dict) -> dict:
    """Cheap listing-only gate before any PDF download.

    Use only GeM card/listing fields (title/department/organisation/keyword) so
    obviously irrelevant tenders are rejected without spending bandwidth, PDF
    parsing time, or LLM tokens.
    """
    listing = {
        "title": candidate.get("title"),
        "make": candidate.get("title"),
        "item_category": candidate.get("title"),
        "primary_product": candidate.get("title"),
        "organization_name": candidate.get("organisation"),
        "department_name": candidate.get("department"),
        "office_name_location": None,
        "matched_keywords_text": "",
        "boq_items": [],
        "required_documents": [],
        "total_quantity": candidate.get("quantity"),
        "tender_approx_value": None,
    }
    blocks = _collect_text_blocks(listing, "")
    matched_brands, brand_context = _classify_brand_context(capability, blocks)
    matched_products = _match_products(capability, blocks)
    business_category, category_gate = _classify_business_category(blocks["combined_text"])
    strong_brand_match = bool(matched_brands) and brand_context in {"PRIMARY_REQUIRED_BRAND", "ACCEPTED_EQUIVALENT_BRAND"}
    strong_product_match = bool(matched_products) or business_category in {"industrial_automation", "industrial_electrical", "automation_amc_service"}

    hard_failures = []
    risks = []
    strengths = []
    manual_checks = []

    if category_gate == "reject_scope" and not (strong_brand_match or strong_product_match):
        hard_failures.append(f"Listing scope is outside industrial automation ({business_category.replace('_', ' ')}).")

    automation_related = business_category in {"industrial_automation", "industrial_electrical", "automation_amc_service"}
    if not matched_products and not matched_brands and not automation_related:
        hard_failures.append("Listing does not show any relevant automation product or required brand.")

    if brand_context in {"REFERENCE_ONLY", "UNRELATED_MENTION"} and not matched_products and not matched_brands:
        hard_failures.append("Brand keyword is not shown as the actual required product scope in the listing.")

    if STRICT_LISTING_GATE and not strong_brand_match and not strong_product_match:
        hard_failures.append("Strict listing gate failed: listing does not clearly show a handled brand or automation product term.")

    bed = _parse_date(candidate.get("bid_end_date"))
    if bed:
        days_remaining = (bed - date.today()).days
        if days_remaining < MIN_DEADLINE_DAYS:
            risks.append(f"Tender closing date is too near ({days_remaining} day(s) remaining).")
            manual_checks.append("Deadline is tight. Review commercial feasibility quickly before bidding.")
        elif days_remaining <= MIN_DEADLINE_DAYS + 2:
            risks.append(f"Bid deadline is tight ({days_remaining} day(s) remaining).")

    if matched_products:
        strengths.append("Listing title shows automation-relevant product/category terms.")
    if matched_brands and brand_context in {"PRIMARY_REQUIRED_BRAND", "ACCEPTED_EQUIVALENT_BRAND"}:
        strengths.append("Listing title contains a relevant handled brand: " + ", ".join(matched_brands[:4]))

    positive_signal = strong_brand_match or strong_product_match or automation_related

    if hard_failures and not positive_signal:
        score = 0.0
        decision = "REJECT"
        eligibility_status = "REJECTED"
        summary = "Listing was rejected before PDF download because it does not look commercially relevant."
    elif positive_signal:
        score = 6.5 if strong_product_match else 7.0 if strong_brand_match else 5.5
        decision = "PROCEED"
        eligibility_status = "QUEUED"
        if hard_failures:
            summary = "Listing has positive automation signals, so it will proceed despite some ambiguity."
            manual_checks.append("Positive automation signal found in listing. Allow PDF/full evaluation before final decision.")
        else:
            summary = "Listing passed the cheap relevance gate and can proceed to PDF download."
    else:
        score = 4.0
        decision = "REVIEW"
        eligibility_status = "REVIEW"
        manual_checks.append("Listing is ambiguous. Review manually before downloading the PDF.")
        summary = "Listing is ambiguous and was held back before PDF download."

    evaluation_json = {
        "method": "listing_prefilter",
        "decision": decision,
        "recommended_action": decision,
        "matched_brands": matched_brands,
        "matched_products": matched_products,
        "brand_context": brand_context,
        "business_category": business_category,
        "eligibility_status": eligibility_status,
        "risk_level": _risk_level(hard_failures, score),
        "profit_potential": "UNKNOWN",
        "missing_documents": [],
        "rejection_reason": "; ".join(_clean_list(hard_failures, limit=5)) if hard_failures else None,
        "ai_explanation": summary,
        "hard_failures": _clean_list(hard_failures, limit=6),
        "strengths": _clean_list(strengths, limit=4),
        "risks": _clean_list(risks, limit=4),
        "manual_checks_needed": _clean_list(manual_checks, limit=4),
        "phase": "pre_pdf_gate",
    }
    return {
        "should_download_pdf": decision == "PROCEED",
        "score": score,
        "reason": _build_reason(summary, strengths=strengths, risks=risks, hard_failures=hard_failures, manual_checks=manual_checks),
        "status": eligibility_status,
        "evaluation_json": evaluation_json,
    }


KEYWORD_EVAL_SYSTEM_PROMPT = """You are evaluating whether a GeM tender should even proceed to full evaluation for an industrial automation supplier/reseller/system integrator.

You will receive only a compact keyword evidence packet, not the full tender. Judge how the matched keyword is being used.

Return ONLY valid JSON with this exact structure:
{
  "keyword_context_type": "PRIMARY_REQUIRED_BRAND",
  "matched_product_keywords": [],
  "negative_keywords_found": [],
  "criteria": {
    "brand_keyword_context": {"score": 0, "justification": ""},
    "product_category_match": {"score": 0, "justification": ""},
    "company_business_match": {"score": 0, "justification": ""},
    "commercial_usefulness": {"score": 0, "justification": ""},
    "risk_rejection_signal": {"score": 0, "justification": ""}
  },
  "overall_score": 0,
  "keyword_decision": "ACCEPT_FOR_FULL_EVALUATION",
  "reason": ""
}

Allowed keyword_context_type values only:
- PRIMARY_REQUIRED_BRAND
- ACCEPTED_EQUIVALENT_BRAND
- PRODUCT_SPECIFICATION_MATCH
- REFERENCE_ONLY
- EXISTING_SYSTEM_ONLY
- SERVICE_OR_AMC_ONLY
- UNRELATED_MENTION

Scoring:
- brand_keyword_context: 0 to 3
- product_category_match: 0 to 3
- company_business_match: 0 to 2
- commercial_usefulness: 0 to 1
- risk_rejection_signal: 0 to 1

Decision:
- overall_score >= 8 => ACCEPT_FOR_FULL_EVALUATION
- overall_score 6 to 7.9 => REVIEW
- overall_score < 6 => REJECT

Do not approve only because a brand name appears. Consider how the keyword is used. Example: 'ABB VFD' is useful, but 'existing ABB panel AMC manpower' is not strongly useful."""


def _keyword_pre_eval_rule_only(candidate: dict, tender: dict, capability: dict, tender_text: str) -> dict:
    packet = _build_keyword_evidence_packet(candidate, tender, capability, tender_text)
    matched_products = packet["matched_product_keywords"]
    matched_brands = packet["matched_brands"]
    negative_keywords = packet["negative_keywords_found"]
    contexts = packet["contexts"]
    title_text = _norm(candidate.get("title") or "")
    item_category_text = _norm(packet.get("item_category") or "")
    all_context_text = " ".join(context["text"] for context in contexts)

    useful_contexts = []
    brand_product_contexts = []
    service_only_contexts = []
    has_equivalent_signal = False
    has_reference_signal = False
    has_existing_signal = False

    for context in contexts:
        text = context["text"]
        context_brands = _clean_list(_find_context_brands(text, _canonical_brand_map(capability)), limit=6)
        context_products = _clean_list(_find_context_products(text, _build_product_terms(capability)), limit=8)
        negative_here = _extract_negative_keywords(text)
        if "equivalent" in _norm(text):
            has_equivalent_signal = True
        if any(token in _norm(text) for token in ["existing", "installed", "already installed"]):
            has_existing_signal = True
        if any(token in _norm(text) for token in ["authorization", "compliance", "reference", "past experience"]):
            has_reference_signal = True
        if context_brands or context_products:
            useful_contexts.append({
                "kind": context["kind"],
                "text": text,
                "brands": context_brands,
                "products": context_products,
                "negative_keywords": negative_here,
            })
        if context_brands and context_products:
            brand_product_contexts.append(text)
        if negative_here and not context_products:
            if any(token in " ".join(negative_here) for token in ["service/amc", "manpower/labour"]):
                service_only_contexts.append(text)

    strong_context_match = bool(brand_product_contexts)
    useful_item_category = bool(_find_context_products(packet.get("item_category") or "", _build_product_terms(capability)))
    quantity_available = packet.get("quantity") not in (None, "", "—")

    score_breakdown = {
        "brand_found": 2.0 if matched_brands else 0.0,
        "useful_product_found": 3.0 if matched_products else 0.0,
        "brand_product_same_context": 3.0 if strong_context_match else 0.0,
        "quantity_available": 0.5 if quantity_available else 0.0,
        "useful_item_category": 1.0 if useful_item_category else 0.0,
        "negative_keyword_penalty": -3.0 if negative_keywords else 0.0,
        "brand_without_product_penalty": -2.0 if matched_brands and not matched_products else 0.0,
        "service_only_penalty": -4.0 if service_only_contexts and not strong_context_match else 0.0,
    }
    raw_score = sum(score_breakdown.values())
    score = round(max(0.0, min(10.0, raw_score)), 1)

    if strong_context_match and has_equivalent_signal:
        context_type = "ACCEPTED_EQUIVALENT_BRAND"
    elif strong_context_match and matched_brands:
        context_type = "PRIMARY_REQUIRED_BRAND"
    elif matched_products:
        context_type = "PRODUCT_SPECIFICATION_MATCH"
    elif service_only_contexts:
        context_type = "SERVICE_OR_AMC_ONLY"
    elif has_existing_signal and matched_brands and not matched_products:
        context_type = "EXISTING_SYSTEM_ONLY"
    elif has_reference_signal and matched_brands and not matched_products:
        context_type = "REFERENCE_ONLY"
    else:
        context_type = "UNRELATED_MENTION"

    if score >= 8:
        decision = "ACCEPT_FOR_FULL_EVALUATION"
    elif score >= 5:
        decision = "REVIEW"
    else:
        decision = "REJECT"

    if negative_keywords and not strong_context_match and not matched_products:
        decision = "REJECT"
        score = min(score, 4.5)
    elif service_only_contexts and not strong_context_match:
        decision = "REJECT" if score < 5.5 else "REVIEW"
        score = min(score, 5.5 if decision == "REVIEW" else 4.5)
    elif matched_brands and not matched_products:
        decision = "REVIEW" if score >= 5 else "REJECT"
    elif strong_context_match and negative_keywords:
        decision = "REVIEW" if score < 8 else "ACCEPT_FOR_FULL_EVALUATION"
    if strong_context_match and matched_brands and matched_products and not negative_keywords:
        decision = "ACCEPT_FOR_FULL_EVALUATION" if score >= 7 else "REVIEW"
        score = max(score, 7.0 if decision == "ACCEPT_FOR_FULL_EVALUATION" else 5.5)
    elif matched_brands and matched_products and not negative_keywords and decision == "REJECT":
        decision = "REVIEW"
        score = max(score, 5.0)
    elif score < 4 and not negative_keywords and decision == "REJECT":
        decision = "REVIEW"
        score = 4.0

    reason_bits = []
    if matched_brands:
        reason_bits.append("Matched brand: " + ", ".join(matched_brands[:4]))
    if matched_products:
        reason_bits.append("Matched product: " + ", ".join(matched_products[:5]))
    if brand_product_contexts:
        reason_bits.append("Brand and product appear in the same tender context.")
    if useful_item_category:
        reason_bits.append("Item category is automation-relevant.")
    if quantity_available:
        reason_bits.append("Quantity is available.")
    if negative_keywords:
        reason_bits.append("Negative scope signals: " + ", ".join(negative_keywords[:4]))
    if matched_brands and not matched_products:
        reason_bits.append("Brand appears without a useful automation product keyword.")
    if service_only_contexts:
        reason_bits.append("Tender context looks service/AMC/manpower oriented.")
    if not reason_bits:
        reason_bits.append("No strong automation brand-product fit found in extracted tender fields.")
    reason = " ".join(reason_bits)

    return {
        "keyword_pre_score": score,
        "keyword_decision": decision,
        "keyword_fit_score": score,
        "keyword_fit_decision": "APPROVE" if decision == "ACCEPT_FOR_FULL_EVALUATION" else decision,
        "matched_keywords": packet["matched_keywords"],
        "matched_brands": matched_brands,
        "matched_products": matched_products,
        "matched_product_keywords": matched_products,
        "negative_keywords": negative_keywords,
        "negative_keywords_found": negative_keywords,
        "keyword_context_type": context_type,
        "keyword_fit_reason": reason,
        "keyword_evaluation_reason": reason,
        "confidence": "LOW" if negative_keywords or service_only_contexts else "MEDIUM" if matched_brands or matched_products else "LOW",
        "requires_full_evaluation": decision == "ACCEPT_FOR_FULL_EVALUATION",
        "keyword_evaluation_json": {
            "method": "keyword_rule_based",
            "evidence_packet": packet,
            "criteria": score_breakdown,
            "context_type": context_type,
            "matched_brands": matched_brands,
            "matched_products": matched_products,
            "negative_keywords_found": negative_keywords,
            "brand_product_contexts": _clean_list(brand_product_contexts, limit=4),
            "brand_product_same_context": strong_context_match,
            "hard_negative_found": bool(negative_keywords and not matched_products),
            "hard_negative_reasons": negative_keywords if negative_keywords and not matched_products else [],
            "reason": reason,
        },
    }


def _llm_keyword_pre_eval(candidate: dict, tender: dict, capability: dict, tender_text: str) -> dict:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key or api_key.strip() in {"", "YOUR_OPENAI_KEY_HERE"}:
        raise RuntimeError("OPENAI_API_KEY not configured")

    from openai import OpenAI

    packet = _build_keyword_evidence_packet(candidate, tender, capability, tender_text)
    if not packet["matched_keywords"]:
        return _keyword_pre_eval_rule_only(candidate, tender, capability, tender_text)

    client = OpenAI(api_key=api_key)
    user_content = (
        "=== COMPANY PROFILE ===\n"
        + _build_profile_summary(capability)
        + "\n\n=== KEYWORD EVIDENCE PACKET ===\n"
        + json.dumps(packet, ensure_ascii=False)
    )
    response = client.chat.completions.create(
        model=KEYWORD_EVAL_MODEL,
        messages=[
            {"role": "system", "content": KEYWORD_EVAL_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        response_format={"type": "json_object"},
        temperature=0,
        max_tokens=1400,
    )
    llm = json.loads(response.choices[0].message.content)
    try:
        score = round(float(llm.get("overall_score", 0)), 1)
    except (TypeError, ValueError):
        score = 0.0
    score = max(0.0, min(10.0, score))
    decision = _clean_text(llm.get("keyword_decision")).upper() or (
        "ACCEPT_FOR_FULL_EVALUATION" if score >= 8 else "REVIEW" if score >= 6 else "REJECT"
    )
    context_type = _clean_text(llm.get("keyword_context_type"))
    if context_type not in KEYWORD_CONTEXT_TYPES:
        context_type = "UNRELATED_MENTION"
    reason = _clean_text(llm.get("reason")) or "Keyword pre-evaluation completed."
    matched_product_keywords = _clean_list(llm.get("matched_product_keywords")) or packet["matched_product_keywords"]
    negative_keywords_found = _clean_list(llm.get("negative_keywords_found")) or packet["negative_keywords_found"]
    if negative_keywords_found and not matched_product_keywords and decision == "ACCEPT_FOR_FULL_EVALUATION":
        decision = "REVIEW"
        score = min(score, 7.5)
    return {
        "keyword_pre_score": score,
        "keyword_decision": decision,
        "keyword_fit_score": score,
        "keyword_fit_decision": "APPROVE" if decision == "ACCEPT_FOR_FULL_EVALUATION" else decision,
        "matched_keywords": packet["matched_keywords"],
        "matched_brands": packet["matched_brands"],
        "matched_products": matched_product_keywords,
        "matched_product_keywords": matched_product_keywords,
        "negative_keywords": negative_keywords_found,
        "negative_keywords_found": negative_keywords_found,
        "keyword_context_type": context_type,
        "keyword_fit_reason": reason,
        "keyword_evaluation_reason": reason,
        "confidence": _clean_text(llm.get("confidence")) or "MEDIUM",
        "requires_full_evaluation": decision == "ACCEPT_FOR_FULL_EVALUATION",
        "keyword_evaluation_json": {
            "method": "keyword_llm",
            "evidence_packet": packet,
            "llm": llm,
            "reason": reason,
        },
    }


def keyword_pre_evaluate_gem_candidate(candidate: dict, tender: dict, capability: dict, tender_text: str) -> dict:
    if KEYWORD_PRE_EVAL_MODE != "llm":
        return _keyword_pre_eval_rule_only(candidate, tender, capability, tender_text)
    try:
        return _llm_keyword_pre_eval(candidate, tender, capability, tender_text)
    except Exception as e:
        print(f"[gem_watcher] keyword pre-eval failed ({type(e).__name__}: {e}) - falling back to rule-based.")
        return _keyword_pre_eval_rule_only(candidate, tender, capability, tender_text)


def evaluate_gem_candidate(tender: dict, capability: dict, bid_end_date=None, tender_text: str = None) -> dict:
    """Score a GeM candidate out of 10 and return APPROVED / REVIEW / REJECTED."""
    local_eval = _weighted_local_eval(tender, capability, bid_end_date, tender_text=tender_text)
    local_json = local_eval.get("evaluation_json") or {}
    has_positive_automation_match = bool(local_json.get("matched_brands")) and bool(local_json.get("matched_products"))
    brand_product_same_context = bool(local_json.get("brand_product_same_context"))
    hard_negative_found = bool(local_json.get("hard_negative_found"))
    if (
        not has_positive_automation_match
        and not brand_product_same_context
        and (local_eval["eligibility_status"] == "REJECTED" or float(local_eval.get("score", 0) or 0) < LLM_MIN_RULE_SCORE)
        and hard_negative_found
    ):
        return local_eval

    if tender_text:
        try:
            llm = _llm_evaluate(tender, capability, tender_text, local_eval)
            return _finalize_from_llm(llm, local_eval)
        except Exception as e:
            print(f"[gem_watcher] LLM evaluation failed ({type(e).__name__}: {e}) - falling back to rule-based.")
    return local_eval
