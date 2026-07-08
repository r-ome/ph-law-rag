import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { expect, test, vi } from "vitest";
import CorpusList from "@/routes/CorpusList";
import type { DocumentSummary } from "@/api/client";

const docs: DocumentSummary[] = [
  {
    doc_id: "1",
    source_id: "civil-code",
    title: "Civil Code of the Philippines",
    url: "https://example.com/civil",
    doc_type: "code",
    category: "civil",
    enabled: true,
    updated_at: null,
    last_fetched: null,
    chunk_count: 10,
    status: "operative",
    source_index: "lawphil",
    official_number: "RA 386",
    tags: ["civil-law", "obligations"],
  },
  {
    doc_id: "2",
    source_id: "family-code",
    title: "Family Code of the Philippines",
    url: "https://example.com/family",
    doc_type: "code",
    category: "family",
    enabled: true,
    updated_at: null,
    last_fetched: null,
    chunk_count: 5,
    status: "operative",
    source_index: "lawphil",
    official_number: "EO 209",
    tags: ["family-law"],
  },
  {
    doc_id: "3",
    source_id: "malolos",
    title: "Malolos Constitution",
    url: "https://example.com/malolos",
    doc_type: "constitution",
    category: "constitutional",
    enabled: true,
    updated_at: null,
    last_fetched: null,
    chunk_count: 3,
    status: "superseded",
    source_index: "sc_elibrary",
    official_number: null,
    tags: [],
  },
];

vi.mock("@/api/client", async () => {
  const actual = await vi.importActual<typeof import("@/api/client")>("@/api/client");
  return {
    ...actual,
    listDocuments: vi.fn(async () => ({ documents: docs })),
  };
});

function renderList() {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <CorpusList />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

test("renders all rows initially with result count", async () => {
  renderList();
  await screen.findByText("Civil Code of the Philippines");
  expect(screen.getByText("Family Code of the Philippines")).toBeInTheDocument();
  expect(screen.getByText("Malolos Constitution")).toBeInTheDocument();
  expect(screen.getByText("3 of 3 documents")).toBeInTheDocument();
});

test("search narrows rows by title substring", async () => {
  const user = userEvent.setup();
  renderList();
  await screen.findByText("Civil Code of the Philippines");

  const search = screen.getByPlaceholderText("Search title or tags…");
  await user.type(search, "family");

  await waitFor(() => {
    expect(screen.queryByText("Civil Code of the Philippines")).not.toBeInTheDocument();
  });
  expect(screen.getByText("Family Code of the Philippines")).toBeInTheDocument();
  expect(screen.getByText("1 of 3 documents")).toBeInTheDocument();
});

test("category filter narrows rows", async () => {
  const user = userEvent.setup();
  renderList();
  await screen.findByText("Civil Code of the Philippines");

  const trigger = screen.getByRole("combobox", { name: /category/i });
  await user.click(trigger);
  const option = await screen.findByRole("option", { name: "family" });
  await user.click(option);

  await waitFor(() => {
    expect(screen.queryByText("Civil Code of the Philippines")).not.toBeInTheDocument();
  });
  expect(screen.getByText("Family Code of the Philippines")).toBeInTheDocument();
});
