import { useCallback, useEffect, useState } from "react";
import { Navigate, Outlet, useLocation, useNavigate } from "react-router-dom";
import { Button } from "@gravity-ui/uikit";
import { ArrowLeft, Bars } from "@gravity-ui/icons";
import { api } from "../api.js";
import { useAuth } from "../AuthContext.jsx";
import { ChatSidebar } from "../pages/ai-search/ChatSidebar.jsx";


const SECTION_TITLES = {
  "/dashboard": "Личный кабинет",
  "/analysis": "Анализ компаний",
  "/downloads": "История выгрузок",
  "/support": "Поддержка",
  "/admin": "Администрирование",
  "/live-lab": "Сохранённые результаты",
};


export function WorkspaceLayout() {
  const { me, loading } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [sessions, setSessions] = useState([]);
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const loadSessions = useCallback(async () => {
    if (!me) return;
    try { setSessions(await api("/api/chat/sessions")); }
    catch { setSessions([]); }
  }, [me]);

  useEffect(() => { loadSessions(); }, [loadSessions]);

  const goHome = () => {
    setSidebarOpen(false);
    navigate("/");
  };
  const openSession = (id) => {
    setSidebarOpen(false);
    navigate(`/?chat=${encodeURIComponent(id)}`);
  };
  const archiveSession = async (id) => {
    await api(`/api/chat/sessions/${id}`, { method: "DELETE" });
    await loadSessions();
  };

  if (loading) return null;
  if (!me) return <Navigate to={`/login?next=${encodeURIComponent(location.pathname)}`} replace />;

  return (
    <div className="dm-workspace-layout">
      <ChatSidebar
        sessions={sessions}
        activeId={null}
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        onNew={goHome}
        onSelect={openSession}
        onArchive={archiveSession}
      />
      <section className="dm-workspace-content">
        <header className="dm-workspace-content__bar">
          <Button view="flat" className="dm-workspace-content__menu" onClick={() => setSidebarOpen(true)} aria-label="Открыть меню">
            <Bars />
          </Button>
          <Button view="flat" onClick={goHome} aria-label="Вернуться к поиску">
            <ArrowLeft /> <span>К поиску</span>
          </Button>
          <span>{SECTION_TITLES[location.pathname] || "DataMaps"}</span>
        </header>
        <div className="dm-workspace-content__scroll">
          <Outlet />
        </div>
      </section>
    </div>
  );
}
