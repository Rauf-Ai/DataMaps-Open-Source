import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@gravity-ui/uikit";
import { useSearchParams } from "react-router-dom";
import { Bars, LayoutSplitSideContentRight, Xmark } from "@gravity-ui/icons";
import { api, post } from "../../api.js";
import { useAuth } from "../../AuthContext.jsx";
import { ChatComposer } from "./ChatComposer.jsx";
import { ChatSidebar } from "./ChatSidebar.jsx";
import { ChatThread } from "./ChatThread.jsx";
import { CompanyAnalysisPanel } from "./CompanyAnalysisPanel.jsx";
import { SearchResultsPanel } from "./SearchResultsPanel.jsx";
import { buildMapData } from "./liveResultsMap.js";


const EMPTY_PLAN = {
  searches: [],
  source: "yandex",
  max: 50,
  target_count: 50,
  f_phone: "any",
  f_site: "any",
  f_franchise: "any",
  social_nets: [],
  reviews_min: 0,
  reviews_max: null,
  rating_min: 0,
  rating_max: 5,
};


export function AiSearchWorkspace() {
  const { me, reload } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestRef = useRef(0);
  const [sessions, setSessions] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [plan, setPlan] = useState(EMPTY_PLAN);
  const [task, setTask] = useState(null);
  const [result, setResult] = useState(null);
  const [liveRows, setLiveRows] = useState([]);
  const [analysisTask, setAnalysisTask] = useState(null);
  const [analysisStarting, setAnalysisStarting] = useState(false);
  const [analysisRetrying, setAnalysisRetrying] = useState(false);
  const [panelMode, setPanelMode] = useState("search");
  const [sending, setSending] = useState(false);
  const [saving, setSaving] = useState(false);
  const [searching, setSearching] = useState(false);
  const [planDirty, setPlanDirty] = useState(false);
  const [error, setError] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [resultsOpen, setResultsOpen] = useState(false);
  const [mobileView, setMobileView] = useState("chat");
  const [mapOpen, setMapOpen] = useState(false);
  const [selectedBusinessId, setSelectedBusinessId] = useState(null);

  const loadSessions = useCallback(async () => {
    try {
      setSessions(await api("/api/chat/sessions"));
    } catch (requestError) {
      setError(requestError.message);
    }
  }, []);

  const loadResult = useCallback(async (resultId, sequence = requestRef.current) => {
    const detail = await api(`/api/live-lab/saved-results/${resultId}`);
    if (sequence === requestRef.current) setResult(detail);
  }, []);

  const liveCursorRef = useRef(0);
  const liveTaskIdRef = useRef(null);
  const loadLiveRows = useCallback(async (taskId, refresh = false, sequence = requestRef.current) => {
    if (!taskId) return [];
    if (refresh) {
      let after = 0;
      const rows = [];
      do {
        const page = await api(`/api/parser/tasks/${taskId}/results?after=${after}`);
        if (sequence !== requestRef.current || liveTaskIdRef.current !== taskId) return [];
        const items = Array.isArray(page.items) ? page.items : [];
        rows.push(...items);
        const nextAfter = Number(page.next_after) || after;
        if (!items.length || nextAfter <= after || items.length < 200) {
          liveCursorRef.current = nextAfter;
          setLiveRows(rows);
          return rows;
        }
        after = nextAfter;
      } while (true);
    }

    const after = liveCursorRef.current;
    const page = await api(`/api/parser/tasks/${taskId}/results?after=${after}`);
    if (sequence !== requestRef.current || liveTaskIdRef.current !== taskId) return [];
    const items = Array.isArray(page.items) ? page.items : [];
    const nextAfter = Number(page.next_after) || after;
    if (items.length && nextAfter > after) {
      liveCursorRef.current = nextAfter;
      setLiveRows((current) => {
        const byPosition = new Map(current.map((row) => [Number(row.position), row]));
        items.forEach((row) => byPosition.set(Number(row.position), row));
        return [...byPosition.values()].sort((a, b) => Number(a.position) - Number(b.position));
      });
    }
    return items;
  }, []);

  const loadTask = useCallback(async (sessionId, sequence = requestRef.current) => {
    try {
      const nextTask = await api(`/api/chat/sessions/${sessionId}/task`);
      if (sequence !== requestRef.current) return;
      if (liveTaskIdRef.current !== nextTask.id) {
        liveTaskIdRef.current = nextTask.id;
        liveCursorRef.current = 0;
        setLiveRows([]);
      }
      setTask(nextTask);
      const streamed = await loadLiveRows(nextTask.id, nextTask.status === "done", sequence);
      if (streamed.length && sequence === requestRef.current) setResultsOpen(true);
      if (nextTask.saved_result_id) {
        await loadResult(nextTask.saved_result_id, sequence);
        if (sequence === requestRef.current) setResultsOpen(true);
      }
    } catch (requestError) {
      if (requestError.status !== 404 && sequence === requestRef.current) setError(requestError.message);
    }
  }, [loadLiveRows, loadResult]);

  const loadAnalysisTask = useCallback(async (taskId, sequence = requestRef.current) => {
    try {
      const nextTask = await api(`/api/parser/company-analysis/${taskId}/status`);
      if (sequence !== requestRef.current) return;
      setAnalysisTask(nextTask);
      if (nextTask.status === "done") {
        setPanelMode("analysis");
        setResultsOpen(true);
      }
      return nextTask;
    } catch (requestError) {
      if (requestError.status !== 404 && sequence === requestRef.current) setError(requestError.message);
    }
  }, []);

  useEffect(() => {
    loadSessions();
  }, [loadSessions]);

  const resetConversation = useCallback(() => {
    requestRef.current += 1;
    setActiveId(null);
    setMessages([]);
    setPlan(EMPTY_PLAN);
    setTask(null);
    setResult(null);
    liveTaskIdRef.current = null;
    liveCursorRef.current = 0;
    setLiveRows([]);
    setAnalysisTask(null);
    setAnalysisStarting(false);
    setAnalysisRetrying(false);
    setPanelMode("search");
    setPlanDirty(false);
    setError("");
    setResultsOpen(false);
    setMobileView("chat");
    setMapOpen(false);
    setSelectedBusinessId(null);
    setSidebarOpen(false);
    setSearchParams({}, { replace: true });
  }, [setSearchParams]);

  const openSession = useCallback(async (sessionId) => {
    const sequence = requestRef.current + 1;
    requestRef.current = sequence;
    setActiveId(sessionId);
    setMessages([]);
    setPlan(EMPTY_PLAN);
    setTask(null);
    setResult(null);
    liveTaskIdRef.current = null;
    liveCursorRef.current = 0;
    setLiveRows([]);
    setAnalysisTask(null);
    setPanelMode("search");
    setError("");
    setPlanDirty(false);
    setResultsOpen(false);
    setMobileView("chat");
    setMapOpen(false);
    setSelectedBusinessId(null);
    setSidebarOpen(false);
    setSearchParams({ chat: String(sessionId) }, { replace: true });
    try {
      const detail = await api(`/api/chat/sessions/${sessionId}`);
      if (sequence !== requestRef.current) return;
      setMessages(detail.messages || []);
      setPlan(detail.plan || EMPTY_PLAN);
      if (detail.parser_task_id) await loadTask(sessionId, sequence);
      if (detail.company_analysis_task_id) {
        await loadAnalysisTask(detail.company_analysis_task_id, sequence);
        const refreshed = await api(`/api/chat/sessions/${sessionId}`);
        if (sequence === requestRef.current) setMessages(refreshed.messages || []);
      }
    } catch (requestError) {
      if (sequence === requestRef.current) setError(requestError.message);
    }
  }, [loadAnalysisTask, loadTask, setSearchParams]);

  useEffect(() => {
    const requested = searchParams.get("chat");
    if (requested && requested !== activeId) openSession(requested);
  }, [activeId, openSession, searchParams]);

  const archiveSession = useCallback(async (sessionId) => {
    try {
      await api(`/api/chat/sessions/${sessionId}`, { method: "DELETE" });
      if (sessionId === activeId) resetConversation();
      await loadSessions();
    } catch (requestError) {
      setError(requestError.message);
    }
  }, [activeId, loadSessions, resetConversation]);

  const sendMessage = useCallback(async (content) => {
    if (sending) return;
    setSending(true);
    setError("");
    const optimistic = { id: `local-${Date.now()}`, role: "user", content };
    setMessages((current) => [...current, optimistic]);
    let sessionId = activeId;
    try {
      if (!sessionId) {
        const created = await post("/api/chat/sessions", {});
        sessionId = created.id;
        setActiveId(sessionId);
      }
      const detail = await post(`/api/chat/sessions/${sessionId}/messages`, { content });
      setMessages(detail.messages || []);
      setPlan(detail.plan || EMPTY_PLAN);
      setPlanDirty(false);
      await loadSessions();
    } catch (requestError) {
      setError(requestError.message);
      if (sessionId) {
        try {
          const detail = await api(`/api/chat/sessions/${sessionId}`);
          setMessages(detail.messages || [optimistic]);
          setPlan(detail.plan || EMPTY_PLAN);
          await loadSessions();
        } catch {
          // Оптимистичное сообщение уже показывает, что именно не удалось обработать.
        }
      }
    } finally {
      setSending(false);
    }
  }, [activeId, loadSessions, sending]);

  const changePlan = useCallback((nextPlan) => {
    setPlan(nextPlan);
    setPlanDirty(true);
  }, []);

  const savePlan = useCallback(async () => {
    if (!activeId || !planDirty) return plan;
    setSaving(true);
    setError("");
    try {
      const detail = await api(`/api/chat/sessions/${activeId}/plan`, {
        method: "PATCH",
        body: JSON.stringify(plan),
      });
      setPlan(detail.plan);
      setPlanDirty(false);
      return detail.plan;
    } catch (requestError) {
      setError(requestError.message);
      throw requestError;
    } finally {
      setSaving(false);
    }
  }, [activeId, plan, planDirty]);

  const startSearch = useCallback(async () => {
    if (!activeId || searching) return;
    setSearching(true);
    setError("");
    try {
      if (planDirty) await savePlan();
      const response = await post(`/api/chat/sessions/${activeId}/search`, {});
      setTask({ id: response.task_id, status: "queued", progress: 0, message: "Поиск поставлен в очередь" });
      setResult(null);
      liveTaskIdRef.current = response.task_id;
      liveCursorRef.current = 0;
      setLiveRows([]);
      setPanelMode("search");
      setResultsOpen(false);
      setMobileView("chat");
      setMapOpen(false);
      setSelectedBusinessId(null);
      setMessages((current) => [
        ...current,
        { id: `task-${response.task_id}`, role: "system", content: "Поиск запущен" },
      ]);
      await loadSessions();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setSearching(false);
    }
  }, [activeId, loadSessions, planDirty, savePlan, searching]);

  useEffect(() => {
    if (!activeId || !task?.id || ["done", "failed"].includes(task.status)) return undefined;
    let cancelled = false;
    const poll = async () => {
      try {
        const nextTask = await api(`/api/chat/sessions/${activeId}/task`);
        if (cancelled) return;
        if (liveTaskIdRef.current !== nextTask.id) {
          liveTaskIdRef.current = nextTask.id;
          liveCursorRef.current = 0;
          setLiveRows([]);
        }
        setTask(nextTask);
        const streamed = await loadLiveRows(nextTask.id, nextTask.status === "done");
        if (cancelled) return;
        if (streamed.length) {
          setResultsOpen(true);
          setMobileView("results");
        }
        if (nextTask.saved_result_id) {
          await loadResult(nextTask.saved_result_id);
          setResultsOpen(true);
          setMobileView("results");
          await reload();
        }
        if (["done", "failed"].includes(nextTask.status)) {
          const detail = await api(`/api/chat/sessions/${activeId}`);
          if (!cancelled) setMessages(detail.messages || []);
          await loadSessions();
        }
      } catch (requestError) {
        if (!cancelled) setError(requestError.message);
      }
    };
    poll();
    const interval = window.setInterval(poll, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [activeId, loadLiveRows, loadResult, loadSessions, reload, task?.id, task?.status]);

  const requestAnalysis = useCallback(async (businessIds) => {
    if (!activeId || !result?.id || analysisStarting) return;
    setAnalysisStarting(true);
    setError("");
    try {
      const detail = await post(`/api/chat/sessions/${activeId}/analysis-intent`, {
        saved_result_id: result.id,
        business_ids: businessIds,
      });
      setMessages(detail.messages || []);
      setResultsOpen(false);
      setMobileView("chat");
      await loadSessions();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setAnalysisStarting(false);
    }
  }, [activeId, analysisStarting, loadSessions, result?.id]);

  const startAnalysis = useCallback(async (payload) => {
    if (!activeId || analysisStarting) return;
    setAnalysisStarting(true);
    setError("");
    try {
      const created = await post(`/api/chat/sessions/${activeId}/company-analysis`, payload);
      setAnalysisTask({
        id: created.task_id,
        status: "queued",
        progress: 0,
        org_count: created.org_count,
        message: "Анализ поставлен в очередь",
      });
      const detail = await api(`/api/chat/sessions/${activeId}`);
      setMessages(detail.messages || []);
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setAnalysisStarting(false);
    }
  }, [activeId, analysisStarting]);

  useEffect(() => {
    if (!activeId || !analysisTask?.id || ["done", "failed"].includes(analysisTask.status)) return undefined;
    let cancelled = false;
    const poll = async () => {
      try {
        const nextTask = await api(`/api/parser/company-analysis/${analysisTask.id}/status`);
        if (cancelled) return;
        setAnalysisTask(nextTask);
        if (["done", "failed"].includes(nextTask.status)) {
          const detail = await api(`/api/chat/sessions/${activeId}`);
          if (cancelled) return;
          setMessages(detail.messages || []);
          if (nextTask.status === "done") {
            setPanelMode("analysis");
            setResultsOpen(true);
            setMobileView("results");
          }
          await Promise.all([loadSessions(), reload()]);
        }
      } catch (requestError) {
        if (!cancelled) setError(requestError.message);
      }
    };
    poll();
    const interval = window.setInterval(poll, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [activeId, analysisTask?.id, analysisTask?.status, loadSessions, reload]);

  const retryAnalysisSummary = useCallback(async () => {
    if (!analysisTask?.id || analysisRetrying) return;
    setAnalysisRetrying(true);
    setError("");
    try {
      await post(`/api/parser/company-analysis/${analysisTask.id}/retry-summary`, {});
      await loadAnalysisTask(analysisTask.id);
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setAnalysisRetrying(false);
    }
  }, [analysisRetrying, analysisTask?.id, loadAnalysisTask]);

  const openMessageResult = useCallback(async (metadata) => {
    setError("");
    try {
      if (
        (metadata?.action === "open_company_analysis" || metadata?.type === "company_analysis_completion")
        && metadata.task_id
      ) {
        await loadAnalysisTask(metadata.task_id);
        setPanelMode("analysis");
      } else if (metadata?.type === "search_completion") {
        if (metadata.saved_result_id) await loadResult(metadata.saved_result_id);
        if (activeId) await loadTask(activeId);
        setPanelMode("search");
      } else {
        return;
      }
      setResultsOpen(true);
      setMobileView("results");
    } catch (requestError) {
      setError(requestError.message);
    }
  }, [activeId, loadAnalysisTask, loadResult, loadTask]);

  const openMap = useCallback(() => {
    setPanelMode("search");
    setResultsOpen(true);
    setMapOpen(true);
    setMobileView("results");
  }, []);

  const selectBusiness = useCallback((businessId) => {
    setSelectedBusinessId(businessId);
    openMap();
  }, [openMap]);

  const hasConversation = messages.length > 0;
  const showSearchResults = panelMode === "search" && (liveRows.length > 0 || (task?.status === "done" && Boolean(result)));
  const showAnalysisResults = panelMode === "analysis" && Boolean(analysisTask);
  const showResults = resultsOpen && (showSearchResults || showAnalysisResults);
  const busy = sending || saving || searching;
  const analysisBusy = analysisStarting || Boolean(analysisTask && !["done", "failed"].includes(analysisTask.status));
  const mapPointCount = useMemo(() => buildMapData(result?.items).mappableCount, [result?.items]);

  return (
    <main className={`dm-ai-workspace${showResults ? " has-results" : ""}`}>
      <ChatSidebar
        sessions={sessions}
        activeId={activeId}
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        onNew={resetConversation}
        onSelect={openSession}
        onArchive={archiveSession}
      />

      <section className={`dm-chat-main${mobileView === "results" ? " is-mobile-hidden" : ""}`}>
        <div className="dm-chat-mobile-head">
          <Button view="flat" onClick={() => setSidebarOpen(true)} aria-label="Открыть историю"><Bars /></Button>
          {(result || analysisTask?.status === "done") && <Button view="flat" onClick={() => { setPanelMode(analysisTask?.status === "done" ? "analysis" : "search"); setResultsOpen(true); setMobileView("results"); }}><LayoutSplitSideContentRight /> {analysisTask?.status === "done" ? "Анализ" : "Результаты"}</Button>}
        </div>
        {!hasConversation ? (
          <div className="dm-chat-empty">
            <ChatComposer centered onSend={sendMessage} disabled={sending} />
          </div>
        ) : (
          <>
            <ChatThread
              messages={messages}
              plan={plan}
              availableOrganizations={me?.subscription?.quotas?.organizations?.available ?? me?.credits_balance ?? 0}
              busy={sending}
              dirty={planDirty}
              error={error}
              task={task}
              analysisTask={analysisTask}
              analysisBusy={analysisBusy}
              layoutKey={showResults}
              mapPointCount={mapPointCount}
              mapResultId={result?.id}
              onPlanChange={changePlan}
              onPlanSave={savePlan}
              onSearch={startSearch}
              onStartAnalysis={startAnalysis}
              onOpenResult={openMessageResult}
              onOpenMap={openMap}
            />
            <ChatComposer onSend={sendMessage} disabled={sending} />
          </>
        )}
      </section>

      {showResults && (
        <div className={`dm-results-shell${mobileView === "chat" ? " is-mobile-hidden" : ""}`}>
          <div className="dm-results-mobile-head">
            <Button view="flat" onClick={() => setMobileView("chat")}><Xmark /> Вернуться в чат</Button>
          </div>
          {showSearchResults && <SearchResultsPanel task={task} result={result} liveRows={liveRows} analysisBusy={analysisStarting} mapOpen={mapOpen} selectedBusinessId={selectedBusinessId} onMapOpenChange={setMapOpen} onSelectBusiness={selectBusiness} onRequestAnalysis={requestAnalysis} onClose={() => { setResultsOpen(false); setMobileView("chat"); }} />}
          {showAnalysisResults && <CompanyAnalysisPanel task={analysisTask} retrying={analysisRetrying} onRetrySummary={retryAnalysisSummary} onClose={() => { setResultsOpen(false); setMobileView("chat"); }} />}
        </div>
      )}
    </main>
  );
}
