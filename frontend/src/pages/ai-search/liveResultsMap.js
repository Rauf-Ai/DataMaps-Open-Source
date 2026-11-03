const OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";

export const OSM_RASTER_STYLE = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: [OSM_TILE_URL],
      tileSize: 256,
      attribution: "© OpenStreetMap contributors",
    },
  },
  layers: [{ id: "osm", type: "raster", source: "osm" }],
};

function coordinate(value, min, max) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) && number >= min && number <= max ? number : null;
}

export function buildMapData(items = []) {
  const features = [];
  let unavailableCount = 0;

  for (const item of items) {
    const business = item?.business || {};
    const lat = coordinate(business.lat, -90, 90);
    const lon = coordinate(business.lon, -180, 180);
    if (lat === null || lon === null) {
      unavailableCount += 1;
      continue;
    }

    features.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: [lon, lat] },
      properties: {
        businessId: business.id,
        name: business.name || "Без названия",
        category: business.category || "",
        address: business.address || "",
        rating: business.rating,
        reviewsCount: business.reviews_count,
        phone: business.phone || "",
        website: business.website || "",
        sourceUrl: item.source_url || "",
      },
    });
  }

  const longitudes = features.map((feature) => feature.geometry.coordinates[0]);
  const latitudes = features.map((feature) => feature.geometry.coordinates[1]);

  return {
    featureCollection: { type: "FeatureCollection", features },
    mappableCount: features.length,
    unavailableCount,
    bounds: features.length
      ? [Math.min(...longitudes), Math.min(...latitudes), Math.max(...longitudes), Math.max(...latitudes)]
      : null,
  };
}
