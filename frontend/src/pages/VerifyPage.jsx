import { useEffect, useState } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import { Button, Card, Text, Loader } from "@gravity-ui/uikit";
import { CircleCheck, CircleExclamation } from "@gravity-ui/icons";
import { api } from "../api.js";
import { useAuth } from "../AuthContext.jsx";

export function VerifyPage() {
  const [sp] = useSearchParams();
  const [state, setState] = useState("loading");
  const [msg, setMsg] = useState("");
  const { reload } = useAuth();
  const nav = useNavigate();

  useEffect(() => {
    const token = sp.get("token");
    if (!token) { setState("error"); setMsg("Нет токена в ссылке."); return; }
    (async () => {
      try {
        await api(`/api/auth/verify?token=${encodeURIComponent(token)}`);
        await reload();
        setState("ok");
      } catch (e) { setState("error"); setMsg(e.message); }
    })();
  }, [sp, reload]);

  return (
    <main className="dm-auth-shell">
      <Card view="outlined" theme="normal" className="dm-auth-card dm-card" style={{ textAlign: "center" }}>
        {state === "loading" && (
          <div className="dm-inline" style={{ justifyContent: "center" }}>
            <Loader size="s" /> <Text color="secondary">Подтверждаем…</Text>
          </div>
        )}
        {state === "ok" && (
          <>
            <div style={{ width: 48, height: 48, borderRadius: "50%", background: "var(--g-color-base-positive-light)", color: "var(--g-color-text-positive)", display: "grid", placeItems: "center", margin: "0 auto 12px" }}>
              <CircleCheck width={24} height={24} />
            </div>
            <Text variant="header-2" as="div">Email подтверждён</Text>
            <Text color="secondary" as="div" style={{ marginTop: 4, marginBottom: 16 }}>Зачислены 20 бесплатных кредитов.</Text>
            <Button view="action" size="l" onClick={() => nav("/dashboard")}>Перейти в кабинет</Button>
          </>
        )}
        {state === "error" && (
          <>
            <div style={{ width: 48, height: 48, borderRadius: "50%", background: "var(--g-color-base-danger-light)", color: "var(--g-color-text-danger)", display: "grid", placeItems: "center", margin: "0 auto 12px" }}>
              <CircleExclamation width={24} height={24} />
            </div>
            <Text variant="header-2" as="div">Не получилось</Text>
            <Text color="secondary" as="div" style={{ marginTop: 4, marginBottom: 16 }}>{msg}</Text>
            <Button view="outlined" size="l" onClick={() => nav("/login")}>На страницу входа</Button>
          </>
        )}
      </Card>
    </main>
  );
}
