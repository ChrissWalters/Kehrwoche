/**
 * The "More" screen: household, members, join code, administration, sign-out.
 *
 * Everything an admin can do to the household lives here. For everybody else the same
 * screen is a read-only overview — the actions are not shown at all rather than shown
 * and refused, because a button that always says no is worse than no button.
 */

import { h, onMounted, reactive } from "../../../vendor/vue.esm-browser.prod.js";
import { api } from "../../api.js";
import { Avatar, PersonName } from "../../components/avatar.js";
import { Button } from "../../components/button.js";
import { Dialog } from "../../components/dialog.js";
import { Field, SelectField } from "../../components/field.js";
import { showSnackbar } from "../../components/snackbar.js";
import { errorText } from "../../error-text.js";
import { money } from "../../format.js";
import { t } from "../../i18n.js";
import { clearSession, loadSession, reloadHousehold, store } from "../../store.js";

/** Groups of four are far easier to read out over the phone. */
function groupCode(code) {
  return (code.match(/.{1,4}/g) ?? [code]).join(" ");
}

/**
 * Copy text, also on a plain HTTP instance.
 *
 * `navigator.clipboard` only exists in a secure context — over http://192.168.x.x it is
 * simply undefined, which is the normal case in a home network until TLS arrives. The
 * older selection-based path still works there.
 */
async function writeToClipboard(text) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Denied or unavailable — try the fallback below.
    }
  }

  const area = document.createElement("textarea");
  area.value = text;
  area.readOnly = true;
  // Class instead of an inline style: the strict CSP forbids inline styles.
  area.className = "offscreen";
  document.body.append(area);
  area.select();
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch {
    copied = false;
  }
  area.remove();
  return copied;
}

