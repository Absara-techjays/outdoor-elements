"""Pool / spa surface detection — vision-guided, no estimate required.

Gemini vision looks at the rendered page image (with numbered boxes over
candidate regions) and names each region (POOL, SPA, TANNING LEDGE, etc.)
by reading the text labels and legend printed directly on the drawing.
Scale is parsed from the title-block text. No estimate PDF is needed.
"""
from __future__ import annotations

import io
import json
import logging
import re
import uuid
import warnings
from pathlib import Path

import cv2
import numpy as np

from . import qto_engine
from . import zones as zones_mod

log = logging.getLogger(__name__)

_SURFACE_COLOR = {
    "POOL":           (233, 30,  99),
    "SPA":            (0,  150, 136),
    "TANNING LEDGE":  (255, 193,  7),
    "SUN SHELF":      (255, 193,  7),
    "STONE STEPPERS": (121,  85, 72),
    "STEPS":          (121,  85, 72),
    "BENCH":          (96, 125, 139),
    "DECK":           (189, 189, 189),
}
_DEFAULT_COLOR = (158, 158, 158)

_IGNORE = {"BACKGROUND", "FP", "IGNORE", "NONE", "SCHEDULE", "TITLE", "TABLE"}


def detect_pool(pdf_path, page_idx: int, out_png, dpi: int = 150,
                api_key: str | None = None, clip_right: float = 0.78) -> dict:
    """Detect & measure pool/spa surfaces on one plan page.

    Uses Gemini vision to name each detected region from the drawing's own
    labels and legend. Scale is read from the title-block text.

    Returns:
        {surfaces, overlay, scale_in_per_ft, zones}
    """
    # ── 1. Find candidate enclosed regions (connected-component fill) ────────
    boundary = qto_engine.render_thick_boundaries(pdf_path, page_idx, dpi, min_lw=0.18)
    binary   = qto_engine.preprocess_for_fill(boundary)
    h, w     = binary.shape
    binary[:, int(w * clip_right):] = 0   # drop title-block / schedule column

    n, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=4)

    # min 0.1 % of sheet, max 50 % (avoids background and specks)
    cands = [
        (i, int(stats[i, cv2.CC_STAT_AREA]))
        for i in range(1, n)
        if 0.001 * h * w < int(stats[i, cv2.CC_STAT_AREA]) < 0.50 * h * w
    ]

    if not cands:
        zones_mod.render_from_zones(str(pdf_path), page_idx, [], Path(out_png), dpi=dpi)
        return {"surfaces": [], "overlay": Path(out_png).name,
                "scale_in_per_ft": 0.25, "zones": []}

    # ── 2. Scale from title-block text ───────────────────────────────────────
    scale_in_per_ft = _read_scale(pdf_path, page_idx)
    px_per_sf = (scale_in_per_ft * dpi) ** 2

    # ── 3. Name zones via Gemini (falls back to size-rank if unavailable) ────
    names: dict[int, str] = {}
    if api_key:
        try:
            names = _gemini_name_zones(
                pdf_path, page_idx, dpi, cands, stats, (h, w), api_key
            )
        except Exception as exc:
            log.warning("pool_mode: Gemini zone naming failed (%s) — using size rank", exc)

    if not names:
        _DEFAULTS = ["POOL", "SPA", "TANNING LEDGE", "STONE STEPPERS"]
        for rank, (idx, _) in enumerate(sorted(cands, key=lambda x: -x[1])):
            names[idx] = _DEFAULTS[rank] if rank < len(_DEFAULTS) else "BACKGROUND"

    # ── 4. Build zone dicts ──────────────────────────────────────────────────
    pt_scale = 72.0 / dpi
    zone_list: list[dict] = []

    for idx, area_px in cands:
        name = names.get(idx, "")
        if not name or name.upper() in _IGNORE:
            continue

        name = name.upper()
        mask  = (labels == idx).astype(np.uint8)
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        perim_px = sum(cv2.arcLength(c, True) for c in cnts)

        polys = []
        for cnt in cnts:
            simplified = cv2.approxPolyDP(cnt, 2.0, True)
            if len(simplified) >= 3:
                polys.append([
                    [float(p[0][0]) * pt_scale, float(p[0][1]) * pt_scale]
                    for p in simplified
                ])

        x0   = int(stats[idx, cv2.CC_STAT_LEFT])
        y0   = int(stats[idx, cv2.CC_STAT_TOP])
        cw   = int(stats[idx, cv2.CC_STAT_WIDTH])
        ch   = int(stats[idx, cv2.CC_STAT_HEIGHT])
        bbox = [x0 / w, y0 / h, (x0 + cw) / w, (y0 + ch) / h]

        if not polys:
            polys = [[[int(x0 * pt_scale),        int(y0 * pt_scale)],
                      [int((x0 + cw) * pt_scale),  int(y0 * pt_scale)],
                      [int((x0 + cw) * pt_scale),  int((y0 + ch) * pt_scale)],
                      [int(x0 * pt_scale),          int((y0 + ch) * pt_scale)]]]

        rgb       = _SURFACE_COLOR.get(name, _DEFAULT_COLOR)
        hex_color = "#%02x%02x%02x" % rgb
        area_sf   = round(area_px / px_per_sf, 1)
        perim_lf  = round(perim_px * (1.0 / dpi) / scale_in_per_ft, 1)

        zone_list.append({
            "id":           uuid.uuid4().hex[:16],
            "code":         name,
            "hex":          hex_color,
            "area_sqft":    area_sf,
            "perimeter_lf": perim_lf,
            "geometry":     polys,
            "bbox":         bbox,
            "source":       "pool",
            "status":       "active",
        })

    zones_mod.render_from_zones(str(pdf_path), page_idx, zone_list, Path(out_png), dpi=dpi)

    return {
        "surfaces": [
            {"name": z["code"], "area_sf": z["area_sqft"], "perimeter_lf": z["perimeter_lf"]}
            for z in zone_list
        ],
        "overlay":         Path(out_png).name,
        "scale_in_per_ft": scale_in_per_ft,
        "zones":           zone_list,
    }


