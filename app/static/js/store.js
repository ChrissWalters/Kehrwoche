/** Shared application state: who is signed in, which household, unread notifications. */

import { reactive } from "../vendor/vue.esm-browser.prod.js";
import { api } from "./api.js";
import { preferredLocale, rememberedLocale, setAvailableLocales, setLocale } from "./i18n.js";

/** How often the change markers are polled while a tab is visible. */
export const SYNC_INTERVAL_MS = 15000;

export const store = reactive({
  /** The signed-in person, or null. */
  me: null,
  /** Their household, or null while in the create-or-join state. */
  household: null,
  /** Badge counter of the bell; filled in AP25. */
  unreadNotifications: 0,
  /**
   * Change marker per module. Views watch their own entry and reload only when it
   * changes — that is what keeps the app current without anybody pressing reload.
   */
  markers: { household: "", chores: "", shopping: "", expenses: "", feed: "" },
  /** False until the first session lookup has finished — avoids a flash of the login view. */
  ready: false,
  /** Last error that is worth showing as a toast. */
  notice: null,
  /** False while the device reports no connection — the app says so instead of hiding it. */
  online: true,
  /** True when this instance serves plain HTTP; the settings say so out loud. */
  insecureTransport: false,
  /** False on a closed instance: the sign-up form is hidden instead of failing. */
  registrationOpen: true,
});

if (typeof navigator !== "undefined") {
  store.online = navigator.onLine !== false;
  window.addEventListener("online", () => (store.online = true));
  window.addEventListener("offline", () => (store.online = false));
}

export const isSignedIn = () => store.me !== null;
export const hasHousehold = () => store.me !== null && store.me.household_id !== null;

/** The profile of the signed-in person, else this device, else the browser. */
async function applyLocale() {
  const meta = await api.get("/meta").catch(() => null);
  const available = meta?.languages ?? [];
  setAvailableLocales(available);
  store.insecureTransport = meta?.insecure_transport === true;
  store.registrationOpen = meta?.registration_open !== false;

  // The profile wins for a signed-in person: it is the setting they can actually see
  // and change. Only without a session does the device copy decide.
  const wanted = store.me?.locale ?? rememberedLocale() ?? preferredLocale(available);
  await setLocale(available.includes(wanted) ? wanted : preferredLocale(available));
}

export async function loadSession() {
  try {
    store.me = await api.get("/me");
    store.household = store.me.household_id ? await api.get("/household") : null;
  } catch {
    store.me = null;
    store.household = null;
  }
  try {
    await applyLocale();
  } finally {
    store.ready = true;
  }
}

/**
 * Language chosen in the header or in the profile.
 *
 * Signed in it is written to the profile, so the choice follows the person to every
 * device. The copy on this device is kept as well — it is what the sign-in view reads
 * before anybody is signed in.
 */
export async function chooseLocale(code) {
  await setLocale(code, { remember: true });
  if (isSignedIn()) {
    store.me = await api.patch("/me", { locale: code }).catch(() => store.me);
  }
}

export async function reloadHousehold() {
  store.household = await api.get("/household");
}

/**
 * Household and own account together — a role can change just like a member list.
 *
 * Failures are swallowed on purpose: this runs in the background poll, where a lost
 * connection is already reported elsewhere and must not clear the view.
 */
async function refreshMembership() {
  try {
    const [me, household] = await Promise.all([api.get("/me"), api.get("/household")]);
    store.me = me;
    store.household = household;
  } catch {
    /* keep what we have */
  }
}

export function clearSession() {
  store.me = null;
  store.household = null;
  store.unreadNotifications = 0;
}

export function notify(message) {
  store.notice = message;
}


let syncTimer = null;

/** Fetch the markers once and hand the result to whoever is watching. */
export async function refreshState() {
  if (!hasHousehold()) {
    return;
  }
  const state = await api.get("/household/state").catch(() => null);
  if (!state) {
    return;
  }

  // The member list first, and before the views react to their own markers: every form
  // builds on it. Somebody who joined a minute ago must not be missing from the next
  // chore or expense just because this tab has not been reloaded since.
  if (store.markers.household !== state.household) {
    const known = store.markers.household !== "";
    store.markers.household = state.household;
    if (known) {
      await refreshMembership();
    }
  }

  for (const section of Object.keys(store.markers)) {
    if (store.markers[section] !== state[section]) {
      store.markers[section] = state[section];
    }
  }
  store.unreadNotifications = state.notifications;
}

/**
 * Keep the markers up to date: every 15 seconds while the tab is visible, and
 * immediately whenever somebody comes back to it.
 *
 * A hidden tab polls nothing — on a phone that would cost battery for a screen nobody
 * is looking at.
 */
export function startSync() {
  const tick = () => {
    if (document.visibilityState === "visible") {
      refreshState();
    }
  };
  if (syncTimer === null) {
    syncTimer = window.setInterval(tick, SYNC_INTERVAL_MS);
    document.addEventListener("visibilitychange", tick);
    window.addEventListener("focus", tick);
  }
  tick();
}
