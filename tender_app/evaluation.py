import re


def _norm_text(*parts) -> str:
    return " ".join(str(p or "") for p in parts).lower()


def _tokens(value: str) -> list:
    return [t.strip().lower() for t in re.split(r"[,;\n/|]+", value or "") if t.strip()]


def _money_to_number(value: str):
    if not value:
        return None
    text = str(value).lower().replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)", text)
    if not m:
        return None
    amount = float(m.group(1))
    if "crore" in text or " cr" in text:
        amount *= 10_000_000
    elif "lakh" in text or " lac" in text:
        amount *= 100_000
    return amount


def _range_contains(range_text: str, amount):
    if amount is None or not range_text:
        return None
    matches = re.findall(r"\d[\d,]*(?:\.\d+)?\s*(?:crore|cr|lakh|lac)?", str(range_text), re.I)
    numbers = [_money_to_number(x) for x in matches]
    numbers = [n for n in numbers if n is not None]
    if len(numbers) >= 2:
        low, high = min(numbers), max(numbers)
        return low <= amount <= high
    if len(numbers) == 1:
        text = str(range_text).lower()
        if any(w in text for w in ("up to", "upto", "below", "<", "under")):
            return amount <= numbers[0]
        if any(w in text for w in ("above", "more than", ">", "over")):
            return amount >= numbers[0]
    return None


def evaluate_tender_against_capability(tender: dict, capability: dict) -> dict:
    items = tender.get("boq_items") or []
    docs = tender.get("required_documents") or []
    item_text = " ".join(_norm_text(i.get("part_number"), i.get("item_description"), i.get("quantity")) for i in items)
    doc_text = " ".join(_norm_text(d.get("label")) for d in docs)
    tender_text = _norm_text(
        tender.get("gem_bidding_number"), tender.get("tender_number"), tender.get("department_name"),
        tender.get("organization_name"), tender.get("office_name_location"), tender.get("make"),
        tender.get("tender_approx_value"), tender.get("total_quantity"), item_text, doc_text,
    )

    score = 50
    checks = []
    strengths = []
    risks = []

    def add_check(name, status, impact, detail):
        nonlocal score
        score += impact
        checks.append({"name": name, "status": status, "impact": impact, "detail": detail})
        if impact > 0:
            strengths.append(detail)
        elif impact < 0:
            risks.append(detail)

    product_matches = [t for t in _tokens(capability.get("product_categories")) if t in tender_text]
    if product_matches:
        add_check("Product category", "match", 18, "Matches product category: " + ", ".join(product_matches[:5]))
    elif capability.get("product_categories"):
        add_check("Product category", "missing", -14, "No clear match with saved product categories")
    else:
        add_check("Product category", "unknown", -4, "Product categories are not filled in Company Capability Profile")

    brand_matches = [t for t in _tokens(capability.get("brands_handled")) if t in tender_text]
    if brand_matches:
        add_check("Brand", "match", 14, "Matches handled brand: " + ", ".join(brand_matches[:5]))
    elif capability.get("brands_handled"):
        add_check("Brand", "missing", -8, "No clear match with saved brands handled")
    else:
        add_check("Brand", "unknown", -2, "Brands handled are not filled")

    years = capability.get("years_experience")
    if years is None:
        add_check("Experience", "unknown", -3, "Year established is not filled")
    elif years >= 3:
        add_check("Experience", "ready", 8, f"Company experience is {years} years")
    else:
        add_check("Experience", "risk", -7, f"Company experience is only {years} years")

    amount = _money_to_number(tender.get("tender_approx_value"))
    typical_fit = _range_contains(capability.get("typical_tender_value_range"), amount)
    if typical_fit is True:
        add_check("Tender value", "fit", 10, "Tender value fits typical tender value range")
    elif typical_fit is False:
        add_check("Tender value", "risk", -10, "Tender value is outside typical tender value range")
    else:
        add_check("Tender value", "unknown", -2, "Tender value or typical range is not clear")

    turnover_fit = _range_contains(capability.get("turnover_range"), amount)
    if turnover_fit is True:
        add_check("Turnover", "fit", 7, "Tender value appears suitable for turnover range")
    elif turnover_fit is False:
        add_check("Turnover", "risk", -8, "Tender value may be high for turnover range")

    required_docs = {
        "GST": capability.get("gst_available"),
        "PAN": capability.get("pan_available"),
        "MSME": capability.get("msme_available"),
        "ITR": capability.get("itr_available"),
        "Bank": capability.get("bank_documents_available"),
        "Letterhead": capability.get("letterhead_available"),
        "Stamp": capability.get("stamp_available"),
        "Signature": capability.get("signature_available"),
    }
    doc_hits = [name for name in required_docs if name.lower() in doc_text]
    missing_docs = [name for name in doc_hits if not required_docs[name]]
    if missing_docs:
        add_check("Documents", "risk", -5 * len(missing_docs), "Missing profile documents: " + ", ".join(missing_docs))
    elif doc_hits:
        add_check("Documents", "ready", 10, "Required profile documents appear available")
    else:
        add_check("Documents", "unknown", 0, "No mapped mandatory profile documents found in extracted requirements")

    needs_oem = "oem" in tender_text or "authorization" in tender_text or "authorisation" in tender_text
    if needs_oem and (capability.get("oem_support_available") or capability.get("oem_authorizations")):
        add_check("OEM support", "ready", 10, "Tender mentions OEM/authorization and OEM support is available")
    elif needs_oem:
        add_check("OEM support", "risk", -12, "Tender may require OEM support/authorization, but profile does not confirm it")
    elif capability.get("oem_support_available"):
        add_check("OEM support", "ready", 4, "OEM support is available if needed")

    govt_like = any(w in tender_text for w in ("government", "govt", "psu", "ministry", "department", "gem"))
    if govt_like and (capability.get("government_experience") or capability.get("psu_experience")):
        add_check("Govt/PSU experience", "ready", 8, "Government/PSU experience is available")
    elif govt_like:
        add_check("Govt/PSU experience", "risk", -6, "Tender appears government/PSU related, but experience is not confirmed")

    score = max(0, min(100, score))
    decision = "BID" if score >= 70 else "REVIEW" if score >= 45 else "SKIP"
    return {
        "tender_id": tender.get("id"),
        "decision": decision,
        "score": round(score),
        "summary": f"{decision} recommendation based on FIAPL capability profile and extracted tender data.",
        "strengths": strengths[:6],
        "risks": risks[:6],
        "checks": checks,
    }
