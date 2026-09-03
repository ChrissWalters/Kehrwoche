/**
 * Application shell: routing table, header, navigation.
 *
 * Components are written as render functions on purpose. The vendored Vue build would
 * compile string templates through `Function(...)`, which the strict Content Security
 * Policy forbids — see `vendor/README.md`.
 */

import { createApp, h, reactive } from "../vendor/vue.esm-browser.prod.js";
import { setUnauthorizedHandler } from "./api.js";
import { Snackbar } from "./components/snackbar.js";
import { i18n, t } from "./i18n.js";
import { LoginView } from "./modules/auth/login-view.js";
import { ChoreListView } from "./modules/chores/list-view.js";
import { ChoreStatisticsView } from "./modules/chores/statistics-view.js";
import { ArchiveView } from "./modules/expenses/archive-view.js";
import { BalancesView } from "./modules/expenses/balances-view.js";
import { ExpenseListView } from "./modules/expenses/list-view.js";
import { FeedView } from "./modules/feed/list-view.js";
import { SettlementView } from "./modules/expenses/settlement-view.js";
import { ShoppingListView } from "./modules/shopping/list-view.js";
import { MoreView } from "./modules/household/more-view.js";
import { HouseholdSetupView } from "./modules/household/setup-view.js";
import { NotificationPanel } from "./modules/notifications/panel.js";
import { ProfileView } from "./modules/settings/profile-view.js";
import { Access, createRouter } from "./router.js";
import {
  chooseLocale,
  clearSession,
  hasHousehold,
  isSignedIn,
  loadSession,
  startSync,
  store,
} from "./store.js";

const routes = [
  {
    path: "/chores",
    labelKey: "nav.chores",
    icon: "🧹",
    access: Access.MEMBER,
    inNav: true,
    component: ChoreListView,
  },
  {
    path: "/chores/statistics",
    labelKey: "chores.stats.title",
    access: Access.MEMBER,
    component: ChoreStatisticsView,
  },
  {
    path: "/shopping",
    labelKey: "nav.shopping",
    icon: "🛒",
    access: Access.MEMBER,
    inNav: true,
    component: ShoppingListView,
  },
  {
    path: "/expenses",
    labelKey: "nav.expenses",
    icon: "💶",
    access: Access.MEMBER,
    inNav: true,
    component: ExpenseListView,
  },
  {
    path: "/expenses/balances",
    labelKey: "expenses.balances.title",
    access: Access.MEMBER,
    component: BalancesView,
  },
  {
    path: "/expenses/settlement",
    labelKey: "expenses.settlement.title",
    access: Access.MEMBER,
    component: SettlementView,
  },
  {
    path: "/expenses/archive",
    labelKey: "expenses.archive.title",
    access: Access.MEMBER,
    component: ArchiveView,
  },
  {
    path: "/",
    labelKey: "nav.feed",
    icon: "📌",
    access: Access.MEMBER,
    inNav: true,
    component: FeedView,
  },
  {
    // Hub for everything that is not a daily action: profile, household, sign-out.
    path: "/more",
    labelKey: "nav.more",
    icon: "⋯",
    access: Access.MEMBER,
    inNav: true,
    component: MoreView,
  },
  {
    path: "/settings/profile",
    labelKey: "settings.profile",
    access: Access.SIGNED_IN,
    component: ProfileView,
  },
  {
    path: "/login",
    labelKey: "auth.login.title",
    access: Access.GUEST,
    component: LoginView,
  },
  {
    path: "/household",
    labelKey: "household.setup.title",
    access: Access.SIGNED_IN,
    component: HouseholdSetupView,
  },
];

/** Route guards: where does this visitor actually belong? */
function resolveRedirect(route) {
  if (!store.ready) {
    return null;
  }
  if (route.access === Access.GUEST) {
    return isSignedIn() ? "/" : null;
  }
  if (!isSignedIn()) {
    return "/login";
  }
  if (route.access === Access.MEMBER && !hasHousehold()) {
    return "/household";
  }
  return null;
}

const router = createRouter(routes, resolveRedirect);

/** Shell state that is not worth a route: the notification panel is open or it is not. */
const ui = reactive({ notificationsOpen: false });

const LanguagePicker = {
  render() {
    return h(
      "select",
      {
        class: "language",
        "aria-label": t("header.language"),
        value: i18n.locale,
        onChange: (event) => chooseLocale(event.target.value),
      },
      i18n.available.map((code) =>
        h("option", { value: code, selected: code === i18n.locale }, code.toUpperCase()),
      ),
    );
  },
};

const AppHeader = {
  render() {
    const title = store.household ? store.household.name : t("app.name");
    return h("header", { class: "app-header" }, [
      h("h1", { class: "app-title" }, title),
      h("div", { class: "header-actions" }, [
        h(LanguagePicker),
        hasHousehold()
          ? h(
              "button",
              {
                class: "bell",
                type: "button",
                "aria-label": t("header.notifications"),
                "aria-expanded": String(ui.notificationsOpen),
                onClick: () => (ui.notificationsOpen = !ui.notificationsOpen),
              },
              [
                h("span", { "aria-hidden": "true" }, "🔔"),
                store.unreadNotifications > 0
                  ? h("span", { class: "badge" }, String(store.unreadNotifications))
                  : null,
              ],
            )
          : null,
      ]),
    ]);
  },
};

const AppNav = {
  render() {
    if (!hasHousehold()) {
      return null;
    }
    return h(
      "nav",
      { class: "app-nav", "aria-label": t("nav.label") },
      routes
        .filter((route) => route.inNav)
        .map((route) =>
          h(
            "a",
            {
              class: ["nav-item", router.state.path === route.path ? "is-active" : ""],
              href: `#${route.path}`,
              "aria-current": router.state.path === route.path ? "page" : null,
            },
            [
              h("span", { class: "nav-icon", "aria-hidden": "true" }, route.icon),
              h("span", { class: "nav-label" }, t(route.labelKey)),
            ],
          ),
        ),
    );
  },
};

const App = {
  render() {
    if (!store.ready) {
      return h("div", { class: "app-loading" }, t("app.loading"));
    }
    return h("div", { class: "app-layout" }, [
      h(AppHeader),
      // Says out loud what would otherwise only show up as a failing request.
      !store.online ? h("p", { class: "offline-banner", role: "status" }, t("app.offline")) : null,
      ui.notificationsOpen
        ? h(NotificationPanel, { onClose: () => (ui.notificationsOpen = false) })
        : null,
      h("main", { class: "app-main" }, [h(router.state.route.component)]),
      h(Snackbar),
      h(AppNav),
    ]);
  },
};

setUnauthorizedHandler(() => {
  clearSession();
  router.navigate("/login", { replace: true });
});

createApp(App).mount("#app");

// The first render waits for the session so the login view does not flash up.
loadSession().then(() => {
  router.apply();
  startSync();
});
