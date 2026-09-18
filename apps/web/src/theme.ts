/** Themes offered by the navbar theme controller. Must match the daisyUI
 * theme `name`s in src/index.css — that file is the source of truth for
 * colors; this list is only what the picker displays. */
export const THEMES = [
  { id: "ohdp", label: "Daylight" },
  { id: "ohdp-dark", label: "After dark" },
] as const;

export type ThemeId = (typeof THEMES)[number]["id"];

export function applyTheme(id: ThemeId) {
  document.documentElement.setAttribute("data-theme", id);
  localStorage.setItem("ohdp-theme", id);
}

export function storedTheme(): ThemeId | null {
  const value = localStorage.getItem("ohdp-theme");
  return THEMES.some((t) => t.id === value) ? (value as ThemeId) : null;
}
