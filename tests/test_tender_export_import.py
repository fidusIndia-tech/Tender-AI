"""Unit tests for the round-trippable tender JSON export/import mapping.

These cover the pure (DB-free) logic: the status enum <-> section mapping that
keeps imported tenders in their correct All Tenders section, and the BOQ-item
shape mapping. The DB-touching functions (export_company_tenders /
import_company_tenders) are exercised end-to-end against Postgres separately.
"""

from tender_app import database as db


def test_every_export_status_maps_to_a_valid_section():
    valid_sections = {
        "IN PROGRESS", "FILED", "QUALIFIED", "DISQUALIFIED", "WON", "LOST", "FAILED",
    }
    for enum, section in db.EXPORT_STATUS_TO_PARTICIPATION.items():
        assert section in valid_sections, (enum, section)


def test_section_mapping_matches_spec():
    m = db.EXPORT_STATUS_TO_PARTICIPATION
    assert m["NEW"] == m["SAVED"] == m["UNDER_REVIEW"] == "IN PROGRESS"
    assert m["FILED"] == m["PARTICIPATED"] == "FILED"
    assert m["REVIEWED"] == "QUALIFIED"
    assert m["REJECTED"] == "DISQUALIFIED"
    assert m["WON"] == "WON"
    assert m["LOST"] == "LOST"
    assert m["FAILED"] == "FAILED"


def test_representative_status_round_trips_through_section():
    # Export picks a representative enum per section; re-importing it must land
    # the tender back in the same section.
    for section, rep in db.PARTICIPATION_TO_EXPORT_STATUS.items():
        assert db.EXPORT_STATUS_TO_PARTICIPATION[rep] == section, (section, rep)


def test_normalize_export_status_defaults_to_under_review():
    assert db.normalize_export_status("garbage") == "UNDER_REVIEW"
    assert db.normalize_export_status(None) == "UNDER_REVIEW"
    assert db.normalize_export_status("") == "UNDER_REVIEW"
    assert db.normalize_export_status("won") == "WON"
    assert db.normalize_export_status("under review") == "UNDER_REVIEW"


def test_effective_export_status_prefers_stored_enum():
    assert db._effective_export_status({"status_enum": "NEW"}) == "NEW"
    # Falls back to a representative derived from participation_status.
    assert db._effective_export_status({"participation_status": "IN PROGRESS"}) == "UNDER_REVIEW"
    assert db._effective_export_status({"participation_status": "WON"}) == "WON"
    assert db._effective_export_status({}) == "UNDER_REVIEW"


def test_boq_items_from_top_level_and_nested_extractions():
    top = {"boqItems": [{"itemNumber": "1", "itemTitle": "Pump", "makeModel": "Bosch", "quantity": "2", "unit": "Nos"}]}
    items = db._boq_items_from_export(top)
    assert items[0] == {
        "part_number": "1", "item_description": "Pump",
        "quantity": "2", "make_model": "Bosch", "unit": "Nos",
    }

    nested = {"extractions": [{"data": {"boqItems": [{"itemNumber": "9", "itemTitle": "X", "quantity": "3"}]}}]}
    items = db._boq_items_from_export(nested)
    assert items[0]["part_number"] == "9"
    assert items[0]["item_description"] == "X"


if __name__ == "__main__":
    import traceback
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                passed += 1
                print(f"PASS {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