export const MoreView = {
  setup() {
    const state = reactive({
      copied: false,
      failed: false,
      busy: false,
      /** Member awaiting the removal confirmation. */
      removing: null,
      /** True while the join code replacement is being confirmed. */
      regenerating: false,
      /** Open household form, or null. */
      editing: null,
      /** True while the departure is being confirmed. */
      leaving: false,
      /** Own balance in the open period — leaving with debts should not be silent. */
      balance: 0,
    });

    async function loadBalance() {
      const rows = await api.get("/expenses/balances").catch(() => []);
      state.balance = rows.find((row) => row.user_id === store.me?.id)?.balance_cents ?? 0;
    }

    async function leave() {
      state.leaving = false;
      try {
        await api.post("/household/leave");
        // Back to the state of a fresh account: the session decides what is shown, so it
        // is read again rather than guessed at.
        await loadSession();
        window.location.hash = "#/household";
        showSnackbar(t("household.left"));
      } catch (error) {
        showSnackbar(errorText(error));
      }
    }

    function member(id) {
      return store.household?.members.find((entry) => entry.id === id) ?? null;
    }

    /** Give the admin role or take it back. */
    async function toggleRole(person) {
      const role = person.role === "admin" ? "member" : "admin";
      try {
        await api.patch(`/household/members/${person.id}`, { role });
        await reloadHousehold();
        showSnackbar(
          t(role === "admin" ? "household.role.granted" : "household.role.revoked", {
            name: person.first_name,
          }),
        );
      } catch (error) {
        // The server knows the rules — the last admin cannot step down alone. Say why.
        showSnackbar(errorText(error));
      }
    }

    async function removeMember() {
      const person = state.removing;
      state.removing = null;
      try {
        await api.delete(`/household/members/${person.id}`);
        await reloadHousehold();
        showSnackbar(t("household.members.removed", { name: person.first_name }));
      } catch (error) {
        showSnackbar(errorText(error));
      }
    }

    async function regenerateCode() {
      state.regenerating = false;
      try {
        await api.post("/household/regenerate-code");
        await reloadHousehold();
        showSnackbar(t("household.code.regenerated"));
      } catch (error) {
        showSnackbar(errorText(error));
      }
    }

    function startEditing() {
      const household = store.household;
      state.editing = {
        name: household?.name ?? "",
        type: household?.type ?? "wg",
        currency: household?.currency ?? "EUR",
        takeoverKeepsTurn: household?.takeover_keeps_turn ?? false,
        busy: false,
        error: null,
      };
    }

    async function saveHousehold() {
      const form = state.editing;
      form.busy = true;
      form.error = null;
      try {
        store.household = await api.patch("/household", {
          name: form.name,
          type: form.type,
          currency: form.currency,
          takeover_keeps_turn: form.takeoverKeepsTurn,
        });
        state.editing = null;
        showSnackbar(t("household.saved"));
      } catch (error) {
        form.error = errorText(error);
      } finally {
        form.busy = false;
      }
    }

    async function copyCode() {
      const code = store.household?.join_code ?? "";
      state.copied = await writeToClipboard(code);
      // Say so instead of failing silently — the code can still be read out loud.
      state.failed = !state.copied;
      if (state.copied) {
        window.setTimeout(() => (state.copied = false), 2000);
      }
    }

    async function signOut() {
      state.busy = true;
      try {
        await api.post("/auth/logout");
      } finally {
        clearSession();
        state.busy = false;
        window.location.hash = "#/login";
      }
    }

    async function uploadImage(file) {
      if (!file) {
        return;
      }
      const body = new FormData();
      body.append("file", file);
      try {
        store.household = await api.upload("/household/image", body);
        showSnackbar(t("household.image_saved"));
      } catch (error) {
        showSnackbar(errorText(error));
      }
    }

    onMounted(loadBalance);

    return {
      state,
      copyCode,
      leave,
      signOut,
      uploadImage,
      member,
      toggleRole,
      removeMember,
      regenerateCode,
      startEditing,
      saveHousehold,
    };
  },
  render() {
    // `state` comes from setup(); without this line every reference below is a
    // ReferenceError and the whole view renders as nothing at all.
    const { state } = this;
    const household = store.household;
    if (!household) {
      return h("section", { class: "card" }, h("p", { class: "muted" }, t("app.loading")));
    }

    const isAdmin = store.me?.role === "admin";

    return h("div", { class: "stack" }, [
      state.removing
        ? h(Dialog, {
            title: t("household.members.remove_title"),
            message: t("household.members.remove_confirm", { name: state.removing.first_name }),
            confirmLabel: t("household.members.remove"),
            cancelLabel: t("common.cancel"),
            destructive: true,
            onConfirm: () => this.removeMember(),
            onCancel: () => (state.removing = null),
          })
        : null,
      state.leaving
        ? h(Dialog, {
            title: t("household.leave_title"),
            message: t("household.leave_confirm", { name: household.name }),
            confirmLabel: t("household.leave"),
            cancelLabel: t("common.cancel"),
            destructive: true,
            onConfirm: () => this.leave(),
            onCancel: () => (state.leaving = false),
          })
        : null,
      state.regenerating
        ? h(Dialog, {
            title: t("household.code.regenerate"),
            message: t("household.code.regenerate_confirm"),
            confirmLabel: t("household.code.regenerate"),
            cancelLabel: t("common.cancel"),
            destructive: true,
            onConfirm: () => this.regenerateCode(),
            onCancel: () => (state.regenerating = false),
          })
        : null,

      // Said in the settings as well as in the log: an unprotected connection is not a
      // detail the household should have to guess.
      store.insecureTransport
        ? h("p", { class: "offline-banner" }, t("household.insecure"))
        : null,

      // The hub of everything that is not an everyday action.
      h("a", { class: "link-row", href: "#/settings/profile" }, [
        h("span", t("settings.profile")),
        h("span", { "aria-hidden": "true" }, "›"),
      ]),

      h("section", { class: "card" }, [
        h("div", { class: "avatar-row" }, [
          household.image_file
            ? h("img", {
                class: "household-image",
                src: `/media/${household.image_file}`,
                alt: "",
              })
            : null,
          h("div", { class: "feed-text" }, [
            h("h2", household.name),
            h("p", { class: "muted small" }, t(`household.type.${household.type}`)),
          ]),
        ]),
        isAdmin
          ? h("label", { class: "btn btn--secondary avatar-picker" }, [
              t("household.choose_picture"),
              h("input", {
                type: "file",
                accept: "image/*",
                class: "visually-hidden",
                onChange: (event) => this.uploadImage(event.target.files?.[0]),
              }),
            ])
          : null,
        isAdmin && !state.editing
          ? h(Button, {
              label: t("household.edit"),
              variant: "ghost",
              block: true,
              onClick: () => this.startEditing(),
            })
          : null,
        isAdmin && state.editing ? this.renderHouseholdForm() : null,
      ]),

      h("section", { class: "card" }, [
        h("h2", t("household.members.title")),
        h(
          "ul",
          { class: "member-list" },
          household.members.map((person) => this.renderMember(person, isAdmin)),
        ),
      ]),

      h("section", { class: "card" }, [
        h("h2", t("household.code.title")),
        h("p", { class: "muted small" }, t("household.code.hint")),
        // The code itself is a target too — tapping it is the obvious gesture.
        h(
          "button",
          { class: "join-code", type: "button", onClick: () => this.copyCode() },
          groupCode(household.join_code),
        ),
        h(Button, {
          label: this.state.copied ? t("household.code.copied") : t("household.code.copy"),
          variant: "secondary",
          block: true,
          onClick: () => this.copyCode(),
        }),
        this.state.failed ? h("p", { class: "muted small" }, t("household.code.manual")) : null,
        isAdmin
          ? h(Button, {
              label: t("household.code.regenerate"),
              variant: "ghost",
              block: true,
              onClick: () => (state.regenerating = true),
            })
          : null,
      ]),

      // Leaving is not signing out: the account stays, only the membership ends.
      h("section", { class: "card stack" }, [
        h("h2", t("household.leave_title")),
        h("p", { class: "muted small" }, t("household.leave_explains")),
        state.balance !== 0
          ? h(
              "p",
              { class: "form-error" },
              // A bare number says nothing: owing 23,50 € and being owed 23,50 € are
              // opposite situations, and only one of them is the household's problem.
              t(state.balance < 0 ? "household.leave_owed" : "household.leave_credit", {
                amount: money(Math.abs(state.balance), household.currency),
              }),
            )
          : null,
        household.members.length === 1
          ? h("p", { class: "muted small" }, t("household.leave_last_member"))
          : null,
        h(Button, {
          label: t("household.leave"),
          variant: "danger",
          block: true,
          onClick: () => (state.leaving = true),
        }),
      ]),

      h("section", { class: "card" }, [
        h(Button, {
          label: t("auth.sign_out"),
          variant: "ghost",
          block: true,
          busy: this.state.busy,
          onClick: () => this.signOut(),
        }),
      ]),
    ]);
  },
  methods: {
    renderMember(person, isAdmin) {
      const isMe = person.id === store.me?.id;
      return h("li", { class: "member" }, [
        h(Avatar, { person }),
        h(PersonName, { person }),
        person.role === "admin" ? h("span", { class: "tag" }, t("household.role.admin")) : null,
        h("span", { class: "member-points" }, t("household.points", { points: person.points })),
        // Administration is invisible for everybody else, and nobody administers
        // themselves out of the household — that is what leaving is for.
        isAdmin && !isMe
          ? h("div", { class: "member-actions" }, [
              h(Button, {
                label: t(
                  person.role === "admin" ? "household.role.revoke" : "household.role.grant",
                ),
                variant: "ghost",
                onClick: () => this.toggleRole(person),
              }),
              h(Button, {
                label: t("household.members.remove"),
                variant: "ghost",
                onClick: () => (this.state.removing = person),
              }),
            ])
          : null,
      ]);
    },
    renderHouseholdForm() {
      const form = this.state.editing;
      return h(
        "form",
        {
          class: "stack",
          onSubmit: (event) => {
            event.preventDefault();
            this.saveHousehold();
          },
        },
        [
          h(Field, {
            label: t("household.field.name"),
            modelValue: form.name,
            "onUpdate:modelValue": (value) => (form.name = value),
            required: true,
            maxlength: 80,
          }),
          h(SelectField, {
            label: t("household.field.type"),
            modelValue: form.type,
            options: ["wg", "couple", "family"].map((type) => ({
              value: type,
              label: t(`household.type.${type}`),
            })),
            "onUpdate:modelValue": (value) => (form.type = value),
          }),
          h(Field, {
            label: t("household.field.currency"),
            modelValue: form.currency,
            "onUpdate:modelValue": (value) => (form.currency = value.toUpperCase()),
            maxlength: 3,
          }),
          // Amounts are plain cents; the currency only decides how they are printed.
          h("p", { class: "muted small" }, t("household.currency_hint")),
          h("div", { class: "field" }, [
            h("label", { class: "check" }, [
              h("input", {
                type: "checkbox",
                checked: form.takeoverKeepsTurn,
                onChange: (event) => (form.takeoverKeepsTurn = event.target.checked),
              }),
              h("span", t("household.field.takeover_keeps_turn")),
            ]),
            h("p", { class: "muted small" }, t("household.takeover_hint")),
          ]),
          form.error ? h("p", { class: "form-error" }, form.error) : null,
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
              onClick: () => (this.state.editing = null),
            }),
          ]),
        ],
      );
    },
  },
};
