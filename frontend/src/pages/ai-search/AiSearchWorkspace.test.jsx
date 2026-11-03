import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api.js";
import { AiSearchWorkspace } from "./AiSearchWorkspace.jsx";

vi.mock("../../api.js", () => ({ api: vi.fn(), post: vi.fn() }));
vi.mock("../../AuthContext.jsx", () => ({ useAuth: () => ({ me: { subscription: { quotas: { organizations: { available: 10 } } } }, reload: vi.fn() }) }));
vi.mock("./ChatComposer.jsx", () => ({ ChatComposer: () => null }));
vi.mock("./ChatSidebar.jsx", () => ({ ChatSidebar: () => null }));
vi.mock("./ChatThread.jsx", () => ({
  ChatThread: ({ onOpenMap }) => <button type="button" onClick={onOpenMap}>Открыть карту из чата</button>,
}));
vi.mock("./SearchResultsPanel.jsx", () => ({
  SearchResultsPanel: ({ mapOpen, liveRows = [] }) => <><output data-testid="map-state">{mapOpen ? "open" : "closed"}</output><output data-testid="live-row-count">{liveRows.length}</output></>,
}));
vi.mock("./CompanyAnalysisPanel.jsx", () => ({ CompanyAnalysisPanel: () => null }));

const completedTask = { id: "task-1", status: "done", saved_result_id: 42 };
const savedResult = { id: 42, row_count: 1, items: [{ business: { id: 7, lat: 55.75, lon: 37.61 } }] };

describe("AiSearchWorkspace map state", () => {
  beforeEach(() => {
    api.mockImplementation((path) => {
      if (path === "/api/chat/sessions") return Promise.resolve([]);
      if (path === "/api/chat/sessions/1") return Promise.resolve({ messages: [{ id: "m1", role: "assistant", content: "Готово" }], plan: { searches: [] }, parser_task_id: "task-1" });
      if (path === "/api/chat/sessions/1/task") return Promise.resolve(completedTask);
      if (path === "/api/live-lab/saved-results/42") return Promise.resolve(savedResult);
      return Promise.resolve({});
    });
  });

  it("opens the existing results panel in map mode from the chat action", async () => {
    const { unmount } = render(<MemoryRouter initialEntries={["/?chat=1"]}><AiSearchWorkspace /></MemoryRouter>);

    await screen.findByRole("button", { name: "Открыть карту из чата" });
    expect(screen.getByTestId("map-state").textContent).toBe("closed");
    fireEvent.click(screen.getByRole("button", { name: "Открыть карту из чата" }));
    expect(screen.getByTestId("map-state").textContent).toBe("open");
    unmount();
  });

});
