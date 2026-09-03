/**
 * Texts. Never a literal in a component — always `t('some.key')`.
 *
 * Lookup order: chosen language → English → the key itself. A visible key is a loud,
 * harmless hint that a translation is missing.
 */

import { reactive } from "../vendor/vue.esm-browser.prod.js";
import { api } from "./api.js";

export const FALLBACK_LOCALE = "en";
/**
 * Where the choice of this device is kept. Signed in, the profile decides (see
 * `store.js`); this copy is what the sign-in view reads before anybody is signed in.
 */
const STORAGE_KEY = "kehrwoche.locale";

const state = reactive({
  locale: FALLBACK_LOCALE,
  /** code → catalogue, kept once loaded. */
  catalogues: {},
  /** Codes the server offers, filled from /meta. */
  available: [FALLBACK_LOCALE],
});

export const i18n = state;

/** The first offered language matching the browser's preferences, else English. */
export function preferredLocale(available) {
  const wanted = [...(navigator.languages ?? [navigator.language ?? ""])];
  for (const tag of wanted) {
    const code = tag.toLowerCase().split("-")[0];
    if (available.includes(code)) {
      return code;
    }
  }
  return FALLBACK_LOCALE;
}

async function fetchCatalogue(code) {
  if (state.catalogues[code]) {
    return;
  }
  try {
    state.catalogues[code] = await api.get(`/locales/${code}`);
  } catch {
    // A missing catalogue must not break the app; the fallback chain covers it.
    state.catalogues[code] = {};
  }
}

/** Switch language, loading the catalogue and the fallback if needed. */
export async function setLocale(code, { remember = false } = {}) {
  await Promise.all([fetchCatalogue(FALLBACK_LOCALE), fetchCatalogue(code)]);
  state.locale = code;
  document.documentElement.lang = code;
  if (remember) {
    try {
      window.localStorage.setItem(STORAGE_KEY, code);
    } catch {
      // Private mode may refuse storage; the choice then lasts for this visit only.
    }
  }
}

/** The language explicitly chosen on this device, if any. */
export function rememberedLocale() {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export function setAvailableLocales(codes) {
  state.available = codes.length > 0 ? codes : [FALLBACK_LOCALE];
}

function interpolate(text, params) {
  if (!params) {
    return text;
  }
  return text.replace(/\{(\w+)\}/g, (match, name) =>
    Object.hasOwn(params, name) ? String(params[name]) : match,
  );
}

export function t(key, params) {
  const chosen = state.catalogues[state.locale]?.[key];
  const fallback = state.catalogues[FALLBACK_LOCALE]?.[key];
  const text = chosen ?? fallback ?? key;
  return typeof text === "string" ? interpolate(text, params) : key;
}

/** The catalogue value behind a key, for lists such as chore templates. */
export function tv(key) {
  return state.catalogues[state.locale]?.[key] ?? state.catalogues[FALLBACK_LOCALE]?.[key] ?? null;
}
