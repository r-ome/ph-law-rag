import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import { citedRefs, renderAnswerWithCitations } from "@/lib/citations";
import type { ChatSource } from "@/api/client";

const sources: ChatSource[] = [
  {
    ref: 1,
    title: "Revised Penal Code",
    url: "https://example.com/rpc",
    source_id: "rpc_1930",
    locator: "Article 308",
    via: null,
  },
  {
    ref: 2,
    title: "Revised Penal Code",
    url: "https://example.com/rpc",
    source_id: "rpc_1930",
    locator: "Article 309",
    via: null,
  },
  {
    ref: 3,
    title: "Civil Code",
    url: "https://example.com/civil",
    source_id: "civil_code",
    locator: null,
    via: null,
  },
];

test("turns matching citation markers into clickable buttons", async () => {
  const onCite = vi.fn();
  render(
    <div>{renderAnswerWithCitations("Theft is punished [1] under Art. 309 [2].", sources, onCite)}</div>,
  );

  expect(screen.getByRole("button", { name: "Citation 1" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Citation 2" })).toBeVisible();

  await userEvent.click(screen.getByRole("button", { name: "Citation 2" }));
  expect(onCite).toHaveBeenCalledWith(2);
});

test("leaves unmatched citation markers as plain text", () => {
  const onCite = vi.fn();
  render(<div>{renderAnswerWithCitations("No matching source [9]", [sources[0]!], onCite)}</div>);

  expect(screen.queryByRole("button", { name: "Citation 9" })).toBeNull();
  expect(screen.getByText(/No matching source \[9\]/)).toBeVisible();
  expect(onCite).not.toHaveBeenCalled();
});

test("renders grouped citation markers as separate buttons", () => {
  render(<div>{renderAnswerWithCitations("See [2][3].", sources)}</div>);

  expect(screen.getByRole("button", { name: "Citation 2" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Citation 3" })).toBeVisible();
});

test("extracts cited refs from answer text", () => {
  expect([...citedRefs("See [2][3] and [2].")]).toEqual([2, 3]);
});
