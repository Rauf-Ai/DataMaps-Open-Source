import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Alert, Button, Card, Label, SegmentedRadioGroup, Select, Text, TextInput, useToaster,
} from "@gravity-ui/uikit";
import { ArrowDownToLine, Xmark } from "@gravity-ui/icons";
import noUiSlider from "nouislider";
import "nouislider/dist/nouislider.css";
import { api, post, fmtInt, fmtDate } from "../api.js";
import { useAuth } from "../AuthContext.jsx";

const PRICE_PER_ORG = 0.5;
const PRICE_ENRICH = 3;
const PRICE_ZIP = 20;

const SOURCE_OPTIONS = [
  { value: "yandex", content: "Яндекс Карты" },
  { value: "google", content: "Google Maps" },
];

const TRI_OPTIONS = [
  { value: "any", content: "Любой" },
  { value: "yes", content: "Есть" },
  { value: "no", content: "Нет" },
];

const SOCIAL_NETS = [
  { key: "instagram", label: "Instagram" },
  { key: "facebook", label: "Facebook" },
  { key: "telegram", label: "Telegram" },
  { key: "whatsapp", label: "WhatsApp" },
  { key: "vk", label: "ВКонтакте" },
  { key: "max", label: "Max" },
  { key: "twitter", label: "Twitter/X" },
  { key: "youtube", label: "YouTube" },
  { key: "tiktok", label: "TikTok" },
  { key: "linkedin", label: "LinkedIn" },
];

const QUERY_PHRASES = ["coffee shop", "McDonalds", "Маркетинговое агентство", "парикмахерская"];
const LOC_PHRASES = ["USA", "Москва", "Республика Татарстан"];

// Эффект печатания/стирания для плейсхолдеров
function useTypewriter(phrases, active) {
  const [text, setText] = useState("");
  useEffect(() => {
    if (!active) return undefined;
    let i = 0, ci = 0, deleting = false, timer;
    const tick = () => {
      const word = phrases[i % phrases.length];
      if (!deleting) {
        ci += 1;
        setText(word.slice(0, ci));
        if (ci >= word.length) { deleting = true; timer = setTimeout(tick, 1500); return; }
        timer = setTimeout(tick, 95);
      } else {
        ci -= 1;
        setText(word.slice(0, Math.max(0, ci)));
        if (ci <= 0) { deleting = false; i += 1; timer = setTimeout(tick, 350); return; }
        timer = setTimeout(tick, 45);
      }
    };
    timer = setTimeout(tick, 400);
    return () => clearTimeout(timer);
  }, [active, phrases]);
  return text;
}

