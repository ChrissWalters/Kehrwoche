/**
 * The chore list — the screen people open most often.
 *
 * One tap books a chore as done and a snackbar offers to take it back — no
 * confirmation dialog for something people do every day.
 */

import { h, reactive, watch } from "../../../vendor/vue.esm-browser.prod.js";
import { api } from "../../api.js";
import { Avatar, PersonName } from "../../components/avatar.js";
import { Button } from "../../components/button.js";
import { Dialog } from "../../components/dialog.js";
import { showSnackbar } from "../../components/snackbar.js";
import { errorText } from "../../error-text.js";
import { intervalLabel, relativeTime } from "../../format.js";
import { i18n, t } from "../../i18n.js";
import { store } from "../../store.js";
import { ChoreForm } from "./form-view.js";

const ON_DEMAND = -1;

function member(id) {
  return store.household?.members.find((entry) => entry.id === id) ?? null;
}

export const ChoreListView = {
  setup() {
    const state = reactive({
      chores: [],
      templates: [],
      loading: true,
      error: null,
      /** null, "new" or the chore being edited. */
      editing: null,
      /** Ids currently waiting for the server, so a row cannot be tapped twice. */
      pending: [],
      /** Chore awaiting the delete confirmation — deleting is destructive. */
      deleting: null,
      /** Chore somebody wants to take over from the person on duty. */
      takingOver: null,
      /** Id of the chore whose extra actions are unfolded — one at a time. */
      expanded: null,
    });

    async function load() {
      try {
        state.chores = await api.get("/chores");
        state.error = null;
      } catch (error) {
        state.error = errorText(error);
      } finally {
        state.loading = false;
      }
    }

    async function loadTemplates() {
      state.templates = await api
        .get(`/chores/templates?locale=${i18n.locale}`)
        .catch(() => []);
    }

    async function complete(chore, forUserId = null) {
      state.pending = [...state.pending, chore.id];
      try {
        await api.post(`/chores/${chore.id}/complete`, { for_user_id: forUserId });
        await load();
        const credited = forUserId ? member(forUserId)?.first_name : null;
        showSnackbar(
          credited
            ? t("chores.booked_for_snackbar", { title: chore.title, name: credited })
            : t("chores.done_snackbar", { title: chore.title }),
          { actionLabel: t("common.undo"), action: () => undo(chore) },
        );
      } catch (error) {
        showSnackbar(errorText(error));
      } finally {
        state.pending = state.pending.filter((id) => id !== chore.id);
      }
    }

    async function undo(chore) {
      try {
        await api.post(`/chores/${chore.id}/undo-complete`);
        await load();
        showSnackbar(t("chores.undone_snackbar", { title: chore.title }));
      } catch (error) {
        showSnackbar(errorText(error));
      }
    }

    async function remind(chore) {
      try {
        const target = await api.post(`/chores/${chore.id}/remind`);
        showSnackbar(t("chores.reminded", { name: target.first_name }));
      } catch (error) {
        showSnackbar(errorText(error));
      }
    }

    async function remove() {
      const chore = state.deleting;
      if (!chore) {
        return;
      }
      try {
        await api.delete(`/chores/${chore.id}`);
        state.deleting = null;
        await load();
      } catch (error) {
        state.deleting = null;
        showSnackbar(errorText(error));
      }
    }

    function startEditing(chore) {
      state.editing = chore ?? "new";
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    async function finishEditing() {
      state.editing = null;
      await load();
    }

    load();
    loadTemplates();
    // Somebody else booking a chore shows up here without a reload.
    watch(() => store.markers.chores, load);

    return { state, complete, remind, remove, startEditing, finishEditing };
  },
  render() {
    const { state } = this;

    if (state.editing) {
      return h(ChoreForm, {
        chore: state.editing === "new" ? null : state.editing,
        templates: state.templates,
        onSaved: () => this.finishEditing(),
        onCancel: () => (state.editing = null),
      });
    }

    if (state.loading) {
      return h("p", { class: "muted" }, t("app.loading"));
    }

    return h("div", { class: "stack chores-view" }, [
      state.deleting
        ? h(Dialog, {
            title: t("chores.delete_title"),
            message: t("chores.delete_confirm", { title: state.deleting.title }),
            confirmLabel: t("common.delete"),
            cancelLabel: t("common.cancel"),
            destructive: true,
            onConfirm: () => this.remove(),
            onCancel: () => (state.deleting = null),
          })
        : null,
      state.takingOver
        ? h(Dialog, {
            title: t("chores.take_over_title"),
            message: t("chores.take_over_confirm", {
              title: state.takingOver.title,
              name: member(state.takingOver.current_user_id)?.first_name ?? t("chores.nobody"),
            }),
            confirmLabel: t("chores.take_over"),
            secondaryLabel: t("chores.book_for", {
              name: member(state.takingOver.current_user_id)?.first_name ?? "",
            }),
            cancelLabel: t("common.cancel"),
            onConfirm: () => {
              const chore = state.takingOver;
              state.takingOver = null;
              this.complete(chore);
            },
            onSecondary: () => {
              const chore = state.takingOver;
              state.takingOver = null;
              // The person on duty did the work; they get the points.
              this.complete(chore, chore.current_user_id);
            },
            onCancel: () => (state.takingOver = null),
          })
        : null,
      state.error ? h("p", { class: "form-error" }, state.error) : null,
      state.chores.length === 0
        ? h("section", { class: "card" }, [
            h("h2", t("chores.empty.title")),
            h("p", { class: "muted" }, t("chores.empty.hint")),
          ])
        : h(
            "ul",
            { class: "chore-list" },
            state.chores.map((chore) => this.renderChore(chore)),
          ),
      h(
        "a",
        { class: "link-row", href: "#/chores/statistics" },
        [h("span", t("chores.stats.title")), h("span", { "aria-hidden": "true" }, "›")],
      ),
      // Pinned above the tab bar on a phone, at the top of the view on a desktop — with
      // a long list it would otherwise sit a whole scroll away from the eye and thumb.
      h("div", { class: "pinned-action" }, [
        h(Button, {
          label: t("chores.add"),
          block: true,
          onClick: () => this.startEditing(null),
        }),
      ]),
    ]);
  },
  methods: {
    renderChore(chore) {
      const responsible = member(chore.current_user_id);
      const overdue = chore.due_at !== null && new Date(chore.due_at) < new Date();
      const pending = this.state.pending.includes(chore.id);
      // Own chores are booked with a single tap; taking somebody else's turn asks first.
      const mine = store.me !== null && chore.current_user_id === store.me.id;
      const open = this.state.expanded === chore.id;

      return h("li", { class: ["card", "chore", overdue ? "is-overdue" : ""] }, [
        h("div", { class: "chore-head" }, [
          h(Avatar, { person: responsible }),
          h("div", { class: "chore-text" }, [
            h("span", { class: "chore-title" }, [
              chore.title,
              // The rhythm rides along with the title instead of taking a line of its
              // own at the foot of the card. On-demand chores skip it: the line below
              // already says so, and saying it twice is worse than not saying it.
              chore.rotation_seconds === ON_DEMAND
                ? null
                : h(
                    "span",
                    { class: "chore-rhythm" },
                    ` (${intervalLabel(chore.rotation_seconds)})`,
                  ),
              mine ? h("span", { class: "tag tag--mine" }, t("chores.your_turn")) : null,
            ]),
            h("span", { class: "chore-meta" }, [
              responsible ? h(PersonName, { person: responsible }) : t("chores.nobody"),
              " · ",
              chore.rotation_seconds === ON_DEMAND
                ? t("chores.interval.on_demand")
                : relativeTime(chore.due_at),
              chore.points > 0 ? ` · ${t("chores.points", { points: chore.points })}` : "",
            ]),
          ]),
        ]),
        chore.description ? h("p", { class: "muted small" }, chore.description) : null,
        // The main action fills the row; the three rarer ones fold away behind the
        // caret, so a list of chores stays readable on a phone without scrolling.
        h("div", { class: "chore-actions" }, [
          h("div", { class: "chore-main" }, [
            h(Button, {
              label: mine ? t("chores.done") : t("chores.take_over"),
              variant: mine ? "primary" : "secondary",
              block: true,
              busy: pending,
              onClick: () => (mine ? this.complete(chore) : (this.state.takingOver = chore)),
            }),
            h(
              "button",
              {
                class: ["icon-button", "chore-more", open ? "is-open" : ""],
                type: "button",
                "aria-label": t("chores.more_actions"),
                "aria-expanded": String(open),
                onClick: () => (this.state.expanded = open ? null : chore.id),
              },
              "⌄",
            ),
          ]),
          open
            ? h("div", { class: "chore-secondary" }, [
                h(Button, {
                  label: t("chores.remind"),
                  variant: "ghost",
                  block: true,
                  onClick: () => this.remind(chore),
                }),
                h(Button, {
                  label: t("common.edit"),
                  variant: "ghost",
                  block: true,
                  onClick: () => this.startEditing(chore),
                }),
                h(Button, {
                  label: t("common.delete"),
                  variant: "ghost",
                  block: true,
                  onClick: () => (this.state.deleting = chore),
                }),
              ])
            : null,
        ]),
      ]);
    },
  },
};
