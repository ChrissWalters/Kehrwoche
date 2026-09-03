/**
 * Create or edit a chore.
 *
 * Templates are a shortcut, never a limit: every field stays editable and a chore can be
 * written from scratch.
 */

import { h, reactive } from "../../../vendor/vue.esm-browser.prod.js";
import { ApiError, api } from "../../api.js";
import { Button } from "../../components/button.js";
import { PersonName } from "../../components/avatar.js";
import { Field, SelectField } from "../../components/field.js";
import {
  INTERVAL_UNITS,
  intervalLabel,
  splitInterval,
  unitSeconds,
  weekdayName,
} from "../../format.js";
import { errorText } from "../../error-text.js";
import { t } from "../../i18n.js";
import { onEscape } from "../../keyboard.js";
import { store } from "../../store.js";

const ON_DEMAND = -1;

/** `YYYY-MM-DD` in local time — the value a date input expects. */
function toDateInput(value) {
  const date = new Date(value);
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 10);
}

function defaultDue() {
  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  return tomorrow;
}

/**
 * A due date is a day, not a minute: the chore counts as overdue once that day is over.
 */
function dueDateToInstant(value) {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(year, month - 1, day, 23, 59, 0).toISOString();
}

function memberOf(id) {
  return store.household?.members.find((entry) => entry.id === id) ?? null;
}

function memberName(id) {
  const member = memberOf(id);
  if (!member) {
    return String(id);
  }
  const name = [member.first_name, member.last_name].filter(Boolean).join(" ");
  return member.username ? `${name} (${member.username})` : name;
}

