import { useEffect, useMemo, useRef } from "react";
import { Link, StarFill } from "@gravity-ui/icons";
import { Map, Marker } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { buildMapData, OSM_RASTER_STYLE } from "./liveResultsMap.js";


function validLink(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch {
    return "";
  }
}


function MapCompanyCard({ feature }) {
  const business = feature.properties;
  const website = validLink(business.website);
  const sourceUrl = validLink(business.sourceUrl);
  const hasRating = business.rating !== null && business.rating !== undefined;
  const hasReviews = business.reviewsCount !== null && business.reviewsCount !== undefined;
  const ratingLabel = `${hasRating ? business.rating : "—"}, ${hasReviews ? business.reviewsCount : "—"} отзывов`;
  return (
    <aside className="dm-map-card" aria-label="Карточка компании">
      <div className="dm-map-card__heading">
        <strong className="dm-map-card__title">{business.name}</strong>
        {sourceUrl && <a className="dm-map-card__source" href={sourceUrl} target="_blank" rel="noreferrer" aria-label="Открыть источник компании" title="Открыть источник"><Link /></a>}
      </div>
      {business.address && <div><span>Адрес</span><b>{business.address}</b></div>}
      {(hasRating || hasReviews) && <div className="dm-map-card__rating" aria-label={`Рейтинг ${ratingLabel}`}><StarFill /><b>{hasRating ? business.rating : "—"}</b><span>· {hasReviews ? business.reviewsCount : "—"} отзывов</span></div>}
      {business.phone && <div><span>Телефон</span><b>{business.phone}</b></div>}
      {website && <a href={website} target="_blank" rel="noreferrer">Сайт компании</a>}
    </aside>
  );
}


function createMarkerElement(feature, onSelectBusiness) {
  const element = document.createElement("button");
  element.type = "button";
  element.className = "dm-map-marker";
  element.setAttribute("aria-label", `Открыть ${feature.properties.name || "организацию"} на карте`);
  element.title = feature.properties.name || "Организация";
  element.addEventListener("click", () => onSelectBusiness?.(Number(feature.properties.businessId)));
  return element;
}


export function LiveResultsMap({ items, selectedBusinessId, onSelectBusiness }) {
  const containerRef = useRef(null);
  const mapRef = useRef(null);
  const data = useMemo(() => buildMapData(items), [items]);
  const selectedFeature = data.featureCollection.features.find(
    (item) => Number(item.properties.businessId) === Number(selectedBusinessId),
  );

  useEffect(() => {
    if (!containerRef.current || !data.mappableCount) return undefined;

    const map = new Map({
      container: containerRef.current,
      style: OSM_RASTER_STYLE,
      center: data.featureCollection.features[0].geometry.coordinates,
      zoom: data.mappableCount === 1 ? 13 : 4,
      attributionControl: false,
    });
    mapRef.current = map;
    const markers = [];

    map.on("load", () => {
      data.featureCollection.features.forEach((feature) => {
        const marker = new Marker({ element: createMarkerElement(feature, onSelectBusiness), anchor: "bottom" })
          .setLngLat(feature.geometry.coordinates)
          .addTo(map);
        markers.push(marker);
      });

      if (data.mappableCount > 1) {
        map.fitBounds(data.bounds, { padding: 48, maxZoom: 14 });
      }
    });

    return () => {
      markers.forEach((marker) => marker.remove());
      map.remove();
      mapRef.current = null;
    };
  }, [data, onSelectBusiness]);

  useEffect(() => {
    const feature = data.featureCollection.features.find(
      (item) => Number(item.properties.businessId) === Number(selectedBusinessId),
    );
    const map = mapRef.current;
    if (!feature || !map) return;
    map.flyTo({ center: feature.geometry.coordinates, zoom: Math.max(map.getZoom(), 14), duration: 400, essential: true });
  }, [data, selectedBusinessId]);

  if (!data.mappableCount) {
    return <section className="dm-live-results-map is-empty" aria-label="Карта результатов поиска">Координаты для найденных компаний недоступны</section>;
  }

  const total = data.mappableCount + data.unavailableCount;
  return (
    <section className={`dm-live-results-map${selectedFeature ? " has-card" : ""}`} aria-label="Карта результатов поиска">
      {selectedFeature && <MapCompanyCard feature={selectedFeature} />}
      <div className="dm-live-results-map__map-area">
        <div className="dm-live-results-map__canvas" ref={containerRef} />
        <a className="dm-live-results-map__attribution" href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">© OpenStreetMap contributors</a>
        {data.unavailableCount > 0 && <p className="dm-live-results-map__summary">На карте показано {data.mappableCount} из {total} компаний: для {data.unavailableCount} координаты недоступны</p>}
      </div>
    </section>
  );
}
