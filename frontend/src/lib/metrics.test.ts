import { expect, test } from "vitest";
import { deltaClass, fmtDelta, fmtMetric } from "@/lib/metrics";

test("formats nullable metrics", () => {
  expect(fmtMetric(null)).toBe("—");
  expect(fmtMetric(0.8507)).toBe("0.851");
});

test("classes metric deltas", () => {
  expect(deltaClass(0.03)).toContain("emerald");
  expect(deltaClass(-0.01)).toContain("red");
  expect(deltaClass(null)).toContain("muted");
});

test("formats signed deltas", () => {
  expect(fmtDelta(0.031)).toBe("+0.031");
  expect(fmtDelta(-0.012)).toBe("−0.012");
  expect(fmtDelta(null)).toBe("—");
});
