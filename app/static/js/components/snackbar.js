/**
 * Snackbar with an undo action.
 *
 * This is what replaces confirmation dialogs for everyday actions: tap once, see it
 * happen, take it back within a few seconds if it was a mistap.
 */

import { h, reactive } from "../../vendor/vue.esm-browser.prod.js";

const VISIBLE_MS = 6000;

const state = reactive({ message: null, actionLabel: null, action: null, token: 0 });

export const snackbar = state;

export function showSnackbar(message, { actionLabel = null, action = null } = {}) {
  state.token += 1;
  const token = state.token;
  state.message = message;
  state.actionLabel = actionLabel;
  state.action = action;
  window.setTimeout(() => {
    if (state.token === token) {
      dismissSnackbar();
    }
  }, VISIBLE_MS);
}

export function dismissSnackbar() {
  state.message = null;
  state.actionLabel = null;
  state.action = null;
}

export const Snackbar = {
  render() {
    if (!state.message) {
      return null;
    }
    return h("div", { class: "snackbar", role: "status" }, [
      h("span", { class: "snackbar-text" }, state.message),
      state.action
        ? h(
            "button",
            {
              class: "snackbar-action",
              type: "button",
              onClick: () => {
                const action = state.action;
                dismissSnackbar();
                action();
              },
            },
            state.actionLabel,
          )
        : null,
    ]);
  },
};
