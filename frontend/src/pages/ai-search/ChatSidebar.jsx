import { memo } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Button } from "@gravity-ui/uikit";
import {
  Archive,
  ChartColumn,
  ClockArrowRotateLeft,
  Comment,
  Gear,
  Moon,
  PencilToSquare,
  Sun,
  Xmark,
} from "@gravity-ui/icons";
import { Logo } from "../../Logo.jsx";
import { fmtDate, fmtInt } from "../../api.js";
import { useAuth } from "../../AuthContext.jsx";
import { useAppTheme } from "../../main.jsx";


export const ChatSidebar = memo(function ChatSidebar({
  sessions,
  activeId,
  open,
  onClose,
  onNew,
  onSelect,
  onArchive,
}) {
  const navigate = useNavigate();
  const location = useLocation();
  const { me } = useAuth();
  const { theme, toggle } = useAppTheme();
  const organizationsQuota = me?.subscription?.quotas?.organizations;
  const availableOrganizations = Math.max(0, Number(organizationsQuota?.available ?? me?.credits_balance ?? 0));
  const quotaCapacity = Math.max(
    availableOrganizations,
    Number(organizationsQuota?.limit ?? 0) + Number(organizationsQuota?.extra ?? 0),
    1,
  );
  const balancePercent = Math.max(0, Math.min(100, (availableOrganizations / quotaCapacity) * 100));
  const balanceTone = balancePercent <= 20 ? "critical" : balancePercent <= 50 ? "warning" : "healthy";

  const go = (path) => {
    onClose();
    navigate(path);
  };

  return (
    <>
      <aside className={`dm-chat-sidebar${open ? " is-open" : ""}`} aria-label="История запросов">
        <div className="dm-chat-sidebar__head">
          <button className="dm-chat-sidebar__logo" onClick={onNew} aria-label="DataMaps — новый чат">
            <Logo className="dm-chat-sidebar__logo-img" />
          </button>
          <Button view="flat" size="m" onClick={onClose} className="dm-chat-sidebar__close" aria-label="Закрыть меню">
            <Xmark />
          </Button>
        </div>

        <Button view="outlined" size="l" width="max" onClick={onNew} className="dm-chat-sidebar__new">
          <PencilToSquare /> Новый чат
        </Button>

        <div className="dm-chat-sidebar__section-title">История</div>
        <div className="dm-chat-sidebar__history">
          {!sessions.length && <div className="dm-chat-sidebar__empty">Запросов пока нет</div>}
          {sessions.map((session) => (
            <div className={`dm-chat-history-item${session.id === activeId ? " is-active" : ""}`} key={session.id}>
              <button
                className="dm-chat-history-item__main"
                onClick={() => onSelect(session.id)}
                title={`${session.title}\n${fmtDate(session.updated_at)}`}
              >
                <span>{session.title}</span>
              </button>
              <button
                className="dm-chat-history-item__archive"
                onClick={() => onArchive(session.id)}
                aria-label={`Удалить из истории: ${session.title}`}
                title="Удалить из истории"
              >
                <Archive />
              </button>
            </div>
          ))}
        </div>

        <div className="dm-chat-sidebar__footer">
          <button className={location.pathname === "/analysis" ? "is-active" : ""} onClick={() => go("/analysis")}><ChartColumn /> Анализ компаний</button>
          <button className={location.pathname === "/downloads" ? "is-active" : ""} onClick={() => go("/downloads")}><ClockArrowRotateLeft /> История выгрузок</button>
          <button className={location.pathname === "/support" ? "is-active" : ""} onClick={() => go("/support")}><Comment /> Поддержка</button>
          {me?.is_admin && <button className={location.pathname === "/admin" ? "is-active" : ""} onClick={() => go("/admin")}><Gear /> Админка</button>}
          <div className="dm-chat-sidebar__account">
            <div className="dm-chat-sidebar__balance">
              <span>{me?.subscription?.local_mode ? "Режим" : "Баланс"}</span>
              <strong>{me?.subscription?.local_mode ? "Локально · бесплатно" : `${fmtInt(availableOrganizations)} орг.`}</strong>
            </div>
            {!me?.subscription?.local_mode && <div
              className={`dm-chat-sidebar__balance-meter is-${balanceTone}`}
              role="progressbar"
              aria-label="Остаток организаций"
              aria-valuemin="0"
              aria-valuemax="100"
              aria-valuenow={Math.round(balancePercent)}
              title={`Осталось ${Math.round(balancePercent)}% месячного объёма`}
            >
              <i style={{ width: `${balancePercent}%` }} />
            </div>}
            <div className="dm-chat-sidebar__profile">
              <button className={`dm-chat-sidebar__profile-main${location.pathname === "/dashboard" ? " is-active" : ""}`} onClick={() => go("/dashboard")} title={me?.email}>
                <span className="dm-chat-sidebar__avatar">{me?.email?.slice(0, 1).toUpperCase()}</span>
                <span>{me?.email}</span>
              </button>
              <button className="dm-chat-sidebar__theme" onClick={toggle} aria-label={theme === "dark" ? "Включить светлую тему" : "Включить тёмную тему"} title={theme === "dark" ? "Светлая тема" : "Тёмная тема"}>
                {theme === "dark" ? <Sun /> : <Moon />}
              </button>
            </div>
          </div>
        </div>
      </aside>
      {open && <button className="dm-chat-sidebar-backdrop" onClick={onClose} aria-label="Закрыть меню" />}
    </>
  );
});
