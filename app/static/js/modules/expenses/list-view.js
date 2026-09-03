/**
 * The expenses of the open period.
 *
 * Money is entered right at the till, so the list stays short and the entry form is one
 * tap away in the thumb zone. Deleting asks first — an expense somebody else relies on
 * is not an everyday action.
 */

import { h, reactive, watch } from "../../../vendor/vue.esm-browser.prod.js";
import { api } from "../../api.js";
import { Button } from "../../components/button.js";
import { Dialog } from "../../components/dialog.js";
import { showSnackbar } from "../../components/snackbar.js";
import { errorText } from "../../error-text.js";
import { dateLabel, money } from "../../format.js";
import { t } from "../../i18n.js";
import { refreshState, store } from "../../store.js";
import { ExpenseForm } from "./form-view.js";

function member(id) {
  return store.household?.members.find((entry) => entry.id === id) ?? null;
}

export const ExpenseListView = {
  setup() {
    const state = reactive({
      items: [],
      cursor: null,
      loading: true,
      loadingMore: false,
      error: null,
      /** null, "new" or the expense being edited. */
      editing: null,
      /** Expense awaiting the delete confirmation. */
      deleting: null,
      /** Only what concerns me: paid by me or with a share of mine. */
      mine: false,
    });

    async function load(cursor = null) {
      const query = new URLSearchParams();
      if (state.mine) {
        query.set("mine", "true");
      }
      if (cursor) {
        query.set("cursor", cursor);
      }
      try {
        const page = await api.get(`/expenses?${query.toString()}`);
        state.items = cursor ? [...state.items, ...page.items] : page.items;
        state.cursor = page.next_cursor;
        state.error = null;
      } catch (error) {
        state.error = errorText(error);
      } finally {
        state.loading = false;
        state.loadingMore = false;
      }
    }

    async function loadMore() {
      state.loadingMore = true;
      await load(state.cursor);
    }

    function setFilter(mine) {
      if (state.mine === mine) {
        return;
      }
      state.mine = mine;
      state.loading = true;
      load();
    }

    async function saved() {
      state.editing = null;
      await load();
      await refreshState();
      showSnackbar(t("expenses.saved"));
    }

    async function remove() {
      const expense = state.deleting;
      state.deleting = null;
      try {
        await api.delete(`/expenses/${expense.id}`);
        await load();
        await refreshState();
        showSnackbar(t("expenses.deleted"));
      } catch (error) {
        showSnackbar(errorText(error));
      }
    }

    load();
    // Somebody entering the shopping bill on their phone shows up here on its own.
    watch(() => store.markers.expenses, () => load());

    return { state, load, loadMore, setFilter, saved, remove };
  },
  render() {
    const { state } = this;
    if (state.loading) {
      return h("p", { class: "muted" }, t("app.loading"));
    }
    if (state.editing) {
      return h(ExpenseForm, {
        expense: state.editing === "new" ? null : state.editing,
        onSaved: () => this.saved(),
        onCancel: () => (state.editing = null),
      });
    }

    return h("div", { class: "stack expenses-view" }, [
      state.deleting
        ? h(Dialog, {
            title: t("expenses.delete_title"),
            message: t("expenses.delete_confirm", { title: state.deleting.title }),
            confirmLabel: t("common.delete"),
            cancelLabel: t("common.cancel"),
            destructive: true,
            onConfirm: () => this.remove(),
            onCancel: () => (state.deleting = null),
          })
        : null,

      h("div", { class: "segmented", role: "group", "aria-label": t("expenses.filter.label") }, [
        h(
          "button",
          {
            class: ["segment", state.mine ? "" : "is-active"],
            type: "button",
            "aria-pressed": String(!state.mine),
            onClick: () => this.setFilter(false),
          },
          t("expenses.filter.all"),
        ),
        h(
          "button",
          {
            class: ["segment", state.mine ? "is-active" : ""],
            type: "button",
            "aria-pressed": String(state.mine),
            onClick: () => this.setFilter(true),
          },
          t("expenses.filter.mine"),
        ),
      ]),

      // Balances and archive belong within reach, not behind the whole list.
      h("nav", { class: "link-rows", "aria-label": t("expenses.balances.title") }, [
        h("a", { class: "link-row", href: "#/expenses/balances" }, [
          h("span", t("expenses.balances.title")),
          h("span", { "aria-hidden": "true" }, "›"),
        ]),
        h("a", { class: "link-row", href: "#/expenses/archive" }, [
          h("span", t("expenses.archive.title")),
          h("span", { "aria-hidden": "true" }, "›"),
        ]),
      ]),

      state.error ? h("p", { class: "form-error" }, state.error) : null,

      state.items.length === 0
        ? h("section", { class: "card" }, [
            h("h2", t("expenses.empty.title")),
            h("p", { class: "muted" }, t("expenses.empty.hint")),
          ])
        : h(
            "ul",
            { class: "expense-list" },
            state.items.map((expense) => this.renderExpense(expense)),
          ),

      state.cursor
        ? h(Button, {
            label: t("common.load_more"),
            variant: "secondary",
            block: true,
            busy: state.loadingMore,
            onClick: () => this.loadMore(),
          })
        : null,

      // Pinned above the tab bar: with a long list the button would otherwise sit an
      // endless scroll away from the thumb that needs it.
      h("div", { class: "pinned-action" }, [
        h(Button, {
          label: t("expenses.add"),
          block: true,
          onClick: () => (state.editing = "new"),
        }),
      ]),
    ]);
  },
  methods: {
    renderExpense(expense) {
      const currency = store.household?.currency;
      const payer = member(expense.paid_by_id);
      const own = expense.shares.find((share) => share.user_id === store.me?.id);

      return h("li", { class: "card expense" }, [
        h("div", { class: "expense-head" }, [
          h("div", { class: "expense-text" }, [
            h("span", { class: "expense-title" }, expense.title),
            h("span", { class: "expense-meta" }, [
              payer ? t("expenses.paid_by", { name: payer.first_name }) : "",
              " · ",
              dateLabel(expense.spent_at),
            ]),
          ]),
          h("span", { class: "expense-amount" }, money(expense.amount_cents, currency)),
        ]),
        h("p", { class: "muted small" }, [
          t("expenses.participants", { count: expense.shares.length }),
          own ? ` · ${t("expenses.your_share", { amount: money(own.share_cents, currency) })}` : "",
        ]),
        h("div", { class: "expense-actions" }, [
          h(Button, {
            label: t("common.edit"),
            variant: "ghost",
            onClick: () => (this.state.editing = expense),
          }),
          h(Button, {
            label: t("common.delete"),
            variant: "ghost",
            onClick: () => (this.state.deleting = expense),
          }),
        ]),
      ]);
    },
  },
};
