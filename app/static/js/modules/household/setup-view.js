/**
 * The create-or-join state: signed in, but not part of a household yet.
 *
 * Reached after registering and after leaving a household. The tab bar is hidden in this
 * state, so this screen carries the two ways out itself: the own profile and signing
 * out. Otherwise somebody who just left would be stranded here.
 */

import { h, reactive } from "../../../vendor/vue.esm-browser.prod.js";
import { ApiError, api } from "../../api.js";
import { Button } from "../../components/button.js";
import { Field, SelectField } from "../../components/field.js";
import { errorText } from "../../error-text.js";
import { t } from "../../i18n.js";
import { showSnackbar } from "../../components/snackbar.js";
import { clearSession, loadSession, store } from "../../store.js";

const HOUSEHOLD_TYPES = ["wg", "couple", "family"];
const JOIN_CODE_LENGTH = 12;

/**
 * Show the code the way it is passed on: groups of four.
 *
 * The spaces are cosmetic — they are stripped again before the code is sent, and the
 * server ignores them as well.
 */
function formatJoinCode(input) {
  const clean = input
    .toUpperCase()
    .replace(/[^A-Z0-9]/g, "")
    .slice(0, JOIN_CODE_LENGTH);
  return (clean.match(/.{1,4}/g) ?? []).join(" ");
}

export const HouseholdSetupView = {
  setup() {
    const form = reactive({
      name: "",
      type: "wg",
      joinCode: "",
      busy: null,
      errors: {},
      generalError: null,
    });

    function report(error) {
      if (error instanceof ApiError && error.field) {
        form.errors = { [error.field]: errorText(error) };
      } else {
        form.generalError = errorText(error);
      }
    }

    async function send(action, request) {
      form.busy = action;
      form.errors = {};
      form.generalError = null;
      try {
        await request();
        await loadSession();
        window.location.hash = "#/";
      } catch (error) {
        report(error);
      } finally {
        form.busy = null;
      }
    }

    const create = () =>
      send("create", () => api.post("/household", { name: form.name, type: form.type }));
    const join = () =>
      send("join", () =>
        api.post("/household/join", { join_code: form.joinCode.replace(/\s/g, "") }),
      );

    async function signOut() {
      try {
        await api.post("/auth/logout");
      } catch (error) {
        showSnackbar(errorText(error));
      } finally {
        clearSession();
        window.location.hash = "#/login";
      }
    }

    return { form, create, join, signOut };
  },
  render() {
    const { form } = this;
    const greeting = store.me
      ? t("household.setup.greeting", { name: store.me.first_name })
      : t("household.setup.title");

    return h("div", { class: "stack" }, [
      h("p", { class: "muted" }, greeting),

      // Without a household there is no tab bar — the way to the own account has to be
      // on this screen, or it does not exist at all.
      h("a", { class: "link-row", href: "#/settings/profile" }, [
        h("span", t("settings.profile")),
        h("span", { "aria-hidden": "true" }, "›"),
      ]),

      h("section", { class: "card" }, [
        h("h2", t("household.join.title")),
        h("p", { class: "muted small" }, t("household.join.hint")),
        h(
          "form",
          {
            class: "form",
            onSubmit: (event) => {
              event.preventDefault();
              this.join();
            },
          },
          [
            h(Field, {
              label: t("household.field.join_code"),
              modelValue: form.joinCode,
              autocomplete: "off",
              placeholder: "XXXX XXXX XXXX",
              maxlength: 14,
              required: true,
              error: form.errors.join_code,
              "onUpdate:modelValue": (value) => (form.joinCode = formatJoinCode(value)),
            }),
            h("div", { class: "form-actions" }, [
              h(Button, {
                label: t("household.join.submit"),
                type: "submit",
                block: true,
                busy: form.busy === "join",
              }),
            ]),
          ],
        ),
      ]),

      h("section", { class: "card" }, [
        h("h2", t("household.create.title")),
        h("p", { class: "muted small" }, t("household.create.hint")),
        h(
          "form",
          {
            class: "form",
            onSubmit: (event) => {
              event.preventDefault();
              this.create();
            },
          },
          [
            h(Field, {
              label: t("household.field.name"),
              modelValue: form.name,
              required: true,
              maxlength: 120,
              error: form.errors.name,
              "onUpdate:modelValue": (value) => (form.name = value),
            }),
            h(SelectField, {
              label: t("household.field.type"),
              modelValue: form.type,
              options: HOUSEHOLD_TYPES.map((type) => ({
                value: type,
                label: t(`household.type.${type}`),
              })),
              error: form.errors.type,
              "onUpdate:modelValue": (value) => (form.type = value),
            }),
            h("div", { class: "form-actions" }, [
              h(Button, {
                label: t("household.create.submit"),
                type: "submit",
                variant: "secondary",
                block: true,
                busy: form.busy === "create",
              }),
            ]),
          ],
        ),
      ]),

      form.generalError ? h("p", { class: "form-error" }, form.generalError) : null,
      h("section", { class: "card" }, [
        h(Button, {
          label: t("auth.sign_out"),
          variant: "ghost",
          block: true,
          onClick: () => this.signOut(),
        }),
      ]),
    ]);
  },
};
