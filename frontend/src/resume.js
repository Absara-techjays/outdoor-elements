// Decide where to land when resuming a previously-processed job, from the
// Stage-2 status map returned by GET /api/jobs/{id}/stage2/status.
//
// pages: { "<keptIndex>": "pending"|"queued"|"running"|"done"|"error" }  (kept pages only)
// returns: { view: "stage1"|"stage2"|"stage3", firstDone: number|null }
//
// Rule: all kept pages "done" -> Stage 3 (estimate); some done -> Stage 2;
// none done -> Stage 1. firstDone is the lowest "done" page index (or null).
export function pickResumeStage(pages) {
  const entries = Object.entries(pages || {});
  const total = entries.length;
  const doneIdx = entries
    .filter(([, s]) => s === "done")
    .map(([k]) => Number(k));
  const doneCount = doneIdx.length;
  const firstDone = doneCount ? Math.min(...doneIdx) : null;
  if (total > 0 && doneCount === total) return { view: "stage3", firstDone };
  if (doneCount > 0) return { view: "stage2", firstDone };
  return { view: "stage1", firstDone: null };
}
