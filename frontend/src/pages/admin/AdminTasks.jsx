import { useCallback, useEffect, useMemo, useState } from "react";
import { Button, Label, Loader, Modal, Select, Text, TextInput } from "@gravity-ui/uikit";
import { useLocation, useNavigate } from "react-router-dom";
import { api, fmtDate, fmtInt } from "../../api.js";

const ACTIVE_STATUSES = new Set(["queued", "running"]);
const STATUS_OPTIONS = [["", "Все статусы"], ["queued", "В очереди"], ["running", "В работе"], ["done", "Готово"], ["partial", "Частично"], ["failed", "Ошибка"], ["canceled", "Отменено"]];
const TYPE_OPTIONS = [["", "Все типы"], ["parser", "Поиск"], ["export", "XLSX"], ["review", "Отзывы"], ["company", "Анализ компании"], ["social", "Соцсети"]];

function statusTheme(status) {
  if (status === "done") return "success";
  if (status === "failed") return "danger";
  if (status === "partial" || status === "canceled") return "warning";
  return "info";
}

function statusLabel(status) {
  return ({ queued: "В очереди", running: "В работе", done: "Готово", partial: "Частично", failed: "Ошибка", canceled: "Отменено" }[status] || status);
}

function taskTypeLabel(type) {
  return ({ parser: "Поиск", export: "XLSX", review: "Отзывы", company: "Анализ компании", social: "Соцсети" }[type] || type);
}

function queryToFilters(search) {
  const params = new URLSearchParams(search);
  return {
    status: params.get("status") || "",
    task_type: params.get("task_type") || "",
    user_query: params.get("user_query") || "",
    error_code: params.get("error_code") || "",
    stalled: params.get("stalled") === "1" || params.get("stalled") === "true",
    page: Math.max(1, Number(params.get("page")) || 1),
  };
}

function requestPath(filters) {
  const params = new URLSearchParams({ page: String(filters.page), page_size: "20" });
  for (const key of ["status", "task_type", "user_query", "error_code"]) {
    if (filters[key]) params.set(key, filters[key]);
  }
  if (filters.stalled) params.set("stalled", "true");
  return `/api/admin/control/tasks?${params.toString()}`;
}

function TaskDetails({ detail, onClose, onChanged }) {
  const [confirmation, setConfirmation] = useState("");
  const [comment, setComment] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const canCancel = ACTIVE_STATUSES.has(detail.status) && !detail.cancel_requested;
  const canRetry = !ACTIVE_STATUSES.has(detail.status) && !(detail.status === "done" && !detail.error_message);

  const runAction = async () => {
    if (!comment.trim()) { setError("Введите комментарий к действию"); return; }
    setSaving(true); setError("");
    try {
      const action = confirmation === "cancel" ? "cancel" : "retry";
      const updated = await api(`/api/admin/control/tasks/${detail.type}/${detail.id}/${action}`, {
        method: "POST", body: JSON.stringify({ comment: comment.trim() }),
      });
      await onChanged?.(updated, action);
      setConfirmation("");
      setComment("");
    } catch (err) {
      setError(err.message || "Действие не выполнено");
    } finally { setSaving(false); }
  };

  return (
    <Modal open onClose={() => !saving && onClose()}>
      <section className="dm-admin-task-dialog" aria-label="Детали задачи">
        <header><div><Text variant="subheader-2" as="h2">{taskTypeLabel(detail.type)}</Text><Text className="dm-admin-email" color="secondary">{detail.user_email}</Text></div><Button view="flat" size="s" aria-label="Закрыть" disabled={saving} onClick={onClose}>×</Button></header>
        <dl className="dm-admin-task-dialog__meta">
          <div><dt>Статус</dt><dd><Label theme={statusTheme(detail.status)}>{statusLabel(detail.status)}</Label></dd></div>
          <div><dt>Прогресс</dt><dd>{fmtInt(detail.progress)}%</dd></div>
          <div><dt>ID задачи</dt><dd>{detail.id}</dd></div>
          <div><dt>Request ID</dt><dd>{detail.request_id || "Не сохранён"}</dd></div>
          <div><dt>Ошибка</dt><dd>{detail.error_code || "—"}</dd></div>
          <div><dt>Источник</dt><dd>{detail.source || "—"}</dd></div>
          <div><dt>Создана</dt><dd>{fmtDate(detail.created_at)}</dd></div>
          <div><dt>Обновлена</dt><dd>{fmtDate(detail.updated_at)}</dd></div>
        </dl>
        {detail.error_message && <div className="dm-admin-task-dialog__error"><strong>{detail.error_code || "Ошибка"}</strong><span>{detail.error_message}</span></div>}
        {(canCancel || canRetry) && <div className="dm-admin-task-dialog__actions">
          {canCancel && <Button view="outlined-danger" size="m" onClick={() => { setConfirmation("cancel"); setError(""); }}>Отменить</Button>}
          {canRetry && <Button view="outlined" size="m" onClick={() => { setConfirmation("retry"); setError(""); }}>Повторить</Button>}
        </div>}
        {confirmation && <section className="dm-admin-confirmation" aria-label="Подтверждение действия">
          <Text variant="subheader-1" as="h3">Подтвердите действие</Text>
          <Text color="secondary">{confirmation === "cancel" ? "Отменить задачу без списания оставшейся квоты." : "Создать новую задачу с исходными параметрами."}</Text>
          <label className="dm-admin-field"><span>Комментарий к действию</span><TextInput aria-label="Комментарий к действию" value={comment} onUpdate={setComment} placeholder="Причина действия" /></label>
          {error && <div className="dm-admin-control__inline-error" role="alert">{error}</div>}
          <div className="dm-admin-confirmation__actions"><Button view="flat" size="m" disabled={saving} onClick={() => { setConfirmation(""); setError(""); }}>Отмена</Button><Button view={confirmation === "cancel" ? "outlined-danger" : "action"} size="m" loading={saving} onClick={runAction}>Подтвердить</Button></div>
        </section>}
      </section>
    </Modal>
  );
}

