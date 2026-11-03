import { useCallback, useEffect, useState } from "react";
import { Button, Label, Loader, Text, TextInput } from "@gravity-ui/uikit";
import { api, fmtDate, fmtInt, fmtRub } from "../../api.js";
import { AdminUserDrawer } from "./AdminUserDrawer.jsx";

function planTheme(code) {
  return code === "business" ? "success" : code === "pro" ? "info" : "normal";
}

export function AdminUsers() {
  const [query, setQuery] = useState("");
  const [users, setUsers] = useState([]);
  const [selectedUserId, setSelectedUserId] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadUsers = useCallback(async (nextQuery = query) => {
    setLoading(true);
    setError("");
    try {
      const suffix = nextQuery.trim() ? `?q=${encodeURIComponent(nextQuery.trim())}` : "";
      setUsers(await api(`/api/admin/users${suffix}`));
    } catch (err) {
      setError(err.message || "Не удалось загрузить пользователей");
    } finally {
      setLoading(false);
    }
  }, [query]);

  useEffect(() => { loadUsers(""); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <section className="dm-admin-control dm-admin-users" aria-label="Пользователи">
      <header className="dm-admin-control__header">
        <div><Text variant="subheader-2" as="h1">Пользователи</Text><Text variant="caption-2" color="secondary">Карточка, лимиты, платежи и журнал действий</Text></div>
        <Text variant="caption-2" color="secondary">Показано: {fmtInt(users.length)}</Text>
      </header>
      <form className="dm-admin-users__search" onSubmit={(event) => { event.preventDefault(); loadUsers(); }}>
        <TextInput aria-label="Поиск пользователей" value={query} onUpdate={setQuery} placeholder="Email пользователя" hasClear />
        <Button view="action" type="submit">Найти</Button>
      </form>
      {error && <div className="dm-admin-control__error" role="alert">{error}<Button view="outlined" size="s" onClick={() => loadUsers()}>Повторить</Button></div>}
      {loading && !users.length ? <div className="dm-admin-control__loading"><Loader size="m" /> <span>Загрузка пользователей…</span></div> : null}
      <div className="dm-admin-users__desktop">
        <table>
          <thead><tr><th>Email</th><th>Тариф</th><th>Баланс</th><th>Потрачено</th><th>Оплачено</th><th>Регистрация</th><th></th></tr></thead>
          <tbody>{users.map((user) => <tr key={user.id}>
            <td><div className="dm-admin-email">{user.email}</div>{user.is_admin && <Label theme="info" size="s">admin</Label>}</td>
            <td><Label theme={planTheme(user.plan_code)}>{user.plan_name || "Бесплатно"}</Label></td>
            <td className="dm-tabular">{fmtInt(user.credits_balance)}</td><td className="dm-tabular">{fmtInt(user.credits_spent)}</td>
            <td className="dm-tabular">{fmtRub(user.total_paid_rub)}</td><td>{fmtDate(user.created_at)}</td>
            <td><Button size="s" view="outlined" onClick={() => setSelectedUserId(user.id)}>Открыть</Button></td>
          </tr>)}</tbody>
        </table>
      </div>
      <div className="dm-admin-users__mobile">{users.map((user) => <article key={user.id} className="dm-admin-user-card">
        <div><strong className="dm-admin-email">{user.email}</strong>{user.is_admin && <Label theme="info" size="s">admin</Label>}</div>
        <div><Label theme={planTheme(user.plan_code)}>{user.plan_name || "Бесплатно"}</Label><span>{fmtInt(user.credits_balance)} кр.</span></div>
        <Button size="m" view="outlined" onClick={() => setSelectedUserId(user.id)}>Открыть</Button>
      </article>)}</div>
      {!loading && !users.length && <div className="dm-admin-empty">Пользователей по этому запросу нет.</div>}
      <AdminUserDrawer userId={selectedUserId} onClose={() => setSelectedUserId(null)} onUpdated={() => loadUsers()} />
    </section>
  );
}
