/**
 * Confirmation dialog — reserved for destructive actions (delete, archive, leave).
 *
 * Everything else follows the one-tap-plus-undo rule. On a phone
 * it fills the screen from the bottom, on a wide screen it becomes a centred modal.
 */

import { h } from "../../vendor/vue.esm-browser.prod.js";
import { Button } from "./button.js";

export const Dialog = {
  props: {
    title: { type: String, required: true },
    message: { type: String, default: "" },
    confirmLabel: { type: String, required: true },
    /** Optional second way forward, e.g. "book it for somebody else". */
    secondaryLabel: { type: String, default: null },
    cancelLabel: { type: String, required: true },
    destructive: { type: Boolean, default: false },
    busy: { type: Boolean, default: false },
  },
  emits: ["confirm", "secondary", "cancel"],
  data() {
    return { onKey: null };
  },
  mounted() {
    this.onKey = (event) => {
      if (event.key === "Escape") {
        this.$emit("cancel");
      }
    };
    window.addEventListener("keydown", this.onKey);
  },
  unmounted() {
    window.removeEventListener("keydown", this.onKey);
  },
  render() {
    return h(
      "div",
      {
        class: "dialog-backdrop",
        onClick: (event) => {
          if (event.target === event.currentTarget) {
            this.$emit("cancel");
          }
        },
      },
      [
        h(
          "div",
          { class: "dialog", role: "dialog", "aria-modal": "true", "aria-label": this.title },
          [
            h("h2", { class: "dialog-title" }, this.title),
            this.message ? h("p", { class: "muted" }, this.message) : null,
            h("div", { class: "dialog-actions" }, [
              h(Button, {
                label: this.confirmLabel,
                variant: this.destructive ? "danger" : "primary",
                block: true,
                busy: this.busy,
                onClick: () => this.$emit("confirm"),
              }),
              this.secondaryLabel
                ? h(Button, {
                    label: this.secondaryLabel,
                    variant: "secondary",
                    block: true,
                    onClick: () => this.$emit("secondary"),
                  })
                : null,
              h(Button, {
                label: this.cancelLabel,
                variant: "ghost",
                block: true,
                onClick: () => this.$emit("cancel"),
              }),
            ]),
          ],
        ),
      ],
    );
  },
};
