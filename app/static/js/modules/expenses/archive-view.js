/**
 * Closed settlement periods.
 *
 * The archive is read-only by design: what was settled stays settled. Tapping a period
 * unfolds what it froze — the expenses and the payments that balanced them.
 */

import { h, reactive } from "../../../vendor/vue.esm-browser.prod.js";
import { api } from "../../api.js";
import { dateLabel, dateTime, memberLabel, money } from "../../format.js";
import { t } from "../../i18n.js";
import { store } from "../../store.js";



export const ArchiveView = {
  setup() {
    const state = reactive({
      periods: [],
      loading: true,
      /** Id of the period that is unfolded, plus its details. */
      openId: null,
      detail: null,
    });

    async function load() {
      state.periods = await api.get("/expenses/periods").catch(() => []);
      state.loading = false;
    }

    async function toggle(period) {
      if (state.openId === period.id) {
        state.openId = null;
        state.detail = null;
        return;
      }
      state.openId = period.id;
      state.detail = await api.get(`/expenses/periods/${period.id}`).catch(() => null);
    }

    load();

    return { state, toggle };
  },
  render() {
    const { state } = this;
    if (state.loading) {
      return h("p", { class: "muted" }, t("app.loading"));
    }

    const currency = store.household?.currency;

    return h("div", { class: "stack" }, [
      h("a", { class: "link-row", href: "#/expenses" }, [
        h("span", { "aria-hidden": "true" }, "‹"),
        h("span", t("nav.expenses")),
      ]),

      h("h2", t("expenses.archive.title")),

      state.periods.length === 0
        ? h("section", { class: "card" }, [
            h("p", { class: "muted" }, t("expenses.archive.empty")),
          ])
        : h(
            "ul",
            { class: "period-list" },
            state.periods.map((period) =>
              h("li", { class: "card stack" }, [
                h(
                  "button",
                  {
                    class: "period-head",
                    type: "button",
                    "aria-expanded": String(state.openId === period.id),
                    onClick: () => this.toggle(period),
                  },
                  [
                    h("span", { class: "period-text" }, [
                      h("span", t("expenses.archive.period", { date: dateTime(period.closed_at) })),
                      h(
                        "span",
                        { class: "muted small" },
                        t("expenses.archive.summary", {
                          count: period.expense_count,
                          total: money(period.total_cents, currency),
                        }),
                      ),
                    ]),
                    h("span", { "aria-hidden": "true" }, state.openId === period.id ? "▾" : "›"),
                  ],
                ),
                state.openId === period.id && state.detail
                  ? this.renderDetail(state.detail, currency)
                  : null,
              ]),
            ),
          ),
    ]);
  },
  methods: {
    renderDetail(detail, currency) {
      return h("div", { class: "stack" }, [
        h("h3", { class: "section-title" }, t("expenses.archive.payments")),
        detail.payments.length === 0
          ? h("p", { class: "muted small" }, t("expenses.settlement.none"))
          : h(
              "ul",
              { class: "payment-list" },
              detail.payments.map((payment) =>
                h("li", { class: "payment" }, [
                  h(
                    "span",
                    { class: "payment-text" },
                    t("expenses.settlement.pays", {
                      from: memberLabel(payment.from_user_id, detail.names),
                      to: memberLabel(payment.to_user_id, detail.names),
                    }),
                  ),
                  h("span", { class: "payment-amount" }, money(payment.amount_cents, currency)),
                ]),
              ),
            ),
        h("h3", { class: "section-title" }, t("expenses.archive.expenses")),
        h(
          "ul",
          { class: "payment-list" },
          detail.expenses.map((expense) =>
            h("li", { class: "payment" }, [
              h("span", { class: "payment-text" }, [
                expense.title,
                h("span", { class: "muted small" }, ` · ${dateLabel(expense.spent_at)}`),
              ]),
              h("span", { class: "payment-amount" }, money(expense.amount_cents, currency)),
            ]),
          ),
        ),
      ]);
    },
  },
};
