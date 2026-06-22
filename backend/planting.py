"""Planting-plan takeoff via Gemini 3.1 Pro.

A planting plan's takeoff is a SCHEDULE legend of per-species COUNTS (trees /
palms / shrubs, each) plus AREA items (groundcover / perennial / annual color /
sod, sq ft) — like the human QTO. Plants are labeled individually or in clusters
with a quantity (e.g. "BG 15"), and codes don't map cleanly from text, so we let
gemini-3.1-pro-preview read the sheet and total each code.

    from backend import planting
    counts = planting.count_species(raw_pdf, page)   # {'IC': 44, 'BG': 199, ...}
"""
from __future__ import annotations

import json
import os
import re

import google.generativeai as genai

from . import gemini_config

MODEL = "gemini-3.1-pro-preview"

COUNT_PROMPT = """This is a landscape PLANTING PLAN with a plant SCHEDULE / legend.
Plants are marked on the plan with code labels (e.g. IC, BG, QVE-1, P-FJ, WR).
Some plants are labeled individually; many are CLUSTERS labeled with the code and
a QUANTITY number (e.g. "BG 15" means 15 of plant BG in that cluster).

Count the TOTAL quantity of EACH plant code on this plan — sum the cluster
quantities, and count individually-labeled plants as 1 each.

Return STRICT JSON only, no prose:
[{"code":"<code exactly as written>","count":<integer total>}]"""


SCHEDULE_PROMPT = """This is a landscape PLANT SCHEDULE / legend table mapping plant
CODES to botanical / common names, grouped by category (Canopy Trees, Palm Trees,
Understory Trees, Shrubs, Perennials, Groundcovers, Vines, Annual Color, Sod...).

Return STRICT JSON only, one object per row:
[{"code":"<code exactly as written>","name":"<common name>","category":"<category>"}]
Include EVERY row. JSON only, no prose."""

# categories whose items are COUNTED (each) vs measured by AREA (sq ft)
_COUNT_CATS = ("tree", "palm", "shrub")
_AREA_CATS = ("groundcover", "ground cover", "perennial", "vine", "annual", "sod", "grass")


def find_schedule_page(pdf: str) -> int | None:
    """Page holding the plant schedule (a table with botanical names)."""
    import fitz
    doc = fitz.open(pdf)
    try:
        for i in range(len(doc)):
            up = doc[i].get_text().upper()
            if "SCHEDULE" in up and ("BOTANICAL" in up or "COMMON NAME" in up):
                return i
    finally:
        doc.close()
    return None


def read_schedule(pdf: str, page: int | None = None, api_key: str | None = None,
                  dpi: int = 160) -> list[dict]:
    """Vision-read the plant schedule -> [{code, name, category, unit}]. unit is
    'count' for trees/palms/shrubs, 'area' for groundcover/perennial/sod/etc."""
    if page is None:
        page = find_schedule_page(pdf)
    if page is None:
        return []
    key = _api_key(api_key)
    genai.configure(api_key=key)
    model = genai.GenerativeModel(MODEL)
    jpeg = gemini_config.render_page_jpeg(pdf, page, dpi=dpi)
    resp = model.generate_content([SCHEDULE_PROMPT, {"mime_type": "image/jpeg", "data": jpeg}])
    return _parse_schedule(resp.text or "")


def _parse_schedule(text: str) -> list[dict]:
    t = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.I | re.M).strip()
    m = re.search(r"\[.*\]", t, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    out = []
    for d in data if isinstance(data, list) else []:
        if not isinstance(d, dict) or not d.get("code"):
            continue
        cat = str(d.get("category", "")).lower()
        unit = "count" if any(k in cat for k in _COUNT_CATS) else \
               ("area" if any(k in cat for k in _AREA_CATS) else "count")
        out.append({"code": str(d["code"]).strip().upper(),
                    "name": str(d.get("name", "")).strip(),
                    "category": cat, "unit": unit})
    return out


def _api_key(explicit: str | None) -> str:
    key = explicit or os.environ.get("GEMINI_API_KEY")
    if not key:
        from dotenv import load_dotenv
        from . import store
        load_dotenv(store.BACKEND_DIR.parent / ".env")
        key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set")
    return key


def _parse_counts(text: str) -> dict[str, int]:
    t = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.I | re.M).strip()
    m = re.search(r"\[.*\]", t, re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}
    out: dict[str, int] = {}
    for d in data if isinstance(data, list) else []:
        if not isinstance(d, dict):
            continue
        code = str(d.get("code", "")).strip().upper()
        try:
            n = int(round(float(d.get("count", 0))))
        except (TypeError, ValueError):
            continue
        if code and n > 0:
            out[code] = out.get(code, 0) + n
    return out


def count_species(pdf: str, page: int, api_key: str | None = None,
                  dpi: int = 170, valid_codes: set | None = None) -> dict[str, int]:
    """Total count of each plant code on one planting-plan page (vision). When
    valid_codes is given, only those schedule codes are kept (anchoring)."""
    key = _api_key(api_key)
    genai.configure(api_key=key)
    model = genai.GenerativeModel(MODEL)
    prompt = COUNT_PROMPT
    if valid_codes:
        prompt += "\nOnly use these plant codes: " + ", ".join(sorted(valid_codes))
    jpeg = gemini_config.render_page_jpeg(pdf, page, dpi=dpi)
    resp = model.generate_content([prompt, {"mime_type": "image/jpeg", "data": jpeg}])
    c = _parse_counts(resp.text or "")
    if valid_codes:
        vc = {v.upper() for v in valid_codes}
        c = {k: n for k, n in c.items() if k in vc}
    return c


