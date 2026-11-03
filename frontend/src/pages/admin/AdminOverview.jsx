import { useCallback, useEffect, useState } from "react";
import { Button, Label, Loader, Text } from "@gravity-ui/uikit";
import { api, fmtInt, fmtDate } from "../../api.js";
import { integrationLabel, integrationTheme, severityTheme } from "./adminFormat.js";

function Metric({ label, value, hint, tone = "default" }) {
  return (
    <div className={`dm-admin-metric dm-admin-metric--${tone}`}>
      <Text variant="caption-2" color="secondary">{label}</Text>
      <strong className="dm-admin-metric__value">{fmtInt(value)}</strong>
      {hint && <Text variant="caption-2" color="secondary">{hint}</Text>}
    </div>
  );
}

function attentionTarget(item) {
  if (item.kind === "incident") {
    return { tab: "incidents", params: { incident: String(item.id) } };
  }
  return { tab: "tasks", params: { status: "running", stalled: "1" } };
}

export function AdminOverview({ onNavigate }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setData(await api("/api/admin/control/overview"));
    } catch (err) {
      setError(err.message || "Не удалось загрузить обзор");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading && !data) {
    return <div className="dm-admin-control__loading"><Loader size="l" /> <span>Загрузка операционного обзора…</span></div>;
  }

  if (error && !data) {
    return (
      <section className="dm-admin-control__error" role="alert">
        <Text variant="subheader-2">Не удалось загрузить обзор</Text>
        <Text color="secondary">{error}</Text>
        <Button view="outlined" size="m" onClick={load}>Повторить</Button>
      </section>
    );
  }

  const attention = data?.attention || [];
  const tasks = data?.tasks || {};
  const users = data?.users || {};
  const payments = data?.payments || {};
  const support = data?.support || {};
  const incidents = data?.incidents || {};
  const subscriptions = data?.subscriptions || {};

  return (
    <div className="dm-admin-control">
      <header className="dm-admin-control__header">
        <div>
          <Text variant="subheader-2" as="h1">Операционный центр</Text>
          <Text variant="caption-2" color="secondary">Сводка по сервису и отклонениям</Text>
        </div>
        <Button view="flat" size="s" onClick={load} loading={loading}>Обновить</Button>
      </header>

      <section className="dm-admin-attention" aria-labelledby="admin-attention-title">
        <div className="dm-admin-section-head">
          <div>
            <Text variant="subheader-2" as="h2" id="admin-attention-title">Требует внимания</Text>
            <Text variant="caption-2" color="secondary">Нерешённые инциденты и зависшие операции</Text>
          </div>
          <Label theme={incidents.critical ? "danger" : "normal"}>{fmtInt(attention.length)}</Label>
        </div>
        {attention.length ? (
          <div className="dm-admin-attention__rows">
            {attention.map((item, index) => {
              const target = attentionTarget(item);
              return (
                <Button
                  key={`${item.kind}-${item.id || item.filter || index}`}
                  view="flat"
                  className="dm-admin-attention__row"
                  onClick={() => onNavigate?.(target.tab, target.params)}
                >
                  <Label theme={severityTheme(item.severity)}>{item.code || "СИГНАЛ"}</Label>
                  <span className="dm-admin-attention__message">{item.message}</span>
                  <span className="dm-admin-attention__count">{fmtInt(item.occurrence_count)}</span>
                </Button>
              );
            })}
          </div>
        ) : (
          <div className="dm-admin-empty">Открытых инцидентов и зависших задач нет.</div>
        )}
      </section>

      {error && <div className="dm-admin-control__inline-error" role="alert">Данные обновлены не полностью: {error}</div>}

      <section className="dm-admin-band" aria-labelledby="admin-operations-title">
        <div className="dm-admin-section-head">
          <Text variant="subheader-2" as="h2" id="admin-operations-title">Операции</Text>
          <Text variant="caption-2" color="secondary">Фоновые задачи и платежи за 24 часа</Text>
        </div>
        <div className="dm-admin-metrics">
          <Metric label="В очереди" value={tasks.queued} />
          <Metric label="Выполняются" value={tasks.running} tone="active" />
          <Metric label="С ошибкой за 24ч" value={tasks.failed_24h} tone={tasks.failed_24h ? "danger" : "default"} />
          <Metric label="Зависли" value={tasks.stalled} tone={tasks.stalled ? "warning" : "default"} />
          <Metric label="Платежей успешно" value={payments.succeeded_24h} />
          <Metric label="Платежей неуспешно" value={payments.failed_24h} tone={payments.failed_24h ? "danger" : "default"} />
        </div>
      </section>

      <section className="dm-admin-band" aria-labelledby="admin-users-title">
        <div className="dm-admin-section-head">
          <Text variant="subheader-2" as="h2" id="admin-users-title">Пользователи и поддержка</Text>
          <Text variant="caption-2" color="secondary">Активность и текущая нагрузка</Text>
        </div>
        <div className="dm-admin-metrics">
          <Metric label="Активны за 24ч" value={users.active_24h} />
          <Metric label="Активны за 7д" value={users.active_7d} />
          <Metric label="Новые за 7д" value={users.new_7d} />
          <Metric label="Открытые обращения" value={support.open} tone={support.open ? "warning" : "default"} />
          <Metric label="Нужен оператор" value={support.needs_human} tone={support.needs_human ? "warning" : "default"} />
          <Metric label="Новые инциденты" value={incidents.new} tone={incidents.new ? "danger" : "default"} />
        </div>
      </section>

      <section className="dm-admin-band" aria-labelledby="admin-integrations-title">
        <div className="dm-admin-section-head">
          <Text variant="subheader-2" as="h2" id="admin-integrations-title">Интеграции</Text>
          <Text variant="caption-2" color="secondary">Состояние определяется только по сигналам приложения</Text>
        </div>
        <div className="dm-admin-integrations">
          {(data?.integrations || []).map((integration) => (
            <div key={integration.name} className="dm-admin-integration">
              <div>
                <strong>{integration.name}</strong>
                <Text variant="caption-2" color="secondary">{integration.signal}: {fmtInt(integration.value)}</Text>
              </div>
              <Label theme={integrationTheme(integration.state)}>{integrationLabel(integration.state)}</Label>
            </div>
          ))}
          {!data?.integrations?.length && <div className="dm-admin-empty">Сигналы интеграций пока не накоплены.</div>}
        </div>
      </section>

      <footer className="dm-admin-control__footer">
        <Text variant="caption-2" color="secondary">Срез сформирован: {fmtDate(data?.generated_at)}</Text>
        <div className="dm-admin-subscriptions">
          <span>Free {fmtInt(subscriptions.free)}</span>
          <span>Pro {fmtInt(subscriptions.pro)}</span>
          <span>Business {fmtInt(subscriptions.business)}</span>
        </div>
      </footer>
    </div>
  );
}
