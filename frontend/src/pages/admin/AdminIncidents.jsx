import { useCallback, useEffect, useMemo, useState } from "react";
import { Button, Label, Loader, Modal, Select, Text, TextInput } from "@gravity-ui/uikit";
import { api, fmtDate, fmtInt } from "../../api.js";
import { formatSeverity, formatStatus, safeJson, severityTheme, statusTheme } from "./adminFormat.js";

const PAGE_SIZE = 20;
const EMPTY_FILTERS = { status: "", severity: "", source: "", q: "", from: "", to: "" };

function buildQuery(filters, page) {
  const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
  for (const [key, value] of Object.entries(filters)) {
    if (value) params.set(key === "from" ? "date_from" : key === "to" ? "date_to" : key, value);
  }
  return params.toString();
}

function IncidentStatus({ value }) {
  return <Label theme={statusTheme(value)}>{formatStatus(value)}</Label>;
}

export function AdminIncidents({ initialIncidentId, initialFilters = {} }) {
  const [filters, setFilters] = useState({ ...EMPTY_FILTERS, ...initialFilters });
  const [appliedFilters, setAppliedFilters] = useState({ ...EMPTY_FILTERS, ...initialFilters });
  const [page, setPage] = useState(1);
  const [result, setResult] = useState({ items: [], total: 0, page: 1, page_size: PAGE_SIZE });
  const [selected, setSelected] = useState(null);
  const [comment, setComment] = useState("");
  const [actionError, setActionError] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const initialFilterKey = JSON.stringify({ ...EMPTY_FILTERS, ...initialFilters });

  const query = useMemo(() => buildQuery(appliedFilters, page), [appliedFilters, page]);
  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const next = await api(`/api/admin/control/incidents?${query}`);
      setResult(next);
    } catch (err) {
      setError(err.message || "Не удалось загрузить инциденты");
    } finally {
      setLoading(false);
    }
  }, [query]);

  const openDetails = useCallback((incident) => {
    setSelected(incident);
    setComment("");
    setActionError("");
  }, []);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    const nextFilters = { ...EMPTY_FILTERS, ...initialFilters };
    setFilters(nextFilters);
    setAppliedFilters(nextFilters);
    setPage(1);
  }, [initialFilterKey]); // Initial values are derived from AdminPage URL query params.

  useEffect(() => {
    if (!initialIncidentId) return;
    let active = true;
    api(`/api/admin/control/incidents/${initialIncidentId}`)
      .then((incident) => { if (active) openDetails(incident); })
      .catch((err) => { if (active) setError(err.message || "Не удалось открыть инцидент"); });
    return () => { active = false; };
  }, [initialIncidentId, openDetails]);

  const applyFilters = () => {
    setPage(1);
    setAppliedFilters({ ...filters });
  };

  const resetFilters = () => {
    setFilters(EMPTY_FILTERS);
    setAppliedFilters(EMPTY_FILTERS);
    setPage(1);
  };

  const updateStatus = async (status) => {
    const trimmed = comment.trim();
    if (!trimmed) {
      setActionError("Введите комментарий к действию");
      return;
    }
    if (!selected) return;
    setSaving(true);
    setActionError("");
    try {
      const updated = await api(`/api/admin/control/incidents/${selected.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status, comment: trimmed }),
      });
      setSelected(updated);
      await load();
    } catch (err) {
      setActionError(err.message || "Не удалось изменить статус");
    } finally {
      setSaving(false);
    }
  };

  const sourceOptions = Array.from(new Set(["yandex", "google", "maps", "email", "llm", ...result.items.map((item) => item.source).filter(Boolean)]));
  const totalPages = Math.max(1, Math.ceil((result.total || 0) / PAGE_SIZE));

  return (
    <div className="dm-admin-control dm-admin-incidents">
      <header className="dm-admin-control__header">
        <div>
          <Text variant="subheader-2" as="h1">Инциденты</Text>
          <Text variant="caption-2" color="secondary">Сгруппированные сбои и история их обработки</Text>
        </div>
        <Button view="flat" size="s" onClick={load} loading={loading}>Обновить</Button>
      </header>

      <section className="dm-admin-filterbar" aria-label="Фильтры инцидентов">
        <label className="dm-admin-field" htmlFor="admin-incidents-query">
          <span>Код или request ID</span>
          <TextInput id="admin-incidents-query" value={filters.q} placeholder="MAP-500 или req-…" onUpdate={(q) => setFilters((current) => ({ ...current, q }))} />
        </label>
        <label className="dm-admin-field">
          <span>Статус</span>
          <Select value={[filters.status]} onUpdate={([status]) => setFilters((current) => ({ ...current, status }))}>
            <Select.Option value="">Все</Select.Option>
            <Select.Option value="new">Новые</Select.Option>
            <Select.Option value="acknowledged">Принятые</Select.Option>
            <Select.Option value="resolved">Решённые</Select.Option>
          </Select>
        </label>
        <label className="dm-admin-field">
          <span>Критичность</span>
          <Select value={[filters.severity]} onUpdate={([severity]) => setFilters((current) => ({ ...current, severity }))}>
            <Select.Option value="">Все</Select.Option>
            <Select.Option value="critical">Критично</Select.Option>
            <Select.Option value="warning">Внимание</Select.Option>
            <Select.Option value="info">Информация</Select.Option>
          </Select>
        </label>
        <label className="dm-admin-field">
          <span>Источник</span>
          <Select value={[filters.source]} onUpdate={([source]) => setFilters((current) => ({ ...current, source }))}>
            <Select.Option value="">Все</Select.Option>
            {sourceOptions.map((source) => <Select.Option key={source} value={source}>{source}</Select.Option>)}
          </Select>
        </label>
        <label className="dm-admin-field">
          <span>С даты</span>
          <TextInput type="date" value={filters.from} onUpdate={(from) => setFilters((current) => ({ ...current, from }))} />
        </label>
        <label className="dm-admin-field">
          <span>По дату</span>
          <TextInput type="date" value={filters.to} onUpdate={(to) => setFilters((current) => ({ ...current, to }))} />
        </label>
        <div className="dm-admin-filterbar__actions">
          <Button view="outlined" size="m" onClick={resetFilters}>Сбросить</Button>
          <Button view="action" size="m" onClick={applyFilters}>Применить</Button>
        </div>
      </section>

      {error && <div className="dm-admin-control__error" role="alert">{error}</div>}
      {loading && !result.items.length ? (
        <div className="dm-admin-control__loading"><Loader size="l" /> <span>Загрузка инцидентов…</span></div>
      ) : (
        <>
          <div className="dm-admin-incidents__summary">
            <Text variant="caption-2" color="secondary">Всего: {fmtInt(result.total)}</Text>
            <Text variant="caption-2" color="secondary">Страница {page} из {totalPages}</Text>
          </div>
          <div className="dm-admin-incidents__desktop">
            <table>
              <thead><tr><th>Инцидент</th><th>Источник</th><th>Статус</th><th>Повторов</th><th>Последний сигнал</th><th></th></tr></thead>
              <tbody>
                {result.items.map((incident) => (
                  <tr key={incident.id}>
                    <td><div className="dm-admin-incident__title"><Label theme={severityTheme(incident.severity)}>{incident.code}</Label><span>{incident.message}</span></div></td>
                    <td>{incident.source || "—"}</td>
                    <td><IncidentStatus value={incident.status} /></td>
                    <td className="dm-tabular">{fmtInt(incident.occurrence_count)}</td>
                    <td>{fmtDate(incident.last_seen)}</td>
                    <td><Button view="flat" size="s" onClick={() => openDetails(incident)}>Подробнее</Button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="dm-admin-incidents__mobile">
            {result.items.map((incident) => (
              <details key={incident.id} className="dm-admin-incident-mobile">
                <summary>
                  <Label theme={severityTheme(incident.severity)}>{incident.code}</Label>
                  <span>{incident.message}</span>
                  <IncidentStatus value={incident.status} />
                </summary>
                <div>
                  <span>Источник: {incident.source || "—"}</span>
                  <span>Повторов: {fmtInt(incident.occurrence_count)}</span>
                  <span>Последний: {fmtDate(incident.last_seen)}</span>
                  <Button view="outlined" size="s" onClick={() => openDetails(incident)}>Подробнее</Button>
                </div>
              </details>
            ))}
          </div>
          {!result.items.length && <div className="dm-admin-empty">По этим фильтрам инцидентов нет.</div>}
          <footer className="dm-admin-pagination">
            <Button view="outlined" size="s" disabled={page <= 1 || loading} onClick={() => setPage((current) => current - 1)}>Назад</Button>
            <span>{page} / {totalPages}</span>
            <Button view="outlined" size="s" disabled={page >= totalPages || loading} onClick={() => setPage((current) => current + 1)}>Вперёд</Button>
          </footer>
        </>
      )}

      <Modal open={Boolean(selected)} onClose={() => !saving && setSelected(null)}>
        <section className="dm-admin-incident-dialog" aria-label="Детали инцидента">
          <header>
            <div>
              <div className="dm-row"><Label theme={severityTheme(selected?.severity)}>{selected?.code}</Label><IncidentStatus value={selected?.status} /></div>
              <Text variant="subheader-2" as="h2">{selected?.message}</Text>
            </div>
            <Button view="flat" size="s" onClick={() => setSelected(null)} disabled={saving} aria-label="Закрыть">×</Button>
          </header>
          <dl className="dm-admin-incident-dialog__meta">
            <div><dt>Источник</dt><dd>{selected?.source || "—"}</dd></div>
            <div><dt>Request ID</dt><dd>{selected?.request_id || "—"}</dd></div>
            <div><dt>Первый сигнал</dt><dd>{fmtDate(selected?.first_seen)}</dd></div>
            <div><dt>Последний сигнал</dt><dd>{fmtDate(selected?.last_seen)}</dd></div>
            <div><dt>Повторов</dt><dd>{fmtInt(selected?.occurrence_count)}</dd></div>
          </dl>
          <div className="dm-admin-incident-dialog__details">
            <Text variant="caption-2" color="secondary">Безопасные технические данные</Text>
            <pre>{safeJson(selected?.details)}</pre>
          </div>
          {selected?.status !== "resolved" && (
            <div className="dm-admin-incident-dialog__action">
              <label htmlFor="admin-incident-comment">Комментарий</label>
              <TextInput id="admin-incident-comment" placeholder="Что было проверено или исправлено" value={comment} onUpdate={setComment} />
              {actionError && <div className="dm-admin-control__inline-error" role="alert">{actionError}</div>}
              <div className="dm-admin-incident-dialog__buttons">
                {selected?.status === "new" && <Button view="outlined" loading={saving} onClick={() => updateStatus("acknowledged")}>Принять</Button>}
                <Button view="action" loading={saving} onClick={() => updateStatus("resolved")}>Решён</Button>
              </div>
            </div>
          )}
        </section>
      </Modal>
    </div>
  );
}
