/**
 * The pinboard: what the household did, plus what it wants to tell each other.
 *
 * System events and posts share one stream. Reading is the main activity here, so the
 * list scrolls endlessly and the composer stays within reach at the bottom edge —
 * a button below an endless list would drift away as soon as anybody scrolls.
 */

import { h, onMounted, onUnmounted, reactive, watch } from "../../../vendor/vue.esm-browser.prod.js";
import { api } from "../../api.js";
import { Avatar, PersonName } from "../../components/avatar.js";
import { Button } from "../../components/button.js";
import { Dialog } from "../../components/dialog.js";
import { showSnackbar } from "../../components/snackbar.js";
import { errorText } from "../../error-text.js";
import { memberLabel, relativeTime } from "../../format.js";
import { t } from "../../i18n.js";
import { refreshState, store } from "../../store.js";

/** Icon per event type — the eye finds the module before the text is read. */
const ICONS = {
  chore_created: "🧹",
  chore_updated: "✏️",
  chore_deleted: "🗑️",
  chore_done: "✅",
  chore_statistics_reset: "🏆",
  shopping_added: "🛒",
  shopping_bought_bulk: "🧺",
  expense_added: "💶",
  settlement_archived: "🧾",
  member_joined: "👋",
  member_left: "📦",
  user_post: "📌",
};

/** Where an entry leads: from the referenced object into its module. */
const TARGETS = {
  chore: { route: "#/chores", label: "feed.open.chores" },
  shopping_item: { route: "#/shopping", label: "feed.open.shopping" },
  expense: { route: "#/expenses", label: "feed.open.expenses" },
  settlement_period: { route: "#/expenses/archive", label: "feed.open.archive" },
  user: { route: "#/more", label: "feed.open.members" },
};

/** Types whose text names the referenced person, not whoever pressed the button. */
const ABOUT_THE_MEMBER = ["member_joined", "member_left"];

function member(id) {
  return store.household?.members.find((entry) => entry.id === id) ?? null;
}



/** The sentence of a system entry, assembled from one key per type. */
function eventText(event) {
  const id = ABOUT_THE_MEMBER.includes(event.type) ? event.reference_id : event.actor_id;
  const key = ICONS[event.type] ? `feed.event.${event.type}` : "feed.event.unknown";
  // An edit travels as the names of the fields it touched, not as a finished list —
  // that is what lets everybody read it in their own language.
  const fields = (event.params?.fields ?? []).map((name) => t(`chores.field.${name}`)).join(", ");
  return t(key, {
    name: memberLabel(id),
    item: event.body ?? "",
    count: event.body ?? "",
    fields,
  });
}

