import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Alert, Button, Card, Text, TextInput } from "@gravity-ui/uikit";
import { Logo } from "../Logo.jsx";
import { post } from "../api.js";
import { LOCAL_MODE } from "../localMode.js";

export function RegisterPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");
  const [busy, setBusy] = useState(false);
  const nav = useNavigate();

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setErr(""); setOk("");
    try {
      const r = await post("/api/auth/register", { email, password });
      if (r.message && r.message.includes("Войдите")) {
        setOk(r.message);
        setTimeout(() => nav("/login"), 1500);
      } else {
        setOk(r.message || "Письмо отправлено. Проверьте почту.");
      }
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };

  return (
    <main className="dm-auth-shell">
      <Card view="outlined" theme="normal" className="dm-auth-card dm-card">
        <div className="dm-auth-brand">
          <Logo className="dm-auth-logo" />
        </div>
        <Text variant="header-2" as="div">Создать аккаунт</Text>
        <Text variant="body-1" color="secondary" as="div" style={{ marginBottom: 16 }}>
          {LOCAL_MODE ? "Локальный аккаунт нужен для сохранения выгрузок. Оплата и подтверждение email не требуются." : <>После подтверждения email вы получите <b>20 бесплатных кредитов</b>.</>}
        </Text>

        <form onSubmit={submit}>
          <div className="dm-stack" style={{ gap: 12 }}>
            <div>
              <Text variant="body-2">Email</Text>
              <TextInput size="l" type="email" value={email} onUpdate={setEmail} required autoFocus />
            </div>
            <div>
              <Text variant="body-2">Пароль <Text variant="caption-2" color="secondary">(мин. 8 символов)</Text></Text>
              <TextInput size="l" type="password" value={password} onUpdate={setPassword} required minLength={8} />
            </div>
            <Button view="action" size="l" type="submit" loading={busy} width="max">Зарегистрироваться</Button>
          </div>
          {err && <div style={{ marginTop: 12 }}><Alert theme="danger" message={err} /></div>}
          {ok && <div style={{ marginTop: 12 }}><Alert theme="success" message={ok} /></div>}
        </form>

        <Text as="div" color="secondary" style={{ marginTop: 16 }}>
          Уже есть аккаунт? <a href="/login" onClick={(e) => { e.preventDefault(); nav("/login"); }} className="dm-link">Войти</a>
        </Text>
      </Card>
    </main>
  );
}
