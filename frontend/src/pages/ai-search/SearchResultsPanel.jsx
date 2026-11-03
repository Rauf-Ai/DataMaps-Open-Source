import { memo, useEffect, useMemo, useRef, useState } from "react";
import { Alert, Button, Checkbox, Progress } from "@gravity-ui/uikit";
import { ArrowDownToLine, ArrowUpRightFromSquare, ChevronDown, ChevronRight, FileMagnifier, Xmark } from "@gravity-ui/icons";
import { fmtDate, fmtInt } from "../../api.js";
import { LiveResultsMap } from "./LiveResultsMap.jsx";


function valueOrDash(value) {
  return value === null || value === undefined || value === "" ? "—" : value;
}

function absoluteHref(value) {
  if (!value) return "";
  return /^https?:\/\//i.test(value) ? value : `https://${value}`;
}


function socialLinks(raw) {
  if (!raw) return [];
  return String(raw)
    .split(/[\n,;]+/)
    .map((value) => value.trim())
    .filter(Boolean)
    .map((value) => {
      const href = /^https?:\/\//i.test(value) ? value : `https://${value}`;
      let label = "Ссылка";
      try {
        const host = new URL(href).hostname.replace(/^www\./, "");
        if (host === "vk.com" || host.endsWith(".vk.com") || host === "vk.ru" || host.endsWith(".vk.ru")) label = "VK";
        else if (host === "max.ru" || host.endsWith(".max.ru")) label = "Max";
        else if (host.includes("instagram.com")) label = "Instagram";
        else if (host.includes("t.me") || host.includes("telegram")) label = "Telegram";
        else if (host.includes("youtube")) label = "YouTube";
        else if (host.includes("facebook")) label = "Facebook";
        else if (host.includes("ok.ru")) label = "OK";
        else label = host;
      } catch {
        return null;
      }
      return { href, label };
    })
    .filter(Boolean);
}

function normalizeResultItem(item) {
  if (item?.business) return item;
  return {
    ...item,
    source_url: item?.source_url || item?.map_url,
    business: {
      id: item?.business_id || item?.oid || item?.id || item?.position,
      name: item?.name,
      category: item?.category,
      country: item?.country,
      region: item?.region,
      city: item?.city,
      address: item?.address,
      phone: item?.phone,
      website: item?.website || item?.site,
      social: item?.social,
      rating: item?.rating,
      reviews_count: item?.reviews_count ?? item?.reviews,
      last_seen_at: item?.last_seen_at,
    },
  };
}


