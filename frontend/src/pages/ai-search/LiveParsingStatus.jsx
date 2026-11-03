import { Progress } from "@gravity-ui/uikit";
import { fmtInt } from "../../api.js";


function sourceName(source) {
  const value = String(source || "").toLowerCase();
  if (value.includes("yandex") || value.includes("янд")) return "Яндекс Карты";
  if (value.includes("google")) return "Google Maps";
  return source || "Карты";
}


export function LiveParsingStatus({ task }) {
  const found = Number(task?.found_count ?? task?.org_count ?? 0);
  const target = Math.max(1, Number(task?.target_count || 0));
  const checked = Number(task?.checked_count || 0);
  const progress = task?.status === "done" ? 100 : Math.min(99, Math.round(found * 100 / target));
  const recent = Array.isArray(task?.recent_organizations) ? task.recent_organizations : [];
  const active = !["done", "failed"].includes(task?.status);

  return (
    <section className={`dm-live-scan is-${task?.status || "queued"}`} aria-label="Ход поиска организаций">
      <div className="dm-live-scan__beam" aria-hidden="true" />
      <header className="dm-live-scan__head">
        <div className="dm-live-scan__signal" aria-hidden="true"><i /><i /><i /></div>
        <div>
          <strong>{active ? "Ищем организации в реальном времени" : task?.status === "done" ? "Поиск завершён" : "Поиск остановлен"}</strong>
          <span>{task?.message || "Подключаемся к источникам карт"}</span>
        </div>
        <b>{fmtInt(found)} <small>/ {fmtInt(target)}</small></b>
      </header>
      <Progress value={progress} theme={task?.status === "failed" ? "danger" : task?.status === "done" ? "success" : "info"} />
      <div className="dm-live-scan__meta">
        <span>Найдено: <b>{fmtInt(found)}</b></span>
        <span>Проверено карточек: <b>{fmtInt(checked)}</b></span>
        <span>Готово: <b>{progress}%</b></span>
      </div>
      {recent.length > 0 && (
        <div className="dm-live-scan__feed">
          {recent.slice().reverse().map((organization, index) => (
            <div className="dm-live-scan__item" style={{ "--delay": `${index * 45}ms` }} key={`${organization.id || organization.name}-${index}`}>
              <i aria-hidden="true" />
              <div>
                <strong>{organization.name || "Организация"}</strong>
                <span>{[organization.city, sourceName(organization.source)].filter(Boolean).join(" · ")}</span>
              </div>
              {organization.rating ? <b>★ {organization.rating}</b> : null}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
