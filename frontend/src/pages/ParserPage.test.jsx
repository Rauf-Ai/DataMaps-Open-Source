import { fireEvent, render, screen } from "@testing-library/react";
import { ThemeProvider, ToasterProvider } from "@gravity-ui/uikit";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, post } from "../api.js";
import { ParserPage } from "./ParserPage.jsx";

vi.mock("../api.js", () => ({ api: vi.fn(), post: vi.fn(), fmtInt: (value) => String(value ?? 0), fmtDate: (value) => value || "—" }));
vi.mock("../AuthContext.jsx", () => ({
  useAuth: () => ({ me: { credits_balance: 100 }, reload: vi.fn() }),
}));

const runningStatuses = [
  { status: "running", progress: 10, message: "Ищем" },
  { status: "running", progress: 20, message: "Собираем" },
];
const resultPages = [
  { items: [{ position: 1, name: "Кафе А", address: "Москва" }], next_after: 1 },
  { items: [{ position: 2, name: "Кафе Б", address: "Москва" }], next_after: 2 },
];
const toaster = {
  add: vi.fn(), remove: vi.fn(), removeAll: vi.fn(), update: vi.fn(), has: vi.fn(), subscribe: () => () => {},
};

function renderPage() {
  return render(
    <ThemeProvider theme="dark">
      <ToasterProvider toaster={toaster}>
        <MemoryRouter><ParserPage /></MemoryRouter>
      </ToasterProvider>
    </ThemeProvider>,
  );
}

describe("ParserPage live results", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    let statusIndex = 0;
    let resultIndex = 0;
    api.mockImplementation((path) => {
      if (path === "/api/parser/tasks") return Promise.resolve([]);
      if (path === "/api/parser/tasks/task-1/status") return Promise.resolve(runningStatuses[Math.min(statusIndex++, runningStatuses.length - 1)]);
      if (path.startsWith("/api/parser/tasks/task-1/results")) return Promise.resolve(resultPages[Math.min(resultIndex++, resultPages.length - 1)]);
      return Promise.resolve({});
    });
    post.mockResolvedValue({ task_id: "task-1" });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("opens the live results table with the first item and appends later deltas", async () => {
    renderPage();
    const inputs = screen.getAllByRole("textbox");
    fireEvent.change(inputs[0], { target: { value: "кафе" } });
    fireEvent.change(inputs[1], { target: { value: "Москва" } });
    fireEvent.click(screen.getByRole("button", { name: "Предпросмотр" }));

    await vi.advanceTimersByTimeAsync(2000);
    expect(screen.getByText("Кафе А")).toBeTruthy();

    await vi.advanceTimersByTimeAsync(2000);
    expect(screen.getByText("Кафе Б")).toBeTruthy();
    expect(api).toHaveBeenCalledWith("/api/parser/tasks/task-1/results?after=1");
  });
});
