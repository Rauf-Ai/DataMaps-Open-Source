import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi, describe, expect, it, beforeEach } from "vitest";
import { ThemeProvider } from "@gravity-ui/uikit";
import { api } from "../../api.js";
import { AdminIncidents } from "./AdminIncidents.jsx";

vi.mock("../../api.js", () => ({
  api: vi.fn(),
  fmtDate: (value) => value || "-",
  fmtInt: (value) => String(value ?? 0),
}));

const page = {
  items: [{
    id: 41, code: "MAP-500", source: "yandex", severity: "critical", status: "new",
    message: "Карты не ответили", details: { endpoint: "/search" }, request_id: "req-1",
    occurrence_count: 3, first_seen: "2026-07-22T10:00:00Z", last_seen: "2026-07-22T10:05:00Z",
  }],
  total: 1, page: 1, page_size: 20,
};

const renderUi = (node) => render(<ThemeProvider theme="dark">{node}</ThemeProvider>);

describe("AdminIncidents", () => {
  beforeEach(() => {
    api.mockImplementation((path, options) => {
      if (options?.method === "PATCH") return Promise.resolve({ ...page.items[0], status: "resolved" });
      if (path === "/api/admin/control/incidents/41") return Promise.resolve({ ...page.items[0], status: "acknowledged" });
      return Promise.resolve(page);
    });
  });

  it("sends incident filters in the server query", async () => {
    renderUi(<AdminIncidents />);
    await screen.findAllByText("MAP-500");

    fireEvent.change(screen.getByLabelText("Код или request ID"), { target: { value: "req-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Применить" }));

    await waitFor(() => expect(api).toHaveBeenLastCalledWith(expect.stringContaining("q=req-1")));
  });

  it("requires a comment before resolving an incident", async () => {
    renderUi(<AdminIncidents />);
    await screen.findAllByText("MAP-500");

    fireEvent.click(screen.getAllByRole("button", { name: "Подробнее" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "Решён" }));
    expect(screen.getByText("Введите комментарий к действию")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Комментарий"), { target: { value: "Источник восстановлен" } });
    fireEvent.click(screen.getByRole("button", { name: "Решён" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith(
      "/api/admin/control/incidents/41",
      expect.objectContaining({ method: "PATCH" }),
    ));
    await waitFor(() => expect(api).toHaveBeenLastCalledWith(expect.stringContaining("/api/admin/control/incidents?page=1")));
  });

  it("opens an exact deep-linked incident even when it is not on the current filtered page", async () => {
    api.mockImplementation((path) => {
      if (path === "/api/admin/control/incidents/41") return Promise.resolve({ ...page.items[0], status: "acknowledged" });
      return Promise.resolve({ items: [], total: 0, page: 2, page_size: 20 });
    });

    renderUi(<AdminIncidents initialIncidentId="41" initialFilters={{ status: "new" }} />);

    expect(await screen.findByLabelText("Детали инцидента")).toBeTruthy();
    expect(screen.getByText("Принят")).toBeTruthy();
    expect(api).toHaveBeenCalledWith("/api/admin/control/incidents/41");
  });

  it("uses server pagination and renders an explicit empty state", async () => {
    api.mockResolvedValueOnce({ ...page, total: 40 }).mockResolvedValueOnce({ items: [], total: 40, page: 2, page_size: 20 });
    renderUi(<AdminIncidents />);

    await screen.findAllByText("MAP-500");
    fireEvent.click(screen.getByRole("button", { name: "Вперёд" }));

    expect(await screen.findByText("По этим фильтрам инцидентов нет.")).toBeTruthy();
    expect(api).toHaveBeenLastCalledWith(expect.stringContaining("page=2"));
  });

  it("keeps mobile incident rows meaningful without the desktop table", async () => {
    renderUi(<AdminIncidents />);
    await screen.findAllByText("MAP-500");

    expect(document.querySelector(".dm-admin-incidents__mobile details")).toBeTruthy();
    expect(screen.getAllByText("Карты не ответили").length).toBeGreaterThan(1);
  });

  it("shows API errors and lets the administrator retry", async () => {
    api.mockRejectedValueOnce(new Error("Сеть недоступна")).mockResolvedValueOnce(page);
    renderUi(<AdminIncidents />);

    expect((await screen.findByRole("alert")).textContent).toContain("Сеть недоступна");
    fireEvent.click(screen.getByRole("button", { name: "Обновить" }));
    expect(await screen.findAllByText("MAP-500")).not.toHaveLength(0);
  });
});
