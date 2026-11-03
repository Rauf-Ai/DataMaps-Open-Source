import { useEffect, useRef, useState } from "react";
import { Button, Text, TextArea } from "@gravity-ui/uikit";
import { Comment, Xmark, PaperPlane, Paperclip } from "@gravity-ui/icons";
import { api, post } from "../api.js";

const GREETING = {
  role: "assistant",
  content: "Здравствуйте! Я ИИ-помощник поддержки DataMaps. Спросите про тарифы, выгрузки, поиск, анализ отзывов — помогу разобраться.",
};

export function SupportWidget() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState([GREETING]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [convId, setConvId] = useState(null);
  const [awaiting, setAwaiting] = useState(false); // ждём оператора/аппрув
  const bodyRef = useRef(null);
  const pollRef = useRef(null);
  const fileRef = useRef(null);

  const scrollDown = () => {
    requestAnimationFrame(() => {
      if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
    });
  };

  useEffect(scrollDown, [messages, open]);

  // Восстановление истории при загрузке страницы.
  useEffect(() => {
    let alive = true;
    api("/api/support/latest").then((r) => {
      if (!alive) return;
      if (r.conversation_id && Array.isArray(r.messages) && r.messages.length) {
        setConvId(r.conversation_id);
        setMessages(r.messages);
        setAwaiting(r.status === "needs_human");
      }
    }).catch(() => {});
    return () => { alive = false; };
  }, []);

  // Поллинг треда, пока ждём оператора/подтверждения (и не во время отправки).
  useEffect(() => {
    if (!open || !convId || !awaiting || busy) {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
      return;
    }
    pollRef.current = setInterval(async () => {
      try {
        const t = await api(`/api/support/conversation/${convId}`);
        if (Array.isArray(t.messages) && t.messages.length) setMessages(t.messages);
      } catch { /* тихо */ }
    }, 5000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); pollRef.current = null; };
  }, [open, convId, awaiting, busy]);

  const send = async () => {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", content: text }]);
    setBusy(true);
    try {
      const r = await post("/api/support/message", { message: text, conversation_id: convId });
      setConvId(r.conversation_id);
      setMessages((m) => [...m, { role: "assistant", content: r.reply }]);
      setAwaiting(Boolean(r.needs_human || r.pending_action_id));
    } catch (e) {
      setMessages((m) => [...m, { role: "assistant", content: `Ошибка: ${e.message}` }]);
    } finally {
      setBusy(false);
    }
  };

  const uploadFile = async (f) => {
    if (!f || busy) return;
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", f);
      if (convId) fd.append("conversation_id", String(convId));
      const res = await fetch("/api/support/upload", { method: "POST", credentials: "include", body: fd });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Ошибка ${res.status}`);
      }
      const r = await res.json();
      setConvId(r.conversation_id);
      setAwaiting(true);
      const t = await api(`/api/support/conversation/${r.conversation_id}`);
      if (Array.isArray(t.messages)) setMessages(t.messages);
    } catch (e) {
      setMessages((m) => [...m, { role: "assistant", content: `Не удалось загрузить фото: ${e.message}` }]);
    } finally {
      setBusy(false);
    }
  };

  const onPick = (e) => {
    const f = e.target.files && e.target.files[0];
    e.target.value = ""; // чтобы можно было выбрать тот же файл ещё раз
    if (f) uploadFile(f);
  };

  const onKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  };

  return (
    <>
      {!open && (
        <button className="dm-support-fab" onClick={() => setOpen(true)} title="Поддержка" aria-label="Открыть поддержку">
          <Comment />
        </button>
      )}
      {open && (
        <div className="dm-support">
          <div className="dm-support__head">
            <Text variant="subheader-2">Поддержка DataMaps</Text>
            <Button view="flat" size="s" onClick={() => setOpen(false)} aria-label="Закрыть"><Xmark /></Button>
          </div>
          <div className="dm-support__body" ref={bodyRef}>
            {messages.map((m, i) => (
              <div key={i} className={`dm-support__msg dm-support__msg--${m.role === "user" ? "user" : "bot"}`}>
                {m.role === "admin" && <div className="dm-support__op">Оператор</div>}
                <div className="dm-support__bubble">
                  {m.image_url && <img className="dm-support__img" src={m.image_url} alt="вложение" />}
                  {m.content}
                </div>
              </div>
            ))}
            {busy && <div className="dm-support__msg dm-support__msg--bot"><div className="dm-support__bubble dm-support__bubble--typing">…</div></div>}
            {awaiting && !busy && (
              <div className="dm-support__hint">Ожидаем ответа оператора…</div>
            )}
          </div>
          <div className="dm-support__foot">
            <input ref={fileRef} type="file" accept="image/*" hidden onChange={onPick} />
            <Button view="flat" size="l" onClick={() => fileRef.current && fileRef.current.click()}
                    disabled={busy} title="Прикрепить фото" aria-label="Прикрепить фото">
              <Paperclip />
            </Button>
            <TextArea
              value={input}
              onUpdate={setInput}
              onKeyDown={onKey}
              placeholder="Напишите вопрос…"
              minRows={1}
              maxRows={4}
              disabled={busy}
            />
            <Button view="action" size="l" onClick={send} loading={busy} disabled={!input.trim()} aria-label="Отправить">
              <PaperPlane />
            </Button>
          </div>
        </div>
      )}
    </>
  );
}
