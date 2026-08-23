/**
 * User preferences persisted in localStorage: language (default Hindi),
 * theme (system/light/dark) and the "show demo controls" flag. Zustand with the
 * `persist` middleware; every read is try/catch-safe because some embedding
 * contexts throw on localStorage access.
 */
import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type { Lang } from "@contracts/types";

export type Theme = "system" | "light" | "dark";

interface SettingsState {
  lang: Lang;
  theme: Theme;
  demoOpen: boolean;
  setLang: (lang: Lang) => void;
  toggleLang: () => void;
  setTheme: (theme: Theme) => void;
  setDemoOpen: (open: boolean) => void;
}

const safeStorage = {
  getItem: (k: string) => {
    try {
      return localStorage.getItem(k);
    } catch {
      return null;
    }
  },
  setItem: (k: string, v: string) => {
    try {
      localStorage.setItem(k, v);
    } catch {
      /* ignore */
    }
  },
  removeItem: (k: string) => {
    try {
      localStorage.removeItem(k);
    } catch {
      /* ignore */
    }
  },
};

export const useSettings = create<SettingsState>()(
  persist(
    (set, get) => ({
      lang: "hi",
      theme: "system",
      demoOpen: false,
      setLang: (lang) => set({ lang }),
      toggleLang: () => set({ lang: get().lang === "hi" ? "en" : "hi" }),
      setTheme: (theme) => set({ theme }),
      setDemoOpen: (demoOpen) => set({ demoOpen }),
    }),
    {
      name: "senseboard.settings",
      storage: createJSONStorage(() => safeStorage),
      partialize: (s) => ({ lang: s.lang, theme: s.theme }),
    },
  ),
);

/** Apply the theme to <html data-theme> so the CSS tokens flip. */
export function applyTheme(theme: Theme): void {
  if (typeof document === "undefined") return;
  const el = document.documentElement;
  if (theme === "system") el.removeAttribute("data-theme");
  else el.setAttribute("data-theme", theme);
}