export const SearchResultsPanel = memo(function SearchResultsPanel({
  task,
  result,
  liveRows = [],
  analysisBusy,
  mapOpen = false,
  selectedBusinessId = null,
  onMapOpenChange,
  onSelectBusiness,
  onRequestAnalysis,
  onClose,
}) {
  const done = task?.status === "done";
  const canUseSavedResult = done && Boolean(result);
  const items = useMemo(
    () => (canUseSavedResult ? result.items || [] : liveRows).map(normalizeResultItem),
    [canUseSavedResult, liveRows, result],
  );
  const [expanded, setExpanded] = useState(() => new Set());
  const [selected, setSelected] = useState(() => new Set());
  const rowRefs = useRef(new Map());
  const groupedItems = useMemo(() => {
    if (!canUseSavedResult) return items.map((item) => ({
      key: `business:${item.business?.id || item.position}`,
      items: [item],
      franchise: false,
    }));
    const groups = [];
    const byChain = new Map();
    for (const item of items) {
      const business = item.business || {};
      if (!business.is_franchise || !business.chain_key) {
        groups.push({ key: `business:${business.id || item.position}`, items: [item], franchise: false });
        continue;
      }
      let group = byChain.get(business.chain_key);
      if (!group) {
        group = { key: business.chain_key, items: [], franchise: true };
        byChain.set(business.chain_key, group);
        groups.push(group);
      }
      group.items.push(item);
    }
    return groups;
  }, [canUseSavedResult, items]);

  useEffect(() => {
    setSelected(new Set());
  }, [result?.id]);

  useEffect(() => {
    if (selectedBusinessId === null || selectedBusinessId === undefined) return;
    rowRefs.current.get(Number(selectedBusinessId))?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
  }, [selectedBusinessId]);

  const itemsById = useMemo(() => new Map(
    items.map((item) => [Number(item.business?.id), item]),
  ), [items]);
  const selectedItems = useMemo(
    () => [...selected].map((id) => itemsById.get(id)).filter(Boolean),
    [itemsById, selected],
  );
  const toggleGroup = (key) => setExpanded((current) => {
    const next = new Set(current);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });

  const toggleSelected = (businessId) => setSelected((current) => {
    const next = new Set(current);
    if (next.has(businessId)) next.delete(businessId); else next.add(businessId);
    return next;
  });

  const toggleAll = () => setSelected((current) => (
    current.size === items.length ? new Set() : new Set(items.map((item) => Number(item.business?.id)).filter(Boolean))
  ));

  const toggleGroupSelection = (groupItems) => setSelected((current) => {
    const ids = groupItems.map((item) => Number(item.business?.id)).filter(Boolean);
    const allSelected = ids.every((id) => current.has(id));
    const next = new Set(current);
    ids.forEach((id) => allSelected ? next.delete(id) : next.add(id));
    return next;
  });

  const requestCompanyAnalysis = () => {
    if (!selected.size || selected.size > 20 || analysisBusy) return;
    onRequestAnalysis?.(selectedItems.map((item) => Number(item.business.id)));
  };

  const renderBusinessRow = (item, className = "") => {
    const business = item.business || {};
    return (
      <tr
        className={`${className}${Number(business.id) === Number(selectedBusinessId) ? " is-map-selected" : ""}`}
        data-testid={`business-row-${business.id}`}
        key={`${item.position}-${business.id}`}
        ref={(node) => {
          if (node) rowRefs.current.set(Number(business.id), node);
          else rowRefs.current.delete(Number(business.id));
        }}
      >
        {canUseSavedResult && <td className="is-check"><Checkbox checked={selected.has(Number(business.id))} onUpdate={() => toggleSelected(Number(business.id))} aria-label={`Выбрать ${business.name || "организацию"}`} /></td>}
        <td className="is-name">{canUseSavedResult ? <button type="button" className="dm-results-table__company" onClick={() => onSelectBusiness?.(Number(business.id))}>{valueOrDash(business.name)}</button> : valueOrDash(business.name)}</td>
        <td>{valueOrDash(business.category)}</td>
        <td>{valueOrDash(business.country)}</td>
        <td>{valueOrDash(business.region)}</td>
        <td>{valueOrDash(business.city)}</td>
        <td className="is-address">{valueOrDash(business.address)}</td>
        <td>{valueOrDash(business.phone)}</td>
        <td>{business.website ? <a href={absoluteHref(business.website)} target="_blank" rel="noreferrer">Открыть</a> : "—"}</td>
        <td className="is-social">{socialLinks(business.social).length ? socialLinks(business.social).map((link) => <a href={link.href} target="_blank" rel="noreferrer" key={link.href}>{link.label}</a>) : "—"}</td>
        <td>{valueOrDash(business.rating)}</td>
        <td>{fmtInt(business.reviews_count)}</td>
        <td>{item.source_url ? <a href={absoluteHref(item.source_url)} target="_blank" rel="noreferrer" className="dm-source-link">{valueOrDash(item.source)} <ArrowUpRightFromSquare /></a> : valueOrDash(item.source)}</td>
        <td>{fmtDate(item.parsed_at || business.last_seen_at)}</td>
      </tr>
    );
  };

  return (
    <aside className={`dm-results-panel${mapOpen ? " has-map is-mobile-map" : " is-mobile-list"}`} aria-label="Результаты поиска">
      <header className="dm-results-panel__head">
        <div>
          <span>Результаты</span>
          <strong>{canUseSavedResult ? `${fmtInt(result?.row_count || task?.org_count || 0)} организаций` : `${fmtInt(items.length)} найдено`}</strong>
        </div>
        <Button view="flat" onClick={onClose} aria-label="Закрыть результаты" title="Закрыть"><Xmark /></Button>
      </header>

      {!done && task?.status !== "failed" && (
        <div className="dm-results-panel__progress">
          <Progress value={task?.progress || 0} theme="info" />
          <span>{task?.message || "Подключаемся к картам и собираем карточки организаций…"}</span>
        </div>
      )}
      {task?.status === "failed" && <Alert theme="danger" title="Поиск не выполнен" message={task.error || "Источник не вернул организации"} />}

      {canUseSavedResult && (
        <>
          <div className="dm-results-panel__toolbar">
            <div className="dm-results-panel__selection">
              <Button view="flat" onClick={toggleAll}>{selected.size === items.length && items.length ? "Снять выбор" : "Выбрать все"}</Button>
              <span>Выбрано: {fmtInt(selected.size)}</span>
            </div>
            <div className="dm-results-panel__actions">
              <Button view="flat" onClick={() => onMapOpenChange?.(!mapOpen)}>{mapOpen ? "Скрыть карту" : "Показать карту"}</Button>
              <Button view="outlined" loading={analysisBusy} disabled={!selected.size || selected.size > 20 || analysisBusy} onClick={requestCompanyAnalysis} title="Настроить глубокий анализ в чате"><FileMagnifier /> Анализ компании</Button>
              {task.download_url && <Button view="action" href={task.download_url}><ArrowDownToLine /> XLSX</Button>}
            </div>
          </div>
          {selected.size > 20 && <Alert theme="warning" title="Выбрано слишком много" message="За один глубокий анализ можно выбрать до 20 компаний." />}
          {mapOpen && (
            <>
              <div className="dm-results-map-switcher" aria-label="Представление результатов">
                <Button view="normal" onClick={() => onMapOpenChange?.(true)}>Карта</Button>
                <Button view="flat" onClick={() => onMapOpenChange?.(false)}>Список</Button>
              </div>
              <LiveResultsMap items={items} selectedBusinessId={selectedBusinessId} onSelectBusiness={onSelectBusiness} />
            </>
          )}
        </>
      )}
      {(canUseSavedResult || items.length > 0) && <div className="dm-results-table-wrap">
        <table className="dm-results-table">
          <thead>
            <tr>
              {canUseSavedResult && <th className="is-check"><Checkbox checked={items.length > 0 && selected.size === items.length} indeterminate={selected.size > 0 && selected.size < items.length} onUpdate={toggleAll} aria-label="Выбрать все организации" /></th>}
              <th>Название</th><th>Категория</th><th>Страна</th><th>Регион</th><th>Город</th><th>Адрес</th>
              <th>Телефон</th><th>Сайт</th><th>Соцсети</th><th>Рейтинг</th><th>Отзывы</th><th>Источник</th><th>Актуальность</th>
            </tr>
          </thead>
          <tbody>
            {groupedItems.flatMap((group) => {
              if (!group.franchise) return [renderBusinessRow(group.items[0])];
              const first = group.items[0].business || {};
              const isOpen = expanded.has(group.key);
              return [
                <tr className="dm-results-table__group" key={`group:${group.key}`}>
                  <td colSpan={canUseSavedResult ? 14 : 13}>
                    <div className="dm-results-table__group-inner">
                      <Checkbox checked={group.items.every((item) => selected.has(Number(item.business?.id)))} indeterminate={group.items.some((item) => selected.has(Number(item.business?.id))) && !group.items.every((item) => selected.has(Number(item.business?.id)))} onUpdate={() => toggleGroupSelection(group.items)} aria-label="Выбрать все точки сети" />
                      <button type="button" onClick={() => toggleGroup(group.key)} aria-expanded={isOpen}>
                      {isOpen ? <ChevronDown /> : <ChevronRight />}
                      <strong>{valueOrDash(first.brand_name || first.name)}</strong>
                      <span>{fmtInt(first.branch_count || group.items.length)} точек</span>
                      </button>
                    </div>
                  </td>
                </tr>,
                ...(isOpen ? group.items.map((item) => renderBusinessRow(item, "is-branch")) : []),
              ];
            })}
          </tbody>
        </table>
        {!items.length && <div className="dm-results-panel__empty">Результат сохранён, но строки пока недоступны.</div>}
      </div>}
    </aside>
  );
});
