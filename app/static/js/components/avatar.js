/** Round avatar: the uploaded picture when there is one, the initials otherwise. */

import { h } from "../../vendor/vue.esm-browser.prod.js";

function initials(person) {
  const letters = [person?.first_name, person?.last_name]
    .filter(Boolean)
    .map((name) => name.trim()[0])
    .join("");
  return letters.toUpperCase() || "?";
}

export const Avatar = {
  props: {
    person: { type: Object, default: null },
    size: { type: String, default: "medium" },
  },
  render() {
    const file = this.person?.avatar_file;
    if (file) {
      return h("img", {
        class: ["avatar", `avatar--${this.size}`],
        src: `/media/${file}`,
        alt: "",
        loading: "lazy",
      });
    }
    return h(
      "span",
      { class: ["avatar", `avatar--${this.size}`], "aria-hidden": "true" },
      initials(this.person),
    );
  },
};


/** Name plus login name, so people with the same name stay distinguishable. */
export const PersonName = {
  props: {
    person: { type: Object, default: null },
  },
  render() {
    if (!this.person) {
      return null;
    }
    const name = [this.person.first_name, this.person.last_name].filter(Boolean).join(" ");
    return h("span", { class: "person" }, [
      h("span", { class: "person-name" }, name),
      this.person.username
        ? h("span", { class: "person-login" }, ` (${this.person.username})`)
        : null,
    ]);
  },
};
