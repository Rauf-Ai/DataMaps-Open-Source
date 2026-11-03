import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Label,
  Loader,
  SegmentedRadioGroup,
  Table,
  Text,
  TextInput,
  Link as GLink,
  Spin,
  useToaster,
} from "@gravity-ui/uikit";
import { ArrowDownToLine, ChevronDown, Magnifier, Xmark } from "@gravity-ui/icons";
import noUiSlider from "nouislider";
import "nouislider/dist/nouislider.css";

import { api, post, fmtInt, fmtRub, fmtDate } from "../api.js";
import { useAuth } from "../AuthContext.jsx";
import { LOCAL_MODE } from "../localMode.js";

// ────── default state ──────
const defaultFilters = () => ({
  categories: [],
  regions: [],
  cities: [],
  keyword: "",
  has_phone: "any", has_website: "any", has_email: "any",
  has_vk: "any", has_instagram: "any", has_telegram: "any",
  rating_min: 0, rating_max: 5,
  reviews_min: 0, reviews_max: null,
});

const TRI = [
  { value: "any", content: "Любой" },
  { value: "yes", content: "Есть" },
  { value: "no", content: "Нет" },
];

const MASK_ROW = {
  name: "████████ ██████████",
  category: "██████ ██████",
  region: "█████████ ████.",
  city: "█████████",
  address: "█████████ █████████ ███",
  phone: "+7 (███) ███-██-██",
  website: "███████████.██",
  email: "█████@███████.██",
  rating: "█.█",
  reviews_count: "███",
  vk: "—",
  instagram: "—",
  telegram: "—",
};

const SOCIAL_ICONS = [
  { key: "vk", label: "VK" },
  { key: "instagram", label: "IG" },
  { key: "telegram", label: "TG" },
  { key: "whatsapp", label: "WA" },
];

function StatItem({ icon, label, value, total }) {
  const v = Number(value) || 0;
  const t = Number(total) || 0;
  const pct = t > 0 ? Math.round((v / t) * 100) : 0;
  return (
    <div className="dm-stat-item">
      <span className="dm-stat-item__icon">{icon}</span>
      <div className="dm-stat-item__body">
        <div className="dm-stat-item__label">{label}</div>
        <div className="dm-stat-item__nums">
          <b className="dm-tabular">{new Intl.NumberFormat("ru-RU").format(v)}</b>
          <span className="dm-stat-item__pct">{pct}%</span>
        </div>
      </div>
    </div>
  );
}

