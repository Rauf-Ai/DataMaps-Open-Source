import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@gravity-ui/uikit";
import { api } from "../../api.js";
import { AdminTasks } from "./AdminTasks.jsx";

vi.mock("../../api.js", () => ({
  api: vi.fn(),
  fmtDate: (value) => value || "-",
  fmtInt: (value) => String(value ?? 0),
}));

const activeTask = {
  type: "parser", id: "task-1", user_id: 7, user_email: "customer@example.com", status: "running",
  progress: 42, source: "yandex", requested_count: 20, found_count: 8,
  created_at: "2026-07-22T10:00:00Z", updated_at: "2026-07-22T10:01:00Z",
  error_code: "MAP-500", error_message: "[MAP-500] Source unavailable", stalled: true,
};

const detail = { ...activeTask, cancel_requested: false, priority: 0, retry_of_id: null, started_at: activeTask.created_at, finished_at: null, request_id: "req-123" };

function renderUi(entry = "/admin?tab=tasks&status=running&stalled=1") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <ThemeProvider theme="dark"><AdminTasks isActive /></ThemeProvider>
    </MemoryRouter>,
  );
}

describe("AdminTasks", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
    api.mockImplementation((path, options) => {
      if (options?.method === "POST") return Promise.resolve({ ...detail, cancel_requested: true });
      if (path.includes("/tasks/parser/task-1")) return Promise.resolve(detail);
      return Promise.resolve({ items: [activeTask], total: 1, page: 1, page_size: 20 });
    });
  });
  afterEach(() => vi.useRealTimers());

  it("uses URL filters for the server-side task request and marks stalled tasks", async () => {
    renderUi();
    expect((await screen.findAllByText("customer@example.com")).length).toBeGreaterThan(0);
    expect(api).toHaveBeenCalledWith(expect.stringContaining("status=running"));
    expect(api).toHaveBeenCalledWith(expect.stringContaining("stalled=true"));
    expect(screen.getAllByText("Зависла").length).toBeGreaterThan(0);
  });

  it("requires comment for cancel and keeps code plus request id in details", async () => {
    renderUi();
    await screen.findAllByText("customer@example.com");
    fireEvent.click(screen.getAllByRole("button", { name: "Подробнее" })[0]);
    expect(await screen.findByLabelText("Детали задачи")).toBeTruthy();
    expect(screen.getAllByText("MAP-500").length).toBeGreaterThan(0);
    expect(screen.getByText("req-123")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Отменить" }));
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить" }));
    expect(screen.getByText("Введите комментарий к действию")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Комментарий к действию"), { target: { value: "Остановка по запросу" } });
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith(
      "/api/admin/control/tasks/parser/task-1/cancel",
      expect.objectContaining({ method: "POST" }),
    ));
  });

  it("requires a comment before retrying a failed task", async () => {
    const failedTask = { ...activeTask, status: "failed", stalled: false };
    const failedDetail = { ...detail, status: "failed", stalled: false };
    api.mockImplementation((path, options) => {
      if (options?.method === "POST") return Promise.resolve({ ...failedDetail, id: "retry-task" });
      if (path.includes("/tasks/parser/task-1")) return Promise.resolve(failedDetail);
      return Promise.resolve({ items: [failedTask], total: 1, page: 1, page_size: 20 });
    });
    renderUi("/admin?tab=tasks&status=failed");
    await screen.findAllByText("customer@example.com");
    fireEvent.click(screen.getAllByRole("button", { name: "Подробнее" })[0]);
    await screen.findByLabelText("Детали задачи");
    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить" }));
    expect(screen.getByText("Введите комментарий к действию")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Комментарий к действию"), { target: { value: "Повтор после сбоя" } });
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить" }));
    await waitFor(() => expect(screen.getByText("retry-task")).toBeTruthy());
  });

  it("polls active tasks every five seconds only while visible and active", async () => {
    renderUi();
    await screen.findAllByText("customer@example.com");
    const initialCalls = api.mock.calls.length;
    await vi.advanceTimersByTimeAsync(5000);
    expect(api.mock.calls.length).toBeGreaterThan(initialCalls);
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
    const beforeHidden = api.mock.calls.length;
    await vi.advanceTimersByTimeAsync(5000);
    expect(api.mock.calls.length).toBe(beforeHidden);
  });
});
