import { useState } from "react";
import { Button, Text, TextArea } from "@gravity-ui/uikit";
import { post } from "../api.js";
import { SupportHistory } from "../components/SupportHistory.jsx";


export function SupportPage() {
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");

  const send = async () => {
    const text = message.trim();
    if (!text || busy) return;
    setBusy(true); setStatus("");
    try {
      const result = await post("/api/support/message", { message: text });
      setMessage("");
      setStatus(result.needs_human ? "Обращение передано менеджеру." : result.message || "Ответ получен. Обновите историю ниже.");
      window.location.reload();
    } catch (error) { setStatus(error.message); }
    finally { setBusy(false); }
  };

  return <main className="dm-container dm-support-page">
    <div className="dm-page-heading"><div><Text variant="header-1" as="h1">Поддержка</Text><Text color="secondary">Сначала ответит AI-агент, при необходимости подключится менеджер.</Text></div></div>
    <section className="dm-account__section dm-account__narrow">
      <Text variant="subheader-2" as="h2">Новое обращение</Text>
      <TextArea minRows={4} maxRows={8} value={message} onUpdate={setMessage} placeholder="Опишите вопрос или ошибку" />
      <Button view="action" size="l" onClick={send} loading={busy} disabled={!message.trim()}>Отправить</Button>
      {status && <Text color="secondary">{status}</Text>}
    </section>
    <SupportHistory />
  </main>;
}
