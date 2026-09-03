/** Leader board and the log of what was done. */

import { h, reactive, watch } from "../../../vendor/vue.esm-browser.prod.js";
import { api } from "../../api.js";
import { Avatar, PersonName } from "../../components/avatar.js";
import { Button } from "../../components/button.js";
import { Dialog } from "../../components/dialog.js";
import { showSnackbar } from "../../components/snackbar.js";
import { errorText } from "../../error-text.js";
import { dateTime, relativeTime } from "../../format.js";
import { t } from "../../i18n.js";
import { store } from "../../store.js";

function member(id) {
  return store.household?.members.find((entry) => entry.id === id) ?? null;
}

function memberLabel(id) {
  const person = member(id);
  if (!person) {
    return "";
  }
  const name = [person.first_name, person.last_name].filter(Boolean).join(" ");
  return person.username ? `${name} (${person.username})` : name;
}

export const ChoreStatisticsView = {
  setup() {
    const state = reactive({
      rows: [],
      history: [],
      cursor: null,
      loading: true,
      loadingMore: false,
      resetting: false,
    });

    async function loadStatistics() {
      state.rows = await api.get("/chores/statistics").catch(() => []);
    }

    async function loadHistory(cursor = null) {
      const query = cursor ? `?cursor=${cursor}` : "";
      const page = await api.get(`/chores/history${query}`).catch(() => null);
      if (!page) {
        return;
      }
      state.history = cursor ? [...state.history, ...page.items] : page.items;
      state.cursor = page.next_cursor;
    }

    async function loadMore() {
      state.loadingMore = true;
      await loadHistory(state.cursor);
      state.loadingMore = false;
    }

    async function reset() {
      try {
        await api.post("/chores/reset-statistics");
        state.resetting = false;
        await Promise.all([loadStatistics(), loadHistory()]);
        showSnackbar(t("chores.stats.reset_done"));
      } catch (error) {
        state.resetting = false;
        showSnackbar(errorText(error));
      }
    }

    Promise.all([loadStatistics(), loadHistory()]).finally(() => (state.loading = false));
    watch(() => store.markers.chores, () => Promise.all([loadStatistics(), loadHistory()]));

    return { state, loadMore, reset };
  },
  render() {
    const { state } = this;
    if (state.loading) {
      return h("p", { class: "muted" }, t("app.loading"));
    }

    const isAdmin = store.me?.role === "admin";

    return h("div", { class: "stack" }, [
      h("a", { class: "link-row", href: "#/chores" }, [
        h("span", { "aria-hidden": "true" }, "‹"),
        h("span", t("nav.chores")),
      ]),
      state.resetting
        ? h(Dialog, {
            title: t("chores.stats.reset"),
            message: t("chores.stats.reset_confirm"),
            confirmLabel: t("chores.stats.reset"),
            cancelLabel: t("common.cancel"),
            destructive: true,
            onConfirm: () => this.reset(),
            onCancel: () => (state.resetting = false),
          })
        : null,

      h("section", { class: "card" }, [
        h("h2", t("chores.stats.ranking")),
        h(
          "ol",
          { class: "ranking" },
          state.rows.map((row, index) =>
            h("li", { class: "ranking-row" }, [
              h("span", { class: "ranking-place" }, `${index + 1}.`),
              h(Avatar, { person: member(row.user_id) ?? { first_name: row.first_name } }),
              h(PersonName, { person: member(row.user_id) ?? { first_name: row.first_name } }),
              h("span", { class: "member-points" }, [
                t("chores.points", { points: row.points }),
                h("span", { class: "muted small" }, ` · ${t("chores.stats.done_recent", {
                  count: row.completions_recent,
                })}`),
              ]),
            ]),
          ),
        ),
      ]),

      h("section", { class: "card" }, [
        h("h2", t("chores.stats.history")),
        state.history.length === 0
          ? h("p", { class: "muted" }, t("chores.stats.history_empty"))
          : h(
              "ul",
              { class: "history-list" },
              state.history.map((entry) =>
                h("li", { class: "history-row" }, [
                  h("span", { class: "member-name" }, entry.chore_title),
                  h("span", { class: "muted small" }, [
                    memberLabel(entry.user_id),
                    entry.booked_by_id
                      ? ` (${t("chores.booked_by", { name: memberLabel(entry.booked_by_id) })})`
                      : "",
                    " · ",
                    h("time", { datetime: entry.done_at, title: dateTime(entry.done_at) },
                      relativeTime(entry.done_at)),
                  ]),
                ]),
              ),
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
      ]),

      isAdmin
        ? h("section", { class: "card" }, [
            h(Button, {
              label: t("chores.stats.reset"),
              variant: "ghost",
              block: true,
              onClick: () => (state.resetting = true),
            }),
          ])
        : null,
    ]);
  },
};
