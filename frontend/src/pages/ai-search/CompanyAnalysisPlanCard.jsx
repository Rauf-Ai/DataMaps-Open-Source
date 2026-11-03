import { useMemo, useState } from "react";
import { Button, Checkbox, Select, TextInput } from "@gravity-ui/uikit";
import { Sparkles } from "@gravity-ui/icons";
import { fmtInt } from "../../api.js";


const RATINGS = [1, 2, 3, 4, 5];


export function CompanyAnalysisPlanCard({ metadata, busy, onStart }) {
  const [options, setOptions] = useState(() => ({
    photos: true,
    reviews: true,
    review_count: 50,
    review_rating_min: 1,
    review_rating_max: 5,
    services_prices: true,
    website: false,
    telegram: false,
    vk: false,
    ...(metadata?.options || {}),
  }));

  const patch = (values) => setOptions((current) => ({ ...current, ...values }));
  const availableChannels = metadata?.available_channels || {};
  const channelAvailable = (key) => Array.isArray(availableChannels[key]) && availableChannels[key].length > 0;
  const optionalModules = [
    ["website", "Сайт", "Заголовок, описание и содержание"],
    ["telegram", "Telegram", "Описание и 30 последних постов канала"],
    ["vk", "ВКонтакте", "Описание и 30 последних постов сообщества"],
  ].filter(([key]) => channelAvailable(key));
  const effectiveOptions = {
    ...options,
    website: channelAvailable("website") && options.website,
    telegram: channelAvailable("telegram") && options.telegram,
    vk: channelAvailable("vk") && options.vk,
  };
  const moduleCount = ["photos", "reviews", "services_prices", "website", "telegram", "vk"]
    .filter((key) => effectiveOptions[key]).length;
  const validation = useMemo(() => {
    if (!moduleCount) return "Выберите хотя бы один вид данных";
    const count = Number(options.review_count);
    if (options.reviews && (!Number.isInteger(count) || count < 1 || count > 500)) {
      return "Количество отзывов: от 1 до 500";
    }
    if (Number(options.review_rating_min) > Number(options.review_rating_max)) {
      return "Рейтинг «от» не может быть выше рейтинга «до»";
    }
    return "";
  }, [moduleCount, options]);

  const start = () => {
    if (validation || busy) return;
    onStart?.({
      saved_result_id: metadata.saved_result_id,
      business_ids: metadata.business_ids,
      options: {
        ...effectiveOptions,
        review_count: Number(options.review_count),
        review_rating_min: Number(options.review_rating_min),
        review_rating_max: Number(options.review_rating_max),
      },
    });
  };

  return (
    <section className="dm-analysis-plan">
      <header className="dm-analysis-plan__head">
        <div>
          <strong>Настройка глубокого анализа</strong>
          <span>Одинаковые настройки применятся ко всем выбранным компаниям</span>
        </div>
        <b>{fmtInt(metadata?.business_count || metadata?.business_ids?.length || 0)} операций</b>
      </header>

      <div className="dm-analysis-plan__modules">
        <label><Checkbox checked={options.photos} onUpdate={(value) => patch({ photos: value })} /> <span><b>Фотографии</b><small>Оригиналы из карточки на карте</small></span></label>
        <label><Checkbox checked={options.services_prices} onUpdate={(value) => patch({ services_prices: value })} /> <span><b>Услуги и цены</b><small>Товары, услуги и прайс</small></span></label>
        {optionalModules.map(([key, label, hint]) => <label key={key}><Checkbox checked={effectiveOptions[key]} onUpdate={(value) => patch({ [key]: value })} /> <span><b>{label}</b><small>{hint}</small></span></label>)}
      </div>

      {(optionalModules.length > 0 || metadata?.unavailable_channels?.length > 0) && <section className="dm-analysis-plan__sources">
        <h3>Источники из карточки Яндекс Карт</h3>
        {optionalModules.length > 0 && <p>Выберите только доступные публичные источники.</p>}
        {(metadata?.unavailable_channels || []).map((channel, index) => <div className="dm-analysis-plan__source-notice" key={`${channel.business_id}-${channel.key}-${index}`}>
          <b>{channel.label}</b><span>{channel.reason}</span>{channel.url && <a href={channel.url} target="_blank" rel="noreferrer">Открыть ссылку</a>}
        </div>)}
      </section>}

      <div className="dm-analysis-plan__reviews">
        <label className="dm-analysis-plan__review-toggle">
          <Checkbox checked={options.reviews} onUpdate={(value) => patch({ reviews: value })} />
          <span><b>Отзывы</b><small>Яндекс Карты или Google Maps</small></span>
        </label>
        <label className="dm-plan-field">
          <span>Количество</span>
          <TextInput type="number" disabled={!options.reviews} value={String(options.review_count)} onUpdate={(value) => patch({ review_count: value })} />
        </label>
        <label className="dm-plan-field">
          <span>Рейтинг от</span>
          <Select disabled={!options.reviews} value={[String(options.review_rating_min)]} onUpdate={(value) => patch({ review_rating_min: Number(value[0]) })}>
            {RATINGS.map((rating) => <Select.Option key={rating} value={String(rating)}>{rating} зв.</Select.Option>)}
          </Select>
        </label>
        <label className="dm-plan-field">
          <span>Рейтинг до</span>
          <Select disabled={!options.reviews} value={[String(options.review_rating_max)]} onUpdate={(value) => patch({ review_rating_max: Number(value[0]) })}>
            {RATINGS.map((rating) => <Select.Option key={rating} value={String(rating)}>{rating} зв.</Select.Option>)}
          </Select>
        </label>
      </div>

      <footer className="dm-analysis-plan__footer">
        <span className={validation ? "is-error" : ""}>{validation || `Будет собрано ${moduleCount} видов данных. Одна компания = одна операция.`}</span>
        <Button view="action" size="l" loading={busy} disabled={Boolean(validation) || busy} onClick={start}>
          <Sparkles /> Начать глубокий анализ
        </Button>
      </footer>
    </section>
  );
}
