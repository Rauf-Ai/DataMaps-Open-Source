import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Button, Card, Text, TextInput, Alert } from "@gravity-ui/uikit";
import { post } from "../api.js";
import { useAuth } from "../AuthContext.jsx";
import { Logo } from "../Logo.jsx";

export function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const nav = useNavigate();
  const [sp] = useSearchParams();
  const { reload } = useAuth();

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      await post("/api/auth/login", { email, password });
      await reload();
      nav(sp.get("next") || "/");
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };

  return (
    <main className="dm-auth-shell">
      <Card view="outlined" theme="normal" className="dm-auth-card dm-card">
        <div className="dm-auth-brand">
          <Logo className="dm-auth-logo" />
        </div>
        <Text variant="header-2" as="div">Вход</Text>
        <Text variant="body-1" color="secondary" as="div" style={{ marginBottom: 16 }}>Войдите, чтобы выгружать данные.</Text>

        <form onSubmit={submit}>
          <div className="dm-stack" style={{ gap: 12 }}>
            <div>
              <Text variant="body-2">Email</Text>
              <TextInput size="l" type="email" value={email} onUpdate={setEmail} required autoFocus />
            </div>
            <div>
              <Text variant="body-2">Пароль</Text>
              <TextInput size="l" type="password" value={password} onUpdate={setPassword} required />
            </div>
            <Button view="action" size="l" type="submit" loading={busy} width="max">Войти</Button>
          </div>
          {err && <div style={{ marginTop: 12 }}><Alert theme="danger" message={err} /></div>}
        </form>

        <div className="dm-row" style={{ justifyContent: "space-between", marginTop: 16 }}>
          <a href="/register" onClick={(e) => { e.preventDefault(); nav("/register"); }} className="dm-link">Регистрация</a>
          <a href="/reset" onClick={(e) => { e.preventDefault(); nav("/reset"); }} className="dm-link">Забыли пароль?</a>
        </div>
      </Card>
    </main>
  );
}
