/**
 * The panel behind the bell.
 *
 * Notifications arrive as i18n keys plus parameters and are turned into sentences here,
 * at display time — that is what lets everybody read them in their own language, no
 * matter which language they had when the notification was written.
 */

import { h, onMounted, onUnmounted, reactive } from "../../../vendor/vue.esm-browser.prod.js";
import { api } from "../../api.js";
import { Button } from "../../components/button.js";
import { showSnackbar } from "../../components/snackbar.js";
import { relativeTime } from "../../format.js";
import { t } from "../../i18n.js";
import { refreshState, store } from "../../store.js";

/** Where a notification leads when it is tapped. */
const TARGETS = {
  chore: "#/chores",
  feed_event: "#/",
  settlement_period: "#/expenses/archive",
  expense: "#/expenses",
  shopping_item: "#/shopping",
};

export const NotificationPanel = {
  emits: ["close"],
  setup(_, { emit }) {
    const state = reactive({
      items: [],
      cursor: null,
      loading: true,
      loadingMore: false,
      busy: false,
    });

    async function load(cursor = null) {
      const page = await api
        .get(`/notifications${cursor ? `?cursor=${cursor}` : ""}`)
        .catch(() => null);
      if (page) {
        state.items = cursor ? [...state.items, ...page.items] : page.items;
        state.cursor = page.next_cursor;
        store.unreadNotifications = page.unread;
      }
      state.loading = false;
      state.loadingMore = false;
    }

    async function loadMore() {
      state.loadingMore = true;
      await load(state.cursor);
    }

    async function open(item) {
      if (item.read_at === null) {
        item.read_at = new Date().toISOString();
        store.unreadNotifications = Math.max(0, store.unreadNotifications - 1);
        await api.post(`/notifications/${item.id}/read`).catch(() => refreshState());
      }
      const target = item.reference_type ? TARGETS[item.reference_type] : null;
      if (target) {
        window.location.hash = target;
      }
      emit("close");
    }

    async function readAll() {
      state.busy = true;
      try {
        const result = await api.post("/notifications/read-all");
        showSnackbar(t("notifications.all_read", { count: result.read }));
        await load();
        await refreshState();
      } finally {
        state.busy = false;
      }
    }

    const onKey = (event) => {
      if (event.key === "Escape") {
        emit("close");
      }
    };

    onMounted(() => {
      load();
      window.addEventListener("keydown", onKey);
    });
    onUnmounted(() => window.removeEventListener("keydown", onKey));

    return { state, loadMore, open, readAll };
  },
  render() {
    const { state } = this;
    const unread = state.items.some((item) => item.read_at === null);

    return h(
      "div",
      {
        class: "panel-backdrop",
        onClick: (event) => {
          if (event.target === event.currentTarget) {
            this.$emit("close");
          }
        },
      },
      [
        h(
          "section",
          {
            class: "notification-panel",
            role: "dialog",
            "aria-modal": "false",
            "aria-label": t("notifications.title"),
          },
          [
            // "All read" belongs where the eye already is. At the foot of the list it
            // sat behind every notification and behind "load more" — reachable only by
            // whoever scrolled to the very end, which is nobody with a full list.
            h("div", { class: "panel-head" }, [
              h("h2", t("notifications.title")),
              h("div", { class: "panel-head-actions" }, [
                unread
                  ? h(
                      "button",
                      {
                        class: "icon-button",
                        type: "button",
                        disabled: state.busy,
                        "aria-label": t("notifications.read_all"),
                        title: t("notifications.read_all"),
                        onClick: () => this.readAll(),
                      },
                      "✓",
                    )
                  : null,
                h(
                  "button",
                  {
                    class: "icon-button",
                    type: "button",
                    "aria-label": t("common.close"),
                    onClick: () => this.$emit("close"),
                  },
                  "×",
                ),
              ]),
            ]),

            state.loading
              ? h("p", { class: "muted" }, t("app.loading"))
              : state.items.length === 0
                ? h("p", { class: "muted" }, t("notifications.empty"))
                : h(
                    "ul",
                    { class: "notification-list" },
                    state.items.map((item) => this.renderItem(item)),
                  ),

            state.cursor
              ? h(Button, {
                  label: t("common.load_more"),
                  variant: "ghost",
                  block: true,
                  busy: state.loadingMore,
                  onClick: () => this.loadMore(),
                })
              : null,
          ],
        ),
      ],
    );
  },
  methods: {
    renderItem(item) {
      return h("li", { class: ["notification", item.read_at === null ? "is-unread" : ""] }, [
        h(
          "button",
          {
            class: "notification-button",
            type: "button",
            onClick: () => this.open(item),
          },
          [
            h("span", { class: "notification-title" }, t(item.title_key)),
            item.body_key
              ? h("span", { class: "notification-body" }, t(item.body_key, item.params))
              : null,
            h("span", { class: "muted small" }, relativeTime(item.created_at)),
          ],
        ),
      ]);
    },
  },
};
