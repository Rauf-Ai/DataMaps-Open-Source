import { useEffect, useMemo, useState } from "react";
import { Alert, Button, Label, Loader, Modal, Select, Text, TextInput } from "@gravity-ui/uikit";
import { api, post, fmtDate, fmtInt, fmtRub } from "../../api.js";

const USER_TABS = [
  ["summary", "Сводка"],
  ["activity", "Активность"],
  ["payments", "Платежи"],
  ["support", "Поддержка"],
  ["audit", "Аудит"],
];

function UserTabContent({ tab, data }) {
  const profile = data.profile || {};
  if (tab === "summary") {
    const organizationQuota = data.quotas?.organizations || {};
    return (
      <div className="dm-admin-user-summary">
        <div><span>Тариф</span><strong>{data.subscription?.plan_name || "Бесплатно"}</strong></div>
        <div><span>Период</span><strong>{fmtDate(data.subscription?.period_end)}</strong></div>
        <div><span>Баланс</span><strong>{fmtInt(profile.credits_balance)} кр.</strong></div>
        <div><span>Потрачено</span><strong>{fmtInt(profile.credits_spent)} кр.</strong></div>
        <div><span>Лимит организаций</span><strong>{fmtInt(organizationQuota.available)}</strong></div>
        <div><span>Регистрация</span><strong>{fmtDate(profile.created_at)}</strong></div>
      </div>
    );
  }
  if (tab === "activity") {
    const items = data.recent_activity || [];
    return <List title="Последние действия" items={items} render={(item) => <><Label theme="info">{item.type}</Label><span>{fmtDate(item.created_at)}</span></>} />;
  }
  if (tab === "payments") {
    return <List title="Платежи" items={data.payments || []} render={(item) => <><Label theme={item.status === "succeeded" ? "success" : item.status === "pending" ? "warning" : "danger"}>{item.status}</Label><span>{item.plan_code || item.kind || "Пакет"}</span><span>{fmtRub((item.amount_kop || 0) / 100)}</span><span>{fmtDate(item.created_at)}</span></>} />;
  }
  if (tab === "support") {
    return <List title="Обращения" items={data.support_conversations || []} render={(item) => <><Label theme={item.status === "closed" ? "normal" : "warning"}>{item.status}</Label><span>#{item.id}</span><span>{fmtDate(item.updated_at)}</span></>} />;
  }
  return <List title="Журнал администратора" items={data.audit_entries || []} render={(item) => <><Label theme="info">{item.action}</Label><span>{item.comment}</span><span>{fmtDate(item.created_at)}</span><span>{item.request_id || "—"}</span></>} />;
}

function List({ title, items, render }) {
  return (
    <section className="dm-admin-user-list">
      <Text variant="subheader-1" as="h3">{title}</Text>
      {!items.length ? <div className="dm-admin-empty">Записей пока нет.</div> : items.map((item, index) => <div className="dm-admin-user-list__row" key={item.id || index}>{render(item)}</div>)}
    </section>
  );
}

export function AdminUserDrawer({ userId, onClose, onUpdated }) {
  const [detail, setDetail] = useState(null);
  const [tab, setTab] = useState("summary");
  const [loading, setLoading] = useState(Boolean(userId));
  const [error, setError] = useState("");
  const [planCode, setPlanCode] = useState("free");
  const [creditDelta, setCreditDelta] = useState("");
  const [confirmation, setConfirmation] = useState(null);
  const [comment, setComment] = useState("");
  const [actionError, setActionError] = useState("");
  const [saving, setSaving] = useState(false);

  const loadDetail = async () => {
    if (!userId) return;
    setLoading(true);
    setError("");
    try {
      const next = await api(`/api/admin/control/users/${userId}`);
      setDetail(next);
      setPlanCode(next.subscription?.plan_code || "free");
    } catch (err) {
      setError(err.message || "Не удалось загрузить пользователя");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    setDetail(null);
    setTab("summary");
    setCreditDelta("");
    setConfirmation(null);
    setComment("");
    setActionError("");
    if (userId) loadDetail();
    // loadDetail is intentionally tied to userId, not local form changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId]);

  const profile = detail?.profile || {};
  const blocked = Boolean(profile.is_blocked);
  const canClose = !saving;
  const planLabel = useMemo(() => ({ free: "Бесплатно", pro: "Про", business: "Бизнес" }[planCode] || planCode), [planCode]);

  const beginConfirmation = (kind) => {
    if (kind === "credits" && (!creditDelta.trim() || !Number.isInteger(Number(creditDelta)))) {
      setActionError("Введите целое значение корректировки кредитов");
      return;
    }
    setActionError("");
    setComment("");
    setConfirmation(kind);
  };

  const confirmAction = async () => {
    if (!confirmation || !detail) return;
    const normalized = comment.trim();
    if (!normalized) {
      setActionError("Введите комментарий к действию");
      return;
    }
    const userIdValue = detail.profile.id;
    const routes = {
      plan: [`/api/admin/users/${userIdValue}/plan`, { plan_code: planCode, comment: normalized }],
      credits: [`/api/admin/users/${userIdValue}/credits`, { delta: Number(creditDelta), comment: normalized }],
      block: [`/api/admin/users/${userIdValue}/block`, { blocked: !blocked, comment: normalized }],
    };
    setSaving(true);
    setActionError("");
    try {
      const [path, payload] = routes[confirmation];
      await post(path, payload);
      await loadDetail();
      await onUpdated?.();
      setConfirmation(null);
      setComment("");
      if (confirmation === "credits") setCreditDelta("");
    } catch (err) {
      // Keep the drawer, confirmation and all form values intact for correction/retry.
      setActionError(err.message || "Действие не выполнено");
    } finally {
      setSaving(false);
    }
  };

  const confirmationTitle = confirmation === "plan"
    ? `Назначить тариф «${planLabel}»`
    : confirmation === "credits"
      ? `Изменить баланс на ${creditDelta} кр.`
      : blocked ? "Разблокировать пользователя" : "Заблокировать пользователя";

  return (
    <Modal open={Boolean(userId)} onClose={() => canClose && onClose?.()}>
      <aside className="dm-admin-user-drawer" aria-label="Карточка пользователя">
        <header className="dm-admin-user-drawer__header">
          <div>
            <Text variant="subheader-2" as="h2">Пользователь</Text>
            <Text className="dm-admin-email" color="secondary">{detail?.profile?.email || "Загрузка…"}</Text>
          </div>
          <Button view="flat" size="s" aria-label="Закрыть" disabled={!canClose} onClick={onClose}>×</Button>
        </header>

        {loading && !detail ? <div className="dm-admin-control__loading"><Loader size="m" /> <span>Загрузка карточки…</span></div> : null}
        {error ? <div className="dm-admin-control__error" role="alert">{error}<Button view="outlined" size="s" onClick={loadDetail}>Повторить</Button></div> : null}

        {detail && <>
          <nav className="dm-admin-user-tabs" aria-label="Разделы пользователя">
            {USER_TABS.map(([value, label]) => <Button key={value} size="s" view={tab === value ? "normal" : "flat"} onClick={() => setTab(value)}>{label}</Button>)}
          </nav>
          <div className="dm-admin-user-drawer__content"><UserTabContent tab={tab} data={detail} /></div>

          <section className="dm-admin-user-controls" aria-label="Обычные действия">
            <Text variant="subheader-1" as="h3">Тариф и квота</Text>
            <label className="dm-admin-field"><span>Тариф</span>
              <Select value={[planCode]} onUpdate={([value]) => setPlanCode(value)}>
                <Select.Option value="free">Бесплатно</Select.Option>
                <Select.Option value="pro">Про</Select.Option>
                <Select.Option value="business">Бизнес</Select.Option>
              </Select>
            </label>
            <Button view="outlined" size="m" onClick={() => beginConfirmation("plan")}>Применить тариф</Button>
            <label className="dm-admin-field"><span>Корректировка кредитов</span>
              <TextInput aria-label="Корректировка кредитов" type="number" placeholder="Например: 100 или -25" value={creditDelta} onUpdate={setCreditDelta} />
            </label>
            <Button view="outlined" size="m" onClick={() => beginConfirmation("credits")}>Изменить кредиты</Button>
          </section>

          <section className="dm-admin-user-controls dm-admin-user-controls--danger" aria-label="Опасные действия">
            <Text variant="subheader-1" as="h3">Доступ к сервису</Text>
            <Text variant="body-1" color="secondary">{blocked ? "Пользователь заблокирован." : "Блокировка сразу закроет доступ к сервису."}</Text>
            <Button view={blocked ? "outlined" : "outlined-danger"} size="m" onClick={() => beginConfirmation("block")}>
              {blocked ? "Разблокировать" : "Заблокировать"}
            </Button>
          </section>
        </>}

        {confirmation && <section className="dm-admin-confirmation" aria-label="Подтверждение действия">
          <Text variant="subheader-1" as="h3">Подтвердите действие</Text>
          <Text color="secondary">{confirmationTitle}</Text>
          <label className="dm-admin-field"><span>Комментарий к действию</span>
            <TextInput aria-label="Комментарий к действию" placeholder="Причина или контекст" value={comment} onUpdate={setComment} />
          </label>
          {actionError && <div className="dm-admin-control__inline-error" role="alert">{actionError}</div>}
          <div className="dm-admin-confirmation__actions">
            <Button view="flat" size="m" disabled={saving} onClick={() => { setConfirmation(null); setActionError(""); }}>Отмена</Button>
            <Button view={confirmation === "block" && !blocked ? "outlined-danger" : "action"} size="m" loading={saving} onClick={confirmAction}>Подтвердить</Button>
          </div>
        </section>}
      </aside>
    </Modal>
  );
}
