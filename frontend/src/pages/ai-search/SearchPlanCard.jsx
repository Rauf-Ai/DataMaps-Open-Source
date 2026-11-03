import { memo } from "react";
import { Button, Checkbox, Select, TextInput } from "@gravity-ui/uikit";
import { Magnifier, Plus, Xmark } from "@gravity-ui/icons";
import { fmtInt } from "../../api.js";


const SOURCE_OPTIONS = [
  ["yandex", "Яндекс Карты"],
  ["google", "Google Maps"],
];

const TRI_OPTIONS = [
  ["any", "Любой"],
  ["yes", "Есть"],
  ["no", "Нет"],
];

const FRANCHISE_OPTIONS = [
  ["any", "Любые"],
  ["yes", "Только сети"],
  ["no", "Без сетей"],
];

const SOCIALS = [
  ["vk", "ВКонтакте"],
  ["max", "Max"],
  ["instagram", "Instagram"],
  ["telegram", "Telegram"],
];


function NumberField({ label, value, onUpdate, min = 0, max }) {
  return (
    <label className="dm-plan-field">
      <span>{label}</span>
      <TextInput
        type="number"
        size="m"
        value={value === null || value === undefined ? "" : String(value)}
        onUpdate={onUpdate}
        controlProps={{ min, max }}
      />
    </label>
  );
}


export const SearchPlanCard = memo(function SearchPlanCard({
  plan,
  availableOrganizations,
  dirty,
  busy,
  onChange,
  onSave,
  onSearch,
}) {
  if (!plan?.searches?.length) return null;

  const patch = (value) => onChange({ ...plan, ...value });
  const setRow = (index, value) => patch({
    searches: plan.searches.map((row, rowIndex) => rowIndex === index ? { ...row, ...value } : row),
  });
  const removeRow = (index) => patch({ searches: plan.searches.filter((_, rowIndex) => rowIndex !== index) });
  const addRow = () => patch({
    searches: [...plan.searches, { query: "", location: "", country: "", source: plan.source || "yandex" }],
  });
  const toggleSocial = (network, checked) => {
    const current = new Set(plan.social_nets || []);
    if (checked) current.add(network); else current.delete(network);
    patch({ social_nets: [...current] });
  };
  const targetCount = Number(plan.target_count ?? plan.max) || 50;

  return (
    <section className="dm-search-plan" aria-label="Параметры поиска">
      <div className="dm-search-plan__head">
        <div>
          <strong>План поиска</strong>
          <span>Можно изменить вручную или уточнить сообщением</span>
        </div>
        <span className="dm-search-plan__count">{plan.searches.length} направл.</span>
      </div>

      <div className="dm-search-plan__controls">
        <NumberField
          label="Нужно организаций"
          value={targetCount}
          min={1}
          max={5000}
          onUpdate={(value) => patch({ target_count: Number(value) || 1, max: Number(value) || 1 })}
        />
        <label className="dm-plan-field">
          <span>Франшизы и сети</span>
          <Select value={[plan.f_franchise || "any"]} onUpdate={(value) => patch({ f_franchise: value[0] })}>
            {FRANCHISE_OPTIONS.map(([value, label]) => <Select.Option key={value} value={value}>{label}</Select.Option>)}
          </Select>
        </label>
        <label className="dm-plan-field">
          <span>Сайт</span>
          <Select value={[plan.f_site || "any"]} onUpdate={(value) => patch({ f_site: value[0] })}>
            {TRI_OPTIONS.map(([value, label]) => <Select.Option key={value} value={value}>{label}</Select.Option>)}
          </Select>
        </label>
        <label className="dm-plan-field">
          <span>Телефон</span>
          <Select value={[plan.f_phone || "any"]} onUpdate={(value) => patch({ f_phone: value[0] })}>
            {TRI_OPTIONS.map(([value, label]) => <Select.Option key={value} value={value}>{label}</Select.Option>)}
          </Select>
        </label>
        <NumberField label="Рейтинг от" value={plan.rating_min} min={0} max={5} onUpdate={(value) => patch({ rating_min: Number(value) || 0 })} />
        <NumberField label="Рейтинг до" value={plan.rating_max} min={0} max={5} onUpdate={(value) => patch({ rating_max: Number(value) || 5 })} />
        <NumberField label="Отзывов от" value={plan.reviews_min} onUpdate={(value) => patch({ reviews_min: Number(value) || 0 })} />
        <NumberField label="Отзывов до" value={plan.reviews_max} onUpdate={(value) => patch({ reviews_max: value === "" ? null : Number(value) })} />
      </div>

      <div className="dm-search-plan__socials">
        {SOCIALS.map(([network, label]) => (
          <Checkbox
            key={network}
            checked={(plan.social_nets || []).includes(network)}
            onUpdate={(checked) => toggleSocial(network, checked)}
          >
            {label}
          </Checkbox>
        ))}
      </div>

      <div className="dm-search-plan__rows">
        {plan.searches.map((row, index) => (
          <div className="dm-search-plan__row" key={index}>
            <TextInput value={row.query} onUpdate={(value) => setRow(index, { query: value })} placeholder="Что искать" />
            <TextInput value={row.location} onUpdate={(value) => setRow(index, { location: value })} placeholder="Где искать" />
            <Select
              className="dm-plan-row-source"
              value={[row.source || plan.source || "yandex"]}
              onUpdate={(value) => setRow(index, { source: value[0] })}
              aria-label="Источник строки поиска"
            >
              {SOURCE_OPTIONS.map(([value, label]) => <Select.Option key={value} value={value}>{label}</Select.Option>)}
            </Select>
            <Button view="flat" onClick={() => removeRow(index)} disabled={plan.searches.length === 1} title="Удалить направление" aria-label="Удалить направление">
              <Xmark />
            </Button>
          </div>
        ))}
      </div>

      <div className="dm-search-plan__actions">
        <Button view="flat" onClick={addRow}><Plus /> Добавить</Button>
        <div className="dm-search-plan__primary">
          <span>до {fmtInt(targetCount)} орг. · доступно {fmtInt(availableOrganizations)}</span>
          {dirty && <Button view="outlined" onClick={onSave} disabled={busy}>Сохранить</Button>}
          <Button view="action" size="l" onClick={onSearch} loading={busy} disabled={busy || availableOrganizations < targetCount}>
            <Magnifier /> Поиск
          </Button>
        </div>
      </div>
      {availableOrganizations < targetCount && <div className="dm-search-plan__warning">Недостаточно доступного объёма. Уменьшите количество или докупите организации в кабинете.</div>}
    </section>
  );
});
