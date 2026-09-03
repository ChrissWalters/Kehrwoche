/**
 * Who is in credit, who is in debt.
 *
 * One bar per person, scaled against the largest amount on screen, green for credit and
 * red for debt. The bar width is set through Vue's style binding (CSSOM), not through a
 * style attribute in the markup — the strict CSP of AP29 forbids the latter.
 */

import { h, reactive, watch } from "../../../vendor/vue.esm-browser.prod.js";
import { api } from "../../api.js";
import { PersonName } from "../../components/avatar.js";
import { memberLabel, money } from "../../format.js";
import { t } from "../../i18n.js";
import { store } from "../../store.js";

function member(id) {
  return store.household?.members.find((entry) => entry.id === id) ?? null;
}

export const BalancesView = {
  setup() {
    const state = reactive({ rows: [], loading: true });

    async function load() {
      state.rows = await api.get("/expenses/balances").catch(() => []);
      state.loading = false;
    }

    load();
    watch(() => store.markers.expenses, load);

    return { state };
  },
  render() {
    const { state } = this;
    if (state.loading) {
      return h("p", { class: "muted" }, t("app.loading"));
    }

    const currency = store.household?.currency;
    const largest = Math.max(1, ...state.rows.map((row) => Math.abs(row.balance_cents)));

    return h("div", { class: "stack" }, [
      h("a", { class: "link-row", href: "#/expenses" }, [
        h("span", { "aria-hidden": "true" }, "‹"),
        h("span", t("nav.expenses")),
      ]),

      h("section", { class: "card stack" }, [
        h("h2", t("expenses.balances.title")),
        h(
          "ul",
          { class: "balance-list" },
          state.rows.map((row) => {
            const person = member(row.user_id);
            const credit = row.balance_cents > 0;
            const debt = row.balance_cents < 0;
            return h("li", { class: "balance" }, [
              h("div", { class: "balance-head" }, [
                person
                  ? h(PersonName, { person })
                  : h("span", row.first_name || memberLabel(row.user_id)),
                h(
                  "span",
                  { class: ["balance-amount", credit ? "is-credit" : "", debt ? "is-debt" : ""] },
                  money(row.balance_cents, currency),
                ),
              ]),
              h("div", { class: "balance-track" }, [
                h("span", {
                  class: ["balance-bar", credit ? "is-credit" : "", debt ? "is-debt" : ""],
                  style: { width: `${(Math.abs(row.balance_cents) / largest) * 100}%` },
                }),
              ]),
              h("span", { class: "muted small" }, [
                t("expenses.balances.paid", { amount: money(row.paid_cents, currency) }),
                " · ",
                t("expenses.balances.owed", { amount: money(row.owed_cents, currency) }),
              ]),
            ]);
          }),
        ),
        h("p", { class: "muted small" }, t("expenses.balances.hint")),
      ]),

      h("div", { class: "page-action" }, [
        h("a", { class: "btn btn--primary btn--block", href: "#/expenses/settlement" },
          t("expenses.settlement.title")),
      ]),
    ]);
  },
};
