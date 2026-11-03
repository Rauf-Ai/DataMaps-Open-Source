import { useEffect, useState } from "react";
import { Button, Card, Label, Text, TextInput } from "@gravity-ui/uikit";
import { api, post, fmtDate } from "../api.js";

const STATUS = {
  closed: { label: "закрыт", theme: "normal" },
  needs_human: { label: "ждёт оператора", theme: "warning" },
  open: { label: "открыт", theme: "success" },
};

export function SupportHistory() {
  const [convs, setConvs] = useState([]);
  const [openId, setOpenId] = useState(null);
  const [thread, setThread] = useState(null);
  const [reply, setReply] = useState("");
  const [busy, setBusy] = useState(false);

  const load = () => api("/api/support/my-conversations").then(setConvs).catch(() => setConvs([]));
  useEffect(() => { load(); }, []);

  const openConv = async (id) => {
    if (id === null) { setOpenId(null); setThread(null); return; }
    setOpenId(id); setThread(null); setReply("");
    try { setThread(await api(`/api/support/conversation/${id}`)); } catch { /* */ }
  };

  const sendReply = async () => {
    const text = reply.trim();
    if (!text || busy) return;
    setBusy(true);
    try {
      await post("/api/support/message", { message: text, conversation_id: openId });
      setReply("");
      await openConv(openId);
      load();
    } catch { /* */ } finally { setBusy(false); }
  };

  const closeConv = async (id) => {
    if (!confirm("Отметить обращение решённым и закрыть диалог?")) return;
    try { await post(`/api/support/conversation/${id}/close`, {}); await openConv(id); load(); } catch { /* */ }
  };

  if (!convs.length) return null;

  return (
    <Card view="outlined" theme="normal" className="dm-card">
      <Text variant="caption-2" color="secondary" className="dm-section-caption">Поддержка</Text>
      <Text variant="subheader-2" as="div" style={{ marginBottom: 8 }}>Мои обращения</Text>
      <div className="dm-stack" style={{ gap: 8 }}>
        {convs.map((c) => {
          const st = STATUS[c.status] || STATUS.open;
          return (
            <div key={c.id}>
              <button className="dm-support-admin__conv" style={{ width: "100%" }}
                      onClick={() => openConv(openId === c.id ? null : c.id)}>
                <div className="dm-row" style={{ justifyContent: "space-between", gap: 8 }}>
                  <Text ellipsis>{c.snippet || "Обращение"}</Text>
                  <Label size="s" theme={st.theme}>{st.label}</Label>
                </div>
                <Text variant="caption-2" color="secondary">{fmtDate(c.last_message_at)}</Text>
              </button>

              {openId === c.id && thread && (
                <div className="dm-support-history__thread">
                  {thread.messages.map((m, i) => (
                    <div key={i} className={`dm-support__msg dm-support__msg--${m.role === "user" ? "user" : "bot"}`}>
                      {m.role !== "user" && (
                        <div className="dm-support__op">{m.role === "admin" ? "Оператор" : "Агент"}</div>
                      )}
                      <div className="dm-support__bubble">
                        {m.image_url && <img className="dm-support__img" src={m.image_url} alt="вложение" />}
                        {m.content}
                      </div>
                    </div>
                  ))}
                  {thread.status === "closed" ? (
                    <Text color="secondary" variant="caption-2" style={{ marginTop: 6 }}>
                      Диалог закрыт. Напишите в чат поддержки, чтобы открыть заново.
                    </Text>
                  ) : (
                    <>
                      <div className="dm-row" style={{ marginTop: 8 }}>
                        <TextInput size="m" placeholder="Сообщение…" value={reply} onUpdate={setReply}
                                   onKeyDown={(e) => e.key === "Enter" && sendReply()} disabled={busy} style={{ flex: 1 }} />
                        <Button view="action" size="m" onClick={sendReply} loading={busy} disabled={!reply.trim()}>Отправить</Button>
                      </div>
                      <Button view="flat" size="s" onClick={() => closeConv(c.id)} style={{ marginTop: 6 }}>
                        ✓ Проблема решена
                      </Button>
                    </>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </Card>
  );
}
