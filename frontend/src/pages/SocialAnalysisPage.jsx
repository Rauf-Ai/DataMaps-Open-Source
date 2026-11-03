import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Checkbox, Label, Select, Text } from "@gravity-ui/uikit";
import { api, post, fmtDate, fmtInt } from "../api.js";
import { useAuth } from "../AuthContext.jsx";


export function SocialAnalysisPage() {
  const { me, loading, reload } = useAuth();
  const nav = useNavigate();
  const [saved, setSaved] = useState([]);
  const [resultId, setResultId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [selected, setSelected] = useState([]);
  const [task, setTask] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => { if (!loading && !me) nav("/login?next=/social"); }, [loading, me, nav]);
  useEffect(() => {
    if (!me) return;
    api("/api/live-lab/saved-results").then(setSaved).catch((e) => setError(e.message));
  }, [me]);
  const organizations = useMemo(() => (detail?.items || []).map((item) => item.business).filter((b) => b.social), [detail]);

  const loadResult = async (id) => {
    const value = Number(id);
    setResultId(value); setSelected([]); setTask(null); setError("");
    try { setDetail(await api(`/api/live-lab/saved-results/${value}`)); }
    catch (e) { setError(e.message); }
  };
  const toggleAll = () => setSelected(selected.length === organizations.length ? [] : organizations.map((b) => b.id));
  const start = async () => {
    if (!selected.length) return;
    setBusy(true); setError("");
    try {
      const created = await post("/api/social/tasks", { saved_result_id: resultId, business_ids: selected });
      setTask({ id: created.task_id, status: "queued", progress: 0 });
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };
  useEffect(() => {
    if (!task?.id || ["done", "failed"].includes(task.status)) return undefined;
    const timer = window.setInterval(async () => {
      try {
        const next = await api(`/api/social/tasks/${task.id}/status`);
        setTask(next);
        if (next.status === "done") reload();
      } catch (e) { setError(e.message); }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [task?.id, task?.status, reload]);

  if (!me) return null;
  const available = me.subscription?.quotas?.social?.available ?? 0;
  return (
    <main className="dm-container dm-social-page">
      <div className="dm-page-heading">
        <div><Text variant="header-1" as="h1">Анализ соцсетей</Text><Text color="secondary">Публичные профили из карточек организаций</Text></div>
        <Label theme="info">Доступно: {fmtInt(available)}</Label>
      </div>
      <section className="dm-account__section">
        <Text variant="subheader-2" as="h2">Выберите результат поиска</Text>
        <Select width="max" size="l" placeholder="Сохранённая выгрузка" value={resultId ? [String(resultId)] : []}
          onUpdate={(values) => values[0] && loadResult(values[0])}>
          {saved.map((row) => <Select.Option key={row.id} value={String(row.id)}>{row.title} · {row.row_count} · {fmtDate(row.created_at)}</Select.Option>)}
        </Select>
        {detail && !organizations.length && <Text color="secondary">В этом результате нет организаций с публичными ссылками на поддерживаемые соцсети.</Text>}
        {!!organizations.length && <>
          <div className="dm-social-actions">
            <Button view="outlined" onClick={toggleAll}>{selected.length === organizations.length ? "Снять выбор" : "Выбрать все"}</Button>
            <Text color="secondary">Выбрано {selected.length} из {organizations.length}</Text>
            <Button view="action" onClick={start} disabled={!selected.length || selected.length > available} loading={busy}>Запустить анализ</Button>
          </div>
          <div className="dm-social-orgs">
            {organizations.map((business) => <label key={business.id}>
              <Checkbox checked={selected.includes(business.id)} onUpdate={(checked) => setSelected((current) => checked ? [...current, business.id] : current.filter((id) => id !== business.id))} />
              <span><b>{business.name}</b><small>{business.city || business.region || ""} · {business.social}</small></span>
            </label>)}
          </div>
        </>}
      </section>
      {task && <section className={`dm-social-task is-${task.status}`}>
        <div><b>{task.status === "done" ? "Анализ готов" : task.status === "failed" ? "Анализ завершился с ошибкой" : task.message || "Анализ выполняется"}</b><span>{task.progress || 0}%</span></div>
        <div className="dm-quota-row__track"><i style={{ width: `${task.progress || 0}%` }} /></div>
        {task.error && <Text color="danger">{task.error}</Text>}
        {task.download_url && <Button view="action" href={task.download_url}>Скачать XLSX</Button>}
      </section>}
      {error && <div className="dm-alert dm-alert--error">{error}</div>}
    </main>
  );
}