# ── helpers ──────────────────────────────────────────────────────────────────

def _read_scale(pdf_path, page_idx: int) -> float:
    """Parse drawing scale from title-block text. Returns inches per foot."""
    import fitz
    with fitz.open(str(pdf_path)) as doc:
        text = doc[page_idx].get_text().upper()

    # "1/4" = 1'-0"" or "3/16" = 1'" etc.
    m = re.search(r'(\d+)/(\d+)\s*["”]?\s*=\s*1\s*[\'’\-]', text)
    if m:
        return int(m.group(1)) / int(m.group(2))

    # "1" = 10'-0"" (engineer's scale)
    m = re.search(r'(\d+)\s*["”]\s*=\s*(\d+)\s*[\'’\-]', text)
    if m:
        return int(m.group(1)) / int(m.group(2))

    log.warning("pool_mode: could not parse scale on page %d — defaulting to 1/4\"=1'", page_idx)
    return 0.25


def _render_annotated(pdf_path, page_idx: int, dpi: int,
                      cands: list, stats) -> bytes:
    """Render the page with numbered red boxes over each candidate region."""
    import fitz
    from PIL import Image, ImageDraw

    render_dpi = min(dpi, 100)
    with fitz.open(str(pdf_path)) as doc:
        pix = doc[page_idx].get_pixmap(dpi=render_dpi)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    W, H = img.size

    # binary shape (at full dpi) → scale factor to render_dpi image
    with fitz.open(str(pdf_path)) as doc:
        page_w_pt = doc[page_idx].rect.width
        page_h_pt = doc[page_idx].rect.height
    sx = W / (page_w_pt * render_dpi / 72)
    sy = H / (page_h_pt * render_dpi / 72)

    draw = ImageDraw.Draw(img)
    for num, (idx, _) in enumerate(cands):
        x  = int(stats[idx, cv2.CC_STAT_LEFT]   * (render_dpi / dpi))
        y  = int(stats[idx, cv2.CC_STAT_TOP]    * (render_dpi / dpi))
        cw = int(stats[idx, cv2.CC_STAT_WIDTH]  * (render_dpi / dpi))
        ch = int(stats[idx, cv2.CC_STAT_HEIGHT] * (render_dpi / dpi))
        draw.rectangle([x, y, x + cw, y + ch], outline="red", width=2)
        draw.text((x + 2, y + 2), str(num + 1), fill="red")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _gemini_name_zones(pdf_path, page_idx: int, dpi: int,
                       cands: list, stats, shape: tuple,
                       api_key: str) -> dict[int, str]:
    """Send annotated page to Gemini; returns {label_idx: surface_name}."""
    import google.generativeai as genai

    img_bytes = _render_annotated(pdf_path, page_idx, dpi, cands, stats)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        genai.configure(api_key=api_key)

    model = genai.GenerativeModel("gemini-3.5-flash")

    prompt = (
        f"This is a pool/spa construction plan. "
        f"Red numbered boxes (1 to {len(cands)}) highlight detected surface regions.\n\n"
        "For each numbered box, identify what pool surface it represents by reading "
        "the text labels ON the regions and the drawing legend.\n"
        "Use names like: POOL, SPA, TANNING LEDGE, SUN SHELF, STEPS, BENCH, "
        "STONE STEPPERS, DECK, COPING\n"
        "Return BACKGROUND for boxes covering the sheet background, schedule tables, "
        "equipment rooms, or anything that is NOT a pool surface area.\n\n"
        f'Respond ONLY with valid JSON: {{"1": "POOL", "2": "SPA", "3": "BACKGROUND", ...}}'
    )

    image_part = {"mime_type": "image/png", "data": img_bytes}
    response = model.generate_content([prompt, image_part])
    raw = response.text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    result = json.loads(raw)
    return {
        cands[int(k) - 1][0]: v
        for k, v in result.items()
        if k.isdigit() and 0 < int(k) <= len(cands)
    }
