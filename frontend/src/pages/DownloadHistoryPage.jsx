import { useEffect, useMemo, useState } from "react";
import { Alert, Button, Label, Loader } from "@gravity-ui/uikit";
import { ArrowDownToLine, FileMagnifier, Magnifier } from "@gravity-ui/icons";
import { api, fmtDate, fmtInt } from "../api.js";

const GROUPS = [
  { key: "search", title: "Поиск и выгрузка организаций", icon: Magnifier },
  { key: "company_analysis", title: "Анализ компаний", icon: FileMagnifier },
  { key: "other", title: "Другие файлы", icon: ArrowDownToLine },
];

export function DownloadHistoryPage({ analysisOnly = false }) {
  const [data, setData] = useState(null);
  const [analysisTasks, setAnalysisTasks] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api("/api/me/download-history").then(setData).catch((requestError) => setError(requestError.message));
    if (analysisOnly) api("/api/parser/company-analysis/tasks").then(setAnalysisTasks).catch((requestError) => setError(requestError.message));
  }, [analysisOnly]);
  const groups = useMemo(() => GROUPS
    .filter((group) => !analysisOnly || group.key === "company_analysis")
    .map((group) => ({ ...group, items: (data?.items || []).filter((item) => item.group === group.key) }))
    .filter((group) => group.items.length), [analysisOnly, data]);

  return (
    <main className="dm-history-page">
      <header className="dm-history-page__head">
        <span>{analysisOnly ? "Единый центр анализа" : "Архив DataMaps"}</span>
        <h1>{analysisOnly ? "Анализ компаний" : "История выгрузок"}</h1>
        <p>{analysisOnly
          ? "Все глубокие анализы: отзывы, фото, услуги, сайт и выбранные соцсети. Новый анализ запускается из результата поиска."
          : "Все созданные файлы сгруппированы по назначению и остаются доступны в одном месте."}</p>
      </header>
      {error && <Alert theme="danger" title="Не удалось загрузить историю" message={error} />}
      {!data && !error && <div className="dm-history-page__loading"><Loader size="l" /> Загружаем файлы…</div>}
      {data && (!analysisOnly ? !groups.length : analysisTasks && !analysisTasks.length) && (
        <section className="dm-history-empty">
          <FileMagnifier />
          <strong>{analysisOnly ? "Анализов пока нет" : "Выгрузок пока нет"}</strong>
          <span>{analysisOnly ? "Выберите компании в результатах поиска и нажмите «Анализ компании»." : "После первого поиска готовые файлы появятся здесь."}</span>
        </section>
      )}
      {analysisOnly && analysisTasks?.length > 0 && (
        <section className="dm-history-group dm-analysis-history">
          <header><FileMagnifier /><div><h2>Запуски анализа</h2><span>{fmtInt(analysisTasks.length)} задач</span></div></header>
          <div className="dm-history-list">
            {analysisTasks.map((task) => (
              <article className="dm-history-item" key={task.id}>
                <div className="dm-history-item__main">
                  <strong>{task.status === "done" ? `Проанализировано ${fmtInt(task.org_count)} компаний` : task.message || "Анализ компаний"}</strong>
                  <span>{fmtDate(task.created_at)} · {task.status === "done" ? "Готово" : task.status === "failed" ? "Ошибка" : `${task.progress || 0}%`}</span>
                </div>
                <Label theme={task.status === "done" ? "success" : task.status === "failed" ? "danger" : "info"}>{task.status}</Label>
                <div className="dm-history-item__actions">
                  {task.chat_session_id && <Button view="flat" href={`/?chat=${encodeURIComponent(task.chat_session_id)}`}>Открыть в чате</Button>}
                  {task.xlsx_download_url && <Button view="outlined" href={task.xlsx_download_url}>XLSX</Button>}
                  {task.zip_download_url && <Button view="action" href={task.zip_download_url}><ArrowDownToLine /> Файлы</Button>}
                </div>
              </article>
            ))}
          </div>
        </section>
      )}
      {!analysisOnly && <div className="dm-history-groups">
        {groups.map((group) => {
          const Icon = group.icon;
          return (
            <section className="dm-history-group" key={group.key}>
              <header><Icon /><div><h2>{group.title}</h2><span>{fmtInt(group.items.length)} файлов</span></div></header>
              <div className="dm-history-list">
                {group.items.map((item) => (
                  <article className="dm-history-item" key={item.id}>
                    <div className="dm-history-item__main">
                      <strong>{item.label}</strong>
                      <span>{fmtDate(item.created_at)} · {fmtInt(item.row_count)} организаций</span>
                    </div>
                    <Label theme={item.available ? "success" : "unknown"}>{item.format}</Label>
                    {item.download_url
                      ? <Button view="outlined" href={item.download_url}><ArrowDownToLine /> Скачать</Button>
                      : <span className="dm-history-item__unavailable">Файл недоступен</span>}
                  </article>
                ))}
              </div>
            </section>
          );
        })}
      </div>}
    </main>
  );
}
