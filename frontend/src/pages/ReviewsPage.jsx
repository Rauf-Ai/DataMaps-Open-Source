import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Alert, Button, Card, Checkbox, Label, SegmentedRadioGroup, Select, Spin, Table, Text, useToaster,
} from "@gravity-ui/uikit";
import { ArrowDownToLine } from "@gravity-ui/icons";
import { api, post, fmtInt, fmtDate } from "../api.js";
import { useAuth } from "../AuthContext.jsx";

const STAR_OPTIONS = [
  { value: "1", content: "1★" },
  { value: "2", content: "2★" },
  { value: "3", content: "3★" },
  { value: "4", content: "4★" },
  { value: "5", content: "5★" },
];

const MAX_ORGS_FAST = 50;
const MAX_ORGS_DEEP = 20;

export function ReviewsPage() {
  const nav = useNavigate();
  const { me, reload } = useAuth();
  const { add: toast } = useToaster();

  const [mode, setMode] = useState("download"); // 'download' | 'urls'
  const [downloads, setDownloads] = useState([]);
  const [selectedDownloadId, setSelectedDownloadId] = useState("");
  const [orgs, setOrgs] = useState([]);
  const [orgsLoading, setOrgsLoading] = useState(false);
  const [orgsError, setOrgsError] = useState("");
  const [selectedOrgs, setSelectedOrgs] = useState(new Set());
  const [urlsText, setUrlsText] = useState("");
  const [starMin, setStarMin] = useState("1");
  const [starMax, setStarMax] = useState("5");
  const [deep, setDeep] = useState(false);

  const [task, setTask] = useState(null);
  const [creating, setCreating] = useState(false);
  const [taskError, setTaskError] = useState("");
  const pollRef = useRef(null);

  const [history, setHistory] = useState([]);

  const availableReviews = me?.subscription?.quotas?.reviews?.available ?? 0;
  const loggedIn = !!me;

  useEffect(() => {
    if (!loggedIn) return;
    api("/api/me/downloads").then(setDownloads).catch(() => setDownloads([]));
    api("/api/reviews/tasks").then(setHistory).catch(() => setHistory([]));
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [loggedIn]);

  useEffect(() => {
    if (!selectedDownloadId) { setOrgs([]); setSelectedOrgs(new Set()); return; }
    setOrgsLoading(true); setOrgsError("");
    api(`/api/me/downloads/${selectedDownloadId}/organizations`)
      .then((d) => { setOrgs(d); setSelectedOrgs(new Set()); })
      .catch((e) => { setOrgs([]); setOrgsError(e.message); })
      .finally(() => setOrgsLoading(false));
  }, [selectedDownloadId]);

  const validDownloads = useMemo(
    () => downloads.filter((d) => d.available !== false),
    [downloads],
  );

  const toggleOrg = (id) => {
    setSelectedOrgs((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const selectedList = useMemo(
    () => orgs.filter((o) => selectedOrgs.has(o.id)),
    [orgs, selectedOrgs],
  );

  // Парсинг URL'ов: каждая строка ИЛИ через запятую/пробел = одна ссылка.
  const URL_RE = /https?:\/\/(?:[\w.-]*\.)?yandex\.[a-z]{2,3}\/maps\/(?:[^\s,]*\/)?(\d{6,})/g;
  const parsedUrls = useMemo(() => {
    const text = urlsText || "";
    const seen = new Set();
    const valid = [];
    let m;
    while ((m = URL_RE.exec(text)) !== null) {
      const id = m[1];
      if (!seen.has(id)) { seen.add(id); valid.push({ id, url: m[0] }); }
    }
    // Также — попытка распознать просто numeric IDs если кто-то их вставил без URL
    if (!valid.length) {
      const idRe = /\b(\d{8,})\b/g;
      while ((m = idRe.exec(text)) !== null) {
        const id = m[1];
        if (!seen.has(id)) { seen.add(id); valid.push({ id, url: `https://yandex.ru/maps/org/${id}/` }); }
      }
    }
    return valid;
  }, [urlsText]);

  const maxOrgs = deep ? MAX_ORGS_DEEP : MAX_ORGS_FAST;

  // Текущий «выбор» зависит от режима
  const activeCount = mode === "urls" ? parsedUrls.length : selectedList.length;

  const overLimit = activeCount > maxOrgs;
  const canSubmit = loggedIn && activeCount > 0 && !overLimit
                    && availableReviews >= activeCount && !creating && !task;

  const doAnalyze = async () => {
    setTaskError("");
    if (parseInt(starMin) > parseInt(starMax)) {
      setTaskError("Минимум звёзд не может быть больше максимума");
      return;
    }
    setCreating(true);
    try {
      const body = {
        stars: { min: parseInt(starMin), max: parseInt(starMax) },
        deep,
      };
      if (mode === "urls") {
        body.urls = parsedUrls.map((p) => p.url);
      } else {
        body.download_id = parseInt(selectedDownloadId);
        body.org_ids = Array.from(selectedOrgs);
      }
      const r = await post("/api/reviews/tasks", body);
      if (r.invalid_urls && r.invalid_urls.length) {
        toast({ name: "rv-invalid", theme: "warning", title: "Часть ссылок отброшена",
                content: `Не распознаны: ${r.invalid_urls.join(", ").slice(0, 200)}` });
      }
      setTask({ id: r.task_id, status: "queued", progress: 0 });
      pollRef.current = setInterval(() => pollTask(r.task_id), 2000);
    } catch (e) {
      setTaskError(e.message);
    } finally {
      setCreating(false);
    }
  };

  const pollTask = async (taskId) => {
    try {
      const s = await api(`/api/reviews/tasks/${taskId}/status`);
      setTask({ id: taskId, ...s });
      if (s.status === "done" || s.status === "failed") {
        clearInterval(pollRef.current);
        pollRef.current = null;
        await reload();
        api("/api/reviews/tasks").then(setHistory).catch(() => {});
        if (s.status === "failed") {
          toast({ name: "rv-failed", title: "Анализ не выполнен", content: s.error || "Ошибка", theme: "danger" });
        } else {
          toast({ name: "rv-done", title: "Готово", content: `Учтено в лимите: ${s.credits_charged} организаций`, theme: "success" });
          // Авто-скачивание XLSX
          if (s.download_url) {
            const a = document.createElement("a");
            a.href = s.download_url;
            a.rel = "noopener";
            document.body.appendChild(a);
            a.click();
            a.remove();
          }
        }
      }
    } catch (e) {
      clearInterval(pollRef.current);
      pollRef.current = null;
      setTaskError(e.message);
    }
  };

  if (!loggedIn) {
    return (
      <main className="dm-container">
        <Card view="outlined" theme="normal" className="dm-card">
          <Text variant="subheader-2" as="div" style={{ marginBottom: 8 }}>Анализ отзывов</Text>
          <Text color="secondary" as="div" style={{ marginBottom: 16 }}>
            Войдите, чтобы выбрать организации из своих выгрузок и получить сводку по отзывам.
          </Text>
          <Button view="action" size="m" onClick={() => nav("/login?next=/reviews")}>Войти</Button>
        </Card>
      </main>
    );
  }

  return (
    <main className="dm-container">
      <Card view="outlined" theme="normal" className="dm-card">
        <Text variant="caption-2" color="secondary" className="dm-section-caption">Шаг 1</Text>
        <Text variant="subheader-2" as="div" style={{ marginBottom: 8 }}>Выберите источник организаций</Text>

        <div style={{ marginBottom: 16 }}>
          <SegmentedRadioGroup
            size="m"
            value={mode}
            onUpdate={(v) => { setMode(v); setTaskError(""); }}
            options={[
              { value: "download", content: "Из моей выгрузки" },
              { value: "urls", content: "По ссылкам Яндекс Карт" },
            ]}
          />
        </div>

        {mode === "download" && (
          !validDownloads.length ? (
            <Alert theme="info" message="У вас пока нет выгрузок. Сначала найдите организации и нажмите «Выгрузить» на главной — или используйте режим «По ссылкам»." />
          ) : (
            <Select size="m" value={selectedDownloadId ? [selectedDownloadId] : []}
                    onUpdate={(v) => setSelectedDownloadId(v[0] || "")}
                    placeholder="— выберите выгрузку —" width="max">
              {validDownloads.map((d) => (
                <Select.Option key={d.id} value={String(d.id)}>
                  {fmtDate(d.created_at)} · {fmtInt(d.row_count)} орг.
                </Select.Option>
              ))}
            </Select>
          )
        )}

        {mode === "urls" && (
          <div>
            <Text color="secondary" as="div" style={{ marginBottom: 8 }}>
              Вставьте ссылки на организации Яндекс Карт — по одной на строку. Можно из вкладок браузера / адресной строки.
              Поддерживается формат <code>https://yandex.ru/maps/org/&lt;slug&gt;/&lt;id&gt;/</code>.
            </Text>
            <textarea
              className="dm-ai-hero__input"
              style={{ minHeight: 120, width: "100%" }}
              value={urlsText}
              onChange={(e) => setUrlsText(e.target.value)}
              placeholder={"https://yandex.ru/maps/org/khendai_avtomir/1124715036/\nhttps://yandex.ru/maps/org/.../987654321/"}
              maxLength={20000}
              disabled={creating || !!task}
            />
            <div className="dm-row" style={{ justifyContent: "space-between", marginTop: 8, fontSize: 13 }}>
              <Text color={parsedUrls.length ? "positive" : "secondary"}>
                Распознано ссылок: <b>{parsedUrls.length}</b>
                {parsedUrls.length > 0 && parsedUrls.length <= maxOrgs && " ✓"}
              </Text>
              {parsedUrls.length > maxOrgs && (
                <Text color="danger">Слишком много: лимит {maxOrgs} за раз</Text>
              )}
            </div>
          </div>
        )}
      </Card>

      {/* Sticky панель действий */}
      {((mode === "download" && selectedDownloadId) || (mode === "urls" && parsedUrls.length > 0)) && (
        <Card view="outlined" theme="normal" className="dm-card dm-reviews-sticky">
          <Text variant="caption-2" color="secondary" className="dm-section-caption">Параметры и запуск</Text>

          <div className="dm-row" style={{ flexWrap: "wrap", gap: 16, alignItems: "flex-end" }}>
            <div className="dm-field" style={{ minWidth: 130 }}>
              <span className="dm-field__hint">Звёзды от</span>
              <Select size="m" value={[starMin]} onUpdate={(v) => setStarMin(v[0])}>
                {STAR_OPTIONS.map((o) => <Select.Option key={o.value} value={o.value}>{o.content}</Select.Option>)}
              </Select>
            </div>
            <div className="dm-field" style={{ minWidth: 130 }}>
              <span className="dm-field__hint">Звёзды до</span>
              <Select size="m" value={[starMax]} onUpdate={(v) => setStarMax(v[0])}>
                {STAR_OPTIONS.map((o) => <Select.Option key={o.value} value={o.value}>{o.content}</Select.Option>)}
              </Select>
            </div>
            <div className="dm-stack" style={{ flex: 1, minWidth: 220, fontSize: 14 }}>
              <div>Выбрано: <b>{activeCount}</b> · будет использовано <b>{activeCount}</b> организаций из месячной квоты</div>
              <div style={{ color: "var(--g-color-text-secondary)" }}>Доступно по тарифу: {fmtInt(availableReviews)}</div>
            </div>
            <Button view="action" size="l" onClick={doAnalyze} disabled={!canSubmit} loading={creating}>
              Анализировать ({activeCount})
            </Button>
          </div>

          <div style={{ marginTop: 12, padding: 12, borderRadius: 8,
                        background: deep ? "var(--g-color-base-warning-light)" : "transparent",
                        border: deep ? "1px solid var(--g-color-line-warning)" : "1px solid transparent" }}>
            <Checkbox size="m" checked={deep} onUpdate={setDeep} disabled={creating || !!task}>
              Глубокий парсинг (до 200 отзывов на организацию)
            </Checkbox>
            <Text variant="caption-2" color="secondary" as="div" style={{ marginTop: 4, marginLeft: 26 }}>
              {deep
                ? `~60-120 сек/орг через headless-браузер, до 200 отзывов. Лимит: ${MAX_ORGS_DEEP} орг. за раз.`
                : `Быстро (~5 сек/орг), до 50 отзывов. Лимит: ${MAX_ORGS_FAST} орг. за раз.`}
            </Text>
          </div>

          {overLimit && (
            <div style={{ marginTop: 8 }}>
              <Alert theme="warning"
                     message={`В ${deep ? "глубоком" : "быстром"} режиме лимит ${maxOrgs} организаций за раз. Снимите выбор лишних или ${deep ? "выключите" : "переключитесь на"} глубокий парсинг.`} />
            </div>
          )}
          {!overLimit && availableReviews < activeCount && activeCount > 0 && (
            <div style={{ marginTop: 8 }}>
              <Alert theme="warning"
                     message={`Не хватает месячной квоты: выбрано ${activeCount}, доступно ${availableReviews}. Уменьшите выбор или смените тариф.`} />
            </div>
          )}

          {task && (
            <div style={{ marginTop: 12, padding: 12, background: "var(--g-color-base-misc-light)", borderRadius: 8 }}>
              <div className="dm-row" style={{ justifyContent: "space-between" }}>
                <Text>Статус: <b>{task.status}</b></Text>
                <Text className="dm-tabular">{task.progress || 0}%</Text>
              </div>
              <progress value={task.progress || 0} max="100" style={{ width: "100%", marginTop: 6 }} />
              {task.status === "done" && task.download_url && (
                <div style={{ marginTop: 8 }}>
                  <Button view="action" size="m" href={task.download_url} target="_blank">
                    <ArrowDownToLine /> Скачать XLSX
                  </Button>
                </div>
              )}
              {task.error && <Text color="danger" style={{ marginTop: 6 }}>{task.error}</Text>}
            </div>
          )}

          {taskError && <Text color="danger" style={{ marginTop: 8 }}>{taskError}</Text>}
        </Card>
      )}

      {mode === "download" && selectedDownloadId && (
        <Card view="outlined" theme="normal" className="dm-card">
          <div className="dm-row" style={{ justifyContent: "space-between", marginBottom: 12, flexWrap: "wrap", gap: 8 }}>
            <div>
              <Text variant="caption-2" color="secondary" className="dm-section-caption">Шаг 2</Text>
              <Text variant="subheader-2" as="div">Отметьте организации</Text>
            </div>
            {orgs.length > 0 && (
              <div className="dm-inline">
                <Button view="flat" size="s" onClick={() => setSelectedOrgs(new Set(orgs.map((o) => o.id)))}
                        disabled={selectedOrgs.size === orgs.length}>
                  Выбрать все ({orgs.length})
                </Button>
                <Button view="flat" size="s" onClick={() => setSelectedOrgs(new Set())}
                        disabled={selectedOrgs.size === 0}>
                  Снять все
                </Button>
              </div>
            )}
          </div>

          {orgsError && <Alert theme="danger" message={orgsError} />}
          {orgsLoading ? (
            <div className="dm-inline"><Spin size="s" /> <Text color="secondary">Загрузка…</Text></div>
          ) : orgs.length === 0 && !orgsError ? (
            <Text color="secondary">В выбранной выгрузке нет данных.</Text>
          ) : (
            <div className="dm-table-scroll">
              <Table
                columns={[
                  {
                    id: "sel", name: "", width: 40,
                    template: (item) => (
                      <span onClick={(e) => e.stopPropagation()} style={{ display: "inline-flex" }}>
                        <Checkbox size="m" checked={selectedOrgs.has(item.id)} onUpdate={() => toggleOrg(item.id)} />
                      </span>
                    ),
                  },
                  {
                    id: "name", name: "Название", width: 220,
                    template: (i) => <span className="dm-cell-wrap" style={{ fontWeight: 500 }}>{i.name}</span>,
                  },
                  { id: "city", name: "Город", width: 140, template: (i) => <span className="dm-cell-wrap">{i.city}</span> },
                  {
                    id: "address", name: "Адрес", width: 240,
                    template: (i) => <span className="dm-cell-wrap" style={{ color: "var(--g-color-text-secondary)" }}>{i.address}</span>,
                  },
                  { id: "rating", name: "Рейтинг", align: "end", width: 80, template: (i) => <span className="dm-tabular">{i.rating ? i.rating.toFixed(1) : "—"}</span> },
                  { id: "reviews_count", name: "Отзывов", align: "end", width: 90, template: (i) => <span className="dm-tabular">{fmtInt(i.reviews_count)}</span> },
                ]}
                data={orgs}
                verticalAlign="top"
                onRowClick={(item) => toggleOrg(item.id)}
              />
            </div>
          )}

          <Alert style={{ marginTop: 16 }} theme="info"
                 message="Парсинг отзывов занимает 30-90 сек на организацию. Капча Яндекса может прервать процесс — в этом случае квота не списывается." />
        </Card>
      )}

      {history.length > 0 && (
        <Card view="outlined" theme="normal" className="dm-card">
          <Text variant="caption-2" color="secondary" className="dm-section-caption">История</Text>
          <Text variant="subheader-2" as="div" style={{ marginBottom: 8 }}>Прошлые анализы</Text>
          <div className="dm-table-scroll">
            <Table
              columns={[
                { id: "created_at", name: "Дата", template: (i) => fmtDate(i.created_at) },
                { id: "org_count", name: "Орг.", align: "end", template: (i) => <span className="dm-tabular">{fmtInt(i.org_count)}</span> },
                { id: "stars", name: "Звёзды", template: (i) => `${i.star_min}-${i.star_max}` },
                { id: "mode", name: "Режим", template: (i) => (
                    <Label theme={i.deep ? "warning" : "normal"} size="s">{i.deep ? "глубокий" : "быстрый"}</Label>
                  )
                },
                { id: "credits_charged", name: "Учтено, орг.", align: "end", template: (i) => <span className="dm-tabular">{fmtInt(i.credits_charged)}</span> },
                { id: "status", name: "Статус", template: (i) => (
                    <Label theme={i.status === "done" ? "success" : i.status === "failed" ? "danger" : "info"}>
                      {i.status}
                    </Label>
                  )
                },
                { id: "actions", name: "", align: "end", template: (i) =>
                    i.download_url
                      ? <Button view="outlined" size="s" href={i.download_url}><ArrowDownToLine /> скачать</Button>
                      : (i.error ? <Text color="danger" variant="caption-2">{i.error.slice(0, 80)}</Text> : null)
                },
              ]}
              data={history}
              verticalAlign="middle"
            />
          </div>
        </Card>
      )}
    </main>
  );
}
