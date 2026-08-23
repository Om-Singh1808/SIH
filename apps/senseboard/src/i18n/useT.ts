/**
 * Minimal i18n: flat JSON dictionaries (`hi.json`, `en.json`) with `{param}`
 * interpolation. Hindi is the default because the primary user is a kirana
 * owner; `i18n.test.ts` enforces that every key exists in both files so a
 * missing translation can never ship.
 *
 * `t(key, params)` never throws: an unknown key returns the key itself (visible
 * in dev, harmless in prod) and a missing param renders as "?", mirroring
 * contracts `i18n.render`.
 */
import { useCallback } from "react";
import type { Lang } from "@contracts/types";
import { useSettings } from "@/store/settings";
import hi from "./hi.json";
import en from "./en.json";

export type Dict = Record<string, string>;
export const DICTS: Record<Lang, Dict> = { hi: hi as Dict, en: en as Dict };

export type TParams = Record<string, string | number | null | undefined>;
export type TFn = (key: string, params?: TParams) => string;

export function translate(lang: Lang, key: string, params?: TParams): string {
  const template = DICTS[lang][key] ?? DICTS.en[key] ?? key;
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (_, name: string) => {
    const v = params[name];
    return v === null || v === undefined ? "?" : String(v);
  });
}

export function useT(): { t: TFn; lang: Lang } {
  const lang = useSettings((s) => s.lang);
  const t = useCallback<TFn>((key, params) => translate(lang, key, params), [lang]);
  return { t, lang };
}