function TopList({ title, items, total }) {
  if (!items || items.length <= 1) return null; // не показываем если 0-1 значение
  const max = items.reduce((m, i) => Math.max(m, i.count || 0), 0) || 1;
  return (
    <div className="dm-top-list">
      <div className="dm-top-list__title">{title}</div>
      <ol className="dm-top-list__items">
        {items.map((it, idx) => {
          const cnt = Number(it.count) || 0;
          const pct = total > 0 ? Math.round((cnt / total) * 100) : 0;
          const barWidth = Math.max(2, Math.round((cnt / max) * 100));
          return (
            <li key={idx} className="dm-top-list__row" title={it.name}>
              <div className="dm-top-list__bar" style={{ width: `${barWidth}%` }} />
              <div className="dm-top-list__label">{it.name}</div>
              <div className="dm-top-list__count">
                <span className="dm-tabular">{new Intl.NumberFormat("ru-RU").format(cnt)}</span>
                {total > 0 && <span className="dm-top-list__pct"> · {pct}%</span>}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

// ────── component ──────
export function HomePage() {
  const navigate = useNavigate();
  const [sp, setSp] = useSearchParams();
  const { me, reload } = useAuth();
  const { add: toast } = useToaster();

  const [filters, setFilters] = useState(defaultFilters);
  const [regions, setRegions] = useState([]);
  const [citiesByRegion, setCitiesByRegion] = useState({});
  const [tree, setTree] = useState([]);
  const [packages, setPackages] = useState([]);
  const [downloads, setDownloads] = useState([]);

  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [exportCount, setExportCount] = useState(0);
  const [exportBusy, setExportBusy] = useState(false);
  const [exportErr, setExportErr] = useState("");
  const [task, setTask] = useState(null);
  const pollRef = useRef(null);

  const [catOpen, setCatOpen] = useState(false);
  const [catQuery, setCatQuery] = useState("");
  const [openTops, setOpenTops] = useState({});
  const [openSubs, setOpenSubs] = useState({});

  const [regOpen, setRegOpen] = useState(false);
  const [regQuery, setRegQuery] = useState("");
  const [debouncedRegQuery, setDebouncedRegQuery] = useState("");
  const [openRegions, setOpenRegions] = useState({});

  const [aiPrompt, setAiPrompt] = useState("");
  const [aiBusy, setAiBusy] = useState(false);
  const [aiProgress, setAiProgress] = useState(0);
  const [aiStage, setAiStage] = useState("");
  const [aiReasoning, setAiReasoning] = useState("");
  const [aiError, setAiError] = useState("");

  const sliderRef = useRef(null);
  const sliderInstance = useRef(null);
  const resultRef = useRef(null);

  const set = (patch) => setFilters((f) => ({ ...f, ...patch }));
  const balance = me?.credits_balance || 0;
  const loggedIn = !!me;

  useEffect(() => {
    const id = setTimeout(() => setDebouncedRegQuery(regQuery), 200);
    return () => clearTimeout(id);
  }, [regQuery]);

  useEffect(() => {
    if (!aiBusy) return undefined;
    setAiProgress(8);
    setAiStage("Анализирую задачу");
    const started = Date.now();
    const id = setInterval(() => {
      const elapsed = Date.now() - started;
      setAiStage(
        elapsed > 45000 ? "Жду ответ модели" :
        elapsed > 22000 ? "Сверяю категории и регионы" :
        elapsed > 9000 ? "Выбираю лучшие фильтры" :
        "Анализирую задачу",
      );
      setAiProgress((p) => Math.min(88, p + (p < 45 ? 4 : p < 72 ? 2 : 1)));
    }, 900);
    return () => clearInterval(id);
  }, [aiBusy]);

  // ────── load reference data ──────
  useEffect(() => {
    const safe = async (p) => { try { return await p; } catch (e) { console.warn(e); return null; } };
    (async () => {
      const [r, t, pks] = await Promise.all([
        safe(api("/api/facets/regions")),
        safe(api("/api/facets/category-tree")),
        safe(api("/api/packages")),
      ]);
      setRegions(r || []);
      setTree(t || []);
      setPackages(pks || []);
    })();
  }, []);

  useEffect(() => {
    if (loggedIn) {
      api("/api/me/downloads").then(setDownloads).catch(() => {});
    } else {
      setDownloads([]);
    }
  }, [loggedIn]);

  // ────── url state ──────
  useEffect(() => {
    const next = { ...defaultFilters() };
    if (sp.get("categories")) next.categories = sp.get("categories").split("|").filter(Boolean);
    if (sp.get("regions")) next.regions = sp.get("regions").split("|").filter(Boolean);
    if (sp.get("cities")) next.cities = sp.get("cities").split("|").filter(Boolean);
    next.keyword = sp.get("keyword") || "";
    for (const k of ["has_phone", "has_website", "has_email", "has_vk", "has_instagram", "has_telegram"]) {
      if (sp.get(k)) next[k] = sp.get(k);
    }
    next.rating_min = parseFloat(sp.get("rating_min") || "0");
    next.rating_max = parseFloat(sp.get("rating_max") || "5");
    next.reviews_min = parseInt(sp.get("reviews_min") || "0", 10);
    next.reviews_max = sp.get("reviews_max") ? parseInt(sp.get("reviews_max"), 10) : null;
    setFilters(next);
    if (sp.toString()) doSearch(next);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const syncUrl = (f) => {
    const ns = new URLSearchParams();
    if (f.categories.length) ns.set("categories", f.categories.join("|"));
    if (f.regions.length) ns.set("regions", f.regions.join("|"));
    if (f.cities.length) ns.set("cities", f.cities.join("|"));
    if (f.keyword) ns.set("keyword", f.keyword);
    for (const k of ["has_phone", "has_website", "has_email", "has_vk", "has_instagram", "has_telegram"]) {
      if (f[k] !== "any") ns.set(k, f[k]);
    }
    if (f.rating_min) ns.set("rating_min", f.rating_min);
    if (f.rating_max !== 5) ns.set("rating_max", f.rating_max);
    if (f.reviews_min) ns.set("reviews_min", f.reviews_min);
    if (f.reviews_max) ns.set("reviews_max", f.reviews_max);
    setSp(ns, { replace: true });
  };

  // ────── slider ──────
  useEffect(() => {
    if (!sliderRef.current || sliderInstance.current) return;
    const inst = noUiSlider.create(sliderRef.current, {
      start: [filters.rating_min, filters.rating_max],
      connect: true,
      range: { min: 0, max: 5 },
      step: 0.1,
      behaviour: "tap-drag",
    });
    inst.on("update", (vals) => {
      const lo = parseFloat(vals[0]);
      const hi = parseFloat(vals[1]);
      setFilters((f) => (f.rating_min === lo && f.rating_max === hi ? f : { ...f, rating_min: lo, rating_max: hi }));
    });
    sliderInstance.current = inst;
    return () => { inst.destroy(); sliderInstance.current = null; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ────── categories filtering ──────
  const filteredTree = useMemo(() => {
    if (!catQuery || catQuery.length < 2) return tree;
    const q = catQuery.toLowerCase();
    const out = [];
    const newTops = { ...openTops };
    const newSubs = { ...openSubs };
    for (const top of tree) {
      const subs = [];
      for (const sub of top.subcategories) {
        const cats = sub.categories.filter((c) => c.toLowerCase().includes(q));
        if (cats.length) subs.push({ ...sub, categories: cats, count: cats.length });
      }
      if (subs.length) {
        out.push({ ...top, subcategories: subs, count: subs.reduce((a, s) => a + s.count, 0) });
        newTops[top.name] = true;
        subs.forEach((s) => (newSubs[top.name + "/" + s.name] = true));
      }
    }
    queueMicrotask(() => { setOpenTops(newTops); setOpenSubs(newSubs); });
    return out;
  }, [catQuery, tree]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleCategory = (c) =>
    setFilters((f) => ({
      ...f,
      categories: f.categories.includes(c) ? f.categories.filter((x) => x !== c) : [...f.categories, c],
    }));

  // ────── region/city ──────
  const loadCities = useCallback(async (region) => {
    setCitiesByRegion((m) => {
      if (m[region]) return m;
      (async () => {
        try {
          const list = await api(`/api/facets/cities?region=${encodeURIComponent(region)}&limit=500`);
          setCitiesByRegion((prev) => ({ ...prev, [region]: list }));
        } catch (e) {
          console.warn(e);
        }
      })();
      return { ...m, [region]: [] };
    });
  }, []);

  const filteredRegions = useMemo(() => {
    const q = (debouncedRegQuery || "").toLowerCase().trim();
    if (!q || q.length < 2) return regions;
    const out = [];
    for (const r of regions) {
      const regHit = r.region.toLowerCase().includes(q);
      const cs = citiesByRegion[r.region] || [];
      const cityHit = cs.some((c) => c.city.toLowerCase().includes(q));
      if (regHit || cityHit) out.push(r);
    }
    return out;
  }, [debouncedRegQuery, regions, citiesByRegion]);

  useEffect(() => {
    const q = (debouncedRegQuery || "").toLowerCase().trim();
    if (!q || q.length < 2) return;
    const newOpen = {};
    for (const r of filteredRegions) {
      newOpen[r.region] = true;
      if (!citiesByRegion[r.region]) loadCities(r.region);
    }
    setOpenRegions(newOpen);
  }, [filteredRegions]); // eslint-disable-line react-hooks/exhaustive-deps

  // ────── effective export ──────
  const effective = useMemo(
    () => (!result ? 0 : Math.max(0, Math.min(exportCount || 0, result.total, LOCAL_MODE ? Number.MAX_SAFE_INTEGER : balance))),
    [result, exportCount, balance],
  );

  const recommended = useMemo(() => {
    if (!result || LOCAL_MODE) return null;
    const missing = (exportCount || 0) - balance;
    if (missing <= 0 || !packages.length) return null;
    const sorted = [...packages].sort((a, b) => a.credits - b.credits);
    return sorted.find((p) => p.credits >= missing) || sorted[sorted.length - 1];
  }, [result, exportCount, balance, packages]);

  const costHint = () => {
    if (!effective || LOCAL_MODE) return "";
    const min = packages.length ? Math.min(...packages.map((p) => p.price_per_credit_rub)) : 0;
    if (!min) return "";
    return `Это ≈ ${(effective * min).toFixed(0)} ₽ на максимальном пакете.`;
  };

  // ────── masked rows ──────
  const maskedRows = useMemo(() => {
    if (!result) return [];
    const want = 7;
    const remaining = Math.max(0, result.total - result.preview.length);
    const n = Math.min(want, remaining);
    return Array.from({ length: n });
  }, [result]);

  // ────── actions ──────
  const doSearch = async (f) => {
    const cur = f || filters;
    setLoading(true);
    syncUrl(cur);
    try {
      const payload = { ...cur };
      if (payload.reviews_max === null || payload.reviews_max === "") delete payload.reviews_max;
      const r = await post("/api/search", payload);
      setResult(r);
      if (r) setExportCount(Math.min(r.total, LOCAL_MODE ? 100 : Math.max(balance, 100)));
    } catch (e) {
      toast({ name: "search-error", title: "Ошибка поиска", content: e.message, theme: "danger" });
    } finally {
      setLoading(false);
    }
  };

  const resetFilters = () => {
    const def = defaultFilters();
    setFilters(def);
    setResult(null);
    setSp(new URLSearchParams(), { replace: true });
    if (sliderInstance.current) sliderInstance.current.set([0, 5]);
  };

  const runAi = async () => {
    const prompt = aiPrompt.trim();
    if (!prompt) return;
    setAiBusy(true); setAiProgress(8); setAiStage("Анализирую задачу"); setAiError(""); setAiReasoning("");
    try {
      const r = await post("/api/ai/parse-filters", { prompt });
      setAiProgress(92);
      setAiStage("Применяю фильтры");
      const ai = r.filters || {};
      const merged = { ...filters, ...ai };
      setFilters(merged);
      if (sliderInstance.current && ai.rating_min !== undefined && ai.rating_max !== undefined) {
        sliderInstance.current.set([ai.rating_min, ai.rating_max]);
      }
      setAiReasoning(r.reasoning || "");
      const unk = r.unknown || {};
      if ((unk.categories || []).length || (unk.regions || []).length) {
        toast({ name: "ai-unknown", theme: "warning", title: "Часть подсказок отброшена",
                content: "AI предложил неизвестные категории/регионы — они проигнорированы." });
      }
      toast({ name: "ai-ok", theme: "success", title: "Фильтры подобраны",
              content: `Категорий: ${(ai.categories || []).length}, регионов: ${(ai.regions || []).length}` });
      // Авто-поиск с новыми фильтрами + скролл к результатам
      setAiProgress(96);
      setAiStage("Запускаю поиск");
      await doSearch(merged);
      setAiProgress(100);
      setAiStage("Готово");
      setTimeout(() => {
        resultRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      }, 100);
    } catch (e) {
      setAiError(e.message);
      setAiStage("Не удалось подобрать фильтры");
    } finally {
      setTimeout(() => {
        setAiBusy(false);
        setAiProgress(0);
        setAiStage("");
      }, 450);
    }
  };

  const doExport = async () => {
    setExportErr("");
    if (!loggedIn) {
      sessionStorage.setItem("pendingExport", JSON.stringify({ filters, total: result.total, count: exportCount }));
      navigate("/login?next=/");
      return;
    }
    if (effective <= 0) { setExportErr(LOCAL_MODE ? "Нет записей для выгрузки" : "Не хватает кредитов или 0 записей"); return; }
    setExportBusy(true);
    setTask(null);
    try {
      const r = await post("/api/export", { filters, limit: effective });
      setTask({ status: "queued", progress: 0 });
      pollRef.current = setInterval(() => pollTask(r.task_id), 1500);
    } catch (e) {
      setExportErr(e.message);
    } finally {
      setExportBusy(false);
    }
  };

  const pollTask = async (taskId) => {
    try {
      const s = await api(`/api/export/${taskId}/status`);
      setTask(s);
      if (s.status === "done" || s.status === "failed") {
        clearInterval(pollRef.current);
        pollRef.current = null;
        await reload();
        api("/api/me/downloads").then(setDownloads).catch(() => {});
        // Авто-скачивание файла, как только готов
        if (s.status === "done" && s.download_url) {
          const a = document.createElement("a");
          a.href = s.download_url;
          a.rel = "noopener";
          document.body.appendChild(a);
          a.click();
          a.remove();
        }
      }
    } catch (e) {
      clearInterval(pollRef.current);
      pollRef.current = null;
      setExportErr(e.message);
    }
  };

  const buyPackage = async (id) => {
    if (!loggedIn) {
      sessionStorage.setItem("pendingExport", JSON.stringify({ filters, total: result?.total || 0, count: exportCount }));
      navigate("/login?next=/");
      return;
    }
    try {
      const r = await post("/api/payments/checkout", { package_id: id });
      if (r.confirmation_url) window.location.href = r.confirmation_url;
    } catch (e) {
      toast({ name: "checkout-error", title: "Не удалось создать платёж", content: e.message, theme: "danger" });
    }
  };

  // ────── click outside closes popovers ──────
  useEffect(() => {
    const onDoc = (e) => {
      if (!e.target.closest?.(".dm-cat-wrap")) setCatOpen(false);
      if (!e.target.closest?.(".dm-reg-wrap")) setRegOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    const onEsc = (e) => { if (e.key === "Escape") { setCatOpen(false); setRegOpen(false); } };
    document.addEventListener("keydown", onEsc);
    return () => { document.removeEventListener("mousedown", onDoc); document.removeEventListener("keydown", onEsc); };
  }, []);

  // ────── render ──────
  const categoriesLabel =
    filters.categories.length === 0
      ? "Все категории"
      : `${filters.categories.length} · ${filters.categories[0]}${filters.categories.length > 1 ? " +" + (filters.categories.length - 1) : ""}`;

  const regionLabel = (() => {
    const total = filters.regions.length + filters.cities.length;
    if (!total) return "Вся Россия";
    const first = filters.regions[0] || (filters.cities[0]?.split("|")[1] ?? "");
    return total === 1 ? first : `${total} · ${first} +${total - 1}`;
  })();

  return (
    <main className="dm-container">

      {/* ── AI HERO ── */}
      {!LOCAL_MODE && (loggedIn ? (
        <Card view="outlined" theme="normal" className="dm-card dm-ai-hero">
          <div className="dm-ai-hero__header">
            <Text variant="caption-2" color="secondary">AI-помощник</Text>
            <Text variant="subheader-2" as="div">Опишите задачу — я подберу фильтры</Text>
          </div>
          <div className="dm-ai-hero__input-wrap">
            <textarea
              className="dm-ai-hero__input"
              value={aiPrompt}
              onChange={(e) => setAiPrompt(e.target.value)}
              placeholder="Например: найди клиентов для продажи сайта и продвижения в Яндекс.Директе по Москве и области"
              rows={3}
              maxLength={2000}
              disabled={aiBusy}
              onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) runAi(); }}
            />
            <Button view="action" size="l" onClick={runAi} loading={aiBusy} disabled={!aiPrompt.trim()}>
              {aiBusy ? "Подбираю…" : "Подобрать фильтры"}
            </Button>
          </div>
          {aiBusy && (
            <div className="dm-ai-progress" role="status" aria-live="polite">
              <div className="dm-ai-progress__top">
                <div className="dm-inline" style={{ gap: 8 }}>
                  <Spin size="xs" />
                  <Text variant="body-1">{aiStage || "Подбираю фильтры"}</Text>
                </div>
                <Text variant="caption-1" color="secondary" className="dm-tabular">{Math.round(aiProgress)}%</Text>
              </div>
              <div className="dm-ai-progress__track">
                <div className="dm-ai-progress__bar" style={{ width: `${aiProgress}%` }} />
              </div>
              <Text variant="caption-2" color="secondary" as="div" style={{ marginTop: 6 }}>
                Обычно это занимает 5–20 секунд. Для сложных задач модель может думать дольше.
              </Text>
            </div>
          )}
          {aiReasoning && (
            <div style={{ marginTop: 12 }}>
              <Alert theme="info" title="Логика подбора" message={aiReasoning} />
            </div>
          )}
          {aiError && (
            <div style={{ marginTop: 12 }}>
              <Alert theme="danger" message={aiError} />
            </div>
          )}
          <Text variant="caption-2" color="secondary" as="div" style={{ marginTop: 8 }}>
            Примеры: «магазины и склады для продажи 1С», «строительные компании в Чувашии для анализа конкурентов», «B2B-клиенты с сайтом, кому нужен SEO».
          </Text>
        </Card>
      ) : (
        <Card view="outlined" theme="normal" className="dm-card dm-ai-hero">
          <Text variant="caption-2" color="secondary">AI-помощник</Text>
          <Text variant="subheader-2" as="div" style={{ marginBottom: 8 }}>Опишите задачу — AI подберёт фильтры</Text>
          <Text color="secondary" as="div" style={{ marginBottom: 12 }}>
            Войдите в аккаунт, чтобы воспользоваться помощником. Он осмысленно выбирает категории, регионы и контактные фильтры под вашу задачу — например, «найди клиентов для продажи сайтов».
          </Text>
          <Button view="action" size="m" onClick={() => navigate("/login?next=/")}>Войти</Button>
        </Card>
      ))}

      {/* ── FILTERS ── */}
      <Card view="outlined" theme="normal" className="dm-card">
        <div className="dm-card-header">
          <div>
            <Text variant="subheader-2">Фильтры</Text>
            <Text variant="caption-2" color="secondary" as="div">Отберите организации под задачу.</Text>
          </div>
          <div className="dm-inline">
            <Button view="flat" size="m" onClick={resetFilters}>Сбросить</Button>
            <Button view="action" size="m" onClick={() => doSearch()} loading={loading}>Найти</Button>
          </div>
        </div>

        <div className="dm-filters">
          {/* Категории */}
          <div className="dm-field dm-cat-wrap" style={{ position: "relative" }}>
            <span className="dm-field__label">Категории</span>
            <Button view="outlined" size="m" onClick={() => setCatOpen((v) => !v)} width="max">
              <span style={{ flex: 1, textAlign: "left", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {categoriesLabel}
              </span>
              <ChevronDown />
            </Button>
            {catOpen && (
              <div className="dm-popover">
                <div className="dm-popover__header">
                  <TextInput size="m" value={catQuery} onUpdate={setCatQuery} placeholder="Поиск по словам…" hasClear autoFocus
                             startContent={<Magnifier style={{ marginLeft: 8 }} />} />
                  <div className="dm-row" style={{ justifyContent: "space-between", marginTop: 6 }}>
                    {filters.categories.length > 0 && (
                      <GLink view="secondary" onClick={() => set({ categories: [] })}>Очистить</GLink>
                    )}
                    <GLink view="secondary" onClick={() => {
                      const t = {}, s = {};
                      for (const x of tree) { t[x.name] = true; for (const y of x.subcategories) s[x.name + "/" + y.name] = true; }
                      setOpenTops(t); setOpenSubs(s);
                    }} style={{ marginLeft: "auto" }}>Раскрыть всё</GLink>
                  </div>
                </div>
                <div className="dm-popover__body">
                  {!filteredTree.length && (
                    <div style={{ padding: 24, textAlign: "center" }}>
                      <Text color="secondary">{tree.length ? "Ничего не найдено." : "Дерево не построено. Запустите импорт CSV."}</Text>
                    </div>
                  )}
                  {filteredTree.map((top) => {
                    const allTopCats = top.subcategories.flatMap((s) => s.categories);
                    const selTopCount = allTopCats.filter((c) => filters.categories.includes(c)).length;
                    const topChecked = selTopCount > 0 && selTopCount === allTopCats.length;
                    const topIndet = selTopCount > 0 && !topChecked;
                    return (
                    <div key={top.name}>
                      <div className="dm-tree-row--region-row">
                        <button className="dm-tree-row" style={{ flex: 1 }} onClick={() => setOpenTops((m) => ({ ...m, [top.name]: !m[top.name] }))}>
                          <span className="dm-tree-row__caret">{openTops[top.name] ? "▾" : "▸"}</span>
                          <span className="dm-tree-row__title" style={{ fontWeight: 500 }}>{top.name}</span>
                          <span className="dm-tree-row__count">{top.count}</span>
                        </button>
                        <span className="dm-tree-row__check-btn" onClick={(e) => { e.stopPropagation();
                          setFilters((f) => topChecked
                            ? { ...f, categories: f.categories.filter((c) => !allTopCats.includes(c)) }
                            : { ...f, categories: [...new Set([...f.categories, ...allTopCats])] });
                        }}>
                          <Checkbox size="m" checked={topChecked} indeterminate={topIndet} />
                        </span>
                      </div>
                      {openTops[top.name] && top.subcategories.map((sub) => {
                        const selSubCount = sub.categories.filter((c) => filters.categories.includes(c)).length;
                        const subChecked = selSubCount > 0 && selSubCount === sub.categories.length;
                        const subIndet = selSubCount > 0 && !subChecked;
                        return (
                        <div key={top.name + "/" + sub.name}>
                          <div className="dm-tree-row--region-row" style={{ paddingLeft: 16 }}>
                            <button className="dm-tree-row dm-tree-row--sub" style={{ flex: 1 }}
                                    onClick={() => setOpenSubs((m) => ({ ...m, [top.name + "/" + sub.name]: !m[top.name + "/" + sub.name] }))}>
                              <span className="dm-tree-row__caret">{openSubs[top.name + "/" + sub.name] ? "▾" : "▸"}</span>
                              <span className="dm-tree-row__title">{sub.name}</span>
                              <span className="dm-tree-row__count">{sub.count}</span>
                            </button>
                            <span className="dm-tree-row__check-btn" onClick={(e) => { e.stopPropagation();
                              setFilters((f) => subChecked
                                ? { ...f, categories: f.categories.filter((c) => !sub.categories.includes(c)) }
                                : { ...f, categories: [...new Set([...f.categories, ...sub.categories])] });
                            }}>
                              <Checkbox size="m" checked={subChecked} indeterminate={subIndet} />
                            </span>
                          </div>
                          {openSubs[top.name + "/" + sub.name] && sub.categories.map((c) => (
                            <label key={c} className="dm-tree-leaf">
                              <Checkbox size="m" checked={filters.categories.includes(c)} onUpdate={() => toggleCategory(c)} />
                              <span style={{ fontSize: 13 }}>{c}</span>
                            </label>
                          ))}
                        </div>
                      );})}
                    </div>
                  );})}

                </div>
                <div className="dm-popover__footer">
                  <Button view="action" size="m" onClick={() => setCatOpen(false)}>Готово</Button>
                </div>
              </div>
            )}
            {filters.categories.length > 0 && (
              <div className="dm-row" style={{ flexWrap: "wrap", gap: 4, marginTop: 4 }}>
                {filters.categories.map((c) => (
                  <Label key={c} type="close" onClose={() => toggleCategory(c)}>{c}</Label>
                ))}
              </div>
            )}
          </div>

          {/* Регион / Город */}
          <div className="dm-field dm-reg-wrap" style={{ position: "relative" }}>
            <span className="dm-field__label">Регион и город</span>
            <Button view="outlined" size="m" onClick={() => setRegOpen((v) => !v)} width="max">
              <span style={{ flex: 1, textAlign: "left", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {regionLabel}
              </span>
              <ChevronDown />
            </Button>
            {regOpen && (
              <div className="dm-popover">
                <div className="dm-popover__header">
                  <TextInput size="m" value={regQuery} onUpdate={setRegQuery} placeholder="Регион или город…" hasClear autoFocus
                             startContent={<Magnifier style={{ marginLeft: 8 }} />} />
                  <div className="dm-row" style={{ justifyContent: "space-between", marginTop: 6 }}>
                    {(filters.regions.length > 0 || filters.cities.length > 0) && (
                      <GLink view="secondary" onClick={() => set({ regions: [], cities: [] })}>Очистить</GLink>
                    )}
                    <GLink view="secondary" onClick={() => {
                      set({ regions: filteredRegions.map((r) => r.region), cities: [] });
                    }} style={{ marginLeft: "auto" }}>Выбрать все</GLink>
                  </div>
                </div>
                <div className="dm-popover__body">
                  {!filteredRegions.length && (
                    <div style={{ padding: 24, textAlign: "center" }}>
                      <Text color="secondary">{regions.length ? "Ничего не найдено." : "Список регионов пуст — импортируйте CSV."}</Text>
                    </div>
                  )}
                  {filteredRegions.map((r) => {
                    const regSel = filters.regions.includes(r.region);
                    const opened = openRegions[r.region];
                    const cities = citiesByRegion[r.region];
                    const cq = (regQuery || "").toLowerCase().trim();
                    const cFilter = (cq && cq.length >= 2 && !r.region.toLowerCase().includes(cq))
                      ? (cities || []).filter((c) => c.city.toLowerCase().includes(cq))
                      : (cities || []);
                    const cityKeys = cFilter.map((c) => r.region + "|" + c.city);
                    const selCityCount = cityKeys.filter((k) => filters.cities.includes(k)).length;
                    const hasPartialCities = !regSel && selCityCount > 0;
                    return (
                      <div key={r.region}>
                        <div className="dm-tree-row--region-row">
                          <button className="dm-tree-row" style={{ flex: 1 }} onClick={() => {
                            setOpenRegions((m) => ({ ...m, [r.region]: !m[r.region] }));
                            if (!opened) loadCities(r.region);
                          }}>
                            <span className="dm-tree-row__caret">{opened ? "▾" : "▸"}</span>
                            <span className="dm-tree-row__title" style={{ fontWeight: 500 }}>{r.region}</span>
                            <span className="dm-tree-row__count">{fmtInt(r.count)}</span>
                          </button>
                          <span className="dm-tree-row__check-btn" onClick={(e) => { e.stopPropagation();
                            setFilters((f) => {
                              if (regSel) {
                                return { ...f, regions: f.regions.filter((x) => x !== r.region) };
                              }
                              const cleaned = f.cities.filter((c) => !c.startsWith(r.region + "|"));
                              return { ...f, regions: [...f.regions, r.region], cities: cleaned };
                            });
                          }}>
                            <Checkbox size="m" checked={regSel} indeterminate={hasPartialCities} />
                          </span>
                        </div>
                        {opened && !cities && <Text color="secondary" style={{ paddingLeft: 44, fontSize: 12 }}>Загрузка…</Text>}
                        {opened && cities && cFilter.map((c) => {
                          const ck = r.region + "|" + c.city;
                          const citySel = regSel || filters.cities.includes(ck);
                          return (
                            <label key={ck} className="dm-tree-leaf">
                              <Checkbox size="m" checked={citySel} disabled={regSel} onUpdate={() => {
                                if (regSel) return;
                                setFilters((f) => ({
                                  ...f,
                                  cities: f.cities.includes(ck) ? f.cities.filter((x) => x !== ck) : [...f.cities, ck],
                                }));
                              }} />
                              <span style={{ fontSize: 13 }}>{c.city}</span>
                              <span className="dm-tree-row__count">{fmtInt(c.count)}</span>
                            </label>
                          );
                        })}
                        {opened && cities && !cFilter.length && (
                          <Text color="secondary" style={{ paddingLeft: 44, fontSize: 12, display: "block", padding: "4px 0 4px 44px" }}>Городов не найдено.</Text>
                        )}
                      </div>
                    );
                  })}
                </div>
                <div className="dm-popover__footer">
                  <Button view="action" size="m" onClick={() => setRegOpen(false)}>Готово</Button>
                </div>
              </div>
            )}
            {(filters.regions.length > 0 || filters.cities.length > 0) && (
              <div className="dm-row" style={{ flexWrap: "wrap", gap: 4, marginTop: 4 }}>
                {filters.regions.map((r) => (
                  <Label key={r} type="close" onClose={() => set({ regions: filters.regions.filter((x) => x !== r) })}>{r}</Label>
                ))}
                {filters.cities.map((c) => {
                  const city = c.split("|")[1] || c;
                  return <Label key={c} type="close" onClose={() => set({ cities: filters.cities.filter((x) => x !== c) })}>{city}</Label>;
                })}
              </div>
            )}
          </div>

          {/* Keyword */}
          <div className="dm-field">
            <span className="dm-field__label">Ключевое слово <span style={{ color: "var(--g-color-text-secondary)", fontWeight: 400 }}>(необязательно)</span></span>
            <TextInput size="m" value={filters.keyword} onUpdate={(v) => set({ keyword: v })} placeholder="название, адрес…" hasClear />
          </div>

          {/* Рейтинг */}
          <div className="dm-field">
            <div className="dm-row" style={{ justifyContent: "space-between" }}>
              <span className="dm-field__label">Рейтинг</span>
              <Text variant="caption-1" color="secondary" className="dm-tabular">
                {filters.rating_min.toFixed(1)} — {filters.rating_max.toFixed(1)}
              </Text>
            </div>
            <div ref={sliderRef} style={{ margin: "12px 6px 4px" }}></div>
            <div className="dm-row" style={{ justifyContent: "space-between", padding: "0 6px", fontSize: 11, color: "var(--g-color-text-secondary)" }}>
              <span>0</span><span>5</span>
            </div>
          </div>

          {/* Отзывы */}
          <div className="dm-field">
            <span className="dm-field__label">Отзывов</span>
            <div className="dm-row" style={{ gap: 8 }}>
              <TextInput size="m" type="number" value={String(filters.reviews_min || 0)} onUpdate={(v) => set({ reviews_min: parseInt(v) || 0 })} placeholder="от" />
              <TextInput size="m" type="number" value={filters.reviews_max == null ? "" : String(filters.reviews_max)} onUpdate={(v) => set({ reviews_max: v === "" ? null : parseInt(v) || 0 })} placeholder="до" />
            </div>
          </div>

          {/* Контакты */}
          <div className="dm-field">
            <span className="dm-field__label">Контакты</span>
            <div className="dm-stack">
              {[
                { key: "has_phone", label: "Телефон" },
                { key: "has_website", label: "Сайт" },
                { key: "has_email", label: "Email" },
              ].map((row) => (
                <div key={row.key} className="dm-tri-row">
                  <span className="dm-tri-row__label">{row.label}</span>
                  <SegmentedRadioGroup size="s" name={row.key} options={TRI}
                    value={filters[row.key]} onUpdate={(v) => set({ [row.key]: v })} />
                </div>
              ))}
            </div>
          </div>

          {/* Соцсети */}
          <div className="dm-field">
            <span className="dm-field__label">Соцсети</span>
            <div className="dm-stack">
              {[
                { key: "has_vk", label: "ВКонтакте" },
                { key: "has_instagram", label: "Instagram" },
                { key: "has_telegram", label: "Telegram" },
              ].map((row) => (
                <div key={row.key} className="dm-tri-row">
                  <span className="dm-tri-row__label">{row.label}</span>
                  <SegmentedRadioGroup size="s" name={row.key} options={TRI}
                    value={filters[row.key]} onUpdate={(v) => set({ [row.key]: v })} />
                </div>
              ))}
            </div>
          </div>
        </div>
      </Card>

      {/* ── RESULT ── */}
      <div ref={resultRef}>
      <Card view="outlined" theme="normal" className="dm-card">
        {result ? (
          <>
            <Text variant="caption-2" color="secondary" className="dm-section-caption">Результат</Text>
            <div>
              <span className="dm-hero-number">{fmtInt(result.total)}</span>
              <span className="dm-hero-unit">организаций</span>
            </div>
            {result.stats && result.stats.total_in_segment > 0 && (
              <div className="dm-stats-block">
                <Text variant="caption-2" color="secondary" as="div">
                  В сегменте (без учёта фильтров контактов и соцсетей): <b>{fmtInt(result.stats.total_in_segment)}</b>
                </Text>
                <div className="dm-stats-grid">
                  <StatItem icon="📞" label="С телефоном"  value={result.stats.with_phone}     total={result.stats.total_in_segment} />
                  <StatItem icon="🌐" label="С сайтом"     value={result.stats.with_website}   total={result.stats.total_in_segment} />
                  <StatItem icon="✉" label="С email"       value={result.stats.with_email}     total={result.stats.total_in_segment} />
                  <StatItem icon="VK" label="ВКонтакте"    value={result.stats.with_vk}        total={result.stats.total_in_segment} />
                  <StatItem icon="IG" label="Instagram"    value={result.stats.with_instagram} total={result.stats.total_in_segment} />
                  <StatItem icon="TG" label="Telegram"     value={result.stats.with_telegram}  total={result.stats.total_in_segment} />
                </div>

                {/* Топ-10 разрезы */}
                <div className="dm-top-lists">
                  <TopList title="🌍 Топ регионов"  items={result.stats.top_regions}    total={result.stats.total_in_segment} />
                  <TopList title="🏙 Топ городов"   items={result.stats.top_cities}     total={result.stats.total_in_segment} />
                  <TopList title="🏷 Топ категорий" items={result.stats.top_categories} total={result.stats.total_in_segment} />
                </div>
              </div>
            )}
          </>
        ) : loading ? (
          <div className="dm-inline"><Spin size="s" /> <Text color="secondary">Поиск…</Text></div>
        ) : (
          <>
            <Text variant="caption-2" color="secondary" className="dm-section-caption">Поиск</Text>
            <div className="dm-hero-number" style={{ fontSize: 24 }}>Задайте фильтры</div>
            <Text color="secondary">Нажмите «Найти», чтобы увидеть совпадения и оценить объём.</Text>
          </>
        )}
      </Card>
      </div>

      {/* ── CONFIRM EXPORT ── */}
      {result && result.total > 0 && (
        <Card view="outlined" theme="normal" className="dm-card">
          <Text variant="caption-2" color="secondary" className="dm-section-caption">Выгрузка в Excel</Text>
          <Text variant="subheader-2" as="div" style={{ marginBottom: 12 }}>Подтверждение выгрузки</Text>

          <div className="dm-row" style={{ flexWrap: "wrap", gap: 16, alignItems: "flex-end" }}>
            <div className="dm-field" style={{ width: 200 }}>
              <span className="dm-field__hint">Сколько выгрузить</span>
              <TextInput size="m" type="number" value={String(exportCount || 0)}
                         onUpdate={(v) => setExportCount(parseInt(v) || 0)} />
            </div>
            <div className="dm-stack" style={{ flex: 1, minWidth: 200, fontSize: 14 }}>
              {LOCAL_MODE ? <div>Выгрузка бесплатна · до {fmtInt(result.total)} найденных организаций</div> : <><div>Нужно кредитов: <b className="dm-tabular">{fmtInt(exportCount || 0)}</b></div>
              <div>
                Доступно сейчас: <b className="dm-tabular">{fmtInt(balance)}</b> кредитов
                {result && balance < result.total && (
                  <Text color="secondary"> — это <b>{fmtInt(Math.min(balance, result.total))}</b> организаций из найденных</Text>
                )}
              </div></>}
              {effective > 0 && <Text color="secondary">{costHint()}</Text>}
            </div>
            <Button view="action" size="l" onClick={doExport}
                    disabled={!loggedIn || effective <= 0 || exportBusy}
                    loading={exportBusy}
                    style={{ whiteSpace: "nowrap", minWidth: 200 }}>
              <ArrowDownToLine /> Выгрузить
            </Button>
          </div>

          {!LOCAL_MODE && exportCount > balance && (
            <div style={{ marginTop: 12 }}>
              <Alert
                theme={loggedIn ? "warning" : "info"}
                title={`Не хватает ${fmtInt(exportCount - balance)} кредитов`}
                message={recommended ? (
                  <>
                    Подходит пакет <b>{recommended.name}</b>: {fmtInt(recommended.credits)} кр. за {fmtRub(recommended.price_rub)}.
                  </>
                ) : "Купите подходящий пакет в кабинете."}
                actions={
                  recommended ? (
                    <Button view="action" size="m" onClick={() => buyPackage(recommended.id)}>
                      {loggedIn ? "Купить пакет" : "Войти и купить"}
                    </Button>
                  ) : null
                }
              />
            </div>
          )}

          {task && (
            <div style={{ marginTop: 12, padding: 12, background: "var(--g-color-base-misc-light)", borderRadius: 8 }}>
              <div className="dm-row" style={{ justifyContent: "space-between" }}>
                <Text>Статус: <b>{task.status}</b></Text>
                <Text className="dm-tabular">{task.progress}%</Text>
              </div>
              <progress value={task.progress} max="100" style={{ width: "100%", marginTop: 6 }} />
              {task.status === "done" && task.download_url && (
                <div style={{ marginTop: 8 }}>
                  <Text color="positive" as="div" style={{ marginBottom: 6 }}>
                    ✓ Файл скачан. Если ничего не произошло — нажмите кнопку ниже:
                  </Text>
                  <Button view="action" size="m" href={task.download_url} target="_blank"
                          style={{ whiteSpace: "nowrap" }}>
                    <ArrowDownToLine /> Скачать ещё раз
                  </Button>
                </div>
              )}
              {task.error && <Text color="danger" style={{ marginTop: 6 }}>{task.error}</Text>}
            </div>
          )}

          {exportErr && <Text color="danger" style={{ marginTop: 8 }}>{exportErr}</Text>}
        </Card>
      )}

      {/* ── PREVIEW TABLE: 3 real + masked rows ── */}
      {result && result.preview.length > 0 && (
        <Card view="outlined" theme="normal" style={{ overflow: "hidden" }}>
          <div className="dm-table-scroll">
          <Table
            columns={[
              { id: "name", name: "Название", width: 200, template: (item) => <span className="dm-preview-cell" style={{ fontWeight: 500 }}>{item.name}</span> },
              { id: "category", name: "Категория", width: 150, template: (item) => <span className="dm-preview-cell" style={{ color: "var(--g-color-text-secondary)" }}>{item.category || "—"}</span> },
              { id: "region", name: "Регион", width: 140, template: (item) => <span className="dm-preview-cell">{item.region || "—"}</span> },
              { id: "city", name: "Город", width: 110, template: (item) => <span className="dm-preview-cell">{item.city || "—"}</span> },
              { id: "address", name: "Адрес", width: 200, template: (item) => <span className="dm-preview-cell" style={{ color: "var(--g-color-text-secondary)" }}>{item.address}</span> },
              { id: "phone", name: "Телефон", width: 140, template: (item) => <span className="dm-tabular">{item.phone || "—"}</span> },
              { id: "website", name: "Сайт", width: 160, template: (item) => item.website ? <a href={String(item.website).startsWith("http") ? item.website : "https://" + item.website} target="_blank" rel="noreferrer" style={{ color: "var(--g-color-text-link)" }} className="dm-preview-cell">{item.website}</a> : "—" },
              { id: "email", name: "Email", width: 160, template: (item) => <span className="dm-preview-cell">{item.email || "—"}</span> },
              { id: "rating", name: "Рейтинг", align: "end", width: 80, template: (item) => <span className="dm-tabular">{typeof item.rating === "number" && item.rating ? item.rating.toFixed(1) : (item.rating || "—")}</span> },
              { id: "reviews_count", name: "Отзывов", align: "end", width: 80, template: (item) => <span className="dm-tabular">{item.reviews_count || "—"}</span> },
              { id: "social", name: "Соцсети", width: 110, template: (item) => {
                  if (item._row === "mask") return <span className="dm-preview-cell">—</span>;
                  const active = SOCIAL_ICONS.filter((s) => item[s.key]);
                  if (!active.length) return "—";
                  return <span style={{ fontSize: 11, color: "var(--g-color-text-secondary)" }}>{active.map((s) => s.label).join(" · ")}</span>;
                }
              },
            ]}
            data={[
              ...result.preview.map((p) => ({ ...p, _row: "real" })),
              ...maskedRows.map((_, i) => ({ ...MASK_ROW, id: "m" + i, _row: "mask" })),
            ]}
            getRowClassNames={(item) => item._row === "mask" ? ["dm-row-locked"] : []}
            verticalAlign="top"
          />
          </div>
          {result.total > result.preview.length && (
            <div style={{ padding: 12, textAlign: "center", background: "var(--g-color-base-generic-medium)", fontSize: 13 }}>
              Ещё <b>{fmtInt(result.total - result.preview.length)}</b> организаций доступны после выгрузки.
            </div>
          )}
        </Card>
      )}

      {result && result.total === 0 && (
        <Card view="outlined" theme="normal" className="dm-card">
          <Text color="secondary" style={{ textAlign: "center", display: "block", padding: 32 }}>По заданным фильтрам ничего не найдено.</Text>
        </Card>
      )}

      {/* ── DOWNLOADS HISTORY ── */}
      {loggedIn && downloads.length > 0 && (
        <Card view="outlined" theme="normal" className="dm-card">
          <Text variant="caption-2" color="secondary" className="dm-section-caption">История</Text>
          <Text variant="subheader-2" as="div" style={{ marginBottom: 8 }}>Ваши выгрузки</Text>
          <div className="dm-table-scroll">
            <Table
              columns={[
                { id: "created_at", name: "Дата", template: (d) => fmtDate(d.created_at) },
                { id: "row_count", name: "Записей", align: "end", template: (d) => <span className="dm-tabular">{fmtInt(d.row_count)}</span> },
                ...(!LOCAL_MODE ? [{ id: "credits_charged", name: "Кредитов", align: "end", template: (d) => <span className="dm-tabular">{fmtInt(d.credits_charged)}</span> }] : []),
                { id: "expires_at", name: "Истекает", template: (d) => <span style={{ color: "var(--g-color-text-secondary)" }}>{fmtDate(d.expires_at)}</span> },
                { id: "actions", name: "", align: "end", template: (d) =>
                  d.available && d.download_url
                    ? <Button view="outlined" size="s" href={d.download_url}><ArrowDownToLine /> скачать</Button>
                    : <Label theme="unknown">истекло</Label>
                },
              ]}
              data={downloads}
              verticalAlign="middle"
            />
          </div>
        </Card>
      )}
    </main>
  );
}
