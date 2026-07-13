import { expect, test } from "vitest";
import { band, cell, fmt, pct, splitStyle, trend } from "@/lib/evalBands";

test("band: null is n/a", () => {
  expect(band(null).key).toBe("na");
  expect(band(undefined).key).toBe("na");
});

test("band: >= 0.85 is strong", () => {
  expect(band(0.85).key).toBe("strong");
  expect(band(0.99).key).toBe("strong");
});

test("band: 0.70-0.849 is fair", () => {
  expect(band(0.7).key).toBe("fair");
  expect(band(0.84).key).toBe("fair");
});

test("band: below 0.70 is weak", () => {
  expect(band(0.69).key).toBe("weak");
  expect(band(0).key).toBe("weak");
});

test("fmt: formats to 3 decimals, null is em dash", () => {
  expect(fmt(0.9)).toBe("0.900");
  expect(fmt(null)).toBe("—");
});

test("pct: rounds to whole percent, null is em dash", () => {
  expect(pct(0.833)).toBe("83%");
  expect(pct(null)).toBe("—");
});

test("cell: bundles fmt + band + chip colors consistently", () => {
  const c = cell(0.9);
  expect(c.fmt).toBe("0.900");
  expect(c.bandKey).toBe("strong");
  expect(c.chip.color).toBe(c.color);
});

test("trend: null cur or prev means no trend shown", () => {
  expect(trend(null, 0.8).show).toBe(false);
  expect(trend(0.8, null).show).toBe(false);
});

test("trend: within noise floor shows ≈", () => {
  const t = trend(0.82, 0.8, 0.05);
  expect(t.sym).toBe("≈");
});

test("trend: above noise floor and positive shows ▲", () => {
  const t = trend(0.9, 0.8, 0.05);
  expect(t.sym).toBe("▲");
});

test("trend: above noise floor and negative shows ▼", () => {
  const t = trend(0.7, 0.8, 0.05);
  expect(t.sym).toBe("▼");
});

test("trend: deltaFmt sign matches direction", () => {
  expect(trend(0.9, 0.8, 0.05).deltaFmt).toBe("+0.100");
  expect(trend(0.7, 0.8, 0.05).deltaFmt).toBe("−0.100");
});

test("splitStyle: holdout and non-holdout use distinct colors", () => {
  expect(splitStyle(true).color).not.toBe(splitStyle(false).color);
});
