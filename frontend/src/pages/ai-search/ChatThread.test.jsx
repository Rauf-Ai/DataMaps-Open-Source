import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ThemeProvider } from "@gravity-ui/uikit";
import { ChatThread } from "./ChatThread.jsx";


function renderThread(overrides = {}) {
  const props = {
    messages: [],
    plan: { searches: [] },
    availableOrganizations: 50,
    busy: false,
    dirty: false,
    error: "",
    task: null,
    analysisTask: null,
    analysisBusy: false,
    layoutKey: false,
    onPlanChange: vi.fn(),
    onPlanSave: vi.fn(),
    onSearch: vi.fn(),
    onStartAnalysis: vi.fn(),
    onOpenResult: vi.fn(),
    onOpenMap: vi.fn(),
    mapPointCount: 0,
    mapResultId: null,
    ...overrides,
  };
  render(<ThemeProvider theme="dark"><ChatThread {...props} /></ThemeProvider>);
  return props;
}


describe("ChatThread result actions", () => {
  it("renders legacy object-valued message content without crashing the chat", () => {
    renderThread({
      messages: [{
        id: "legacy-analysis",
        role: "assistant",
        content: {
          website: "https://example.test",
          social_media: ["telegram"],
          news: [],
        },
        metadata: {},
      }],
    });

    expect(screen.getByText(/https:\/\/example\.test/)).toBeTruthy();
  });

  it("removes the live parsing animation after a successful search", () => {
    renderThread({
      task: { id: "task-1", status: "done", found_count: 5, target_count: 5 },
    });
    expect(screen.queryByLabelText("Ход поиска организаций")).toBeNull();
  });

  it("opens a saved search result from the assistant completion message", () => {
    const onOpenResult = vi.fn();
    renderThread({
      onOpenResult,
      messages: [{
        id: "m1",
        role: "assistant",
        content: "Поиск завершён.",
        metadata: {
          type: "search_completion",
          action: "open_search_result",
          task_id: "task-1",
          saved_result_id: 42,
        },
      }],
    });
    fireEvent.click(screen.getByRole("button", { name: "Открыть результат" }));
    expect(onOpenResult).toHaveBeenCalledWith(expect.objectContaining({ saved_result_id: 42 }));
  });

  it("opens the live map from a completed search message", () => {
    const onOpenMap = vi.fn();
    renderThread({
      onOpenMap,
      mapPointCount: 8,
      mapResultId: 42,
      messages: [{
        id: "m-map",
        role: "assistant",
        content: "Поиск завершён.",
        metadata: { type: "search_completion", saved_result_id: 42 },
      }],
    });

    fireEvent.click(screen.getByRole("button", { name: "Карта · 8 точек" }));
    expect(onOpenMap).toHaveBeenCalledOnce();
  });

  it("does not attach the current map to an older search completion", () => {
    renderThread({
      mapPointCount: 8,
      mapResultId: 42,
      messages: [{
        id: "m-old-map",
        role: "assistant",
        content: "Старый поиск завершён.",
        metadata: { type: "search_completion", saved_result_id: 10 },
      }],
    });

    expect(screen.queryByRole("button", { name: /Карта/ })).toBeNull();
  });

  it("opens a company analysis from its completion message", () => {
    const onOpenResult = vi.fn();
    renderThread({
      onOpenResult,
      messages: [{
        id: "m2",
        role: "assistant",
        content: "Анализ завершён.",
        metadata: {
          type: "company_analysis_completion",
          action: "open_company_analysis",
          task_id: "analysis-1",
        },
      }],
    });
    fireEvent.click(screen.getByRole("button", { name: "Открыть анализ" }));
    expect(onOpenResult).toHaveBeenCalledWith(expect.objectContaining({ task_id: "analysis-1" }));
  });
});
