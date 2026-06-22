"""The raw->QTO orchestrator must produce counts (tag occurrences) and a complete
takeoff with all three unit types. Self-contained synthetic plan for the parts
that don't need the slow engine / Gemini."""
import fitz

from backend import takeoff


def _plan(tmp_path, tokens):
    doc = fitz.open()
    page = doc.new_page(width=800, height=600)
    x, y = 60, 60
    for tok in tokens:
        page.insert_text((x, y), tok, fontsize=9)
        x += 90
        if x > 720:
            x, y = 60, y + 40
    p = tmp_path / "plan.pdf"
    doc.save(str(p))
    doc.close()
    return str(p)


def test_count_by_code_counts_tag_instances(tmp_path):
    # 3 benches (B1), 2 fire pits (C4), 1 paver (M.5)
    p = _plan(tmp_path, ["B1", "B1", "B1", "C4", "C4", "M.5"])
    counts = takeoff.count_by_code(p, 0)
    assert counts["B1"] == 3
    assert counts["C4"] == 2
    assert counts["M.5"] == 1


def test_dedupes_overlapping_tags(tmp_path):
    # two tags within 8pt collapse to one instance
    doc = fitz.open()
    page = doc.new_page(width=400, height=400)
    page.insert_text((100, 100), "B1", fontsize=9)
    page.insert_text((103, 100), "B1", fontsize=9)   # ~3pt away -> same instance
    page.insert_text((200, 100), "B1", fontsize=9)   # distinct
    p = tmp_path / "d.pdf"
    doc.save(str(p))
    doc.close()
    assert takeoff.count_by_code(str(p), 0)["B1"] == 2