function autoDownload(url) {
  const a = document.createElement("a");
  a.href = url;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function LiveRowsTable({ rows }) {
  if (!rows.length) return null;
  return (
    <Card view="outlined" theme="normal" className="dm-card">
      <Text variant="caption-2" color="secondary" className="dm-section-caption">Найденные организации</Text>
      <Text variant="subheader-2" as="div" style={{ marginBottom: 8 }}>
        Результаты появляются по мере сбора: {fmtInt(rows.length)}
      </Text>
      <div className="dm-table-scroll">
        <table className="dm-preview-table">
          <thead>
            <tr><th>Название</th><th>Категория</th><th>Город</th><th>Адрес</th><th>Телефон</th><th>Сайт</th><th>Соцсети</th><th>Рейтинг</th><th>Отзывы</th><th>Источник</th></tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const website = row.website || row.site;
              const sourceUrl = row.source_url || row.map_url;
              return (
                <tr key={row.position}>
                  <td>{row.name || "—"}</td><td>{row.category || "—"}</td><td>{row.city || row.region || "—"}</td><td>{row.address || "—"}</td>
                  <td>{row.phone || "—"}</td>
                  <td>{website ? <a href={/^https?:\/\//i.test(website) ? website : `https://${website}`} target="_blank" rel="noreferrer">Открыть</a> : "—"}</td>
                  <td>{row.social || "—"}</td><td className="dm-tabular">{row.rating ?? "—"}</td><td className="dm-tabular">{fmtInt(row.reviews_count ?? row.reviews)}</td>
                  <td>{sourceUrl ? <a href={sourceUrl} target="_blank" rel="noreferrer">{row.source || "Открыть"}</a> : row.source || "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

// ── Карточка обогащённой организации ──────────────────────────────────────
function EnrichResult({ data }) {
  if (!data) return null;
  const services = (data.services || "").split(", ").filter(Boolean);
  const features = (data.features || "").split(", ").filter(Boolean);
  const socials = (data.social && data.social !== "—" ? data.social.split(", ") : []).filter(Boolean);
  return (
    <div className="dm-enrich">
      <Text variant="subheader-2" as="div" style={{ marginBottom: 4 }}>
        {data.name && data.name !== "—" ? data.name : "Организация"}
      </Text>
      {data.address && data.address !== "—" && (
        <Text color="secondary" as="div" style={{ marginBottom: 12 }}>{data.address}</Text>
      )}
      <div className="dm-enrich__grid">
        {data.phone && data.phone !== "—" && <div><b>Телефон:</b> {data.phone}</div>}
        {data.site && data.site !== "—" && (
          <div><b>Сайт:</b> <a href={`https://${data.site}`} target="_blank" rel="noopener noreferrer">{data.site}</a></div>
        )}
        {data.rating && data.rating !== "—" && (
          <div><b>Рейтинг:</b> {data.rating} ({fmtInt(data.reviews)} отзывов)</div>
        )}
        {data.category && data.category !== "—" && <div><b>Категория:</b> {data.category}</div>}
        {data.price_range && <div><b>Цены:</b> {data.price_range}</div>}
      </div>

      {socials.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <Text variant="caption-2" color="secondary" as="div">Соцсети</Text>
          <div className="dm-badges">
            {socials.map((s) => (
              <a key={s} className="dm-badge" href={`https://${s}`} target="_blank" rel="noopener noreferrer">{s}</a>
            ))}
          </div>
        </div>
      )}
      {services.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <Text variant="caption-2" color="secondary" as="div">Товары и услуги ({services.length})</Text>
          <ul className="dm-services">
            {services.map((s, i) => <li key={i}>{s}</li>)}
          </ul>
        </div>
      )}
      {features.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <Text variant="caption-2" color="secondary" as="div">Особенности</Text>
          <div className="dm-badges">
            {features.map((f, i) => <span key={i} className="dm-badge dm-badge--muted">{f}</span>)}
          </div>
        </div>
      )}
    </div>
  );
}

export function ParserPage() {
  const nav = useNavigate();
  const { me, reload } = useAuth();
  const { add: toast } = useToaster();

  const [tab, setTab] = useState("search"); // 'search' | 'enrich'

  // Живой поиск — строки поиска (query + location)
  const [rows, setRows] = useState([{ query: "", location: "" }]);
  const [source, setSource] = useState("yandex");
  const [maxN, setMaxN] = useState("50");
  // Общие фильтры
  const [fPhone, setFPhone] = useState("any");
  const [fSite, setFSite] = useState("any");
  const [fFranchise, setFFranchise] = useState("any");
  const [socialNets, setSocialNets] = useState([]);
  const [reviewsMin, setReviewsMin] = useState("");
  const [reviewsMax, setReviewsMax] = useState("");
  const [rating, setRating] = useState([0, 5]);
  const sliderInstance = useRef(null);

  const setRow = (i, patch) => setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const addRow = () => setRows((rs) => [...rs, { query: "", location: "" }]);
  const removeRow = (i) => setRows((rs) => (rs.length > 1 ? rs.filter((_, j) => j !== i) : rs));
  const toggleNet = (key) =>
    setSocialNets((ns) => (ns.includes(key) ? ns.filter((x) => x !== key) : [...ns, key]));

  // Обогащение / лендинг
  const [url, setUrl] = useState("");
  const [enrichResult, setEnrichResult] = useState(null);

  const [task, setTask] = useState(null);
  const [creating, setCreating] = useState(false);
  const [taskError, setTaskError] = useState("");
  const [taskMode, setTaskMode] = useState(null); // 'preview' | 'export' | 'enrich' | 'zip'
  const [previewRows, setPreviewRows] = useState(null); // образец для предпросмотра
  const [liveRows, setLiveRows] = useState([]);
  const searchPayload = useRef(null);
  const pollRef = useRef(null);
  const liveCursorRef = useRef(0);
  const liveTaskIdRef = useRef(null);

  const [history, setHistory] = useState([]);

  const balance = me?.credits_balance || 0;
  const loggedIn = !!me;

  useEffect(() => {
    if (!loggedIn) return;
    api("/api/parser/tasks").then(setHistory).catch(() => setHistory([]));
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [loggedIn]);

  // Ползунок рейтинга (как в основном разделе). Callback-ref — создаём точно
  // в момент монтирования элемента (он за вкладкой, обычный useEffect ловил null).
  const sliderCb = useCallback((el) => {
    if (el && !sliderInstance.current) {
      const inst = noUiSlider.create(el, {
        start: [0, 5], connect: true, step: 0.1, range: { min: 0, max: 5 }, behaviour: "tap-drag",
      });
      inst.on("update", (vals) => {
        const lo = +(+vals[0]).toFixed(1);
        const hi = +(+vals[1]).toFixed(1);
        setRating((r) => (r[0] === lo && r[1] === hi ? r : [lo, hi]));
      });
      sliderInstance.current = inst;
    } else if (!el && sliderInstance.current) {
      sliderInstance.current.destroy();
      sliderInstance.current = null;
    }
  }, []);

  const startPolling = (taskId) => {
    liveTaskIdRef.current = taskId;
    liveCursorRef.current = 0;
    setLiveRows([]);
    setTask({ id: taskId, status: "queued", progress: 0 });
    void pollTask(taskId);
    pollRef.current = setInterval(() => pollTask(taskId), 2000);
  };

  const readLiveResults = async (taskId, refresh = false) => {
    if (liveTaskIdRef.current !== taskId) return;
    if (refresh) {
      let after = 0;
      const rows = [];
      do {
        const page = await api(`/api/parser/tasks/${taskId}/results?after=${after}`);
        if (liveTaskIdRef.current !== taskId) return;
        const items = Array.isArray(page.items) ? page.items : [];
        rows.push(...items);
        const nextAfter = Number(page.next_after) || after;
        if (!items.length || nextAfter <= after || items.length < 200) {
          liveCursorRef.current = nextAfter;
          setLiveRows(rows);
          return;
        }
        after = nextAfter;
      } while (true);
    }

    const after = liveCursorRef.current;
    const page = await api(`/api/parser/tasks/${taskId}/results?after=${after}`);
    if (liveTaskIdRef.current !== taskId) return;
    const items = Array.isArray(page.items) ? page.items : [];
    const nextAfter = Number(page.next_after) || after;
    if (items.length && nextAfter > after) {
      liveCursorRef.current = nextAfter;
      setLiveRows((current) => {
        const byPosition = new Map(current.map((row) => [Number(row.position), row]));
        items.forEach((row) => byPosition.set(Number(row.position), row));
        return [...byPosition.values()].sort((a, b) => Number(a.position) - Number(b.position));
      });
    }
  };

  const pollTask = async (taskId) => {
    try {
      const s = await api(`/api/parser/tasks/${taskId}/status`);
      if (liveTaskIdRef.current !== taskId) return;
      setTask({ id: taskId, ...s });
      await readLiveResults(taskId, s.status === "done");
      if (s.status === "done" || s.status === "failed") {
        clearInterval(pollRef.current);
        pollRef.current = null;
        await reload();
        api("/api/parser/tasks").then(setHistory).catch(() => {});
        if (s.status === "failed") {
          toast({ name: "ps-failed", title: "Не выполнено", content: s.error || "Ошибка", theme: "danger" });
        } else if (taskMode === "preview") {
          setPreviewRows((s.result && s.result.rows) || []);
        } else {
          toast({ name: "ps-done", title: "Готово",
                  content: s.credits_charged ? `Списано ${s.credits_charged} кредитов` : "Готово", theme: "success" });
          if (taskMode === "enrich" && s.result) setEnrichResult(s.result);
          if (s.download_url) autoDownload(s.download_url);
          if (taskMode === "export") { setPreviewRows(null); searchPayload.current = null; }
        }
      }
    } catch (e) {
      clearInterval(pollRef.current);
      pollRef.current = null;
      setTaskError(e.message);
    }
  };

  const buildPayload = () => ({
    searches: rows.map((x) => ({ query: x.query.trim(), location: x.location.trim() })),
    source,
    max: parseInt(maxN) || 50,
    f_phone: fPhone, f_site: fSite, f_franchise: fFranchise,
    social_nets: socialNets,
    reviews_min: parseInt(reviewsMin) || 0,
    reviews_max: reviewsMax === "" ? null : parseInt(reviewsMax) || 0,
    rating_min: rating[0], rating_max: rating[1],
  });

  // Шаг 1 — бесплатный предпросмотр (образец 1–10 организаций)
  const doPreview = async () => {
    setTaskError(""); setEnrichResult(null); setPreviewRows(null); setLiveRows([]);
    const payload = buildPayload();
    searchPayload.current = payload;
    setCreating(true);
    try {
      const r = await post("/api/parser/preview", payload);
      setTaskMode("preview");
      startPolling(r.task_id);
    } catch (e) {
      setTaskError(e.message);
    } finally {
      setCreating(false);
    }
  };

  // Шаг 2 — полный сбор со списанием и скачиванием
  const doExport = async () => {
    if (!searchPayload.current) return;
    setTaskError(""); setLiveRows([]);
    setCreating(true);
    try {
      const r = await post("/api/parser/search", searchPayload.current);
      setTaskMode("export");
      startPolling(r.task_id);
    } catch (e) {
      setTaskError(e.message);
    } finally {
      setCreating(false);
    }
  };

  const doAction = async (endpoint) => {
    setTaskError(""); setEnrichResult(null);
    setCreating(true);
    try {
      const r = await post(endpoint, { url });
      setTaskMode(endpoint.includes("org-zip") ? "zip" : "enrich");
      startPolling(r.task_id);
    } catch (e) {
      setTaskError(e.message);
    } finally {
      setCreating(false);
    }
  };

  const busy = creating || (task && (task.status === "queued" || task.status === "running"));
  const maxNum = parseInt(maxN) || 0;
  const minStart = Math.max(1, Math.round(PRICE_PER_ORG * Math.min(maxNum || 1, 100)));
  const queryPh = useTypewriter(QUERY_PHRASES, !busy);
  const locPh = useTypewriter(LOC_PHRASES, !busy);
  const canSearch = rows.every((r) => r.query.trim() && r.location.trim());

  if (!loggedIn) {
    return (
      <main className="dm-container">
        <Card view="outlined" theme="normal" className="dm-card">
          <Text variant="subheader-2" as="div" style={{ marginBottom: 8 }}>Сбор данных с карт</Text>
          <Text color="secondary" as="div" style={{ marginBottom: 16 }}>
            Войдите, чтобы искать свежие организации на Яндекс Картах и Google Maps и собирать данные карточек.
          </Text>
          <Button view="action" size="m" onClick={() => nav("/login?next=/parser")}>Войти</Button>
        </Card>
      </main>
    );
  }

  return (
    <main className="dm-container">
      <Card view="outlined" theme="normal" className="dm-card">
        <Text variant="subheader-2" as="div" style={{ marginBottom: 8 }}>Сбор данных с карт</Text>
        <div style={{ marginBottom: 16 }}>
          <SegmentedRadioGroup
            size="m"
            value={tab}
            onUpdate={(v) => { setTab(v); setTaskError(""); setTask(null); setEnrichResult(null); setPreviewRows(null); setLiveRows([]); }}
            options={[
              { value: "search", content: "Живой поиск" },
              { value: "enrich", content: "Обогащение карточки" },
            ]}
          />
        </div>

        {tab === "search" && (
          <div className="dm-stack" style={{ gap: 16 }}>
            {/* Строки поиска */}
            <div className="dm-stack" style={{ gap: 8 }}>
              {rows.map((r, i) => (
                <div key={i} className="dm-row" style={{ gap: 10, alignItems: "flex-end", flexWrap: "wrap" }}>
                  <div className="dm-field" style={{ flex: 2, minWidth: 200 }}>
                    {i === 0 && <span className="dm-field__hint">Что искать</span>}
                    <TextInput size="m" value={r.query} onUpdate={(v) => setRow(i, { query: v })}
                               placeholder={queryPh} disabled={busy} />
                  </div>
                  <div className="dm-field" style={{ flex: 2, minWidth: 200 }}>
                    {i === 0 && <span className="dm-field__hint">Страна / Регион / Город *</span>}
                    <TextInput size="m" value={r.location} onUpdate={(v) => setRow(i, { location: v })}
                               placeholder={locPh} disabled={busy} />
                  </div>
                  <Button view="flat" size="l" disabled={busy || rows.length === 1}
                          onClick={() => removeRow(i)} title="Удалить строку"><Xmark /></Button>
                </div>
              ))}
              <div>
                <Button view="outlined" size="m" onClick={addRow} disabled={busy}
                        style={{ whiteSpace: "nowrap" }}>
                  + Добавить строку поиска
                </Button>
              </div>
            </div>

            <div className="dm-row" style={{ flexWrap: "wrap", gap: 12, alignItems: "flex-end" }}>
              <div className="dm-field" style={{ minWidth: 160 }}>
                <span className="dm-field__hint">Источник</span>
                <Select size="m" value={[source]} onUpdate={(v) => setSource(v[0])}>
                  {SOURCE_OPTIONS.map((o) => <Select.Option key={o.value} value={o.value}>{o.content}</Select.Option>)}
                </Select>
              </div>
              <div className="dm-field" style={{ minWidth: 150 }}>
                <span className="dm-field__hint">На строку (до 120)</span>
                <TextInput size="m" type="number" value={maxN} onUpdate={setMaxN} disabled={busy} />
              </div>
            </div>

            {/* Общие фильтры */}
            <div className="dm-card" style={{ padding: 14, background: "var(--g-color-base-generic)", borderRadius: 10 }}>
              <Text variant="caption-2" color="secondary" as="div" style={{ marginBottom: 10 }}>Общие фильтры (для всех строк)</Text>
              <div className="dm-row" style={{ gap: 24, flexWrap: "wrap", alignItems: "flex-start" }}>
                <div className="dm-stack" style={{ gap: 8, minWidth: 240 }}>
                  {[["Телефон", fPhone, setFPhone], ["Сайт", fSite, setFSite],
                    ["Франшизы", fFranchise, setFFranchise]].map(([lbl, val, set]) => (
                    <div key={lbl} className="dm-tri-row">
                      <span className="dm-tri-row__label">{lbl}</span>
                      <SegmentedRadioGroup size="s" options={TRI_OPTIONS} value={val}
                                           onUpdate={set} disabled={busy} />
                    </div>
                  ))}
                  <div className="dm-tri-row" style={{ alignItems: "center" }}>
                    <span className="dm-tri-row__label">Рейтинг</span>
                    <Text variant="caption-2" color="secondary" className="dm-tabular">
                      {rating[0].toFixed(1)} — {rating[1].toFixed(1)}
                    </Text>
                  </div>
                  <div ref={sliderCb} style={{ margin: "2px 6px 10px" }}></div>
                  <div className="dm-tri-row" style={{ alignItems: "center" }}>
                    <span className="dm-tri-row__label">Отзывов</span>
                    <div className="dm-row" style={{ gap: 8 }}>
                      <TextInput size="s" type="number" value={reviewsMin} onUpdate={setReviewsMin} placeholder="от" disabled={busy} />
                      <TextInput size="s" type="number" value={reviewsMax} onUpdate={setReviewsMax} placeholder="до" disabled={busy} />
                    </div>
                  </div>
                </div>

                <div style={{ minWidth: 240 }}>
                  <Text variant="caption-2" color="secondary" as="div" style={{ marginBottom: 6 }}>
                    Обязательные соцсети (профиль есть у организации)
                  </Text>
                  <div className="dm-badges">
                    {SOCIAL_NETS.map((n) => (
                      <button key={n.key} type="button" disabled={busy}
                              className={`dm-badge dm-badge--toggle${socialNets.includes(n.key) ? " dm-badge--on" : ""}`}
                              onClick={() => toggleNet(n.key)}>{n.label}</button>
                    ))}
                  </div>
                </div>
              </div>
            </div>

            <div className="dm-row" style={{ justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
              <Text color="secondary">
                {PRICE_PER_ORG} кр. за организацию · спишется только за собранные · Баланс: {fmtInt(balance)} кр.
              </Text>
              <Button view="action" size="l" onClick={doPreview}
                      disabled={busy || !canSearch || balance < minStart} loading={creating}>
                Предпросмотр
              </Button>
            </div>
            <Alert theme="info"
                   message="Сначала бесплатный предпросмотр (несколько организаций) — увидите, что за данные. Затем «Списать и скачать» соберёт всё и спишет кредиты только за фактически собранные. На один запрос источник отдаёт до ~120 организаций — чтобы больше, добавляйте строки поиска. Яндекс Карты лучше ищут по России/СНГ, для мира — Google Maps." />

            {/* Результат предпросмотра + подтверждение сбора */}
            {previewRows && (
              <Card view="outlined" theme="normal" style={{ padding: 14 }}>
                {previewRows.length === 0 ? (
                  <Text color="secondary">Предпросмотр не нашёл организаций. Ослабьте фильтры или проверьте запрос/локацию.</Text>
                ) : (
                  <>
                    <Text variant="subheader-2" as="div" style={{ marginBottom: 8 }}>
                      Образец ({previewRows.length}) — так будут выглядеть данные
                    </Text>
                    <div className="dm-table-scroll">
                      <table className="dm-preview-table">
                        <thead><tr><th>Название</th><th>Телефон</th><th>Сайт</th><th>Рейтинг</th><th>Адрес</th></tr></thead>
                        <tbody>
                          {previewRows.map((r, i) => (
                            <tr key={i}>
                              <td>{r.name}</td>
                              <td>{r.phone}</td>
                              <td>{r.has_site === "Да" ? r.site : "—"}</td>
                              <td className="dm-tabular">{r.rating}</td>
                              <td>{r.address}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <div className="dm-row" style={{ justifyContent: "flex-end", marginTop: 12, gap: 8 }}>
                      <Button view="flat" size="l" onClick={() => setPreviewRows(null)} disabled={busy}>Изменить</Button>
                      <Button view="action" size="l" onClick={doExport} disabled={busy} loading={creating}>
                        Списать и скачать (~{PRICE_PER_ORG} кр./орг.)
                      </Button>
                    </div>
                  </>
                )}
              </Card>
            )}
          </div>
        )}

        {tab === "enrich" && (
          <div className="dm-stack" style={{ gap: 16 }}>
            <div className="dm-field">
              <span className="dm-field__hint">Ссылка на организацию Яндекс Карт</span>
              <TextInput size="m" value={url} onUpdate={setUrl}
                         placeholder="https://yandex.ru/maps/org/.../123456789/" disabled={busy} />
            </div>
            <div className="dm-row" style={{ gap: 12, flexWrap: "wrap" }}>
              <Button view="action" size="l" onClick={() => doAction("/api/parser/enrich")}
                      disabled={busy || !url.trim() || balance < PRICE_ENRICH} loading={creating}>
                Обогатить ({PRICE_ENRICH} кр.)
              </Button>
              <Button view="outlined" size="l" onClick={() => doAction("/api/parser/org-zip")}
                      disabled={busy || !url.trim() || balance < PRICE_ZIP} loading={creating}>
                Собрать лендинг ZIP ({PRICE_ZIP} кр.)
              </Button>
              <Text color="secondary" style={{ alignSelf: "center" }}>Баланс: {fmtInt(balance)} кр.</Text>
            </div>
            <Alert theme="info"
                   message="«Обогатить» — догружает услуги, цены, фото, соцсети и особенности карточки. «Лендинг ZIP» — собирает отзывы, фото и акции в архив с готовым промтом для генерации лендинга." />
          </div>
        )}

        {task && (
          <div style={{ marginTop: 16, padding: 12, background: "var(--g-color-base-misc-light)", borderRadius: 8 }}>
            <div className="dm-row" style={{ justifyContent: "space-between" }}>
              <Text>Статус: <b>{task.status}</b></Text>
              <Text className="dm-tabular">{task.progress || 0}%</Text>
            </div>
            {task.message && <Text color="secondary" variant="caption-2" as="div" style={{ marginTop: 4 }}>{task.message}</Text>}
            <progress value={task.progress || 0} max="100" style={{ width: "100%", marginTop: 6 }} />
            {task.status === "done" && task.download_url && (
              <div style={{ marginTop: 8 }}>
                <Button view="action" size="m" href={task.download_url} target="_blank">
                  <ArrowDownToLine /> Скачать
                </Button>
              </div>
            )}
            {task.error && <Text color="danger" style={{ marginTop: 6 }}>{task.error}</Text>}
          </div>
        )}
        {taskError && <Text color="danger" style={{ marginTop: 8 }}>{taskError}</Text>}
      </Card>

      {enrichResult && (
        <Card view="outlined" theme="normal" className="dm-card">
          <Text variant="caption-2" color="secondary" className="dm-section-caption">Результат обогащения</Text>
          <EnrichResult data={enrichResult} />
        </Card>
      )}

      <LiveRowsTable rows={liveRows} />

      {history.length > 0 && (
        <Card view="outlined" theme="normal" className="dm-card">
          <Text variant="caption-2" color="secondary" className="dm-section-caption">История</Text>
          <Text variant="subheader-2" as="div" style={{ marginBottom: 8 }}>Прошлые задачи</Text>
          <div className="dm-stack" style={{ gap: 8 }}>
            {history.map((h) => (
              <div key={h.id} className="dm-row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8,
                   padding: "8px 0", borderBottom: "1px solid var(--g-color-line-generic)" }}>
                <div className="dm-inline" style={{ gap: 8 }}>
                  <Label theme={h.kind === "org_zip" ? "info" : h.kind === "enrich" ? "normal" : "success"} size="s">
                    {h.kind === "org_zip" ? "лендинг" : h.kind === "enrich" ? "карточка" : "поиск"}
                  </Label>
                  <Text>{h.query || "—"}</Text>
                  <Text color="secondary" variant="caption-2">{fmtDate(h.created_at)}</Text>
                </div>
                <div className="dm-inline" style={{ gap: 8 }}>
                  {h.org_count > 0 && <Text color="secondary" variant="caption-2">{fmtInt(h.org_count)} орг.</Text>}
                  {h.credits_charged > 0 && <Text color="secondary" variant="caption-2">{fmtInt(h.credits_charged)} кр.</Text>}
                  <Label theme={h.status === "done" ? "success" : h.status === "failed" ? "danger" : "info"} size="s">
                    {h.status}
                  </Label>
                  {h.download_url && (
                    <Button view="outlined" size="s" href={h.download_url}><ArrowDownToLine /> скачать</Button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}
    </main>
  );
}
