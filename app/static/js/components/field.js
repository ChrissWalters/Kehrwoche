/**
 * One labelled input with its error message.
 *
 * The error comes straight from the server's `{ code, message, field }` object, so the
 * message lands on the field that caused it.
 */

import { h } from "../../vendor/vue.esm-browser.prod.js";

let sequence = 0;

export const Field = {
  props: {
    label: { type: String, required: true },
    modelValue: { type: String, default: "" },
    /** Password managers key off this, not off the id. */
    name: { type: String, default: null },
    type: { type: String, default: "text" },
    autocomplete: { type: String, default: null },
    inputmode: { type: String, default: null },
    placeholder: { type: String, default: null },
    error: { type: String, default: null },
    required: { type: Boolean, default: false },
    maxlength: { type: Number, default: null },
  },
  emits: ["update:modelValue"],
  data() {
    sequence += 1;
    return { fieldId: `field-${sequence}` };
  },
  render() {
    const errorId = `${this.fieldId}-error`;
    return h("div", { class: "field" }, [
      h("label", { class: "field-label", for: this.fieldId }, this.label),
      h("input", {
        id: this.fieldId,
        name: this.name,
        class: ["field-input", this.error ? "is-invalid" : ""],
        type: this.type,
        value: this.modelValue,
        autocomplete: this.autocomplete,
        inputmode: this.inputmode,
        placeholder: this.placeholder,
        required: this.required,
        maxlength: this.maxlength,
        "aria-invalid": this.error ? "true" : null,
        "aria-describedby": this.error ? errorId : null,
        onInput: (event) => this.$emit("update:modelValue", event.target.value),
      }),
      this.error ? h("p", { class: "field-error", id: errorId }, this.error) : null,
    ]);
  },
};

/** Same shape for a list of options — a select is quicker than typing on a phone. */
export const SelectField = {
  props: {
    label: { type: String, required: true },
    modelValue: { type: String, default: "" },
    options: { type: Array, required: true },
    error: { type: String, default: null },
  },
  emits: ["update:modelValue"],
  data() {
    sequence += 1;
    return { fieldId: `select-${sequence}` };
  },
  render() {
    return h("div", { class: "field" }, [
      h("label", { class: "field-label", for: this.fieldId }, this.label),
      h(
        "select",
        {
          id: this.fieldId,
          class: ["field-input", this.error ? "is-invalid" : ""],
          value: this.modelValue,
          onChange: (event) => this.$emit("update:modelValue", event.target.value),
        },
        this.options.map((option) =>
          h(
            "option",
            { value: option.value, selected: option.value === this.modelValue },
            option.label,
          ),
        ),
      ),
      this.error ? h("p", { class: "field-error" }, this.error) : null,
    ]);
  },
};
