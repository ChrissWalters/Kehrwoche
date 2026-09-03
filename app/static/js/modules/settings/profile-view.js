/**
 * The own account: name, language, address, picture, password, devices — and the way out.
 *
 * Everything here belongs to one person, so it lives behind "More" rather than in the
 * tab bar: these are settings, not everyday actions.
 */

import { h, onMounted, reactive } from "../../../vendor/vue.esm-browser.prod.js";
import { ApiError, api } from "../../api.js";
import { Avatar } from "../../components/avatar.js";
import { Button } from "../../components/button.js";
import { Dialog } from "../../components/dialog.js";
import { Field, SelectField } from "../../components/field.js";
import { showSnackbar } from "../../components/snackbar.js";
import { errorText } from "../../error-text.js";
import { dateTime, money } from "../../format.js";
import { i18n, t } from "../../i18n.js";
import { chooseLocale, clearSession, store } from "../../store.js";

export const ProfileView = {
  setup() {
    const state = reactive({
      first_name: store.me?.first_name ?? "",
      last_name: store.me?.last_name ?? "",
      email: store.me?.email ?? "",
      busy: false,
      errors: {},
      sessions: [],
      /** Password change, kept next to the rest of the account. */
      password: { current: "", next: "", busy: false, error: null },
      deleting: false,
      deletePassword: "",
      deleteError: null,
      /** Own balance in the open period, so nobody deletes their debts by accident. */
      balance: 0,
    });

    async function loadSessions() {
      state.sessions = await api.get("/auth/sessions").catch(() => []);
    }

    async function loadBalance() {
      if (!store.me?.household_id) {
        return;
      }
      const rows = await api.get("/expenses/balances").catch(() => []);
      state.balance = rows.find((row) => row.user_id === store.me?.id)?.balance_cents ?? 0;
    }

    async function save() {
      state.busy = true;
      state.errors = {};
      try {
        store.me = await api.patch("/me", {
          first_name: state.first_name,
          last_name: state.last_name,
          email: state.email,
        });
        showSnackbar(t("settings.saved"));
      } catch (error) {
        if (error instanceof ApiError && error.field) {
          state.errors[error.field] = errorText(error);
        } else {
          showSnackbar(errorText(error));
        }
      } finally {
        state.busy = false;
      }
    }

    async function uploadAvatar(file) {
      if (!file) {
        return;
      }
      const body = new FormData();
      body.append("file", file);
      try {
        store.me = await api.upload("/me/avatar", body);
        showSnackbar(t("settings.avatar_saved"));
      } catch (error) {
        showSnackbar(errorText(error));
      }
    }

    async function changePassword() {
      state.password.busy = true;
      state.password.error = null;
      try {
        await api.post("/auth/change-password", {
          current_password: state.password.current,
          new_password: state.password.next,
        });
        state.password.current = "";
        state.password.next = "";
        await loadSessions();
        showSnackbar(t("settings.password_changed"));
      } catch (error) {
        state.password.error = errorText(error);
      } finally {
        state.password.busy = false;
      }
    }

    async function revoke(session) {
      await api.delete(`/auth/sessions/${session.id}`).catch(() => null);
      await loadSessions();
    }

    async function deleteAccount() {
      state.deleteError = null;
      try {
        await api.request("DELETE", "/me", { password: state.deletePassword });
        clearSession();
        window.location.hash = "#/login";
      } catch (error) {
        // The dialog closes first: its backdrop would hide the very message that
        // explains why nothing happened.
        state.deleting = false;
        const message = errorText(error);
        state.deleteError = message;
        showSnackbar(message);
      }
    }

    onMounted(() => {
      loadSessions();
      loadBalance();
    });

    return { state, save, uploadAvatar, changePassword, revoke, deleteAccount };
  },
  render() {
    const { state } = this;

    return h("div", { class: "stack" }, [
      state.deleting
        ? h(Dialog, {
            title: t("settings.delete_title"),
            message: t("settings.delete_confirm"),
            confirmLabel: t("settings.delete"),
            cancelLabel: t("common.cancel"),
            destructive: true,
            onConfirm: () => this.deleteAccount(),
            onCancel: () => {
              state.deleting = false;
              state.deletePassword = "";
              state.deleteError = null;
            },
          })
        : null,

      h("a", { class: "link-row", href: "#/more" }, [
        h("span", { "aria-hidden": "true" }, "‹"),
        h("span", t("nav.more")),
      ]),

      h("section", { class: "card stack" }, [
        h("h2", t("settings.profile")),
        h("div", { class: "avatar-row" }, [
          h(Avatar, { person: store.me, size: "large" }),
          h("label", { class: "btn btn--secondary avatar-picker" }, [
            t("settings.choose_picture"),
            h("input", {
              type: "file",
              accept: "image/*",
              class: "visually-hidden",
              onChange: (event) => this.uploadAvatar(event.target.files?.[0]),
            }),
          ]),
        ]),
        h(Field, {
          label: t("settings.first_name"),
          modelValue: state.first_name,
          "onUpdate:modelValue": (value) => (state.first_name = value),
          error: state.errors.first_name,
          autocomplete: "given-name",
        }),
        h(Field, {
          label: t("settings.last_name"),
          modelValue: state.last_name,
          "onUpdate:modelValue": (value) => (state.last_name = value),
          autocomplete: "family-name",
        }),
        h(Field, {
          label: t("settings.email"),
          modelValue: state.email,
          "onUpdate:modelValue": (value) => (state.email = value),
          error: state.errors.email,
          type: "email",
          autocomplete: "email",
          placeholder: t("settings.email_hint"),
        }),
        h(SelectField, {
          label: t("settings.language"),
          modelValue: i18n.locale,
          options: i18n.available.map((code) => ({ value: code, label: code.toUpperCase() })),
          "onUpdate:modelValue": (code) => chooseLocale(code),
        }),
        h("div", { class: "form-actions" }, [
          h(Button, { label: t("common.save"), block: true, busy: state.busy, onClick: () => this.save() }),
        ]),
      ]),

      h("section", { class: "card stack" }, [
        h("h2", t("settings.password")),
        h(Field, {
          label: t("settings.current_password"),
          modelValue: state.password.current,
          "onUpdate:modelValue": (value) => (state.password.current = value),
          type: "password",
          autocomplete: "current-password",
        }),
        h(Field, {
          label: t("settings.new_password"),
          modelValue: state.password.next,
          "onUpdate:modelValue": (value) => (state.password.next = value),
          type: "password",
          autocomplete: "new-password",
          error: state.password.error,
        }),
        h("div", { class: "form-actions" }, [
          h(Button, {
            label: t("settings.change_password"),
            variant: "secondary",
            block: true,
            busy: state.password.busy,
            disabled: !state.password.current || !state.password.next,
            onClick: () => this.changePassword(),
          }),
        ]),
      ]),

      h("section", { class: "card stack" }, [
        h("h2", t("settings.sessions")),
        h(
          "ul",
          { class: "session-list" },
          state.sessions.map((session) =>
            h("li", { class: "session" }, [
              h("div", { class: "session-text" }, [
                h("span", session.user_agent || t("settings.unknown_device")),
                h(
                  "span",
                  { class: "muted small" },
                  t("settings.last_seen", { when: dateTime(session.last_seen_at) }),
                ),
              ]),
              session.current
                ? h("span", { class: "tag" }, t("settings.this_device"))
                : h(Button, {
                    label: t("settings.revoke"),
                    variant: "ghost",
                    onClick: () => this.revoke(session),
                  }),
            ]),
          ),
        ),
      ]),

      h("section", { class: "card stack" }, [
        h("h2", t("settings.delete_title")),
        h("p", { class: "muted small" }, t("settings.delete_explains")),
        state.balance !== 0
          ? h(
              "p",
              { class: "form-error" },
              t(state.balance < 0 ? "settings.delete_owed" : "settings.delete_credit", {
                amount: money(Math.abs(state.balance), store.household?.currency),
              }),
            )
          : null,
        h(Field, {
          label: t("settings.confirm_password"),
          modelValue: state.deletePassword,
          "onUpdate:modelValue": (value) => (state.deletePassword = value),
          type: "password",
          autocomplete: "current-password",
          error: state.deleteError,
        }),
        h(Button, {
          label: t("settings.delete"),
          variant: "danger",
          block: true,
          disabled: state.deletePassword === "",
          onClick: () => (state.deleting = true),
        }),
      ]),
    ]);
  },
};
