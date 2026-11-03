import { render, screen } from "@testing-library/react";
import { ThemeProvider } from "@gravity-ui/uikit";
import { describe, expect, it } from "vitest";
import { CompanyAnalysisPanel } from "./CompanyAnalysisPanel.jsx";


describe("CompanyAnalysisPanel service positions", () => {
  it("renders a multi-word service with its linked price", () => {
    render(
      <ThemeProvider theme="dark">
        <CompanyAnalysisPanel
          task={{
            status: "done",
            quota_charged: 1,
            result: {
              successful_count: 1,
              partial_count: 0,
              failed_count: 0,
              companies: [{
                business_id: 1,
                source: "yandex",
                company: { name: "Стомус" },
                services: [{ name: "Керамическая коронка", price: "27 800 ₽" }],
                prices: [],
              }],
            },
            options: { services_prices: true },
          }}
          onClose={() => {}}
        />
      </ThemeProvider>,
    );

    expect(screen.getByText("Керамическая коронка")).toBeTruthy();
    expect(screen.getByText("27 800 ₽")).toBeTruthy();
  });

  it("renders normalized sources and public posts in fixed source order", () => {
    render(
      <ThemeProvider theme="dark">
        <CompanyAnalysisPanel
          task={{
            status: "done",
            quota_charged: 1,
            result: {
              successful_count: 1,
              partial_count: 0,
              failed_count: 0,
              companies: [{
                business_id: 1,
                source: "yandex",
                company: { name: "Клиника" },
                reviews: [{ author: "Анна", rating: 5, text: "Хорошо" }],
                web: { pages: [{ url: "https://clinic.test/about", title: "О клинике", text: "Текст сайта" }] },
                telegram: { title: "Канал", posts: [{ date: "2026-08-09", text: "Пост Telegram", media: [{ type: "photo", title: "Фотография" }] }] },
                vk: { title: "Группа", posts: [{ date: "2026-08-08", text: "Пост VK", media: [{ type: "video", title: "Обзор" }] }] },
              }],
            },
            options: { reviews: true, website: true, telegram: true, vk: true },
          }}
          onClose={() => {}}
        />
      </ThemeProvider>,
    );

    const headings = screen.getAllByRole("heading").map((heading) => heading.textContent);
    expect(headings.indexOf("Яндекс Карты")).toBeLessThan(headings.indexOf("Сайт"));
    expect(headings.indexOf("Сайт")).toBeLessThan(headings.indexOf("Telegram"));
    expect(headings.indexOf("Telegram")).toBeLessThan(headings.indexOf("ВКонтакте"));
    expect(screen.getByText("Пост Telegram")).toBeTruthy();
    expect(screen.getByText("photo: Фотография")).toBeTruthy();
    expect(screen.getByText("Пост VK")).toBeTruthy();
  });

  it("renders a completed analysis when AI summary contains structured values", () => {
    render(
      <ThemeProvider theme="dark">
        <CompanyAnalysisPanel
          task={{
            status: "done",
            quota_charged: 1,
            result: {
              successful_count: 1,
              partial_count: 0,
              failed_count: 0,
              companies: [{ business_id: 1, source: "yandex", company: { name: "Стомус" } }],
            },
            summary: {
              overview: { text: "Карточка собрана" },
              recommendations: [{ title: "Обновить сайт" }],
              outliers: [{ name: "Стомус" }],
            },
          }}
          onClose={() => {}}
        />
      </ThemeProvider>,
    );

    expect(screen.getByText("Карточка собрана")).toBeTruthy();
    expect(screen.getByText("Обновить сайт")).toBeTruthy();
    expect(screen.getAllByText("Стомус").length).toBeGreaterThan(0);
  });
});
