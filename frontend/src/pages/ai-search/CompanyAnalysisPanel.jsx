import { useEffect, useMemo, useState } from "react";
import { Alert, Button, Progress } from "@gravity-ui/uikit";
import { ArrowDownToLine, ArrowRotateRight, Xmark } from "@gravity-ui/icons";
import { fmtInt } from "../../api.js";


function textValue(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.map(textValue).filter((item) => item !== "—").join(", ") || "—";
  if (typeof value === "object") {
    for (const key of ["text", "title", "name", "description", "message", "content"]) {
      if (value[key] !== undefined && value[key] !== null) return textValue(value[key]);
    }
    try {
      return JSON.stringify(value);
    } catch {
      return "—";
    }
  }
  return String(value);
}


function listValue(value) {
  if (Array.isArray(value)) return value;
  return value ? [value] : [];
}


function servicePosition(value) {
  if (value && typeof value === "object") {
    return { name: String(value.name || value.title || "").trim(), price: String(value.price || "").trim() };
  }
  const [name, price = ""] = String(value || "").split(/\s+[—–-]\s+/, 2);
  return { name: name.trim(), price: price.trim() };
}


function SummaryList({ title, values }) {
  if (!values?.length) return null;
  return <section><h3>{title}</h3><ul>{values.map((value, index) => <li key={`${index}-${textValue(value)}`}>{textValue(value)}</li>)}</ul></section>;
}


function MediaList({ media }) {
  if (!media?.length) return null;
  return <div className="dm-analysis-post-media">{media.map((item, index) => <span key={`${item.type}-${item.url || item.title}-${index}`}>{item.url ? <a href={item.url} target="_blank" rel="noreferrer">{item.type}: {item.title || "Открыть"}</a> : `${item.type}: ${item.title || "Вложение"}`}</span>)}</div>;
}


function SourcePosts({ posts }) {
  if (!posts?.length) return null;
  return <div className="dm-analysis-posts">{posts.map((post, index) => <article key={`${post.id || "post"}-${index}`}><div><small>{post.date || "Дата не указана"}</small>{post.views !== undefined && <span>{fmtInt(post.views)} просмотров</span>}{post.likes !== undefined && <span>{fmtInt(post.likes)} реакций</span>}</div>{post.text && <p>{post.text}</p>}<MediaList media={post.media} /></article>)}</div>;
}


function WebsiteSource({ data }) {
  if (!data || (!data.pages?.length && !data.errors?.length)) return null;
  return (
    <section className="dm-analysis-channel">
      <h3>Сайт</h3>
      {data.pages?.map((page, index) => <article className="dm-analysis-site-page" key={`${page.url}-${index}`}><div>{typeof page.url === "string" && page.url && <a href={page.url} target="_blank" rel="noreferrer">{textValue(page.title || page.url)}</a>}{!page.url && <strong>{textValue(page.title || "Страница сайта")}</strong>}</div>{page.description && <p>{textValue(page.description)}</p>}{page.text && <p>{textValue(page.text)}</p>}</article>)}
      {data.errors?.map((error, index) => <p className="dm-analysis-source-error" key={`${index}-${textValue(error)}`}>{textValue(error)}</p>)}
    </section>
  );
}


function SocialSource({ title, data }) {
  if (!data || (!data.title && !data.description && !data.posts?.length && !data.errors?.length)) return null;
  return <section className="dm-analysis-channel"><h3>{title}</h3>{typeof data.url === "string" && data.url && <a href={data.url} target="_blank" rel="noreferrer">{data.url}</a>}{data.title && <strong>{textValue(data.title)}</strong>}{data.description && <p>{textValue(data.description)}</p>}<SourcePosts posts={data.posts} />{data.errors?.map((error, index) => <p className="dm-analysis-source-error" key={`${index}-${textValue(error)}`}>{textValue(error)}</p>)}</section>;
}


