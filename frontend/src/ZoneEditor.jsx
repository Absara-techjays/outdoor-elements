import { useEffect, useRef, useState } from "react";

// Vortex-style interactive overlay for Stage 2. Renders the colored overlay
// image with a transparent SVG layer of the detected zone polygons on top.
// Supports click / shift-click / drag-marquee selection, keyboard Delete, and a
// floating action bar — wired to delete-by-id via `onDeleteIds`.
//
// Props:
//   imgUrl     overlay PNG (base plan + colored zones)
//   zones      active zones: [{ id, hex, code, geometry:[[[x,y],...],...] }]
//   page       { width, height } in PDF points -> the SVG viewBox
//   editMode   when false the overlay is just the image (no interaction)
//   highlightId zone id to highlight (e.g. hovered in the Zones panel)
//   onDeleteIds(ids)  delete the given zone ids
//   maskPolys   polygons to white-out instantly (optimistic delete) until the
//               server overlay re-renders
//   onOverlayLoad()  fired when the (new) overlay image finishes loading
export default function ZoneEditor({ imgUrl, zones, page, editMode, highlightId, onDeleteIds, maskPolys = [], onOverlayLoad }) {
  const [selected, setSelected] = useState(() => new Set());
  const [marquee, setMarquee] = useState(null);   // {x0,y0,x1,y1} in point space
  const svgRef = useRef(null);
  const drag = useRef(null);

  // leaving edit mode clears the selection
  useEffect(() => { if (!editMode) setSelected(new Set()); }, [editMode]);
  // a fresh detection / page change resets selection
  useEffect(() => { setSelected(new Set()); }, [imgUrl]);

  // keyboard delete (ignore when typing in a field)
  useEffect(() => {
    if (!editMode) return;
    const onKey = (e) => {
      const t = e.target;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;
      if ((e.key === "Delete" || e.key === "Backspace") && selected.size > 0) {
        e.preventDefault();
        onDeleteIds([...selected]);
        setSelected(new Set());
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [editMode, selected, onDeleteIds]);

  if (!page || !page.width) {
    return <div className="s2img"><img src={imgUrl} alt="detected surfaces" onLoad={onOverlayLoad} /></div>;
  }

  function selectZone(id, additive) {
    setSelected((prev) => {
      const next = new Set(additive ? prev : []);
      if (additive && prev.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  // client coords -> SVG point space
  function toPt(clientX, clientY) {
    const r = svgRef.current.getBoundingClientRect();
    return [
      ((clientX - r.left) / r.width) * page.width,
      ((clientY - r.top) / r.height) * page.height,
    ];
  }

  function bgDown(e) {
    if (!editMode || e.target !== svgRef.current) return;  // only on empty space
    const [x, y] = toPt(e.clientX, e.clientY);
    drag.current = { x, y, moved: false, additive: e.shiftKey };
    setMarquee({ x0: x, y0: y, x1: x, y1: y });
  }
  function bgMove(e) {
    if (!drag.current) return;
    const [x, y] = toPt(e.clientX, e.clientY);
    if (Math.abs(x - drag.current.x) + Math.abs(y - drag.current.y) > 4) drag.current.moved = true;
    setMarquee({ x0: drag.current.x, y0: drag.current.y, x1: x, y1: y });
  }
  function bgUp() {
    const d = drag.current;
    drag.current = null;
    if (!d) return;
    if (!d.moved) { setSelected(new Set()); setMarquee(null); return; }  // empty click = clear
    const lo = [Math.min(marquee.x0, marquee.x1), Math.min(marquee.y0, marquee.y1)];
    const hi = [Math.max(marquee.x0, marquee.x1), Math.max(marquee.y0, marquee.y1)];
    const hits = zones.filter((z) => {
      const c = centroid(z);
      return c && c[0] >= lo[0] && c[0] <= hi[0] && c[1] >= lo[1] && c[1] <= hi[1];
    }).map((z) => z.id);
    setSelected((prev) => new Set([...(d.additive ? prev : []), ...hits]));
    setMarquee(null);
  }

  return (
    <div className={`s2img ${editMode ? "editing" : ""}`}>
      <img src={imgUrl} alt="detected surfaces" onLoad={onOverlayLoad} onError={onOverlayLoad} />
      <svg
        ref={svgRef}
        className={`zone-svg ${editMode ? "on" : ""}`}
        viewBox={`0 0 ${page.width} ${page.height}`}
        preserveAspectRatio="none"
        onMouseDown={bgDown}
        onMouseMove={bgMove}
        onMouseUp={bgUp}
        onMouseLeave={bgUp}
      >
        {/* optimistic erase: white-out just-deleted regions until the overlay reloads */}
        {maskPolys.map((poly, i) => (
          <polygon
            key={"mask-" + i}
            className="zmask"
            points={poly.map((p) => `${p[0]},${p[1]}`).join(" ")}
          />
        ))}
        {zones.map((z) =>
          (z.geometry || []).map((poly, i) => (
            <polygon
              key={z.id + "-" + i}
              data-id={z.id}
              className={`zpoly ${selected.has(z.id) ? "sel" : ""} ${highlightId === z.id ? "hot" : ""}`}
              points={poly.map((p) => `${p[0]},${p[1]}`).join(" ")}
              onClick={(e) => { e.stopPropagation(); if (editMode) selectZone(z.id, true); }}
            />
          ))
        )}
        {marquee && (
          <rect
            className="marquee"
            x={Math.min(marquee.x0, marquee.x1)} y={Math.min(marquee.y0, marquee.y1)}
            width={Math.abs(marquee.x1 - marquee.x0)} height={Math.abs(marquee.y1 - marquee.y0)}
          />
        )}
      </svg>

      {editMode && selected.size > 0 && (
        <div className="select-bar">
          <span className="sel-count">{selected.size} selected</span>
          <span className="sel-div" />
          <button className="sel-del" onClick={() => { onDeleteIds([...selected]); setSelected(new Set()); }}>
            <span className="material-symbols-outlined">delete</span> Delete
          </button>
          <button className="sel-clr" onClick={() => setSelected(new Set())}>
            <span className="material-symbols-outlined">close</span> Clear
          </button>
        </div>
      )}
    </div>
  );
}

function centroid(z) {
  const poly = (z.geometry || [])[0];
  if (!poly || !poly.length) return null;
  let sx = 0, sy = 0;
  for (const p of poly) { sx += p[0]; sy += p[1]; }
  return [sx / poly.length, sy / poly.length];
}
