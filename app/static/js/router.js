/**
 * Minimal hash router.
 *
 * A hash router needs no server-side rewriting beyond the SPA fallback and keeps deep
 * links working when the page is reloaded.
 */

import { reactive } from "../vendor/vue.esm-browser.prod.js";

/** What a route expects of the visitor. */
export const Access = {
  /** Only without a session — sign-in and registration. */
  GUEST: "guest",
  /** Signed in, household not required — create or join. */
  SIGNED_IN: "signed-in",
  /** Signed in and part of a household — the four modules and settings. */
  MEMBER: "member",
};

export function createRouter(routes, resolveRedirect) {
  const home = routes.find((route) => route.path === "/") ?? routes[0];
  const state = reactive({ path: "/", route: home });

  function currentPath() {
    const hash = window.location.hash.replace(/^#/, "");
    return hash === "" ? "/" : hash;
  }

  function navigate(path, { replace = false } = {}) {
    const target = `#${path}`;
    if (window.location.hash === target) {
      apply();
      return;
    }
    if (replace) {
      window.history.replaceState(null, "", target);
      apply();
    } else {
      window.location.hash = target;
    }
  }

  function apply() {
    const path = currentPath();
    const route = routes.find((candidate) => candidate.path === path) ?? home;
    const redirect = resolveRedirect(route);
    if (redirect && redirect !== path) {
      navigate(redirect, { replace: true });
      return;
    }
    state.path = path;
    state.route = route;
  }

  window.addEventListener("hashchange", apply);

  return { state, navigate, apply };
}