export function CompanyAnalysisPanel({ task, retrying, onRetrySummary, onClose }) {
  const companies = task?.result?.companies || [];
  const [selectedId, setSelectedId] = useState(null);
  useEffect(() => {
    const firstId = companies[0]?.business_id;
    if (!companies.some((row) => row.business_id === selectedId)) setSelectedId(firstId || null);
  }, [companies, selectedId]);
  const selected = useMemo(
    () => companies.find((row) => row.business_id === selectedId) || companies[0],
    [companies, selectedId],
  );
  const summary = task?.summary || {};
  const options = task?.options || {};
  const selectedModules = [
    ["photos", "Фото"], ["reviews", "Отзывы"], ["services_prices", "Услуги и цены"],
    ["website", "Сайт"], ["telegram", "Telegram"], ["vk", "ВКонтакте"],
  ].filter(([key]) => options[key]).map(([, label]) => label);
  const company = selected?.company || {};
  const reviews = selected?.reviews || [];
  const services = listValue(selected?.services).map(servicePosition).filter((item) => item.name);
  const ratingCounts = useMemo(() => {
    const counts = { 1: 0, 2: 0, 3: 0, 4: 0, 5: 0 };
    reviews.forEach((review) => {
      const rating = Math.round(Number(review.rating || review.stars || 0));
      if (counts[rating] !== undefined) counts[rating] += 1;
    });
    return counts;
  }, [reviews]);

  return (
    <aside className="dm-analysis-panel" aria-label="Глубокий анализ компаний">
      <header className="dm-analysis-panel__head">
        <div><span>Глубокий анализ</span><strong>{task?.status === "done" ? `${fmtInt(task.quota_charged)} компаний` : "Собираем данные"}</strong></div>
        <Button view="flat" onClick={onClose} aria-label="Закрыть анализ"><Xmark /></Button>
      </header>

      {task?.status !== "done" && task?.status !== "failed" && (
        <section className="dm-analysis-panel__loading">
          <Progress value={task?.progress || 0} theme="info" />
          <strong>{task?.message || "Глубокий анализ выполняется"}</strong>
          <span>{task?.progress_details?.current_company || "Подключаемся к источнику"}</span>
        </section>
      )}
      {task?.status === "failed" && <Alert theme="danger" title="Анализ не выполнен" message={task.error || "Источники не вернули данные"} />}

      {task?.status === "done" && (
        <>
          <div className="dm-analysis-panel__toolbar">
            <div className="dm-analysis-panel__counters">
              <span><b>{fmtInt(task.result?.successful_count)}</b> успешно</span>
              <span><b>{fmtInt(task.result?.partial_count)}</b> частично</span>
              <span><b>{fmtInt(task.result?.failed_count)}</b> ошибок</span>
            </div>
            <div className="dm-analysis-panel__downloads">
              {task.xlsx_download_url && <Button view="outlined" href={task.xlsx_download_url}><ArrowDownToLine /> XLSX</Button>}
              {task.zip_download_url && <Button view="action" href={task.zip_download_url}><ArrowDownToLine /> Выбранные файлы</Button>}
            </div>
          </div>
          {selectedModules.length > 0 && <div className="dm-analysis-panel__modules"><span>В анализе:</span>{selectedModules.map((module) => <b key={module}>{module}</b>)}</div>}

          <div className="dm-analysis-panel__body">
            <section className="dm-analysis-summary">
              <div className="dm-analysis-section-title"><span>AI-сводка</span>{task.summary_error && <Button size="s" view="flat" loading={retrying} onClick={onRetrySummary}><ArrowRotateRight /> Повторить</Button>}</div>
              {task.summary_error ? <Alert theme="warning" title="Сводка временно недоступна" message={task.summary_error} /> : (
                <>
                  <p className="dm-analysis-summary__overview">{textValue(summary.overview || "Сводка формируется на основании собранных данных.")}</p>
                  <div className="dm-analysis-summary__grid">
                    <SummaryList title="Сильные стороны" values={summary.strengths} />
                    <SummaryList title="Слабые стороны" values={summary.weaknesses} />
                    <SummaryList title="Рекомендации" values={summary.recommendations} />
                    <SummaryList title="Заметные компании" values={summary.outliers} />
                  </div>
                  <div className="dm-analysis-summary__notes">
                    {summary.reputation && <p><b>Репутация:</b> {textValue(summary.reputation)}</p>}
                    {summary.digital_presence && <p><b>Цифровое присутствие:</b> {textValue(summary.digital_presence)}</p>}
                    {summary.pricing && <p><b>Цены:</b> {textValue(summary.pricing)}</p>}
                  </div>
                </>
              )}
            </section>

            <nav className="dm-analysis-companies" aria-label="Выбор компании">
              {companies.map((row) => (
                <button type="button" className={row.business_id === selected?.business_id ? "is-active" : ""} onClick={() => setSelectedId(row.business_id)} key={row.business_id}>
                  <span>{row.company?.name || `Компания ${row.business_id}`}</span>
                  <small className={`is-${row.status}`}>{row.status === "success" ? "Готово" : row.status === "partial" ? "Частично" : "Ошибка"}</small>
                </button>
              ))}
            </nav>

            {selected && (
              <article className="dm-analysis-company">
                <header>
                  <div><h3>{selected.source === "google" ? "Google Maps" : "Яндекс Карты"}</h3><h2>{company.name || "Компания без названия"}</h2></div>
                  {selected.source_url && <a href={selected.source_url} target="_blank" rel="noreferrer">Карточка на карте</a>}
                </header>
                <dl className="dm-analysis-company__facts">
                  <div><dt>Адрес</dt><dd>{textValue(company.address)}</dd></div>
                  <div><dt>Телефон</dt><dd>{textValue(company.phone)}</dd></div>
                  <div><dt>Сайт</dt><dd>{textValue(company.site || company.website)}</dd></div>
                  <div><dt>Рейтинг</dt><dd>{textValue(company.rating)}</dd></div>
                  <div><dt>Отзывов</dt><dd>{textValue(company.reviews_count)}</dd></div>
                  <div><dt>Категория</dt><dd>{textValue(company.category)}</dd></div>
                </dl>

                {selected.photos?.length > 0 && <section><h3>Фотографии <small>{selected.photos.length}</small></h3><div className="dm-analysis-photos">{selected.photos.map((photo, index) => <a href={photo} target="_blank" rel="noreferrer" key={`${photo}-${index}`}><img src={photo} alt={`${company.name || "Компания"}, фото ${index + 1}`} loading="lazy" /></a>)}</div></section>}

                {reviews.length > 0 && (
                  <section><h3>Отзывы <small>{reviews.length}</small></h3>
                    <div className="dm-analysis-ratings">{[5, 4, 3, 2, 1].map((rating) => <span key={rating}>{rating}★ <b>{ratingCounts[rating]}</b></span>)}</div>
                    <div className="dm-analysis-reviews">{reviews.slice(0, 20).map((review, index) => <blockquote key={`${review.author}-${index}`}><div><strong>{review.author || "Пользователь"}</strong><b>{textValue(review.rating || review.stars)} ★</b></div><p>{review.text || "Без текста"}</p>{review.date && <small>{review.date}</small>}</blockquote>)}</div>
                  </section>
                )}

                {services.length > 0 && <section><h3>Услуги и цены</h3><div className="dm-analysis-services">{services.map((item, index) => <div className="dm-analysis-service" key={`service-${index}`}><span>{item.name}</span>{item.price && <b>{item.price}</b>}</div>)}</div></section>}
                {selected.news?.length > 0 && <section><h3>Новости и акции <small>{selected.news.length}</small></h3><div className="dm-analysis-news">{selected.news.map((value, index) => { const item = typeof value === "object" ? value : { text: value }; return <article key={`news-${index}`}><div><strong>{item.title || item.name || "Новость"}</strong>{(item.date || item.published_at) && <small>{item.date || item.published_at}</small>}</div><p>{item.text || item.description || item.content || "Без описания"}</p></article>; })}</div></section>}
                <WebsiteSource data={selected.web} />
                <SocialSource title="Telegram" data={selected.telegram} />
                <SocialSource title="ВКонтакте" data={selected.vk} />
                {Object.keys(selected.module_errors || {}).length > 0 && <section className="dm-analysis-errors"><h3>Что не удалось собрать</h3>{Object.entries(selected.module_errors).map(([module, message]) => <p key={module}><b>{module}:</b> {message}</p>)}</section>}
              </article>
            )}
          </div>
        </>
      )}
    </aside>
  );
}