import fitz  # noqa: E402

_CODE_TOK = re.compile(r"^[A-Z]{1,4}(?:-[A-Z0-9]{1,3})?$")


def text_label_counts(pdf: str, page: int, valid_codes: set | None = None) -> dict[str, int]:
    """Number of code LABELS per plant code on the page (deterministic). Accurate
    for individually-labeled plants (trees/palms); a floor for dense beds."""
    doc = fitz.open(pdf)
    words = doc[page].get_text("words")
    doc.close()
    vc = {v.upper() for v in valid_codes} if valid_codes else None
    out: dict[str, int] = {}
    for w in words:
        t = w[4].strip().upper()
        if _CODE_TOK.match(t) and (vc is None or t in vc):
            out[t] = out.get(t, 0) + 1
    return out


def planting_count_rows(pdf: str, count_pages: list[int], schedule_page: int | None = None,
                        api_key: str | None = None) -> tuple[list[dict], list[dict]]:
    """Fully-automatic planting count: read the schedule for the anchor codes,
    hybrid-count each on the planting pages, return takeoff rows (unit 'count').
    No QTO needed. Returns (rows, schedule)."""
    sched = read_schedule(pdf, schedule_page, api_key)
    count_codes = {s["code"] for s in sched if s["unit"] == "count"}
    names = {s["code"]: s["name"] for s in sched}
    if not count_codes:
        return [], sched
    counts = hybrid_counts(pdf, count_pages, valid_codes=count_codes, api_key=api_key)
    rows = []
    for code, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        if n > 0:
            rows.append({"code": code, "name": names.get(code, code), "unit": "count",
                         "detect": "symbol", "quantity": n, "unit_label": "each",
                         "source": "planting"})
    return rows, sched


def label_positions(pdf: str, page: int, valid_codes: set | None = None) -> dict[str, list]:
    """Plant code label centers (PDF points) per code on the page."""
    doc = fitz.open(pdf)
    words = doc[page].get_text("words")
    doc.close()
    vc = {v.upper() for v in valid_codes} if valid_codes else None
    out: dict[str, list] = {}
    for w in words:
        t = w[4].strip().upper()
        if _CODE_TOK.match(t) and (vc is None or t in vc):
            out.setdefault(t, []).append(((w[0] + w[2]) / 2, (w[1] + w[3]) / 2))
    return out


def render_planting_overlay(pdf: str, page: int, sched: list[dict], out_path: str,
                            dpi: int = 150) -> str:
    """Render the plan with each plant label COLORED by species (automatic, during
    extraction) — so a planting page shows colored plants like the human takeoff."""
    import colorsys
    from PIL import Image, ImageDraw
    doc = fitz.open(pdf)
    pg = doc[page]
    pix = pg.get_pixmap(dpi=dpi)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples).convert("RGB")
    W, H, pw, ph = pix.width, pix.height, pg.rect.width, pg.rect.height
    doc.close()
    count_codes = {s["code"] for s in sched if s["unit"] == "count"}
    pos = label_positions(pdf, page, count_codes)
    dr = ImageDraw.Draw(img, "RGBA")
    r = max(7, int(W / 380))
    for i, code in enumerate(sorted(pos)):
        cr, cg, cb = colorsys.hsv_to_rgb((i * 0.137) % 1.0, 0.62, 0.92)
        col = (int(cr * 255), int(cg * 255), int(cb * 255))
        for (x, y) in pos[code]:
            cx, cy = x / pw * W, y / ph * H
            dr.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col + (170,), outline=col, width=2)
    img.save(out_path)
    return out_path


def page_count_rows(pdf: str, page: int, sched: list[dict], api_key: str | None = None,
                    min_labels: int = 8) -> list[dict]:
    """Per-species count rows for ONE planting page, given the schedule. Gated on
    a DETERMINISTIC signal (>= min_labels plant labels on the page) so it only
    runs on real planting plans and never depends on a flaky vision tree count."""
    count_codes = {s["code"] for s in sched if s["unit"] == "count"}
    names = {s["code"]: s["name"] for s in sched}
    labels = text_label_counts(pdf, page, count_codes)
    if sum(labels.values()) < min_labels:
        return []   # not a planting page
    try:
        vis = count_species(pdf, page, api_key=api_key, valid_codes=count_codes)
    except Exception:  # noqa: BLE001
        vis = {}
    rows = []
    for code in count_codes:
        n = max(labels.get(code, 0), vis.get(code, 0))
        if n > 0:
            rows.append({"code": code, "name": names.get(code, code), "unit": "count",
                         "detect": "symbol", "quantity": n, "unit_label": "each",
                         "source": "planting"})
    return sorted(rows, key=lambda r: -r["quantity"])


def hybrid_counts(pdf: str, pages: list[int], valid_codes: set | None = None,
                  api_key: str | None = None) -> dict[str, int]:
    """Per-species count over several planting pages: max(label count, vision
    count) per code — labels nail individually-marked plants, vision catches the
    denser beds. Anchored to valid_codes (the schedule) when given."""
    total: dict[str, int] = {}
    for pg in pages:
        labels = text_label_counts(pdf, pg, valid_codes)
        try:
            vis = count_species(pdf, pg, api_key=api_key, valid_codes=valid_codes)
        except Exception:  # noqa: BLE001
            vis = {}
        for code in set(labels) | set(vis):
            total[code] = total.get(code, 0) + max(labels.get(code, 0), vis.get(code, 0))
    return total
