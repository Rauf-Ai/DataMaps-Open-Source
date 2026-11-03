import { fireEvent, render, screen } from "@testing-library/react";
import { ThemeProvider } from "@gravity-ui/uikit";
import { describe, expect, it, vi } from "vitest";
import { SearchResultsPanel } from "./SearchResultsPanel.jsx";

vi.mock("./LiveResultsMap.jsx", () => ({
  LiveResultsMap: ({ onSelectBusiness }) => (
    <button type="button" aria-label="Карта результатов поиска" onClick={() => onSelectBusiness(7)}>Выбрать компанию на карте</button>
  ),
}));

const resultItem = {
  position: 1,
  source: "Яндекс",
  source_url: "https://yandex.test/company",
  business: {
    id: 7,
    name: "Кафе на углу",
    category: "Кафе",
    region: "Москва",
    city: "Москва",
    address: "ул. Тверская, 1",
    phone: "+7 999 000-00-00",
    website: "https://cafe.test",
    social: "",
    rating: 4.7,
    reviews_count: 23,
    freshness_score: 100,
    last_seen_at: "2026-08-03T00:00:00Z",
  },
};

function renderPanel(props = {}) {
  return render(
    <ThemeProvider theme="dark">
      <SearchResultsPanel
        task={{ status: "done" }}
        result={{ row_count: 1, items: [resultItem] }}
        analysisBusy={false}
        mapOpen
        selectedBusinessId={7}
        onMapOpenChange={vi.fn()}
        onSelectBusiness={vi.fn()}
        onRequestAnalysis={vi.fn()}
        onClose={vi.fn()}
        {...props}
      />
    </ThemeProvider>,
  );
}

describe("SearchResultsPanel map integration", () => {
  it("shows streamed rows while a search is still running without saved-result controls", () => {
    renderPanel({
      task: { status: "running", progress: 25, message: "Собираем карточки" },
      result: null,
      liveRows: [{
        position: 1,
        oid: "live-7",
        name: "Кафе в процессе",
        category: "Кафе",
        city: "Москва",
        address: "ул. Тверская, 1",
        source: "Яндекс",
      }],
      mapOpen: false,
    });

    expect(screen.getByText("Кафе в процессе")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Выбрать все" })).toBeNull();
  });

  it("shows the map above and highlights the selected company", () => {
    renderPanel();

    expect(screen.getByLabelText("Карта результатов поиска")).toBeTruthy();
    expect(screen.getByTestId("business-row-7").classList.contains("is-map-selected")).toBe(true);
  });

  it("lets the user collapse the map", () => {
    const onMapOpenChange = vi.fn();
    renderPanel({ onMapOpenChange });

    fireEvent.click(screen.getByRole("button", { name: "Скрыть карту" }));
    expect(onMapOpenChange).toHaveBeenCalledWith(false);
  });

  it("forwards a map marker selection to the workspace", () => {
    const onSelectBusiness = vi.fn();
    renderPanel({ onSelectBusiness });

    fireEvent.click(screen.getByLabelText("Карта результатов поиска"));
    expect(onSelectBusiness).toHaveBeenCalledWith(7);
  });

  it("uses the company's last seen date when the source parse date is unavailable", () => {
    renderPanel();

    expect(screen.getByText(/03\.08\.2026/)).toBeTruthy();
  });
});