export const ChoreForm = {
  props: {
    chore: { type: Object, default: null },
    templates: { type: Array, default: () => [] },
  },
  emits: ["saved", "cancel"],
  setup(props, { emit }) {
    // On a desktop Escape is what people reach for to back out of a form; the
    // button below does the same thing and stays the only way on a phone.
    onEscape(() => emit("cancel"));
    const existing = props.chore;
    const interval = splitInterval(
      existing && existing.rotation_seconds > 0 ? existing.rotation_seconds : 7 * 24 * 3600,
    );

    const form = reactive({
      title: existing?.title ?? "",
      description: existing?.description ?? "",
      points: String(existing?.points ?? 1),
      onDemand: existing ? existing.rotation_seconds === ON_DEMAND : false,
      count: String(interval.count),
      unit: interval.unit,
      fixed: existing?.fixed ?? false,
      order: existing
        ? [...existing.member_order]
        : (store.household?.members ?? []).map((member) => member.id),
      /** Who is on duty. Changing the order alone does not restart the cycle. */
      currentUserId: String(
        existing?.current_user_id ?? store.household?.members?.[0]?.id ?? "",
      ),
      /** Local date of the next due date — this is how "every Saturday" is set. */
      dueDate: existing?.due_at ? toDateInput(existing.due_at) : toDateInput(defaultDue()),
      busy: false,
      errors: {},
      generalError: null,
    });

    function applyTemplate(title) {
      const template = props.templates.find((entry) => entry.title === title);
      if (!template) {
        return;
      }
      form.title = template.title;
      form.points = String(template.points);
      form.fixed = template.fixed;
      form.onDemand = template.rotation_seconds === ON_DEMAND;
      if (!form.onDemand) {
        const split = splitInterval(template.rotation_seconds);
        form.count = String(split.count);
        form.unit = split.unit;
      }
    }

    function move(index, direction) {
      const target = index + direction;
      if (target < 0 || target >= form.order.length) {
        return;
      }
      const order = [...form.order];
      [order[index], order[target]] = [order[target], order[index]];
      form.order = order;
    }

    function onDutyOptions() {
      return form.order.map((id) => ({ value: String(id), label: memberName(id) }));
    }

    async function save() {
      form.busy = true;
      form.errors = {};
      form.generalError = null;
      const payload = {
        title: form.title,
        description: form.description || null,
        points: Number(form.points) || 0,
        rotation_seconds: form.onDemand
          ? ON_DEMAND
          : Math.max(1, Number(form.count) || 1) * unitSeconds(form.unit),
        fixed: form.fixed,
        member_order: form.order,
        current_user_id: Number(form.currentUserId) || form.order[0],
        due_at: form.onDemand || !form.dueDate ? null : dueDateToInstant(form.dueDate),
      };
      try {
        if (existing) {
          await api.patch(`/chores/${existing.id}`, payload);
        } else {
          await api.post("/chores", payload);
        }
        emit("saved");
      } catch (error) {
        if (error instanceof ApiError && error.field) {
          form.errors = { [error.field]: errorText(error) };
        } else {
          form.generalError = errorText(error);
        }
      } finally {
        form.busy = false;
      }
    }

    function dueHint() {
      if (!form.dueDate) {
        return "";
      }
      const seconds = Math.max(1, Number(form.count) || 1) * unitSeconds(form.unit);
      if (form.fixed && form.unit === "week" && Number(form.count) === 1) {
        return t("chores.form.due_weekly", { weekday: weekdayName(form.dueDate) });
      }
      return t("chores.form.due_then", { interval: intervalLabel(seconds) });
    }

    return { form, applyTemplate, move, save, onDutyOptions, dueHint };
  },
  render() {
    const { form } = this;
    const members = store.household?.members ?? [];

    return h("section", { class: "card" }, [
      h("h2", t(this.chore ? "chores.form.edit" : "chores.form.create")),
      h(
        "form",
        {
          class: "form",
          onSubmit: (event) => {
            event.preventDefault();
            this.save();
          },
        },
        [
          !this.chore && this.templates.length > 0
            ? h(SelectField, {
                label: t("chores.form.template"),
                modelValue: "",
                options: [
                  { value: "", label: t("chores.form.template_none") },
                  ...this.templates.map((template) => ({
                    value: template.title,
                    label: template.title,
                  })),
                ],
                "onUpdate:modelValue": (value) => this.applyTemplate(value),
              })
            : null,
          h(Field, {
            label: t("chores.form.title"),
            name: "title",
            modelValue: form.title,
            required: true,
            maxlength: 120,
            error: form.errors.title,
            "onUpdate:modelValue": (value) => (form.title = value),
          }),
          h(Field, {
            label: t("chores.form.description"),
            name: "description",
            modelValue: form.description,
            error: form.errors.description,
            "onUpdate:modelValue": (value) => (form.description = value),
          }),
          h(Field, {
            label: t("chores.form.points"),
            name: "points",
            modelValue: form.points,
            type: "number",
            inputmode: "numeric",
            error: form.errors.points,
            "onUpdate:modelValue": (value) => (form.points = value),
          }),

          h("div", { class: "field" }, [
            h("span", { class: "field-label" }, t("chores.form.rhythm")),
            h("label", { class: "check" }, [
              h("input", {
                type: "checkbox",
                checked: form.onDemand,
                onChange: (event) => (form.onDemand = event.target.checked),
              }),
              h("span", t("chores.form.on_demand")),
            ]),
            !form.onDemand
              ? h("div", { class: "interval" }, [
                  h("input", {
                    class: "field-input interval-count",
                    type: "number",
                    inputmode: "numeric",
                    min: "1",
                    value: form.count,
                    "aria-label": t("chores.form.every"),
                    onInput: (event) => (form.count = event.target.value),
                  }),
                  h(
                    "select",
                    {
                      class: "field-input",
                      value: form.unit,
                      "aria-label": t("chores.form.unit"),
                      onChange: (event) => (form.unit = event.target.value),
                    },
                    INTERVAL_UNITS.map((unit) =>
                      h(
                        "option",
                        { value: unit.key, selected: unit.key === form.unit },
                        t(`chores.unit.${unit.key}`),
                      ),
                    ),
                  ),
                ])
              : null,
            !form.onDemand
              ? h("label", { class: "check" }, [
                  h("input", {
                    type: "checkbox",
                    checked: form.fixed,
                    onChange: (event) => (form.fixed = event.target.checked),
                  }),
                  h("span", t("chores.form.fixed")),
                ])
              : null,
            !form.onDemand ? h("p", { class: "muted small" }, t("chores.form.fixed_hint")) : null,
          ]),

          !form.onDemand
            ? h("div", { class: "field" }, [
                h("label", { class: "field-label", for: "chore-due" }, t("chores.form.due")),
                h("input", {
                  id: "chore-due",
                  class: "field-input",
                  type: "date",
                  value: form.dueDate,
                  onInput: (event) => (form.dueDate = event.target.value),
                }),
                h("p", { class: "muted small" }, this.dueHint()),
              ])
            : null,

          members.length > 1
            ? h("div", { class: "field" }, [
                h("span", { class: "field-label" }, t("chores.form.order")),
                h(
                  "ul",
                  { class: "order-list" },
                  form.order.map((id, index) =>
                    h("li", { class: "order-item" }, [
                      h("span", { class: "order-position" }, `${index + 1}.`),
                      h(PersonName, { person: memberOf(id) }),
                      h(
                        "button",
                        {
                          class: "icon-button",
                          type: "button",
                          disabled: index === 0,
                          "aria-label": t("chores.form.move_up"),
                          onClick: () => this.move(index, -1),
                        },
                        "↑",
                      ),
                      h(
                        "button",
                        {
                          class: "icon-button",
                          type: "button",
                          disabled: index === form.order.length - 1,
                          "aria-label": t("chores.form.move_down"),
                          onClick: () => this.move(index, 1),
                        },
                        "↓",
                      ),
                    ]),
                  ),
                ),
                form.errors.member_order
                  ? h("p", { class: "field-error" }, form.errors.member_order)
                  : null,
                h(SelectField, {
                  label: t("chores.form.on_duty"),
                  modelValue: form.currentUserId,
                  options: this.onDutyOptions(),
                  error: form.errors.current_user_id,
                  "onUpdate:modelValue": (value) => (form.currentUserId = value),
                }),
                h("p", { class: "muted small" }, t("chores.form.on_duty_hint")),
              ])
            : null,

          form.generalError ? h("p", { class: "form-error" }, form.generalError) : null,
          h("div", { class: "form-actions stack" }, [
            h(Button, {
              label: t("common.save"),
              type: "submit",
              block: true,
              busy: form.busy,
            }),
            h(Button, {
              label: t("common.cancel"),
              variant: "ghost",
              block: true,
              onClick: () => this.$emit("cancel"),
            }),
          ]),
        ],
      ),
    ]);
  },
};
