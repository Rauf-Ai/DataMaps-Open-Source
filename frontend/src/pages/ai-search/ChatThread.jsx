import { useEffect, useMemo, useRef } from "react";
import { Alert, Button, Progress } from "@gravity-ui/uikit";
import { ArrowUpRightFromSquare } from "@gravity-ui/icons";
import { CompanyAnalysisPlanCard } from "./CompanyAnalysisPlanCard.jsx";
import { SearchPlanCard } from "./SearchPlanCard.jsx";
import { LiveParsingStatus } from "./LiveParsingStatus.jsx";


function messageText(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "object") {
    try {
      return JSON.stringify(value);
    } catch {
      return "Сообщение недоступно";
    }
  }
  return String(value);
}


export function ChatThread({
  messages,
  plan,
  availableOrganizations,
  busy,
  dirty,
  error,
  task,
  analysisTask,
  analysisBusy,
  layoutKey,
  mapPointCount,
  mapResultId,
  onPlanChange,
  onPlanSave,
  onSearch,
  onStartAnalysis,
  onOpenResult,
  onOpenMap,
}) {
  const endRef = useRef(null);
  const latestAssistantId = useMemo(
    () => [...messages].reverse().find((message) => message.role === "assistant")?.id,
    [messages],
  );

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [messages.length, busy, task?.status, task?.found_count, layoutKey]);

  return (
    <div className="dm-chat-thread" aria-live="polite">
      <div className="dm-chat-thread__content">
        {messages.map((message) => (
          <div className={`dm-chat-message is-${message.role}`} key={message.id}>
            {message.role === "assistant" && (
              <div className="dm-chat-message__avatar"><img src="/logo-mark-128.png" alt="DataMaps" /></div>
            )}
            <div className="dm-chat-message__body">
              <div className="dm-chat-message__text">{messageText(message.content)}</div>
              {message.role === "assistant" && message.metadata?.type === "search_completion" && (
                <div className="dm-chat-message__actions">
                  <Button view="flat-action" className="dm-chat-message__action" onClick={() => onOpenResult?.(message.metadata)}>
                    Открыть результат <ArrowUpRightFromSquare />
                  </Button>
                  {mapPointCount > 0 && Number(message.metadata?.saved_result_id) === Number(mapResultId) && <Button view="flat-action" className="dm-chat-message__action" onClick={() => onOpenMap?.()}>
                    Карта · {mapPointCount} {mapPointCount === 1 ? "точка" : mapPointCount < 5 ? "точки" : "точек"}
                  </Button>}
                </div>
              )}
              {message.role === "assistant" && message.metadata?.type === "company_analysis_completion" && message.metadata?.status !== "failed" && message.metadata?.task_id && (
                <Button view="flat-action" className="dm-chat-message__action" onClick={() => onOpenResult?.(message.metadata)}>
                  Открыть анализ <ArrowUpRightFromSquare />
                </Button>
              )}
              {message.role === "assistant" && message.id === latestAssistantId && message.metadata?.type === "company_analysis_config" && (
                <CompanyAnalysisPlanCard
                  metadata={message.metadata}
                  busy={analysisBusy}
                  onStart={onStartAnalysis}
                />
              )}
              {message.role === "assistant" && message.id === latestAssistantId && !message.metadata?.type && (
                <SearchPlanCard
                  plan={plan}
                  availableOrganizations={availableOrganizations}
                  dirty={dirty}
                  busy={busy}
                  onChange={onPlanChange}
                  onSave={onPlanSave}
                  onSearch={onSearch}
                />
              )}
            </div>
          </div>
        ))}
        {busy && (
          <div className="dm-chat-message is-assistant is-typing">
            <div className="dm-chat-message__avatar"><img src="/logo-mark-128.png" alt="" /></div>
            <div className="dm-chat-typing"><i /><i /><i /></div>
          </div>
        )}
        {task && !["done"].includes(task.status) && (
          <>
            <LiveParsingStatus task={task} />
            {task.status === "failed" && (
              <Alert
                theme="danger"
                title="Поиск не выполнен"
                message={task.error || "[MAP-299] Источник карт завершил поиск без результата."}
              />
            )}
          </>
        )}
        {analysisTask && !["done"].includes(analysisTask.status) && (
          <section className={`dm-analysis-progress is-${analysisTask.status}`}>
            <div>
              <span>{analysisTask.status === "failed" ? "Анализ завершился с ошибкой" : analysisTask.message || "Глубокий анализ выполняется"}</span>
              <b>{analysisTask.progress || 0}%</b>
            </div>
            <Progress value={analysisTask.progress || 0} theme={analysisTask.status === "failed" ? "danger" : "info"} />
            {analysisTask.progress_details?.current_company && <small>Сейчас: {analysisTask.progress_details.current_company}</small>}
            {analysisTask.error && <small className="is-error">{analysisTask.error}</small>}
          </section>
        )}
        {error && <Alert theme="danger" title="Не удалось выполнить действие" message={error} />}
        <div ref={endRef} />
      </div>
    </div>
  );
}
