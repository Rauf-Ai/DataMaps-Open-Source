import { describe, expect, it } from "vitest";
import { buildMapData } from "./liveResultsMap.js";

const item = (id, lat, lon) => ({
  source_url: `https://example.test/${id}`,
  business: {
    id,
    name: `Company ${id}`,
    category: "Cafe",
    address: "Main street",
    rating: 4.8,
    reviews_count: 12,
    phone: "+7 900",
    website: "https://company.test",
    lat,
    lon,
  },
});

describe("buildMapData", () => {
  it("creates GeoJSON features and bounds from valid coordinates", () => {
    const data = buildMapData([item(1, 55.75, 37.61), item(2, "56.0", "38.0")]);

    expect(data.mappableCount).toBe(2);
    expect(data.unavailableCount).toBe(0);
    expect(data.featureCollection.features[0]).toMatchObject({
      geometry: { coordinates: [37.61, 55.75] },
      properties: { businessId: 1, sourceUrl: "https://example.test/1" },
    });
    expect(data.bounds).toEqual([37.61, 55.75, 38, 56]);
  });

  it("rejects missing, non-finite, and out-of-range coordinates", () => {
    const data = buildMapData([
      item(1, 55.75, 37.61),
      item(2, null, 37),
      item(3, 91, 10),
      item(4, 0, "Infinity"),
    ]);

    expect(data.mappableCount).toBe(1);
    expect(data.unavailableCount).toBe(3);
    expect(data.featureCollection.features).toHaveLength(1);
  });
});
