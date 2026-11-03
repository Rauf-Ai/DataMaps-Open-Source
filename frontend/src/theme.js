// Тема: light / dark. Сохраняется в localStorage.
const KEY = "dm-theme";

export function getInitialTheme() {
  try {
    const saved = localStorage.getItem(KEY);
    if (saved === "light" || saved === "dark") return saved;
    const sysDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    return sysDark ? "dark" : "light";
  } catch {
    return "light";
  }
}

export function saveTheme(t) {
  try {
    localStorage.setItem(KEY, t);
  } catch {}
}