export const FeedView = {
  setup() {
    const state = reactive({
      items: [],
      cursor: null,
      loading: true,
      loadingMore: false,
      error: null,
      draft: "",
      posting: false,
      /** Id of the entry whose comments are unfolded. */
      openComments: null,
      comments: [],
      commentDraft: "",
      commenting: false,
      /** Post awaiting the delete confirmation. */
      deleting: null,
    });

    let observer = null;

    async function load(cursor = null) {
      try {
        const page = await api.get(`/feed${cursor ? `?cursor=${cursor}` : ""}`);
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
      if (state.cursor === null || state.loadingMore) {
        return;
      }
      state.loadingMore = true;
      await load(state.cursor);
    }

    async function submitPost() {
      const body = state.draft.trim();
      if (!body) {
        return;
      }
      state.posting = true;
      try {
        await api.post("/feed", { body });
        state.draft = "";
        await load();
        await refreshState();
      } catch (error) {
        showSnackbar(errorText(error));
      } finally {
        state.posting = false;
      }
    }

    async function remove() {
      const entry = state.deleting;
      state.deleting = null;
      try {
        await api.delete(`/feed/${entry.id}`);
        await load();
        await refreshState();
        showSnackbar(t("feed.deleted"));
      } catch (error) {
        showSnackbar(errorText(error));
      }
    }

    async function toggleLike(entry) {
      // Optimistic: the heart reacts at once, the server confirms afterwards.
      const before = { liked_by_me: entry.liked_by_me, like_count: entry.like_count };
      entry.liked_by_me = !entry.liked_by_me;
      entry.like_count += entry.liked_by_me ? 1 : -1;
      try {
        const result = await api.post(`/feed/${entry.id}/like`);
        entry.liked_by_me = result.liked;
        entry.like_count = result.like_count;
      } catch (error) {
        Object.assign(entry, before);
        showSnackbar(errorText(error));
      }
    }

    async function toggleComments(entry) {
      if (state.openComments === entry.id) {
        state.openComments = null;
        state.comments = [];
        return;
      }
      state.openComments = entry.id;
      state.comments = await api.get(`/feed/${entry.id}/comments`).catch(() => []);
      // Reading them clears the marker on the server; the card follows suit.
      entry.comments_unread = 0;
    }

    async function submitComment(entry) {
      const body = state.commentDraft.trim();
      if (!body) {
        return;
      }
      state.commenting = true;
      try {
        const comment = await api.post(`/feed/${entry.id}/comments`, { body });
        state.comments = [...state.comments, comment];
        state.commentDraft = "";
        entry.comment_count += 1;
        await refreshState();
      } catch (error) {
        showSnackbar(errorText(error));
      } finally {
        state.commenting = false;
      }
    }

    /** Endless scrolling, with the button below it as the way that always works. */
    function observe(element) {
      if (observer !== null || !element || typeof IntersectionObserver === "undefined") {
        return;
      }
      observer = new IntersectionObserver((entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          loadMore();
        }
      });
      observer.observe(element);
    }

    async function reloadOpenComments() {
      if (state.openComments === null) {
        return;
      }
      // Unfolded comments have to follow the marker too — otherwise the card counts a
      // new comment while the list below it still shows the old ones.
      state.comments = await api
        .get(`/feed/${state.openComments}/comments`)
        .catch(() => state.comments);
      const entry = state.items.find((item) => item.id === state.openComments);
      if (entry) {
        entry.comment_count = state.comments.length;
        entry.comments_unread = 0;
      }
    }

    onMounted(() => load());
    onUnmounted(() => observer?.disconnect());
    // Somebody else posting or commenting shows up without a reload.
    watch(
      () => store.markers.feed,
      async () => {
        await load();
        await reloadOpenComments();
      },
    );

    return {
      state,
      loadMore,
      submitPost,
      remove,
      toggleLike,
      toggleComments,
      submitComment,
      observe,
    };
  },
  render() {
    const { state } = this;
    if (state.loading) {
      return h("p", { class: "muted" }, t("app.loading"));
    }

    return h("div", { class: "stack" }, [
      state.deleting
        ? h(Dialog, {
            title: t("feed.delete_title"),
            message: t("feed.delete_confirm"),
            confirmLabel: t("common.delete"),
            cancelLabel: t("common.cancel"),
            destructive: true,
            onConfirm: () => this.remove(),
            onCancel: () => (state.deleting = null),
          })
        : null,

      // At the top, like the input of the shopping list. It used to float above the tab
      // bar, but a comment field further down then ended up underneath it as soon as the
      // on-screen keyboard opened — two text fields cannot share the same corner.
      h(
        "form",
        {
          class: "feed-compose",
          onSubmit: (event) => {
            event.preventDefault();
            this.submitPost();
          },
        },
        [
          h("textarea", {
            class: "field-input feed-input",
            rows: 1,
            value: state.draft,
            placeholder: t("feed.compose.placeholder"),
            "aria-label": t("feed.compose.placeholder"),
            maxlength: 2000,
            onInput: (event) => (state.draft = event.target.value),
          }),
          h(Button, {
            label: t("feed.compose.send"),
            type: "submit",
            busy: state.posting,
            disabled: state.draft.trim() === "",
          }),
        ],
      ),

      state.error ? h("p", { class: "form-error" }, state.error) : null,

      state.items.length === 0
        ? h("section", { class: "card" }, [
            h("h2", t("feed.empty.title")),
            h("p", { class: "muted" }, t("feed.empty.hint")),
          ])
        : h(
            "ul",
            { class: "feed-list" },
            state.items.map((entry) => this.renderEntry(entry)),
          ),

      // Sentinel for the endless scrolling, plus the button for everybody else.
      state.cursor
        ? h("div", { class: "stack", ref: (element) => this.observe(element) }, [
            h(Button, {
              label: t("common.load_more"),
              variant: "secondary",
              block: true,
              busy: state.loadingMore,
              onClick: () => this.loadMore(),
            }),
          ])
        : null,

    ]);
  },
  methods: {
    renderEntry(entry) {
      const { state } = this;
      const isPost = entry.type === "user_post";
      const person = member(entry.actor_id);
      const mine = store.me !== null && entry.actor_id === store.me.id;
      const target = entry.reference_type ? TARGETS[entry.reference_type] : null;
      const open = state.openComments === entry.id;

      return h("li", { class: "card feed-entry" }, [
        h("div", { class: "feed-head" }, [
          isPost && person
            ? h(Avatar, { person })
            : h("span", { class: "feed-icon", "aria-hidden": "true" }, ICONS[entry.type] ?? "•"),
          h("div", { class: "feed-text" }, [
            isPost
              ? h("span", { class: "feed-author" }, [
                  person ? h(PersonName, { person }) : memberLabel(entry.actor_id),
                ])
              : h("span", { class: "feed-sentence" }, eventText(entry)),
            h("span", { class: "muted small" }, relativeTime(entry.created_at)),
          ]),
          mine && isPost
            ? h(
                "button",
                {
                  class: "icon-button",
                  type: "button",
                  "aria-label": t("common.delete"),
                  onClick: () => (state.deleting = entry),
                },
                "×",
              )
            : null,
        ]),

        isPost && entry.body ? h("p", { class: "feed-body" }, entry.body) : null,

        h("div", { class: "feed-actions" }, [
          h(
            "button",
            {
              class: ["chip-action", entry.liked_by_me ? "is-active" : ""],
              type: "button",
              "aria-pressed": String(entry.liked_by_me),
              "aria-label": t("feed.like"),
              onClick: () => this.toggleLike(entry),
            },
            [
              h("span", { "aria-hidden": "true" }, entry.liked_by_me ? "♥" : "♡"),
              entry.like_count > 0 ? h("span", String(entry.like_count)) : null,
            ],
          ),
          h(
            "button",
            {
              class: ["chip-action", open ? "is-active" : ""],
              type: "button",
              "aria-expanded": String(open),
              "aria-label": t("feed.comments"),
              onClick: () => this.toggleComments(entry),
            },
            [
              h("span", { "aria-hidden": "true" }, "💬"),
              entry.comment_count > 0 ? h("span", String(entry.comment_count)) : null,
              entry.comments_unread > 0
                ? h("span", { class: "badge badge--inline" }, String(entry.comments_unread))
                : null,
            ],
          ),
          target
            ? h("a", { class: "chip-action", href: target.route }, [
                h("span", t(target.label)),
                h("span", { "aria-hidden": "true" }, "›"),
              ])
            : null,
        ]),

        open ? this.renderComments(entry) : null,
      ]);
    },
    renderComments(entry) {
      const { state } = this;
      return h("div", { class: "feed-comments" }, [
        state.comments.length === 0
          ? h("p", { class: "muted small" }, t("feed.comments_empty"))
          : h(
              "ul",
              { class: "comment-list" },
              state.comments.map((comment) =>
                h("li", { class: "comment" }, [
                  h("span", { class: "comment-author" }, memberLabel(comment.author_id)),
                  h("span", { class: "comment-body" }, comment.body),
                  h("span", { class: "muted small" }, relativeTime(comment.created_at)),
                ]),
              ),
            ),
        h(
          "form",
          {
            class: "comment-form",
            onSubmit: (event) => {
              event.preventDefault();
              this.submitComment(entry);
            },
          },
          [
            h("input", {
              class: "field-input",
              type: "text",
              value: state.commentDraft,
              placeholder: t("feed.comment.placeholder"),
              "aria-label": t("feed.comment.placeholder"),
              maxlength: 2000,
              enterkeyhint: "send",
              onInput: (event) => (state.commentDraft = event.target.value),
            }),
            h(Button, {
              label: t("feed.comment.send"),
              type: "submit",
              busy: state.commenting,
              disabled: state.commentDraft.trim() === "",
            }),
          ],
        ),
      ]);
    },
  },
};
