import { useEffect, useState } from "react";
import { Alert, Button, Card, Label, Spin, Table, Text } from "@gravity-ui/uikit";
import { api, fmtDate, fmtInt } from "../api.js";


function StatCard({ label, value, hint }) {
  return (
    <Card view="outlined" className="dm-live-stat">
      <Text variant="caption-2" color="secondary" as="div">{label}</Text>
      <div className="dm-live-stat__value">{fmtInt(value || 0)}</div>
      {hint && <Text variant="caption-2" color="secondary">{hint}</Text>}
    </Card>
  );
}


export function LiveLabPage() {
  const [summary, setSummary] = useState(null);
  const [results, setResults] = useState([]);
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const [s, r] = await Promise.all([
        api("/api/live-lab/summary"),
        api("/api/live-lab/saved-results"),
      ]);
      setSummary(s);
      setResults(r || []);
    } catch (e) {
      setError(e.message || "Не удалось загрузить Live Lab");
    } finally {
      setLoading(false);
    }
  };

  const openResult = async (id) => {
    setSelected(null);
    try {
      setSelected(await api(`/api/live-lab/saved-results/${id}`));
    } catch (e) {
      setError(e.message || "Не удалось открыть результат");
    }
  };

  useEffect(() => { load(); }, []);

  return (
    <main className="dm-container">
      <Card view="outlined" className="dm-card dm-live-lab-hero">
        <div className="dm-card-header">
          <div>
            <Text variant="caption-2" color="secondary">Экспериментальный раздел</Text>
            <Text variant="display-1" as="h1" style={{ margin: "4px 0 0" }}>Live Index Lab</Text>
            <Text color="secondary" as="div" style={{ marginTop: 8, maxWidth: 760 }}>
              Здесь видно, как DataMaps будет накапливать постоянную базу из live parsing.
              Основная страница и продакшен-сценарий пока не меняются.
            </Text>
          </div>
          <Button view="outlined" onClick={load} loading={loading}>Обновить</Button>
        </div>
        <Alert
          theme="info"
          title="Черновой режим"
          message="Физические XLSX-файлы всё ещё могут истекать, но состав результата уже сохраняется как список business_id. Следующий шаг — повторная генерация XLSX из сохранённого результата."
        />
      </Card>

      {error && <Alert theme="danger" message={error} />}

      {loading && !summary ? (
        <Card view="outlined" className="dm-card">
          <div className="dm-inline"><Spin size="s" /><Text color="secondary">Загружаем Live Index…</Text></div>
        </Card>
      ) : (
        <>
          <div className="dm-live-stats">
            <StatCard label="Организаций в Live Index" value={summary?.total_businesses} hint="Нормализованные карточки" />
            <StatCard label="Источник-ID" value={summary?.total_sources} hint="Yandex / Google / 2ГИС" />
            <StatCard label="Всего сохранённых результатов" value={summary?.total_saved_results} hint="Постоянная история" />
            <StatCard label="Моих результатов" value={summary?.user_saved_results} hint="Доступны в кабинете позже" />
            <StatCard label="Свежие записи" value={summary?.fresh_businesses} hint="freshness ≥ 80" />
            <StatCard label="Требуют проверки" value={summary?.stale_businesses} hint="freshness < 80" />
          </div>

          <Card view="outlined" className="dm-card">
            <Text variant="subheader-2" as="div" style={{ marginBottom: 12 }}>Мои сохранённые результаты</Text>
            {!results.length ? (
              <Text color="secondary">
                Пока нет сохранённых результатов. Запустите поиск на главной странице, после завершения результат появится здесь.
              </Text>
            ) : (
              <div className="dm-table-scroll">
                <Table
                  columns={[
                    { id: "title", name: "Название", template: (r) => <span className="dm-cell-wrap">{r.title}</span> },
                    { id: "row_count", name: "Орг.", align: "end", template: (r) => <span className="dm-tabular">{fmtInt(r.row_count)}</span> },
                    { id: "created_at", name: "Создан", template: (r) => fmtDate(r.created_at) },
                    { id: "parser_task_id", name: "Task", template: (r) => r.parser_task_id ? <Label>{r.parser_task_id.slice(0, 8)}</Label> : "—" },
                    { id: "actions", name: "", align: "end", template: (r) => <Button size="s" view="outlined" onClick={() => openResult(r.id)}>Открыть</Button> },
                  ]}
                  data={results}
                  verticalAlign="top"
                />
              </div>
            )}
          </Card>

          {selected && (
            <Card view="outlined" className="dm-card">
              <div className="dm-card-header">
                <div>
                  <Text variant="subheader-2" as="div">{selected.title}</Text>
                  <Text color="secondary" variant="caption-2">Показаны первые {selected.items.length} из {fmtInt(selected.row_count)}</Text>
                </div>
                <Label theme="success">persistent</Label>
              </div>
              <div className="dm-table-scroll">
                <Table
                  columns={[
                    { id: "position", name: "#", width: 60, template: (r) => <span className="dm-tabular">{r.position}</span> },
                    { id: "name", name: "Компания", template: (r) => <span className="dm-cell-wrap" style={{ fontWeight: 500 }}>{r.business.name}</span> },
                    { id: "category", name: "Категория", template: (r) => <span className="dm-cell-wrap">{r.business.category || "—"}</span> },
                    { id: "city", name: "Город", template: (r) => r.business.city || r.business.region || "—" },
                    { id: "phone", name: "Телефон", template: (r) => r.business.phone || "—" },
                    { id: "website", name: "Сайт", template: (r) => r.business.website || "—" },
                    { id: "fresh", name: "Свежесть", align: "end", template: (r) => <span className="dm-tabular">{r.business.freshness_score}</span> },
                  ]}
                  data={selected.items}
                  verticalAlign="top"
                />
              </div>
            </Card>
          )}
        </>
      )}
    </main>
  );
}
