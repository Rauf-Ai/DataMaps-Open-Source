import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LiveResultsMap } from "./LiveResultsMap.jsx";

const mapInstances = [];
const markers = [];

vi.mock("maplibre-gl", () => {
  class Map {
    constructor() {
      this.handlers = new globalThis.Map();
      this.addSource = vi.fn();
      this.addLayer = vi.fn();
      this.fitBounds = vi.fn();
      this.setCenter = vi.fn();
      this.setZoom = vi.fn();
      this.getZoom = vi.fn(() => 12);
      this.flyTo = vi.fn();
      this.getCanvas = vi.fn(() => ({ style: {} }));
      this.remove = vi.fn();
      mapInstances.push(this);
    }

    on(event, layerOrHandler, maybeHandler) {
      const handler = typeof layerOrHandler === "function" ? layerOrHandler : maybeHandler;
      this.handlers.set(`${event}:${typeof layerOrHandler === "string" ? layerOrHandler : ""}`, handler);
      if (event === "load") handler();
      return this;
    }

    off() { return this; }
  }

  class Marker {
    constructor(options) {
      this.options = options;
      this.setLngLat = vi.fn(() => this);
      this.addTo = vi.fn(() => this);
      this.remove = vi.fn();
      markers.push(this);
    }
  }

  return { Map, Marker };
});

afterEach(() => {
  mapInstances.length = 0;
  markers.length = 0;
});

describe("LiveResultsMap", () => {
  it("shows every valid company as an individual map point and reports those without coordinates", () => {
    render(
      <LiveResultsMap
        items={[
          { business: { id: 1, name: "Valid", lat: 55.75, lon: 37.61 } },
          { business: { id: 2, name: "Missing", lat: null, lon: null } },
        ]}
        onSelectBusiness={vi.fn()}
      />,
    );

    expect(screen.getByLabelText("Карта результатов поиска")).toBeTruthy();
    expect(screen.getByText("На карте показано 1 из 2 компаний: для 1 координаты недоступны")).toBeTruthy();
    expect(markers).toHaveLength(1);
    expect(markers[0].options.element.className).toBe("dm-map-marker");
    expect(screen.queryByLabelText("Карточка компании")).toBeNull();
  });

  it("shows the selected company in a fixed side card and flies there quickly", () => {
    render(
      <LiveResultsMap
        items={[{
          source_url: "https://maps.example.test/company",
          business: {
            id: 1,
            name: "Кафе на углу",
            category: "Кафе",
            address: "Тверская, 1",
            rating: 4.8,
            reviews_count: 42,
            phone: "+7 999 000-00-00",
            website: "https://cafe.example.test",
            lat: 55.75,
            lon: 37.61,
          },
        }]}
        selectedBusinessId={1}
        onSelectBusiness={vi.fn()}
      />,
    );

    expect(screen.getByLabelText("Карточка компании")).toBeTruthy();
    expect(screen.getByText("Кафе на углу")).toBeTruthy();
    expect(screen.queryByText("Категория")).toBeNull();
    expect(screen.queryByText("Рейтинг")).toBeNull();
    expect(screen.getByLabelText("Рейтинг 4.8, 42 отзывов")).toBeTruthy();
    expect(screen.getByLabelText("Открыть источник компании")).toBeTruthy();
    expect(screen.queryByText("Открыть источник")).toBeNull();
    expect(mapInstances[0].flyTo).toHaveBeenCalledWith(expect.objectContaining({ duration: 400 }));
  });

  it("explains why no map is available when all companies lack coordinates", () => {
    render(<LiveResultsMap items={[{ business: { id: 1, name: "Missing", lat: null, lon: null } }]} onSelectBusiness={vi.fn()} />);

    expect(screen.getByText("Координаты для найденных компаний недоступны")).toBeTruthy();
  });
});
