import { test } from "node:test";
import assert from "node:assert/strict";
import { pickResumeStage } from "./resume.js";

test("no status map → Stage 1", () => {
  assert.deepEqual(pickResumeStage({}), { view: "stage1", firstDone: null });
});

test("nothing extracted yet → Stage 1", () => {
  assert.deepEqual(
    pickResumeStage({ "0": "pending", "1": "queued" }),
    { view: "stage1", firstDone: null }
  );
});

test("every kept page done → Stage 3, firstDone is lowest index", () => {
  assert.deepEqual(
    pickResumeStage({ "2": "done", "0": "done", "1": "done" }),
    { view: "stage3", firstDone: 0 }
  );
});

test("some but not all done → Stage 2, firstDone is lowest done index", () => {
  assert.deepEqual(
    pickResumeStage({ "0": "pending", "3": "done", "5": "done" }),
    { view: "stage2", firstDone: 3 }
  );
});

test("done + error covering all pages is NOT complete → Stage 2", () => {
  assert.deepEqual(
    pickResumeStage({ "0": "done", "1": "error" }),
    { view: "stage2", firstDone: 0 }
  );
});

test("null input is treated as empty → Stage 1", () => {
  assert.deepEqual(pickResumeStage(null), { view: "stage1", firstDone: null });
});
