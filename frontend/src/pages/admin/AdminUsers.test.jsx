import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, beforeEach, vi } from "vitest";
import { ThemeProvider } from "@gravity-ui/uikit";
import { api, post } from "../../api.js";
import { AdminUsers } from "./AdminUsers.jsx";

vi.mock("../../api.js", () => ({
  api: vi.fn(),
  post: vi.fn(),
  fmtDate: (value) => value || "-",
  fmtInt: (value) => String(value ?? 0),
  fmtRub: (value) => `${value ?? 0} RUB`,
}));

const user = {
  id: 7, email: "long.customer.address@example.com", plan_code: "free", plan_name: "Бесплатно",
  credits_balance: 12, credits_spent: 4, total_paid_rub: 0, created_at: "2026-07-22T10:00:00Z",
  email_verified: true, is_blocked: false, is_admin: false,
};

const detail = {
  profile: { id: 7, email: user.email, credits_balance: 12, credits_spent: 4, total_paid_kop: 0, is_blocked: false, email_verified: true, created_at: user.created_at },
  subscription: { plan_code: "free", plan_name: "Бесплатно", status: "active", period_end: null },
  quotas: { organizations: { limit: 50, used: 4, extra: 0, available: 46 } },
  recent_activity: [], payments: [], support_conversations: [], audit_entries: [], limits: {},
};

function renderUi() {
  return render(<ThemeProvider theme="dark"><AdminUsers /></ThemeProvider>);
}

describe("AdminUsers", () => {
  beforeEach(() => {
    api.mockImplementation((path) => {
      if (path === "/api/admin/users") return Promise.resolve([user]);
      if (path === "/api/admin/control/users/7") return Promise.resolve(detail);
      return Promise.resolve([]);
    });
    post.mockResolvedValue({ ok: true });
  });

  it("loads the user detail only after opening a user", async () => {
    renderUi();
    expect((await screen.findAllByText(user.email)).length).toBeGreaterThan(0);
    expect(api).not.toHaveBeenCalledWith("/api/admin/control/users/7");

    fireEvent.click(screen.getAllByRole("button", { name: /открыть/i })[0]);
    expect(await screen.findByLabelText("Карточка пользователя")).toBeTruthy();
    expect(api).toHaveBeenCalledWith("/api/admin/control/users/7");
  });

  it("requires confirmation and a comment before changing a plan", async () => {
    renderUi();
    await screen.findAllByText(user.email);
    fireEvent.click(screen.getAllByRole("button", { name: /открыть/i })[0]);
    await screen.findByLabelText("Карточка пользователя");

    fireEvent.click(screen.getByRole("button", { name: "Применить тариф" }));
    expect(screen.getByText("Подтвердите действие")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить" }));
    expect(screen.getByText("Введите комментарий к действию")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Комментарий к действию"), { target: { value: "Тестовое назначение" } });
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith(
      "/api/admin/users/7/plan", { plan_code: "free", comment: "Тестовое назначение" },
    ));
  });

  it("keeps the drawer and form values open after API error", async () => {
    post.mockRejectedValueOnce(new Error("Сервис недоступен"));
    renderUi();
    await screen.findAllByText(user.email);
    fireEvent.click(screen.getAllByRole("button", { name: /открыть/i })[0]);
    await screen.findByLabelText("Карточка пользователя");
    fireEvent.change(screen.getByLabelText("Корректировка кредитов"), { target: { value: "25" } });
    fireEvent.click(screen.getByRole("button", { name: "Изменить кредиты" }));
    fireEvent.change(screen.getByLabelText("Комментарий к действию"), { target: { value: "Компенсация" } });
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить" }));

    expect(await screen.findByText("Сервис недоступен")).toBeTruthy();
    expect(screen.getByLabelText("Карточка пользователя")).toBeTruthy();
    expect(screen.getByLabelText("Корректировка кредитов").value).toBe("25");
    expect(screen.getByLabelText("Комментарий к действию").value).toBe("Компенсация");
  });

  it("refreshes the open drawer and the list after a successful credit change", async () => {
    let changed = false;
    const updatedUser = { ...user, credits_balance: 37 };
    const updatedDetail = { ...detail, profile: { ...detail.profile, credits_balance: 37 } };
    api.mockImplementation((path) => {
      if (path === "/api/admin/users") return Promise.resolve([changed ? updatedUser : user]);
      if (path === "/api/admin/control/users/7") return Promise.resolve(changed ? updatedDetail : detail);
      return Promise.resolve([]);
    });
    post.mockImplementation(async () => { changed = true; return { ok: true }; });

    renderUi();
    await screen.findAllByText(user.email);
    fireEvent.click(screen.getAllByRole("button", { name: /открыть/i })[0]);
    await screen.findByLabelText("Карточка пользователя");
    fireEvent.change(screen.getByLabelText("Корректировка кредитов"), { target: { value: "25" } });
    fireEvent.click(screen.getByRole("button", { name: "Изменить кредиты" }));
    fireEvent.change(screen.getByLabelText("Комментарий к действию"), { target: { value: "Компенсация" } });
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить" }));

    await waitFor(() => expect(post).toHaveBeenCalledWith(
      "/api/admin/users/7/credits", { delta: 25, comment: "Компенсация" },
    ));
    await waitFor(() => expect(screen.getAllByText("37 кр.").length).toBeGreaterThan(0));
    expect(screen.getByLabelText("Карточка пользователя")).toBeTruthy();
    expect(api.mock.calls.filter(([path]) => path === "/api/admin/users").length).toBeGreaterThan(1);
  });
});
