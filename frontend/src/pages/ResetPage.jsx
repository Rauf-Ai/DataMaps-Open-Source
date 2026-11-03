import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Alert, Button, Card, Text, TextInput } from "@gravity-ui/uikit";
import { Logo } from "../Logo.jsx";
import { post } from "../api.js";

export function ResetPage() {
  const [sp] = useSearchParams();
  const nav = useNavigate();
  const [token, setToken] = useState("");
  const [mode, setMode] = useState("request");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [ok, setOk] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const t = sp.get("token");
    if (t) { setToken(t); setMode("confirm"); }
  }, [sp]);

  const doRequest = async (e) => {
    e.preventDefault();
    setBusy(true); setOk(""); setErr("");
    try {
      const r = await post("/api/auth/password-reset/request", { email });
      setOk(r.message || "Если email зарегистрирован — письмо отправлено.");
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };

  const doConfirm = async (e) => {
    e.preventDefault();
    setBusy(true); setOk(""); setErr("");
    try {
      await post("/api/auth/password-reset/confirm", { token, password });
      setOk("Пароль обновлён. Перенаправляем…");
      setTimeout(() => nav("/login"), 1200);
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };

  return (
    <main className="dm-auth-shell">
      <Card view="outlined" theme="normal" className="dm-auth-card dm-card">
        <div className="dm-auth-brand">
          <Logo className="dm-auth-logo" />
        </div>

        {mode === "request" && (
          <form onSubmit={doRequest}>
            <Text variant="header-2" as="div">Восстановление пароля</Text>
            <Text color="secondary" as="div" style={{ marginTop: 4, marginBottom: 16 }}>Введите email — мы пришлём ссылку.</Text>
            <TextInput size="l" type="email" value={email} onUpdate={setEmail} placeholder="you@example.com" required autoFocus />
            <Button view="action" size="l" type="submit" loading={busy} width="max" style={{ marginTop: 12 }}>Отправить</Button>
            {ok && <div style={{ marginTop: 12 }}><Alert theme="success" message={ok} /></div>}
            {err && <div style={{ marginTop: 12 }}><Alert theme="danger" message={err} /></div>}
          </form>
        )}

        {mode === "confirm" && (
          <form onSubmit={doConfirm}>
            <Text variant="header-2" as="div">Новый пароль</Text>
            <Text color="secondary" as="div" style={{ marginTop: 4, marginBottom: 16 }}>Минимум 8 символов.</Text>
            <TextInput size="l" type="password" value={password} onUpdate={setPassword} placeholder="новый пароль" required minLength={8} autoFocus />
            <Button view="action" size="l" type="submit" loading={busy} width="max" style={{ marginTop: 12 }}>Сохранить</Button>
            {ok && <div style={{ marginTop: 12 }}><Alert theme="success" message={ok} /></div>}
            {err && <div style={{ marginTop: 12 }}><Alert theme="danger" message={err} /></div>}
          </form>
        )}

        <Text as="div" color="secondary" style={{ marginTop: 16, textAlign: "center" }}>
          <a href="/login" onClick={(e) => { e.preventDefault(); nav("/login"); }} className="dm-link">Вернуться к входу</a>
        </Text>
      </Card>
    </main>
  );
}