export function AdminTasks({ isActive = false }) {
  const location = useLocation();
  const navigate = useNavigate();
  const filters = useMemo(() => queryToFilters(location.search), [location.search]);
  const [draft, setDraft] = useState(filters);
  const [result, setResult] = useState({ items: [], total: 0, page: 1, page_size: 20 });
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => setDraft(filters), [filters]);

  const updateUrl = useCallback((next) => {
    const params = new URLSearchParams(location.search);
    params.set("tab", "tasks");
    for (const key of ["status", "task_type", "user_query", "error_code"]) {
      if (next[key]) params.set(key, next[key]); else params.delete(key);
    }
    if (next.stalled) params.set("stalled", "1"); else params.delete("stalled");
    if (next.page > 1) params.set("page", String(next.page)); else params.delete("page");
    navigate({ pathname: location.pathname, search: `?${params.toString()}` });
  }, [location.pathname, location.search, navigate]);

  const load = useCallback(async () => {
    setLoading(true); setError("");
    try { setResult(await api(requestPath(filters))); }
    catch (err) { setError(err.message || "Не удалось загрузить задачи"); }
    finally { setLoading(false); }
  }, [filters]);

  useEffect(() => { load(); }, [load]);

  const hasActiveTasks = result.items.some((item) => ACTIVE_STATUSES.has(item.status));
  useEffect(() => {
    if (!isActive || !hasActiveTasks) return undefined;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") load();
    }, 5000);
    return () => window.clearInterval(timer);
  }, [hasActiveTasks, isActive, load]);

  const openDetail = async (task) => {
    setError("");
    try { setSelected(await api(`/api/admin/control/tasks/${task.type}/${task.id}`)); }
    catch (err) { setError(err.message || "Не удалось загрузить задачу"); }
  };

  const applyFilters = () => updateUrl({ ...draft, page: 1 });
  const totalPages = Math.max(1, Math.ceil(result.total / result.page_size));

  return (
    <section className="dm-admin-control dm-admin-tasks" aria-label="Задачи">
      <header className="dm-admin-control__header"><div><Text variant="subheader-2" as="h1">Задачи</Text><Text variant="caption-2" color="secondary">Все фоновые операции и их техническое состояние</Text></div><Button view="flat" size="s" onClick={load} loading={loading}>Обновить</Button></header>
      <section className="dm-admin-task-filterbar">
        <label className="dm-admin-field"><span>Пользователь или ID</span><TextInput value={draft.user_query} onUpdate={(user_query) => setDraft((value) => ({ ...value, user_query }))} placeholder="email, user ID, task ID" /></label>
        <label className="dm-admin-field"><span>Статус</span><Select value={[draft.status]} onUpdate={([status]) => setDraft((value) => ({ ...value, status }))}>{STATUS_OPTIONS.map(([value, label]) => <Select.Option key={value} value={value}>{label}</Select.Option>)}</Select></label>
        <label className="dm-admin-field"><span>Тип</span><Select value={[draft.task_type]} onUpdate={([task_type]) => setDraft((value) => ({ ...value, task_type }))}>{TYPE_OPTIONS.map(([value, label]) => <Select.Option key={value} value={value}>{label}</Select.Option>)}</Select></label>
        <label className="dm-admin-field"><span>Код ошибки</span><TextInput value={draft.error_code} onUpdate={(error_code) => setDraft((value) => ({ ...value, error_code }))} placeholder="MAP-500" /></label>
        <label className="dm-admin-checkbox"><input type="checkbox" checked={draft.stalled} onChange={(event) => setDraft((value) => ({ ...value, stalled: event.target.checked }))} /> Только зависшие</label>
        <div className="dm-admin-filterbar__actions"><Button view="outlined" onClick={() => { setDraft({ status: "", task_type: "", user_query: "", error_code: "", stalled: false, page: 1 }); updateUrl({ status: "", task_type: "", user_query: "", error_code: "", stalled: false, page: 1 }); }}>Сбросить</Button><Button view="action" onClick={applyFilters}>Применить</Button></div>
      </section>
      {error && <div className="dm-admin-control__error" role="alert">{error}</div>}
      {loading && !result.items.length ? <div className="dm-admin-control__loading"><Loader size="m" /> <span>Загрузка задач…</span></div> : null}
      <div className="dm-admin-tasks__desktop"><table><thead><tr><th>Задача</th><th>Пользователь</th><th>Статус</th><th>Прогресс</th><th>Результат</th><th>Ошибка</th><th></th></tr></thead><tbody>{result.items.map((task) => <tr key={`${task.type}-${task.id}`} className={task.stalled ? "dm-admin-task--stalled" : ""}>
        <td><strong>{taskTypeLabel(task.type)}</strong><span className="dm-admin-task-id">{task.id}</span></td><td className="dm-admin-email">{task.user_email}</td><td><Label theme={task.stalled ? "danger" : statusTheme(task.status)}>{task.stalled ? "Зависла" : statusLabel(task.status)}</Label></td><td className="dm-tabular">{fmtInt(task.progress)}%</td><td>{fmtInt(task.found_count)} / {fmtInt(task.requested_count)}</td><td>{task.error_code && <Label theme="danger">{task.error_code}</Label>}</td><td><Button size="s" view="outlined" onClick={() => openDetail(task)}>Подробнее</Button></td>
      </tr>)}</tbody></table></div>
      <div className="dm-admin-tasks__mobile">{result.items.map((task) => <article key={`${task.type}-${task.id}`} className={`dm-admin-task-card${task.stalled ? " dm-admin-task--stalled" : ""}`}><div><strong>{taskTypeLabel(task.type)}</strong><Label theme={task.stalled ? "danger" : statusTheme(task.status)}>{task.stalled ? "Зависла" : statusLabel(task.status)}</Label></div><span className="dm-admin-email">{task.user_email}</span><span>{fmtInt(task.progress)}% · {fmtInt(task.found_count)} / {fmtInt(task.requested_count)}</span>{task.error_code && <span>{task.error_code}</span>}<Button view="outlined" size="m" onClick={() => openDetail(task)}>Подробнее</Button></article>)}</div>
      {!loading && !result.items.length && <div className="dm-admin-empty">Задач по этим фильтрам нет.</div>}
      <footer className="dm-admin-pagination"><Button view="outlined" size="s" disabled={filters.page <= 1 || loading} onClick={() => updateUrl({ ...filters, page: filters.page - 1 })}>Назад</Button><span>{filters.page} / {totalPages}</span><Button view="outlined" size="s" disabled={filters.page >= totalPages || loading} onClick={() => updateUrl({ ...filters, page: filters.page + 1 })}>Вперёд</Button></footer>
      {selected && <TaskDetails detail={selected} onClose={() => setSelected(null)} onChanged={async (updated, action) => {
        await load();
        if (action === "retry") {
          setSelected(updated);
          return;
        }
        const latest = await api(`/api/admin/control/tasks/${selected.type}/${selected.id}`);
        setSelected(latest);
      }} />}
    </section>
  );
}
