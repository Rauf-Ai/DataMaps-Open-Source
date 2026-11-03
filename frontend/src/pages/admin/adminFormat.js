const STATUS_LABELS = {
  new: "Новый",
  acknowledged: "Принят",
  resolved: "Решён",
  queued: "В очереди",
  running: "Выполняется",
  failed: "Ошибка",
  done: "Готово",
  canceled: "Отменён",
  partial: "Частично",
};

const SEVERITY_LABELS = {
  info: "Информация",
  warning: "Внимание",
  critical: "Критично",
};

export function formatStatus(value) {
  return STATUS_LABELS[value] || value || "—";
}

export function formatSeverity(value) {
  return SEVERITY_LABELS[value] || value || "—";
}

export function severityTheme(value) {
  if (value === "critical") return "danger";
  if (value === "warning") return "warning";
  return "info";
}

export function statusTheme(value) {
  if (value === "resolved" || value === "done") return "success";
  if (value === "new" || value === "failed") return "danger";
  if (value === "acknowledged" || value === "running") return "warning";
  return "normal";
}

export function integrationLabel(state) {
  if (state === "ok") return "Работает";
  if (state === "degraded") return "Деградация";
  return "Нет сигнала";
}

export function integrationTheme(state) {
  if (state === "ok") return "success";
  if (state === "degraded") return "warning";
  return "normal";
}

export function safeJson(value) {
  try {
    return JSON.stringify(value || {}, null, 2);
  } catch {
    return "{}";
  }
}
