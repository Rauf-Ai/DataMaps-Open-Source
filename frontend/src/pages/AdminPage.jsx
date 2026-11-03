import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  Button,
  Card,
  Checkbox,
  Label,
  Text,
  Table,
  TabProvider,
  TabList,
  Tab,
  TabPanel,
  TextInput,
  useToaster,
} from "@gravity-ui/uikit";
import { api, post, fmtInt, fmtRub, fmtDate } from "../api.js";
import { useAuth } from "../AuthContext.jsx";
import { AdminOverview } from "./admin/AdminOverview.jsx";
import { AdminIncidents } from "./admin/AdminIncidents.jsx";
import { AdminTasks } from "./admin/AdminTasks.jsx";
import { AdminUsers } from "./admin/AdminUsers.jsx";
import { AdminUserDrawer } from "./admin/AdminUserDrawer.jsx";
import { LegacyAdminUsers } from "./admin/LegacyAdminUsers.jsx";
import "../styles/admin-control.css";

export function AdminPage() {
  const { me, loading } = useAuth();
  const nav = useNavigate();
  const location = useLocation();
  const { add: toast } = useToaster();
  const controlPreview = me?.is_admin && new URLSearchParams(location.search).get("control") === "1";

  const [tab, setTab] = useState(() => {
    const params = new URLSearchParams(location.search);
    return params.get("tab") || (params.get("control") === "1" ? "overview" : "dashboard");
  });
  const [dash, setDash] = useState(null);
  const [packages, setPackages] = useState([]);
  const [supportUserId, setSupportUserId] = useState(null);
  const [promos, setPromos] = useState([]);
  const [newPromo, setNewPromo] = useState({ code: "", credits: 0, discount_percent: 0, max_activations: 0, expires_at: "" });
  const [reviewTasks, setReviewTasks] = useState([]);
  const [supConvs, setSupConvs] = useState([]);
  const [supThread, setSupThread] = useState(null);
  const [supReply, setSupReply] = useState("");
  const [pendingActions, setPendingActions] = useState([]);
  const [bugReports, setBugReports] = useState([]);
  const [analytics, setAnalytics] = useState(null);

  useEffect(() => {
    if (!loading && (!me || !me.is_admin)) nav("/login?next=/admin");
  }, [loading, me, nav]);

  useEffect(() => {
    if (!me?.is_admin) return;
    api("/api/admin/dashboard").then(setDash).catch(() => {});
    api("/api/admin/subscription-plans").then(setPackages).catch(() => {});
    api("/api/admin/promos").then(setPromos).catch(() => {});
  }, [me]);

  useEffect(() => {
    const requestedTab = new URLSearchParams(location.search).get("tab");
    const previewTabs = new Set(["overview", "incidents", "tasks"]);
    if (requestedTab && (controlPreview || !previewTabs.has(requestedTab))) setTab(requestedTab);
    else if (!controlPreview && previewTabs.has(tab)) setTab("dashboard");
  }, [controlPreview, location.search, tab]);

  const setAdminTab = (nextTab) => {
    setTab(nextTab);
    const params = new URLSearchParams(location.search);
    params.set("tab", nextTab);
    nav({ pathname: location.pathname, search: `?${params.toString()}` });
  };

  const openControlTab = (nextTab, params = {}) => {
    setTab(nextTab);
    const query = new URLSearchParams(location.search);
    query.set("tab", nextTab);
    for (const [key, value] of Object.entries(params)) {
      if (value === undefined || value === null || value === "") query.delete(key);
      else query.set(key, value);
    }
    nav({ pathname: location.pathname, search: `?${query.toString()}` });
  };

  useEffect(() => {
    if (tab === "reviews") api("/api/admin/review-tasks").then(setReviewTasks).catch(() => {});
  }, [tab]);

  const loadSupport = () => {
    api("/api/admin/support/conversations").then(setSupConvs).catch(() => {});
    api("/api/admin/support/pending-actions").then(setPendingActions).catch(() => {});
    api("/api/admin/support/bug-reports").then(setBugReports).catch(() => {});
  };
  useEffect(() => { if (tab === "support") loadSupport(); }, [tab]); // eslint-disable-line

  const openThread = async (cid) => {
    setSupReply("");
    setSupThread(await api(`/api/admin/support/conversations/${cid}`));
  };

  const sendReply = async () => {
    const text = supReply.trim();
    if (!text || !supThread) return;
    try {
      await post(`/api/admin/support/conversations/${supThread.id}/reply`, { text });
      setSupReply("");
      await openThread(supThread.id);
      loadSupport();
    } catch (e) { toast({ name: "sup-reply", title: "Ошибка", content: e.message, theme: "danger" }); }
  };

  const closeAdminConv = async (cid) => {
    try {
      await post(`/api/admin/support/conversations/${cid}/close`, {});
      await openThread(cid);
      loadSupport();
    } catch (e) { toast({ name: "cls", title: "Ошибка", content: e.message, theme: "danger" }); }
  };

  useEffect(() => {
    if (tab === "analytics") api("/api/admin/analytics/parsing").then(setAnalytics).catch(() => {});
  }, [tab]); // eslint-disable-line

  const resolveAction = async (pa, decision) => {
    try {
      await post(`/api/admin/support/pending-actions/${pa.id}/resolve`, { decision });
      loadSupport();
      toast({ name: "pa-res", title: decision === "approve" ? "Подтверждено" : "Отклонено", theme: "success" });
    } catch (e) { toast({ name: "pa-err", title: "Ошибка", content: e.message, theme: "danger" }); }
  };

  const createPromo = async () => {
    try {
      await post("/api/admin/promos", newPromo);
      setPromos(await api("/api/admin/promos"));
      setNewPromo({ code: "", credits: 0, discount_percent: 0, max_activations: 0, expires_at: "" });
      toast({ name: "promo-ok", title: "Промокод создан", theme: "success" });
    } catch (e) { toast({ name: "promo-err", title: "Ошибка", content: e.message, theme: "danger" }); }
  };

  const savePromo = async (p) => {
    try {
      await api(`/api/admin/promos/${p.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          code: p.code, credits: p.credits, discount_percent: p.discount_percent,
          max_activations: p.max_activations, is_active: p.is_active, expires_at: p.expires_at || null,
        }),
      });
      toast({ name: "promo-save", title: "Сохранено", theme: "success" });
    } catch (e) { toast({ name: "promo-save-err", title: "Ошибка", content: e.message, theme: "danger" }); }
  };

  const deletePromo = async (p) => {
    if (!confirm(`Деактивировать промокод ${p.code}?`)) return;
    try {
      await api(`/api/admin/promos/${p.id}`, { method: "DELETE" });
      setPromos(await api("/api/admin/promos"));
    } catch (e) { toast({ name: "promo-del", title: "Ошибка", content: e.message, theme: "danger" }); }
  };

  if (!me?.is_admin) return null;

  return (
    <main className="dm-container">
      <TabProvider value={tab} onUpdate={setAdminTab}>
        <TabList size="l">
          {controlPreview && <Tab value="overview">Операционный центр</Tab>}
          {controlPreview && <Tab value="incidents">Инциденты</Tab>}
          <Tab value="dashboard">Дашборд</Tab>
          <Tab value="packages">Тарифы</Tab>
          <Tab value="promos">Промокоды</Tab>
          <Tab value="users">Пользователи</Tab>
          {controlPreview && <Tab value="tasks">Задачи</Tab>}
          <Tab value="reviews">Анализы отзывов</Tab>
          <Tab value="support">Поддержка</Tab>
          <Tab value="analytics">Аналитика</Tab>
        </TabList>

        {controlPreview && <TabPanel value="overview">
          <AdminOverview onNavigate={openControlTab} />
        </TabPanel>}

        {controlPreview && <TabPanel value="incidents">
          <AdminIncidents
            initialIncidentId={new URLSearchParams(location.search).get("incident") || undefined}
            initialFilters={{
              status: new URLSearchParams(location.search).get("status") || "",
              severity: new URLSearchParams(location.search).get("severity") || "",
              source: new URLSearchParams(location.search).get("source") || "",
              q: new URLSearchParams(location.search).get("q") || "",
            }}
          />
        </TabPanel>}

        {/* DASHBOARD */}
        <TabPanel value="dashboard">
          <div className="dm-stack" style={{ gap: 16, marginTop: 16 }}>
            {dash && (
              <div className="dm-stats">
                <Card view="outlined" theme="normal" className="dm-card">
                  <Text variant="caption-2" color="secondary" className="dm-section-caption">Пользователей</Text>
                  <div className="dm-hero-number">{fmtInt(dash.total_users)}</div>
                  <Text variant="caption-2" color="secondary">+{fmtInt(dash.new_users_7d)} за 7 дней</Text>
                </Card>
                <Card view="outlined" theme="normal" className="dm-card">
                  <Text variant="caption-2" color="secondary" className="dm-section-caption">Выручка 7д</Text>
                  <div className="dm-hero-number">{fmtRub(dash.revenue_7d_rub)}</div>
                </Card>
                <Card view="outlined" theme="normal" className="dm-card">
                  <Text variant="caption-2" color="secondary" className="dm-section-caption">Выручка 30д</Text>
                  <div className="dm-hero-number">{fmtRub(dash.revenue_30d_rub)}</div>
                </Card>
                <Card view="outlined" theme="normal" className="dm-card">
                  <Text variant="caption-2" color="secondary" className="dm-section-caption">Выгрузок 7д</Text>
                  <div className="dm-hero-number">{fmtInt(dash.downloads_7d)}</div>
                </Card>
              </div>
            )}

            <Card view="outlined" theme="normal" className="dm-card">
              <Text variant="caption-2" color="secondary" className="dm-section-caption">Активность</Text>
              <Text variant="subheader-2" as="div" style={{ marginBottom: 8 }}>Последние транзакции</Text>
              <div className="dm-table-scroll">
                <Table
                  columns={[
                    { id: "created_at", name: "Дата", template: (t) => fmtDate(t.created_at) },
                    { id: "user_email", name: "Пользователь" },
                    { id: "package_name", name: "Пакет" },
                    { id: "amount_rub", name: "Сумма", align: "end", template: (t) => <span className="dm-tabular">{fmtRub(t.amount_rub)}</span> },
                    { id: "status", name: "Статус", template: (t) => (
                      <Label theme={t.status === "succeeded" ? "success" : t.status === "pending" ? "warning" : "danger"}>{t.status}</Label>
                    )},
                  ]}
                  data={dash?.recent_transactions || []}
                />
              </div>
            </Card>
          </div>
        </TabPanel>

        {/* CURRENT SUBSCRIPTION TARIFFS */}
        <TabPanel value="packages">
          <div className="dm-stack" style={{ gap: 16, marginTop: 16 }}>
            <Card view="outlined" theme="normal" className="dm-card">
              <Text variant="caption-2" color="secondary" className="dm-section-caption">Действующая тарификация</Text>
              <Text variant="subheader-2" as="div" style={{ marginBottom: 8 }}>Ежемесячные тарифы</Text>
              <Text variant="body-1" color="secondary" as="div" style={{ marginBottom: 16 }}>
                AI-запросы без лимита. Дополнительные организации на платных тарифах — 0,25 ₽ за организацию.
              </Text>
              <div className="dm-table-scroll">
                <Table
                  columns={[
                    { id: "name", name: "Тариф", template: (p) => <strong>{p.name}</strong> },
                    { id: "price_rub", name: "Цена/мес", align: "end", template: (p) => fmtRub(p.price_rub) },
                    { id: "organizations", name: "Организаций", align: "end", template: (p) => fmtInt(p.organizations) },
                    { id: "reviews", name: "Отзывы", align: "end", template: (p) => fmtInt(p.reviews) },
                    { id: "enrichment", name: "Обогащение", align: "end", template: (p) => fmtInt(p.enrichment) },
                    { id: "seasonality", name: "Сезонность", align: "end", template: (p) => fmtInt(p.seasonality) },
                    { id: "social", name: "Соцсети", align: "end", template: (p) => fmtInt(p.social) },
                    { id: "concurrent_tasks", name: "Задач", align: "end" },
                    { id: "queue", name: "Очередь" },
                  ]}
                  data={packages}
                  verticalAlign="middle"
                />
              </div>
            </Card>
          </div>
        </TabPanel>

        {/* PROMOS */}
        <TabPanel value="promos">
          <div className="dm-stack" style={{ gap: 16, marginTop: 16 }}>
            <Card view="outlined" theme="normal" className="dm-card">
              <Text variant="caption-2" color="secondary" className="dm-section-caption">Промокоды</Text>
              <div className="dm-table-scroll">
                <Table
                  columns={[
                  { id: "code", name: "Код", template: (p, idx) => (
                    <TextInput size="s" value={p.code} onUpdate={(v) => setPromos((ps) => ps.map((x, i) => i === idx ? { ...x, code: v } : x))} />
                  )},
                  { id: "credits", name: "Кредитов", align: "end", template: (p, idx) => (
                    <TextInput size="s" type="number" value={String(p.credits)} onUpdate={(v) => setPromos((ps) => ps.map((x, i) => i === idx ? { ...x, credits: parseInt(v) || 0 } : x))} />
                  )},
                  { id: "discount_percent", name: "Скидка %", align: "end", template: (p, idx) => (
                    <TextInput size="s" type="number" value={String(p.discount_percent)} onUpdate={(v) => setPromos((ps) => ps.map((x, i) => i === idx ? { ...x, discount_percent: parseInt(v) || 0 } : x))} />
                  )},
                  { id: "max_activations", name: "Лимит", align: "end", template: (p, idx) => (
                    <TextInput size="s" type="number" value={String(p.max_activations)} onUpdate={(v) => setPromos((ps) => ps.map((x, i) => i === idx ? { ...x, max_activations: parseInt(v) || 0 } : x))} />
                  )},
                  { id: "used_count", name: "Использовано", align: "end", template: (p) => (
                    <span className="dm-tabular">{p.used_count}{p.max_activations > 0 ? ` / ${p.max_activations}` : ""}</span>
                  )},
                  { id: "expires_at", name: "Истекает", template: (p, idx) => (
                    <TextInput size="s" type="date" value={p.expires_at ? p.expires_at.slice(0, 10) : ""} onUpdate={(v) => setPromos((ps) => ps.map((x, i) => i === idx ? { ...x, expires_at: v || null } : x))} />
                  )},
                  { id: "is_active", name: "Акт.", align: "center", template: (p, idx) => (
                    <Checkbox checked={p.is_active} onUpdate={(v) => setPromos((ps) => ps.map((x, i) => i === idx ? { ...x, is_active: v } : x))} />
                  )},
                  { id: "actions", name: "", align: "end", template: (p) => (
                    <div className="dm-row">
                      <Button size="s" view="outlined" onClick={() => savePromo(p)}>сохранить</Button>
                      <Button size="s" view="outlined-danger" onClick={() => deletePromo(p)}>✕</Button>
                    </div>
                  )},
                  ]}
                  data={promos}
                  verticalAlign="middle"
                />
              </div>
            </Card>

            <Card view="outlined" theme="normal" className="dm-card">
              <Text variant="caption-2" color="secondary" className="dm-section-caption">Создать</Text>
              <Text variant="subheader-2" as="div" style={{ marginBottom: 8 }}>Новый промокод</Text>
              <div className="dm-admin-form-grid dm-admin-form-grid--promos">
                <div>
                  <Text variant="caption-2" color="secondary">Код</Text>
                  <TextInput size="m" placeholder="PROMO2026" value={newPromo.code} onUpdate={(v) => setNewPromo({ ...newPromo, code: v })} />
                </div>
                <div>
                  <Text variant="caption-2" color="secondary">Кредитов</Text>
                  <TextInput size="m" type="number" placeholder="0" value={String(newPromo.credits)} onUpdate={(v) => setNewPromo({ ...newPromo, credits: parseInt(v) || 0 })} />
                </div>
                <div>
                  <Text variant="caption-2" color="secondary">Скидка %</Text>
                  <TextInput size="m" type="number" placeholder="0" value={String(newPromo.discount_percent)} onUpdate={(v) => setNewPromo({ ...newPromo, discount_percent: parseInt(v) || 0 })} />
                </div>
                <div>
                  <Text variant="caption-2" color="secondary">Лимит (0 = безлим.)</Text>
                  <TextInput size="m" type="number" placeholder="0" value={String(newPromo.max_activations)} onUpdate={(v) => setNewPromo({ ...newPromo, max_activations: parseInt(v) || 0 })} />
                </div>
                <div>
                  <Text variant="caption-2" color="secondary">Истекает</Text>
                  <TextInput size="m" type="date" value={newPromo.expires_at} onUpdate={(v) => setNewPromo({ ...newPromo, expires_at: v })} />
                </div>
                <Button view="action" size="m" onClick={createPromo} style={{ alignSelf: "end" }}>Добавить</Button>
              </div>
            </Card>
          </div>
        </TabPanel>

        {/* USERS */}
        <TabPanel value="users">
          {controlPreview ? <AdminUsers /> : <LegacyAdminUsers />}
        </TabPanel>

        {controlPreview && <TabPanel value="tasks">
          <AdminTasks isActive={tab === "tasks"} />
        </TabPanel>}

        {/* REVIEW TASKS */}
        <TabPanel value="reviews">
          <Card view="outlined" theme="normal" className="dm-card" style={{ marginTop: 16 }}>
            <div className="dm-table-scroll">
              <Table
                columns={[
                { id: "created_at", name: "Дата", template: (t) => fmtDate(t.created_at) },
                { id: "user_email", name: "Пользователь" },
                { id: "org_count", name: "Орг.", align: "end", template: (t) => <span className="dm-tabular">{fmtInt(t.org_count)}</span> },
                { id: "stars", name: "Звёзды", template: (t) => `${t.star_min}-${t.star_max}` },
                { id: "credits_charged", name: "Кредитов", align: "end", template: (t) => <span className="dm-tabular">{fmtInt(t.credits_charged)}</span> },
                { id: "status", name: "Статус", template: (t) => (
                    <Label theme={t.status === "done" ? "success" : t.status === "failed" ? "danger" : "info"}>
                      {t.status}
                    </Label>
                  )
                },
                { id: "progress", name: "%", align: "end", template: (t) => <span className="dm-tabular">{t.progress}</span> },
                { id: "error", name: "Ошибка", template: (t) => t.error ? <Text color="danger" variant="caption-2">{t.error.slice(0, 100)}</Text> : null },
                ]}
                data={reviewTasks}
                verticalAlign="middle"
              />
            </div>
          </Card>
        </TabPanel>

        {/* SUPPORT */}
        <TabPanel value="support">
          <div className="dm-stack" style={{ gap: 16, marginTop: 16 }}>
            {/* Pending actions */}
            <Card view="outlined" theme="normal" className="dm-card">
              <Text variant="caption-2" color="secondary" className="dm-section-caption">Действия агента на подтверждении</Text>
              <Text variant="subheader-2" as="div" style={{ marginBottom: 8 }}>Очередь действий</Text>
              <div className="dm-table-scroll">
                <Table
                  columns={[
                    { id: "created_at", name: "Дата", template: (a) => fmtDate(a.created_at) },
                    { id: "summary", name: "Действие" },
                    { id: "status", name: "Статус", template: (a) => (
                      <Label theme={a.status === "executed" ? "success" : a.status === "pending" ? "warning" : a.status === "rejected" ? "normal" : "danger"}>{a.status}</Label>
                    )},
                    { id: "result", name: "Результат", template: (a) => a.result ? <Text variant="caption-2">{a.result}</Text> : null },
                    { id: "actions", name: "", align: "end", template: (a) => a.status === "pending" ? (
                      <div className="dm-row">
                        <Button size="s" view="action" onClick={() => resolveAction(a, "approve")}>подтвердить</Button>
                        <Button size="s" view="outlined-danger" onClick={() => resolveAction(a, "reject")}>отклонить</Button>
                      </div>
                    ) : null },
                  ]}
                  data={pendingActions}
                  verticalAlign="middle"
                />
              </div>
            </Card>

            {/* Conversations + thread */}
            <div className="dm-support-admin">
              <Card view="outlined" theme="normal" className="dm-card">
                <div className="dm-row" style={{ justifyContent: "space-between", marginBottom: 8 }}>
                  <Text variant="subheader-2">Беседы</Text>
                  <Button size="s" view="flat" onClick={loadSupport}>обновить</Button>
                </div>
                <div className="dm-support-admin__list">
                  {supConvs.map((c) => (
                    <button key={c.id} className="dm-support-admin__conv" onClick={() => openThread(c.id)}>
                      <div className="dm-row" style={{ justifyContent: "space-between" }}>
                        <Text ellipsis>{c.user_email || `Гость #${c.id}`}</Text>
                        {c.status === "needs_human" && <Label theme="warning" size="s">оператор</Label>}
                      </div>
                      <Text variant="caption-2" color="secondary">{fmtDate(c.last_message_at)}</Text>
                    </button>
                  ))}
                  {!supConvs.length && <Text color="secondary">Бесед пока нет</Text>}
                </div>
              </Card>

              <Card view="outlined" theme="normal" className="dm-card">
                {supThread ? (
                  <>
                    <div className="dm-row" style={{ justifyContent: "space-between", marginBottom: 8, flexWrap: "wrap", gap: 8 }}>
                      <div className="dm-inline" style={{ gap: 8 }}>
                        <Text variant="subheader-2">Беседа #{supThread.id}</Text>
                        <Label size="s" theme={supThread.status === "closed" ? "normal" : supThread.status === "needs_human" ? "warning" : "success"}>{supThread.status}</Label>
                      </div>
                      <div className="dm-row" style={{ gap: 6, flexWrap: "wrap" }}>
                        {supThread.user && (
                          <>
                            <Button size="s" view="outlined" onClick={() => setSupportUserId(supThread.user.id)}>профиль</Button>
                            <Button size="s" view="outlined" onClick={() => setSupportUserId(supThread.user.id)}>тариф и кредиты</Button>
                            <Button size="s" view="outlined-danger" onClick={() => setSupportUserId(supThread.user.id)}>
                              управление доступом
                            </Button>
                          </>
                        )}
                        {supThread.status !== "closed" && (
                          <Button size="s" view="outlined-danger" onClick={() => closeAdminConv(supThread.id)}>закрыть</Button>
                        )}
                      </div>
                    </div>
                    {supThread.user ? (
                      <Text color="secondary" variant="caption-2" as="div" style={{ marginBottom: 8 }}>
                        {supThread.user.email} · баланс {fmtInt(supThread.user.credits_balance)} кр.{supThread.user.is_blocked ? " · заблокирован" : ""}
                      </Text>
                    ) : (
                      <Text color="secondary" variant="caption-2" as="div" style={{ marginBottom: 8 }}>Гость (не залогинен)</Text>
                    )}
                    <div className="dm-support-admin__thread">
                      {supThread.messages.map((m, i) => (
                        <div key={i} className={`dm-support__msg dm-support__msg--${m.role === "user" ? "user" : "bot"}`}>
                          {m.role !== "user" && <div className="dm-support__op">{m.role === "admin" ? "Оператор" : m.role === "assistant" ? "Агент" : m.role}</div>}
                          <div className="dm-support__bubble">
                            {m.image_url && <img className="dm-support__img" src={m.image_url} alt="вложение" />}
                            {m.content}
                          </div>
                        </div>
                      ))}
                    </div>
                    <div className="dm-row" style={{ marginTop: 10 }}>
                      <TextInput size="m" placeholder="Ответ оператора…" value={supReply} onUpdate={setSupReply}
                                 onKeyDown={(e) => e.key === "Enter" && sendReply()} style={{ flex: 1 }} />
                      <Button view="action" size="m" onClick={sendReply} disabled={!supReply.trim()}>Отправить</Button>
                    </div>
                  </>
                ) : (
                  <Text color="secondary">Выберите беседу слева</Text>
                )}
              </Card>
            </div>

            {/* Bug reports */}
            <Card view="outlined" theme="normal" className="dm-card">
              <Text variant="caption-2" color="secondary" className="dm-section-caption">Баг-репорты от агента</Text>
              <Text variant="subheader-2" as="div" style={{ marginBottom: 8 }}>Баги (чинятся через Claude Code)</Text>
              <div className="dm-table-scroll">
                <Table
                  columns={[
                    { id: "created_at", name: "Дата", template: (b) => fmtDate(b.created_at) },
                    { id: "severity", name: "Важн.", template: (b) => (
                      <Label theme={b.severity === "high" ? "danger" : b.severity === "low" ? "normal" : "warning"}>{b.severity}</Label>
                    )},
                    { id: "title", name: "Заголовок" },
                    { id: "proposed_fix", name: "Предлагаемый фикс", template: (b) => b.proposed_fix ? <Text variant="caption-2">{b.proposed_fix.slice(0, 160)}</Text> : null },
                    { id: "status", name: "Статус", template: (b) => <Label>{b.status}</Label> },
                  ]}
                  data={bugReports}
                  verticalAlign="middle"
                />
              </div>
            </Card>
          </div>
        </TabPanel>
        {/* ANALYTICS */}
        <TabPanel value="analytics">
          <div className="dm-stack" style={{ gap: 16, marginTop: 16 }}>
            {!analytics ? (
              <Text color="secondary">Загрузка…</Text>
            ) : (
              <>
                <Card view="outlined" theme="normal" className="dm-card">
                  <Text variant="caption-2" color="secondary" className="dm-section-caption">Сбор данных</Text>
                  <Text variant="subheader-2" as="div" style={{ marginBottom: 8 }}>
                    Всего задач парсинга: {fmtInt(analytics.total_tasks)}
                  </Text>
                  <div className="dm-row" style={{ gap: 24, flexWrap: "wrap" }}>
                    <div>
                      <Text variant="caption-2" color="secondary" as="div">По источникам</Text>
                      {analytics.by_source.map((s) => (
                        <div key={s.source} className="dm-row" style={{ justifyContent: "space-between", minWidth: 160 }}>
                          <span>{s.source}</span><span className="dm-tabular">{fmtInt(s.count)}</span>
                        </div>
                      ))}
                    </div>
                    <div>
                      <Text variant="caption-2" color="secondary" as="div">По видам</Text>
                      {analytics.by_kind.map((k) => (
                        <div key={k.kind} className="dm-row" style={{ justifyContent: "space-between", minWidth: 160 }}>
                          <span>{k.kind}</span><span className="dm-tabular">{fmtInt(k.count)}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </Card>

                <div className="dm-row" style={{ gap: 16, flexWrap: "wrap", alignItems: "flex-start" }}>
                  <Card view="outlined" theme="normal" className="dm-card" style={{ flex: 1, minWidth: 280 }}>
                    <Text variant="subheader-2" as="div" style={{ marginBottom: 8 }}>Топ запросов</Text>
                    {analytics.top_queries.length === 0 ? <Text color="secondary">—</Text> : analytics.top_queries.map((q, i) => (
                      <div key={i} className="dm-row" style={{ justifyContent: "space-between", padding: "3px 0" }}>
                        <span>{q.query}</span><Label size="s" theme="info">{fmtInt(q.count)}</Label>
                      </div>
                    ))}
                  </Card>
                  <Card view="outlined" theme="normal" className="dm-card" style={{ flex: 1, minWidth: 280 }}>
                    <Text variant="subheader-2" as="div" style={{ marginBottom: 8 }}>Активные пользователи</Text>
                    {analytics.top_users.length === 0 ? <Text color="secondary">—</Text> : analytics.top_users.map((u, i) => (
                      <div key={i} className="dm-row" style={{ justifyContent: "space-between", padding: "3px 0" }}>
                        <span>{u.email}</span><Label size="s">{fmtInt(u.count)} задач</Label>
                      </div>
                    ))}
                  </Card>
                </div>

                <Card view="outlined" theme="normal" className="dm-card">
                  <Text variant="subheader-2" as="div" style={{ marginBottom: 8 }}>Последние запросы к AI-помощнику</Text>
                  {analytics.recent_ai.length === 0 ? <Text color="secondary">—</Text> : (
                    <div className="dm-stack" style={{ gap: 6 }}>
                      {analytics.recent_ai.map((a, i) => (
                        <div key={i} className="dm-activity__row">
                          <span>{fmtDate(a.created_at)}</span>
                          <span style={{ color: "var(--g-color-text-secondary)" }}>{a.email}</span>
                          <span style={{ flex: 1 }}>{a.prompt}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </Card>
              </>
            )}
          </div>
        </TabPanel>
      </TabProvider>

      <AdminUserDrawer userId={supportUserId} onClose={() => setSupportUserId(null)} />
    </main>
  );
}
