import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api.js";
import { AiSearchWorkspace } from "./AiSearchWorkspace.jsx";

vi.mock("../../api.js", () => ({ api: vi.fn(), post: vi.fn() }));
vi.mock("../../AuthContext.jsx", () => ({ useAuth: () => ({ me: { subscription: { quotas: { organizations: { available: 10 } } } }, reload: vi.fn() }) }));
vi.mock("./ChatComposer.jsx", () => ({ ChatComposer: () => null }));
vi.mock("./ChatSidebar.jsx", () => ({ ChatSidebar: () => null }));
vi.mock("./ChatThread.jsx", () => ({ ChatThread: () => null }));
vi.mock("./SearchResultsPanel.jsx", () => ({
  SearchResultsPanel: ({ liveRows = [] }) => <output data-testid="live-row-count">{liveRows.length}</output>,
}));
vi.mock("./CompanyAnalysisPanel.jsx", () => ({ CompanyAnalysisPanel: () => null }));

describe("AiSearchWorkspace streamed results", () => {
  beforeEach(() => {
    api.mockImplementation((path) => {
      if (path === "/api/chat/sessions") return Promise.resolve([]);
      if (path === "/api/chat/sessions/1") return Promise.resolve({ messages: [{ id: "m1", role: "assistant", content: "Ищу" }], plan: { searches: [] }, parser_task_id: "task-1" });
      if (path === "/api/chat/sessions/1/task") return Promise.resolve({ id: "task-1", status: "running", progress: 10 });
      if (path === "/api/parser/tasks/task-1/results?after=0") return Promise.resolve({ items: [{ position: 1, name: "Кафе А" }], next_after: 1 });
      return Promise.resolve({});
    });
  });

  it("opens the results panel as soon as the task returns a streamed row", async () => {
    const { unmount } = render(<MemoryRouter initialEntries={["/?chat=1"]}><AiSearchWorkspace /></MemoryRouter>);

    expect((await screen.findByTestId("live-row-count")).textContent).toBe("1");
    unmount();
  });
});
