import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi, describe, expect, it, beforeEach } from "vitest";
import { ThemeProvider } from "@gravity-ui/uikit";
import { api } from "../../api.js";
import { AdminOverview } from "./AdminOverview.jsx";

vi.mock("../../api.js", () => ({
  api: vi.fn(),
  fmtInt: (value) => String(value ?? 0),
  fmtDate: (value) => value || "-",
}));

const overview = {
  generated_at: "2026-07-22T10:00:00Z",
  attention: [
    { kind: "incident", id: 41, severity: "critical", code: "MAP-500", message: "Карты не ответили", occurrence_count: 3 },
    { kind: "task", filter: "stalled", severity: "warning", code: "TASK-STALL", message: "Зависших задач: 2", occurrence_count: 2 },
  ],
  users: { active_24h: 12, active_7d: 48, new_7d: 4 },
  subscriptions: { free: 8, pro: 3, business: 1 },
  tasks: { queued: 4, running: 2, failed_24h: 1, stalled: 2 },
  payments: { succeeded_24h: 3, failed_24h: 0 },
  support: { open: 6, needs_human: 1 },
  incidents: { new: 2, critical: 1 },
  integrations: [
    { name: "Карты", state: "degraded", signal: "failed_tasks_24h", value: 1 },
    { name: "AI", state: "ok", signal: "requests_7d", value: 18 },
  ],
};

const renderUi = (node) => render(<ThemeProvider theme="dark">{node}</ThemeProvider>);

describe("AdminOverview", () => {
  beforeEach(() => api.mockResolvedValue(overview));

  it("shows actionable attention first and opens the related admin tab", async () => {
    const onNavigate = vi.fn();
    renderUi(<AdminOverview onNavigate={onNavigate} />);

    expect(await screen.findByText("Требует внимания")).toBeTruthy();
    const incidentButton = screen.getByRole("button", { name: /MAP-500/i });
    fireEvent.click(incidentButton);
    expect(onNavigate).toHaveBeenCalledWith("incidents", { incident: "41" });

    fireEvent.click(screen.getByRole("button", { name: /TASK-STALL/i }));
    expect(onNavigate).toHaveBeenCalledWith("tasks", { status: "running", stalled: "1" });
  });

  it("keeps operational metrics visible when an integration is degraded", async () => {
    renderUi(<AdminOverview onNavigate={vi.fn()} />);

    expect(await screen.findByText("Операции")).toBeTruthy();
    expect(screen.getByText("Выполняются")).toBeTruthy();
    expect(screen.getByText("Карты")).toBeTruthy();
    expect(screen.getByText("Деградация")).toBeTruthy();
  });
});
