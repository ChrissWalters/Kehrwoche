/**
 * Record or correct an expense.
 *
 * Everything is pre-filled the way an expense is usually entered at the till: paid by
 * me, today, split evenly across the whole household. The preview shows what each
 * person will carry before the entry is saved — the division must never be a surprise.
 */

import { h, reactive } from "../../../vendor/vue.esm-browser.prod.js";
import { ApiError, api } from "../../api.js";
import { Button } from "../../components/button.js";
import { Field, SelectField } from "../../components/field.js";
import { errorText } from "../../error-text.js";
import { money, parseAmount, splitEvenly, today } from "../../format.js";
import { t } from "../../i18n.js";
import { onEscape } from "../../keyboard.js";
import { store } from "../../store.js";

function members() {
  return store.household?.members ?? [];
}

function memberName(id) {
  const person = members().find((entry) => entry.id === id);
  if (!person) {
    return String(id);
  }
  const name = [person.first_name, person.last_name].filter(Boolean).join(" ");
  return person.username ? `${name} (${person.username})` : name;
}

export const ExpenseForm = {
  props: {
    expense: { type: Object, default: null },
  },
  emits: ["saved", "cancel"],
  setup(props, { emit }) {
    onEscape(() => emit("cancel"));
    const existing = props.expense;
    const form = reactive({
      title: existing?.title ?? "",
      amount: existing ? (existing.amount_cents / 100).toFixed(2) : "",
      spentAt: existing?.spent_at ?? today(),
      paidById: String(existing?.paid_by_id ?? store.me?.id ?? ""),
      participants: existing
        ? existing.shares.map((share) => share.user_id)
        : members().map((person) => person.id),
      busy: false,
      errors: {},
      generalError: null,
    });

    function toggleParticipant(id) {
      form.participants = form.participants.includes(id)
        ? form.participants.filter((entry) => entry !== id)
        : [...form.participants, id];
    }

    function allParticipants() {
      form.participants = members().map((person) => person.id);
    }

    async function save() {
      const amountCents = parseAmount(form.amount);
      form.errors = {};
      form.generalError = null;
      if (amountCents === null || amountCents <= 0) {
        form.errors.amount_cents = t("expenses.form.invalid_amount");
        return;
      }
      if (form.participants.length === 0) {
        form.generalError = t("expenses.form.no_participants");
        return;
      }

      const payload = {
        title: form.title.trim(),
        amount_cents: amountCents,
        paid_by_id: Number(form.paidById),
        spent_at: form.spentAt,
        participants: [...form.participants].sort((a, b) => a - b),
      };

      form.busy = true;
      try {
        const saved = existing
          ? await api.patch(`/expenses/${existing.id}`, payload)
          : await api.post("/expenses", payload);
        emit("saved", saved);
      } catch (error) {
        if (error instanceof ApiError && error.field) {
          form.errors[error.field] = errorText(error);
        } else {
          form.generalError = errorText(error);
        }
      } finally {
        form.busy = false;
      }
    }

    return { form, toggleParticipant, allParticipants, save };
  },
  render() {
    const { form } = this;
    const currency = store.household?.currency;
    const amountCents = parseAmount(form.amount) ?? 0;
    const preview = splitEvenly(amountCents, form.participants);
    const everybody = form.participants.length === members().length;

    return h(
      "form",
      {
        class: "card stack",
        onSubmit: (event) => {
          event.preventDefault();
          this.save();
        },
      },
      [
        h("h2", this.expense ? t("expenses.form.title_edit") : t("expenses.form.title_new")),

        h(Field, {
          label: t("expenses.form.what"),
          modelValue: form.title,
          "onUpdate:modelValue": (value) => (form.title = value),
          error: form.errors.title,
          required: true,
          maxlength: 120,
        }),
        h(Field, {
          label: t("expenses.form.amount"),
          modelValue: form.amount,
          "onUpdate:modelValue": (value) => (form.amount = value),
          error: form.errors.amount_cents,
          // A decimal keypad, not a full keyboard — and no spinner that changes values
          // by a stray swipe.
          inputmode: "decimal",
          placeholder: t("expenses.form.amount_hint"),
          required: true,
        }),
        h(Field, {
          label: t("expenses.form.date"),
          type: "date",
          modelValue: form.spentAt,
          "onUpdate:modelValue": (value) => (form.spentAt = value),
          error: form.errors.spent_at,
        }),
        h(SelectField, {
          label: t("expenses.form.paid_by"),
          modelValue: form.paidById,
          "onUpdate:modelValue": (value) => (form.paidById = value),
          options: members().map((person) => ({
            value: String(person.id),
            label: memberName(person.id),
          })),
          error: form.errors.paid_by_id,
        }),

        h("div", { class: "field" }, [
          h("span", { class: "field-label" }, t("expenses.form.participants")),
          h(
            "ul",
            { class: "chip-list" },
            members().map((person) => {
              const chosen = form.participants.includes(person.id);
              return h("li", [
                h(
                  "button",
                  {
                    class: ["chip", chosen ? "is-chosen" : ""],
                    type: "button",
                    "aria-pressed": String(chosen),
                    onClick: () => this.toggleParticipant(person.id),
                  },
                  person.first_name,
                ),
              ]);
            }),
          ),
          !everybody
            ? h(
                "button",
                { class: "link-button", type: "button", onClick: () => this.allParticipants() },
                t("expenses.form.select_all"),
              )
            : null,
        ]),

        preview.length > 0 && amountCents > 0
          ? h("div", { class: "share-preview" }, [
              h("span", { class: "field-label" }, t("expenses.form.preview")),
              h(
                "ul",
                { class: "share-list" },
                preview.map((share) =>
                  h("li", [
                    h("span", memberName(share.user_id)),
                    h("span", { class: "share-amount" }, money(share.share_cents, currency)),
                  ]),
                ),
              ),
            ])
          : null,

        form.generalError ? h("p", { class: "form-error" }, form.generalError) : null,

        h("div", { class: "form-actions" }, [
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
    );
  },
};
