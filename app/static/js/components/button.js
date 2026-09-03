/** Buttons. Primary actions are full width so they are easy to hit with a thumb. */

import { h } from "../../vendor/vue.esm-browser.prod.js";

export const Button = {
  props: {
    label: { type: String, required: true },
    type: { type: String, default: "button" },
    variant: { type: String, default: "primary" },
    disabled: { type: Boolean, default: false },
    busy: { type: Boolean, default: false },
    block: { type: Boolean, default: false },
  },
  render() {
    return h(
      "button",
      {
        class: ["btn", `btn--${this.variant}`, this.block ? "btn--block" : ""],
        type: this.type,
        disabled: this.disabled || this.busy,
        "aria-busy": this.busy ? "true" : null,
      },
      this.label,
    );
  },
};
