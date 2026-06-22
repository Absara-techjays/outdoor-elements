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
                  dpi: int = 170) -> dict[str, int]:
    """Total count of each plant code on one planting-plan page."""
    key = _api_key(api_key)
    genai.configure(api_key=key)
    model = genai.GenerativeModel(MODEL)
    jpeg = gemini_config.render_page_jpeg(pdf, page, dpi=dpi)
    resp = model.generate_content([COUNT_PROMPT, {"mime_type": "image/jpeg", "data": jpeg}])
    return _parse_counts(resp.text or "")
