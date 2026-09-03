/** Sign in and registration — one view with two modes. */

import { h, reactive } from "../../../vendor/vue.esm-browser.prod.js";
import { ApiError, api } from "../../api.js";
import { Button } from "../../components/button.js";
import { Field } from "../../components/field.js";
import { errorText } from "../../error-text.js";
import { i18n, t } from "../../i18n.js";
import { loadSession, store } from "../../store.js";

export const LoginView = {
  setup() {
    const form = reactive({
      mode: "login",
      username: "",
      email: "",
      password: "",
      firstName: "",
      lastName: "",
      busy: false,
      /** Field name → message, straight from the server's error object. */
      errors: {},
      generalError: null,
    });

    function switchMode(mode) {
      form.mode = mode;
      form.errors = {};
      form.generalError = null;
    }

    function report(error) {
      if (error instanceof ApiError && error.field) {
        form.errors = { [error.field]: errorText(error) };
      } else {
        form.generalError = errorText(error);
      }
    }

    async function submit() {
      form.busy = true;
      form.errors = {};
      form.generalError = null;
      try {
        if (form.mode === "register") {
          await api.post("/auth/register", {
            username: form.username,
            email: form.email || null,
            password: form.password,
            first_name: form.firstName,
            last_name: form.lastName || null,
            // Whatever is on screen right now becomes the profile language.
            locale: i18n.locale,
          });
        }
        await api.post("/auth/login", {
          username: form.username,
          password: form.password,
        });
        await loadSession();
        window.location.hash = "#/";
      } catch (error) {
        report(error);
      } finally {
        form.busy = false;
      }
    }

    return { form, switchMode, submit };
  },
  render() {
    const { form } = this;
    const registering = form.mode === "register";

    return h("section", { class: "card auth" }, [
      h("h2", t(registering ? "auth.register.title" : "auth.login.title")),
      // A closed instance hides the sign-up tab instead of letting people fill in a
      // form that can only end in a refusal.
      store.registrationOpen
        ? h("div", { class: "segmented", role: "tablist" }, [
            h(
              "button",
              {
                class: ["segment", !registering ? "is-active" : ""],
                type: "button",
                role: "tab",
                "aria-selected": String(!registering),
                onClick: () => this.switchMode("login"),
              },
              t("auth.login.tab"),
            ),
            h(
              "button",
              {
                class: ["segment", registering ? "is-active" : ""],
                type: "button",
                role: "tab",
                "aria-selected": String(registering),
                onClick: () => this.switchMode("register"),
              },
              t("auth.register.tab"),
            ),
          ])
        : h("p", { class: "muted small" }, t("auth.registration_closed")),
      h(
        "form",
        {
          class: "form",
          onSubmit: (event) => {
            event.preventDefault();
            this.submit();
          },
        },
        [
          registering
            ? h(Field, {
                label: t("auth.field.first_name"),
                name: "given-name",
                modelValue: form.firstName,
                autocomplete: "given-name",
                required: true,
                maxlength: 80,
                error: form.errors.first_name,
                "onUpdate:modelValue": (value) => (form.firstName = value),
              })
            : null,
          registering
            ? h(Field, {
                label: t("auth.field.last_name"),
                name: "family-name",
                modelValue: form.lastName,
                autocomplete: "family-name",
                maxlength: 80,
                error: form.errors.last_name,
                "onUpdate:modelValue": (value) => (form.lastName = value),
              })
            : null,
          h(Field, {
            label: t("auth.field.username"),
            name: "username",
            modelValue: form.username,
            autocomplete: "username",
            required: true,
            maxlength: 255,
            error: form.errors.username,
            "onUpdate:modelValue": (value) => (form.username = value),
          }),
          h(Field, {
            label: t("auth.field.password"),
            name: registering ? "new-password" : "current-password",
            modelValue: form.password,
            type: "password",
            autocomplete: registering ? "new-password" : "current-password",
            required: true,
            error: form.errors.password,
            "onUpdate:modelValue": (value) => (form.password = value),
          }),
          registering ? h("p", { class: "muted small" }, t("auth.password_hint")) : null,
          registering
            ? h(Field, {
                label: t("auth.field.email"),
                name: "email",
                modelValue: form.email,
                type: "email",
                inputmode: "email",
                autocomplete: "email",
                maxlength: 255,
                error: form.errors.email,
                "onUpdate:modelValue": (value) => (form.email = value),
              })
            : null,
          registering ? h("p", { class: "muted small" }, t("auth.email_hint")) : null,
          form.generalError ? h("p", { class: "form-error" }, form.generalError) : null,
          h("div", { class: "form-actions" }, [
            h(Button, {
              label: t(registering ? "auth.register.submit" : "auth.login.submit"),
              type: "submit",
              block: true,
              busy: form.busy,
            }),
          ]),
        ],
      ),
    ]);
  },
};
