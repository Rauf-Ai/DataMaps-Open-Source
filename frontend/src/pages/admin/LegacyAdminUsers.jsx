import { useEffect, useState } from "react";
import { Button, Label, Loader, Modal, Select, Text, TextInput } from "@gravity-ui/uikit";
import { api, fmtDate, fmtInt, fmtRub, post } from "../../api.js";

export function LegacyAdminUsers() {
  const [query, setQuery] = useState("");
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [managed, setManaged] = useState(null);
  const [form, setForm] = useState({ plan_code: "free", credit_delta: "", blocked: false, comment: "" });
  const [saving, setSaving] = useState(false);

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const suffix = query.trim() ? `?q=${encodeURIComponent(query.trim())}` : "";
      setUsers(await api(`/api/admin/users${suffix}`));
    } catch (err) {
      setError(err.message || "Не удалось загрузить пользователей");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const open = (user) => {
    setManaged(user);
    setForm({ plan_code: user.plan_code || "free", credit_delta: "", blocked: Boolean(user.is_blocked), comment: "" });
    setError("");
  };

  const save = async () => {
    const comment = form.comment.trim();
    const delta = form.credit_delta.trim() ? Number(form.credit_delta) : 0;
    if (!comment) { setError("Введите комментарий к изменению"); return; }
    if (!Number.isInteger(delta)) { setError("Кредиты должны быть целым числом"); return; }
    const planChanged = form.plan_code !== managed.plan_code;
    const blockChanged = form.blocked !== Boolean(managed.is_blocked);
    if (!planChanged && !blockChanged && delta === 0) { setError("Нет изменений"); return; }
    setSaving(true);
    setError("");
    try {
      if (planChanged) await post(`/api/admin/users/${managed.id}/plan`, { plan_code: form.plan_code, comment });
      if (delta !== 0) await post(`/api/admin/users/${managed.id}/credits`, { delta, comment });
      if (blockChanged) await post(`/api/admin/users/${managed.id}/block`, { blocked: form.blocked, comment });
      await load();
      setManaged(null);
    } catch (err) {
      setError(err.message || "Не удалось сохранить изменения");
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="dm-admin-control" aria-label="Пользователи">
      <header className="dm-admin-control__header"><div><Text variant="subheader-2" as="h1">Пользователи</Text><Text color="secondary">Тарифы и баланс</Text></div></header>
      <form className="dm-admin-users__search" onSubmit={(event) => { event.preventDefault(); load(); }}>
        <TextInput value={query} onUpdate={setQuery} placeholder="Поиск по email" hasClear />
        <Button view="action" type="submit">Найти</Button>
      </form>
      {error && !managed && <div className="dm-admin-control__error" role="alert">{error}</div>}
      {loading && !users.length && <div className="dm-admin-control__loading"><Loader size="m" /> Загрузка…</div>}
      <div className="dm-admin-users__desktop"><table><thead><tr><th>Email</th><th>Тариф</th><th>Баланс</th><th>Потрачено</th><th>Оплачено</th><th>Регистрация</th><th /></tr></thead><tbody>
        {users.map((user) => <tr key={user.id}><td className="dm-admin-email">{user.email} {user.is_admin && <Label theme="info">admin</Label>}</td><td>{user.plan_name || "Бесплатно"}</td><td>{fmtInt(user.credits_balance)}</td><td>{fmtInt(user.credits_spent)}</td><td>{fmtRub(user.total_paid_rub)}</td><td>{fmtDate(user.created_at)}</td><td><Button view="outlined" size="s" onClick={() => open(user)}>Управление</Button></td></tr>)}
      </tbody></table></div>
      <div className="dm-admin-users__mobile">{users.map((user) => <article className="dm-admin-user-card" key={user.id}><strong className="dm-admin-email">{user.email}</strong><span>{user.plan_name || "Бесплатно"} · {fmtInt(user.credits_balance)} кр.</span><Button view="outlined" onClick={() => open(user)}>Управление</Button></article>)}</div>
      {!loading && !users.length && <div className="dm-admin-empty">Пользователи не найдены.</div>}
      <Modal open={Boolean(managed)} onClose={() => !saving && setManaged(null)}>
        {managed && <section className="dm-admin-task-dialog" aria-label="Управление пользователем">
          <header><div><Text variant="subheader-2" as="h2">{managed.email}</Text><Text color="secondary">Все изменения попадут в аудит</Text></div><Button view="flat" onClick={() => setManaged(null)} disabled={saving}>×</Button></header>
          <label className="dm-admin-field"><span>Тариф</span><Select value={[form.plan_code]} onUpdate={([plan_code]) => setForm((value) => ({ ...value, plan_code }))}><Select.Option value="free">Бесплатно</Select.Option><Select.Option value="pro">Про</Select.Option><Select.Option value="business">Бизнес</Select.Option></Select></label>
          <label className="dm-admin-field"><span>Изменить кредиты</span><TextInput type="number" value={form.credit_delta} onUpdate={(credit_delta) => setForm((value) => ({ ...value, credit_delta }))} placeholder="Например, 100 или -25" /></label>
          <label className="dm-admin-checkbox"><input type="checkbox" checked={form.blocked} onChange={(event) => setForm((value) => ({ ...value, blocked: event.target.checked }))} /> Заблокировать пользователя</label>
          <label className="dm-admin-field"><span>Комментарий</span><TextInput value={form.comment} onUpdate={(comment) => setForm((value) => ({ ...value, comment }))} placeholder="Причина изменения" /></label>
          {error && <div className="dm-admin-control__inline-error" role="alert">{error}</div>}
          <div className="dm-admin-confirmation__actions"><Button view="flat" onClick={() => setManaged(null)} disabled={saving}>Отмена</Button><Button view="action" onClick={save} loading={saving}>Сохранить</Button></div>
        </section>}
      </Modal>
    </section>
  );
}
