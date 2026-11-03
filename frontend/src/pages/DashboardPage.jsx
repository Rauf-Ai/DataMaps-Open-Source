import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Checkbox, Icon, Label, Modal, Table, Text, TextInput, useToaster } from "@gravity-ui/uikit";
import { CreditCard, TrashBin } from "@gravity-ui/icons";
import { api, post, fmtDate, fmtInt, fmtRub } from "../api.js";
import { useAuth } from "../AuthContext.jsx";
import { LOCAL_MODE } from "../localMode.js";


const QUOTA_LABELS = {
  organizations: "Организации",
  reviews: "Анализ отзывов",
  enrichment: "Фото, товары и новости",
  seasonality: "Сезонность",
  social: "Анализ соцсетей",
};


export function DashboardPage() {
  const { me, loading, reload, logout } = useAuth();
  const nav = useNavigate();
  const { add: toast } = useToaster();
  const [plans, setPlans] = useState([]);
  const [subscription, setSubscription] = useState(null);
  const [transactions, setTransactions] = useState([]);
  const [tab, setTab] = useState("plan");
  const [topup, setTopup] = useState("100");
  const [promoCode, setPromoCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [detachOpen, setDetachOpen] = useState(false);
  const [reviewCardAttached, setReviewCardAttached] = useState(true);
  const [reviewAutoRenew, setReviewAutoRenew] = useState(true);
  const billingReview = Boolean(
    me?.is_admin && new URLSearchParams(window.location.search).get("billing-review") === "1"
  );

  const load = async () => {
    if (!me) return;
    const [nextPlans, nextSub, nextTransactions] = await Promise.all([
      api("/api/subscriptions/plans"),
      api("/api/subscriptions/me"),
      api("/api/me/transactions"),
    ]);
    setPlans(nextPlans);
    setSubscription(nextSub);
    setTransactions(nextTransactions);
  };

  useEffect(() => {
    if (!loading && !me) nav("/login?next=/dashboard");
  }, [loading, me, nav]);
  useEffect(() => { load().catch(() => {}); }, [me?.id]);
  useEffect(() => { if (billingReview) setTab("settings"); }, [billingReview]);

  const currentCode = subscription?.plan?.code || me?.subscription?.plan?.code || "free";
  const topupCount = Math.max(100, Number.parseInt(topup, 10) || 100);
  const topupRub = topupCount * 0.25;
  const paid = currentCode === "pro" || currentCode === "business";

  const buyPlan = async (planCode) => {
    if (planCode === "free" || busy) return;
    setBusy(true);
    try {
      const result = await post("/api/subscriptions/checkout", { plan_code: planCode });
      window.location.href = result.confirmation_url;
    } catch (error) {
      toast({ name: "subscription", title: "Не удалось создать платёж", content: error.message, theme: "danger" });
    } finally { setBusy(false); }
  };

  const buyTopup = async () => {
    if (!paid || busy) return;
    setBusy(true);
    try {
      const result = await post("/api/subscriptions/topup", { organizations: topupCount });
      window.location.href = result.confirmation_url;
    } catch (error) {
      toast({ name: "topup", title: "Не удалось создать платёж", content: error.message, theme: "danger" });
    } finally { setBusy(false); }
  };

  const activatePromo = async () => {
    if (!promoCode.trim() || busy) return;
    setBusy(true);
    try {
      const result = await post("/api/me/promo", { code: promoCode.trim() });
      toast({ name: "promo", title: "Промокод активирован", content: `Добавлено ${fmtInt(result.credits_granted)} организаций`, theme: "success" });
      setPromoCode("");
      await reload();
      await load();
    } catch (error) {
      toast({ name: "promo", title: "Промокод не активирован", content: error.message, theme: "danger" });
    } finally { setBusy(false); }
  };

  const cancelSubscription = async () => {
    if (!paid || busy || subscription?.cancel_at_period_end) return;
    setBusy(true);
    try {
      const result = await post("/api/subscriptions/cancel", {});
      toast({
        name: "cancel-subscription",
        title: "Автопродление отключено",
        content: `Тариф действует до ${new Date(result.active_until).toLocaleDateString("ru-RU")}`,
        theme: "success",
      });
      await load();
    } catch (error) {
      toast({ name: "cancel-subscription", title: "Не удалось отключить автопродление", content: error.message, theme: "danger" });
    } finally { setBusy(false); }
  };

  const detachPaymentMethod = async () => {
    if (busy) return;
    if (billingReview) {
      setReviewCardAttached(false);
      setReviewAutoRenew(false);
      setDetachOpen(false);
      toast({
        name: "detach-payment-method-review",
        title: "Карта удалена",
        content: "Демонстрация завершена: автопродление отключено, способ оплаты отвязан.",
        theme: "success",
      });
      return;
    }
    setBusy(true);
    try {
      await api("/api/subscriptions/payment-method", { method: "DELETE" });
      setDetachOpen(false);
      toast({
        name: "detach-payment-method",
        title: "Способ оплаты отвязан",
        content: "Автопродление отключено. Новых списаний по этому способу оплаты не будет.",
        theme: "success",
      });
      await load();
    } catch (error) {
      toast({ name: "detach-payment-method-error", title: "Не удалось отвязать способ оплаты", content: error.message, theme: "danger" });
    } finally { setBusy(false); }
  };

  const paymentMethodAttached = billingReview
    ? reviewCardAttached
    : Boolean(subscription?.payment_method_attached);
  const automaticRenewal = billingReview
    ? reviewAutoRenew
    : paymentMethodAttached && !subscription?.cancel_at_period_end;

  const updateAutomaticRenewal = (checked) => {
    if (billingReview) {
      setReviewAutoRenew(checked);
      return;
    }
    if (!checked) cancelSubscription();
  };

  const tabs = [
    ["plan", "Тариф и лимиты"],
    ["payments", "Платежи"],
    ["promo", "Промокод"],
    ["settings", "Настройки"],
  ];

  if (!me) return null;
  if (LOCAL_MODE) return (
    <main className="dm-account">
      <header className="dm-account__header">
        <div className="dm-account__avatar">{me.email.slice(0, 1).toUpperCase()}</div>
        <div><Text variant="header-1" as="h1">Личный кабинет</Text><Text color="secondary">{me.email}</Text></div>
        <Label theme="success" size="m">Локально · бесплатно</Label>
      </header>
      <section className="dm-account__section">
        <Text variant="subheader-2" as="h2">Бесплатный локальный режим</Text>
        <Text color="secondary">Поиск и выгрузка данных из ваших CSV доступны без кредитов и оплаты. Для AI и живого поиска потребуются собственные ключи внешних сервисов.</Text>
        <div style={{ marginTop: 24 }}><Button view="outlined-danger" onClick={logout}>Выйти из аккаунта</Button></div>
      </section>
    </main>
  );
  return (
    <main className="dm-account">
      <header className="dm-account__header">
        <div className="dm-account__avatar">{me.email.slice(0, 1).toUpperCase()}</div>
        <div>
          <Text variant="header-1" as="h1">Личный кабинет</Text>
          <Text color="secondary">{me.email}</Text>
        </div>
        <Label theme={paid ? "success" : "normal"} size="m">{subscription?.plan?.name || "Бесплатно"}</Label>
      </header>

      <nav className="dm-account__tabs" aria-label="Разделы кабинета">
        {tabs.map(([id, label]) => (
          <button key={id} className={tab === id ? "is-active" : ""} onClick={() => setTab(id)}>{label}</button>
        ))}
      </nav>

      {tab === "plan" && subscription && (
        <section className="dm-account__section">
          <div className="dm-account__section-head">
            <div>
              <Text variant="subheader-3" as="h2">Использование в этом месяце</Text>
              <Text color="secondary">Период до {new Date(subscription.period_end).toLocaleDateString("ru-RU")}</Text>
            </div>
          </div>
          <div className="dm-quota-list">
            {Object.entries(subscription.quotas || {}).map(([key, quota]) => {
              const monthlyAvailable = Math.max(0, quota.limit - quota.used);
              const pct = quota.limit ? Math.min(100, quota.used * 100 / quota.limit) : 0;
              return (
                <div className="dm-quota-row" key={key}>
                  <div className="dm-quota-row__label">
                    <span>{QUOTA_LABELS[key] || key}</span>
                    <b>{fmtInt(monthlyAvailable)} доступно{quota.extra ? ` + ${fmtInt(quota.extra)} докуплено` : ""}</b>
                  </div>
                  <div className="dm-quota-row__track"><i style={{ width: `${pct}%` }} /></div>
                  <Text variant="caption-2" color="secondary">Использовано {fmtInt(quota.used)} из {fmtInt(quota.limit)}</Text>
                </div>
              );
            })}
          </div>

          <Text variant="subheader-3" as="h2" style={{ marginTop: 34 }}>Тарифы</Text>
          <Text color="secondary">На всех тарифах: безлимитные запросы к AI, Яндекс Карты и Google Maps, XLSX, интерактивная карта и постоянное хранение результатов.</Text>
          <div className="dm-subscription-plans">
            {plans.map((plan) => (
              <article className={`dm-subscription-plan${plan.code === "pro" ? " is-featured" : ""}`} key={plan.code}>
                <div className="dm-subscription-plan__top">
                  <Text variant="subheader-2" as="h3">{plan.name}</Text>
                  {plan.code === currentCode && <Label theme="success">Текущий</Label>}
                </div>
                <div className="dm-subscription-plan__price">{plan.price_rub ? `${fmtInt(plan.price_rub)} ₽` : "0 ₽"}<small>/мес</small></div>
                <ul>
                  <li>{fmtInt(plan.organizations)} организаций</li>
                  <li>{fmtInt(plan.reviews)} анализов отзывов</li>
                  <li>{fmtInt(plan.enrichment)} обогащений</li>
                  <li>{fmtInt(plan.social)} анализов соцсетей</li>
                  <li>{fmtInt(plan.seasonality)} анализов сезонности</li>
                  <li>{plan.concurrent_tasks} одновременн. задач · {plan.queue}</li>
                </ul>
                <Button view={plan.code === "pro" ? "action" : "outlined"} width="max" size="l"
                  disabled={plan.code === "free" || plan.code === currentCode} loading={busy}
                  onClick={() => buyPlan(plan.code)}>
                  {plan.code === currentCode ? "Подключён" : plan.code === "free" ? "Базовый доступ" : "Выбрать"}
                </Button>
              </article>
            ))}
          </div>

          <div className="dm-topup">
            <div>
              <Text variant="subheader-2" as="h3">Дополнительный объём</Text>
              <Text color="secondary">Не сгорает · 0,25 ₽ за организацию · минимум 100</Text>
            </div>
            <TextInput type="number" value={topup} onUpdate={setTopup} size="l" disabled={!paid} />
            <b>{fmtRub(topupRub)}</b>
            <Button view="action" size="l" onClick={buyTopup} disabled={!paid} loading={busy}>Докупить</Button>
          </div>
          {!paid && <Text color="secondary">Докупка станет доступна после подключения тарифа «Про» или «Бизнес».</Text>}
        </section>
      )}

      {tab === "payments" && (
        <section className="dm-account__section">
          <Text variant="subheader-3" as="h2">История платежей</Text>
          {!transactions.length ? <Text color="secondary">Платежей пока нет.</Text> : (
            <div className="dm-table-scroll"><Table columns={[
              { id: "created_at", name: "Дата", template: (row) => fmtDate(row.created_at) },
              { id: "package_name", name: "Назначение" },
              { id: "amount_rub", name: "Сумма", template: (row) => fmtRub(row.amount_rub), align: "end" },
              { id: "status", name: "Статус", template: (row) => <Label theme={row.status === "succeeded" ? "success" : row.status === "pending" ? "warning" : "danger"}>{row.status}</Label> },
            ]} data={transactions} /></div>
          )}
        </section>
      )}

      {tab === "promo" && (
        <section className="dm-account__section dm-account__narrow">
          <Text variant="subheader-3" as="h2">Активировать промокод</Text>
          <Text color="secondary">Бонусный объём не сгорает и расходуется после месячного лимита.</Text>
          <div className="dm-row"><TextInput value={promoCode} onUpdate={setPromoCode} placeholder="DATAMAPS" size="l" /><Button view="action" size="l" onClick={activatePromo} loading={busy}>Активировать</Button></div>
        </section>
      )}

      {tab === "settings" && (
        <section className="dm-account__section dm-account__narrow">
          <div className="dm-account__section-head">
            <Text variant="subheader-3" as="h2">Аккаунт и оплата</Text>
            {billingReview && <Label theme="info" size="m">Режим проверки ЮKassa</Label>}
          </div>
          <div className="dm-account__setting"><span>Email</span><b>{me.email}</b></div>
          <div className="dm-account__setting"><span>Всего оплачено</span><b>{fmtRub(me.total_paid_kop / 100)}</b></div>

          <div className="dm-payment-methods">
            <div className="dm-payment-methods__head">
              <div>
                <Text variant="subheader-2" as="h3">Способы оплаты</Text>
                <Text color="secondary">Управляйте сохранённой картой и автоматическим продлением подписки.</Text>
              </div>
            </div>

            {paymentMethodAttached ? (
              <div className="dm-payment-method">
                <div className="dm-payment-method__icon"><Icon data={CreditCard} size={22} /></div>
                <div className="dm-payment-method__details">
                  <b>{billingReview ? "Банковская карта •••• 4242" : "Сохранённый способ оплаты"}</b>
                  <span>Данные карты хранятся на стороне ЮKassa</span>
                </div>
                <Button
                  view="flat-danger"
                  size="l"
                  onClick={() => setDetachOpen(true)}
                  disabled={busy}
                  aria-label="Удалить сохранённую карту"
                >
                  <Icon data={TrashBin} size={18} /> Удалить карту
                </Button>
              </div>
            ) : (
              <div className="dm-payment-methods__empty">Нет привязанных способов оплаты</div>
            )}

            <div className="dm-payment-autorenew">
              <Checkbox
                size="l"
                checked={automaticRenewal}
                onUpdate={updateAutomaticRenewal}
                disabled={!paymentMethodAttached || busy || (!billingReview && subscription?.cancel_at_period_end)}
              >
                Использовать сохранённую карту для автопродления
              </Checkbox>
              <Text variant="caption-2" color="secondary">
                Отключить автопродление или удалить карту можно самостоятельно в любой момент.
              </Text>
            </div>
          </div>

          <Button view="outlined-danger" onClick={logout}>Выйти из аккаунта</Button>
        </section>
      )}

      <Modal open={detachOpen} onClose={() => !busy && setDetachOpen(false)}>
        <div className="dm-payment-detach">
          <Text variant="header-1" as="h2">Удалить сохранённую карту?</Text>
          <Text color="secondary">
            Карта будет отвязана от DataMaps, а автопродление отключено. Текущий тариф продолжит действовать до конца оплаченного периода.
          </Text>
          <div className="dm-payment-detach__actions">
            <Button view="flat" size="l" onClick={() => setDetachOpen(false)} disabled={busy}>Отмена</Button>
            <Button view="outlined-danger" size="l" onClick={detachPaymentMethod} loading={busy}>Удалить карту</Button>
          </div>
        </div>
      </Modal>
    </main>
  );
}
