/**
 * Keyboard shortcuts that only matter on a desktop.
 *
 * The phone is the lead device and has no Escape key, so nothing here may ever be the
 * only way to do something — every shortcut mirrors a button that is already on screen.
 */

import { onBeforeUnmount, onMounted } from "../vendor/vue.esm-browser.prod.js";

/**
 * Run `handler` when Escape is pressed, for as long as the component is mounted.
 *
 * A confirmation dialog brings its own listener and sits on top of whatever opened it;
 * while one is open it alone owns the key, otherwise a single press would close the
 * dialog and the view behind it in one go.
 */
export function onEscape(handler) {
  const listener = (event) => {
    if (event.key !== "Escape" || document.querySelector(".dialog-backdrop")) {
      return;
    }
    handler();
  };

  onMounted(() => window.addEventListener("keydown", listener));
  onBeforeUnmount(() => window.removeEventListener("keydown", listener));
}
