/**
 * The shopping list — used with one hand, in a shop, often on a bad connection.
 *
 * Ticking off reacts immediately and rolls back if the server disagrees; typing is kept
 * short by suggestions that put the household's own items first.
 */

import { h, reactive, watch } from "../../../vendor/vue.esm-browser.prod.js";
import { api } from "../../api.js";
import { Button } from "../../components/button.js";
import { Dialog } from "../../components/dialog.js";
import { showSnackbar } from "../../components/snackbar.js";
import { errorText } from "../../error-text.js";
import { i18n, t } from "../../i18n.js";
import { refreshState, store } from "../../store.js";

/** Long enough to stop typing, short enough to feel instant. */
const SUGGEST_DELAY_MS = 200;

function member(id) {
  return store.household?.members.find((entry) => entry.id === id) ?? null;
}

export const ShoppingListView = {
  setup() {
    const state = reactive({
      items: [],
      loading: true,
      draft: "",
      suggestions: [],
      adding: false,
      clearing: false,
      error: null,
      /** Items whose tick the server has not confirmed yet. */
      pending: [],
    });

    let suggestTimer = null;

    async function load() {
      try {
        state.items = await api.get("/shopping");
        state.error = null;
      } catch (error) {
        state.error = errorText(error);
      } finally {
        state.loading = false;
      }
    }

    function typed(value) {
      state.draft = value;
      window.clearTimeout(suggestTimer);
      if (!value.trim()) {
        state.suggestions = [];
        return;
      }
      suggestTimer = window.setTimeout(async () => {
        const query = encodeURIComponent(value.trim());
        state.suggestions = await api
          .get(`/shopping/suggestions?q=${query}&locale=${i18n.locale}`)
          .catch(() => []);
      }, SUGGEST_DELAY_MS);
    }

    async function add(name) {
      const wanted = (name ?? state.draft).trim();
      if (!wanted) {
        return;
      }
      state.adding = true;
      state.draft = "";
      state.suggestions = [];
      try {
        await api.post("/shopping", { name: wanted });
        await load();
        await refreshState();
      } catch (error) {
        state.draft = wanted;
        showSnackbar(errorText(error));
      } finally {
        state.adding = false;
      }
    }

    async function toggle(item) {
      // Optimistic: the tick appears at once, the request follows. Until the server
      // confirms, the row stays marked — in a shop nobody should pocket the phone
      // believing something was saved that never left the device.
      const before = { bought: item.bought, buyer_id: item.buyer_id };
      item.bought = !item.bought;
      item.buyer_id = item.bought ? store.me?.id : null;
      state.pending = [...state.pending, item.id];
      try {
        const updated = await api.post(`/shopping/${item.id}/toggle`);
        Object.assign(item, updated);
        await refreshState();
      } catch (error) {
        Object.assign(item, before);
        showSnackbar(errorText(error));
      } finally {
        state.pending = state.pending.filter((id) => id !== item.id);
      }
    }

    async function togglePriority(item) {
      const before = item.priority;
      item.priority = !item.priority;
      try {
        Object.assign(item, await api.patch(`/shopping/${item.id}`, { priority: item.priority }));
        await load();
      } catch (error) {
        item.priority = before;
        showSnackbar(errorText(error));
      }
    }

    async function remove(item) {
      try {
        await api.delete(`/shopping/${item.id}`);
        await load();
        showSnackbar(t("shopping.removed", { name: item.name }), {
          actionLabel: t("common.undo"),
          // There is nothing to restore server side, so the item is written again.
          action: () => add(item.name),
        });
      } catch (error) {
        showSnackbar(errorText(error));
      }
    }

    async function clearBought() {
      try {
        const result = await api.post("/shopping/clear-bought");
        state.clearing = false;
        await load();
        await refreshState();
        showSnackbar(t("shopping.cleared", { count: result.removed }));
      } catch (error) {
        state.clearing = false;
        showSnackbar(errorText(error));
      }
    }

    load();
    // Somebody else ticking something off in the shop shows up here within 15 seconds.
    watch(() => store.markers.shopping, load);

    return { state, typed, add, toggle, togglePriority, remove, clearBought };
  },
  render() {
    const { state } = this;
    if (state.loading) {
      return h("p", { class: "muted" }, t("app.loading"));
    }

    const open = state.items.filter((item) => !item.bought);
    const bought = state.items.filter((item) => item.bought);

    return h("div", { class: "stack" }, [
      state.clearing
        ? h(Dialog, {
            title: t("shopping.clear_title"),
            message: t("shopping.clear_confirm", { count: bought.length }),
            confirmLabel: t("shopping.clear"),
            cancelLabel: t("common.cancel"),
            destructive: true,
            onConfirm: () => this.clearBought(),
            onCancel: () => (state.clearing = false),
          })
        : null,

      state.error ? h("p", { class: "form-error" }, state.error) : null,

      h("section", { class: "card" }, [
        h(
          "form",
          {
            class: "shopping-add",
            onSubmit: (event) => {
              event.preventDefault();
              this.add();
            },
          },
          [
            h("input", {
              class: "field-input",
              type: "text",
              value: state.draft,
              placeholder: t("shopping.placeholder"),
              "aria-label": t("shopping.placeholder"),
              autocomplete: "off",
              enterkeyhint: "done",
              onInput: (event) => this.typed(event.target.value),
            }),
            h(Button, {
              label: t("shopping.add"),
              type: "submit",
              busy: state.adding,
            }),
          ],
        ),
        state.suggestions.length > 0
          ? h(
              "ul",
              { class: "suggestions" },
              // One tap adds the item — no need to finish typing.
              state.suggestions.map((name) =>
                h("li", [
                  h(
                    "button",
                    { class: "suggestion", type: "button", onClick: () => this.add(name) },
                    name,
                  ),
                ]),
              ),
            )
          : null,
      ]),

      open.length === 0 && bought.length === 0
        ? h("section", { class: "card" }, [
            h("h2", t("shopping.empty.title")),
            h("p", { class: "muted" }, t("shopping.empty.hint")),
          ])
        : null,

      open.length > 0
        ? h("ul", { class: "shopping-list" }, open.map((item) => this.renderItem(item)))
        : null,

      bought.length > 0
        ? h("section", { class: "stack" }, [
            h("h2", { class: "section-title" }, t("shopping.bought_section", {
              count: bought.length,
            })),
            h("ul", { class: "shopping-list" }, bought.map((item) => this.renderItem(item))),
            h("div", { class: "page-action" }, [
              h(Button, {
                label: t("shopping.clear"),
                variant: "secondary",
                block: true,
                onClick: () => (state.clearing = true),
              }),
            ]),
          ])
        : null,
    ]);
  },
  methods: {
    renderItem(item) {
      const buyer = item.bought ? member(item.buyer_id) : null;
      const pending = this.state.pending.includes(item.id);
      const classes = ["shopping-item", item.bought ? "is-bought" : "", pending ? "is-pending" : ""];

      return h("li", { class: classes }, [
        // The whole row is the target: a small checkbox is hard to hit while walking.
        h(
          "button",
          {
            class: "shopping-tick",
            type: "button",
            "aria-pressed": String(item.bought),
            onClick: () => this.toggle(item),
          },
          [
            h("span", { class: "tick", "aria-hidden": "true" }, item.bought ? "☑" : "☐"),
            h("span", { class: "shopping-text" }, [
              h("span", { class: "shopping-name" }, item.name),
              h("span", { class: "muted small" }, this.itemSubtitle(item, buyer, pending)),
            ]),
          ],
        ),
        h(
          "button",
          {
            class: ["icon-button", item.priority ? "is-active" : ""],
            type: "button",
            "aria-label": t("shopping.priority"),
            "aria-pressed": String(item.priority),
            onClick: () => this.togglePriority(item),
          },
          "★",
        ),
        h(
          "button",
          {
            class: "icon-button",
            type: "button",
            "aria-label": t("common.delete"),
            onClick: () => this.remove(item),
          },
          "×",
        ),
      ]);
    },
    /** Second line of a row: what is still being saved, or note and buyer. */
    itemSubtitle(item, buyer, pending) {
      if (pending) {
        return t("shopping.saving");
      }
      const parts = [item.note, buyer ? t("shopping.bought_by", { name: buyer.first_name }) : null];
      return parts.filter(Boolean).join(" · ");
    },
  },
};
