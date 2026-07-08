import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";
import Ingestion from "@/routes/Ingestion";
import { listSyncRuns, startSync } from "@/api/client";

vi.mock("@/api/client", () => ({
  listSyncRuns: vi.fn(),
  startSync: vi.fn(),
}));

function renderIngestion() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <Ingestion />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.mocked(listSyncRuns).mockReset();
  vi.mocked(startSync).mockReset();
});

test("run sync watches returned id and re-enables on terminal status", async () => {
  let resolveStart: (value: { status: string; sync_run_id: string }) => void = () => {};
  const startPromise = new Promise<{ status: string; sync_run_id: string }>((resolve) => {
    resolveStart = resolve;
  });
  vi.mocked(listSyncRuns)
    .mockResolvedValueOnce({ runs: [] })
    .mockResolvedValue({
      runs: [
        {
          sync_run_id: "run-1",
          started_at: "2026-01-01T00:00:00Z",
          completed_at: "2026-01-01T00:00:05Z",
          status: "completed",
          scanned_count: 2,
          changed_count: 1,
          unchanged_count: 1,
          failed_count: 0,
        },
      ],
    });
  vi.mocked(startSync).mockReturnValue(startPromise);

  renderIngestion();
  const button = await screen.findByRole("button", { name: "Run sync" });

  await userEvent.click(button);

  expect(startSync).toHaveBeenCalled();
  await waitFor(() => expect(button).toBeDisabled());
  resolveStart({ status: "sync started", sync_run_id: "run-1" });
  await waitFor(() => expect(button).toBeEnabled());
  expect(await screen.findByText("completed")).toBeVisible();
});
