/**
 * The payment proposal, and the admin action that freezes it.
 *
 * "Bea pays Alex 12,34 €" — one line per payment, in the fewest transfers the balances
 * allow. Archiving is destructive and irreversible, so it asks first.
 */

import { h, reactive, watch } from "../../../vendor/vue.esm-browser.prod.js";
import { api } from "../../api.js";
import { Button } from "../../components/button.js";
import { Dialog } from "../../components/dialog.js";
import { showSnackbar } from "../../components/snackbar.js";
import { errorText } from "../../error-text.js";
import { memberLabel, money } from "../../format.js";
import { t } from "../../i18n.js";
import { refreshState, store } from "../../store.js";



export const SettlementView = {
  setup() {
    const state = reactive({
      payments: [],
      /** Names the server allows for people who are no longer members. */
      names: [],
      /** Whether the open period holds anything at all — an empty one cannot be closed. */
      hasOpenExpenses: false,
      loading: true,
      archiving: false,
      busy: false,
    });

    async function load() {
      const [settlement, page] = await Promise.all([
        api.get("/expenses/settlement").catch(() => null),
        api.get("/expenses?limit=1").catch(() => null),
      ]);
      state.payments = settlement?.payments ?? [];
      state.names = settlement?.names ?? [];
      state.hasOpenExpenses = (page?.items.length ?? 0) > 0;
      state.loading = false;
    }

    async function archive() {
      state.busy = true;
      try {
        await api.post("/expenses/archive");
        state.archiving = false;
        await load();
        await refreshState();
        showSnackbar(t("expenses.archive.done"));
        window.location.hash = "#/expenses/archive";
      } catch (error) {
        state.archiving = false;
        showSnackbar(errorText(error));
      } finally {
        state.busy = false;
      }
    }

    load();
    watch(() => store.markers.expenses, load);

    return { state, archive };
  },
  render() {
    const { state } = this;
    if (state.loading) {
      return h("p", { class: "muted" }, t("app.loading"));
    }

    const currency = store.household?.currency;
    const isAdmin = store.me?.role === "admin";
    const nothingToDo = state.payments.length === 0;

    return h("div", { class: "stack" }, [
      state.archiving
        ? h(Dialog, {
            title: t("expenses.archive.action"),
            message: t("expenses.archive.confirm"),
            confirmLabel: t("expenses.archive.action"),
            cancelLabel: t("common.cancel"),
            destructive: true,
            busy: state.busy,
            onConfirm: () => this.archive(),
            onCancel: () => (state.archiving = false),
          })
        : null,

      h("a", { class: "link-row", href: "#/expenses/balances" }, [
        h("span", { "aria-hidden": "true" }, "‹"),
        h("span", t("expenses.balances.title")),
      ]),

      h("section", { class: "card stack" }, [
        h("h2", t("expenses.settlement.title")),
        nothingToDo
          ? h("p", { class: "muted" }, t("expenses.settlement.none"))
          : h(
              "ul",
              { class: "payment-list" },
              state.payments.map((payment) =>
                h("li", { class: "payment" }, [
                  // One key for the whole sentence: word order is the translator's job.
                  h(
                    "span",
                    { class: "payment-text" },
                    t("expenses.settlement.pays", {
                      from: memberLabel(payment.from_user_id, state.names),
                      to: memberLabel(payment.to_user_id, state.names),
                    }),
                  ),
                  h("span", { class: "payment-amount" }, money(payment.amount_cents, currency)),
                ]),
              ),
            ),
        h("p", { class: "muted small" }, t("expenses.settlement.hint")),
      ]),

      isAdmin
        ? h("div", { class: "page-action" }, [
            h(Button, {
              label: t("expenses.archive.action"),
              variant: "secondary",
              block: true,
              disabled: !state.hasOpenExpenses,
              onClick: () => (state.archiving = true),
            }),
          ])
        : h("p", { class: "muted small" }, t("expenses.archive.admin_only")),
    ]);
  },
};
