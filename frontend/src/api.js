// Тонкая обёртка над fetch: одноразовый refresh при 401, JSON-десериализация, единый формат ошибок.

async function _fetch(path, opts = {}) {
  const isFormData = typeof FormData !== "undefined" && opts.body instanceof FormData;
  const res = await fetch(path, {
    credentials: "include",
    headers: { ...(isFormData ? {} : { "Content-Type": "application/json" }), ...(opts.headers || {}) },
    ...opts,
  });
  if (res.status === 401 && !path.includes("/auth/refresh")) {
    const r = await fetch("/api/auth/refresh", { method: "POST", credentials: "include" });
    if (r.ok) {
      return fetch(path, {
        credentials: "include",
        headers: { ...(isFormData ? {} : { "Content-Type": "application/json" }), ...(opts.headers || {}) },
        ...opts,
      });
    }
  }
  return res;
}

export async function api(path, opts = {}) {
  const r = await _fetch(path, opts);
  const ct = r.headers.get("content-type") || "";
  const body = ct.includes("application/json") ? await r.json() : await r.text();
  if (!r.ok) {
    const requestId = r.headers.get("x-request-id") || body?.detail?.request_id || "";
    const detail = (body && body.detail) || body || `Ошибка ${r.status}`;
    let message;
    if (typeof detail === "string") {
      message = detail;
    } else if (detail && typeof detail === "object") {
      const code = detail.code ? `[${detail.code}] ` : "";
      const hint = detail.hint ? ` Подсказка: ${detail.hint}` : "";
      const rid = requestId ? ` ID: ${requestId}` : "";
      message = `${code}${detail.message || JSON.stringify(detail)}.${hint}${rid}`;
    } else {
      message = `Ошибка ${r.status}`;
    }
    const err = new Error(message);
    err.status = r.status;
    err.code = typeof detail === "object" ? detail.code : undefined;
    err.requestId = requestId;
    throw err;
  }
  return body;
}

export const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body) });

export const fmtInt = (v) => new Intl.NumberFormat("ru-RU").format(v ?? 0);
export const fmtRub = (v) =>
  new Intl.NumberFormat("ru-RU", { style: "currency", currency: "RUB", maximumFractionDigits: 0 }).format(v ?? 0);
export const fmtDate = (iso) => {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("ru-RU");
  } catch {
    return iso;
  }
};
