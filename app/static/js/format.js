/** Formatting helpers that follow the chosen language. */

import { i18n, t } from "./i18n.js";
import { store } from "./store.js";

const MINUTE = 60;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;
const WEEK = 7 * DAY;

/** Units the interval picker offers, largest first. */
export const INTERVAL_UNITS = [
  { key: "week", seconds: WEEK },
  { key: "day", seconds: DAY },
  { key: "hour", seconds: HOUR },
];

/** "in 3 days", "2 hours ago" — in the language of the interface. */
export function relativeTime(iso) {
  if (!iso) {
    return "";
  }
  const seconds = (new Date(iso).getTime() - Date.now()) / 1000;
  const formatter = new Intl.RelativeTimeFormat(i18n.locale, { numeric: "auto" });
  const steps = [
    [WEEK, "week"],
    [DAY, "day"],
    [HOUR, "hour"],
    [MINUTE, "minute"],
  ];
  for (const [size, unit] of steps) {
    if (Math.abs(seconds) >= size) {
      return formatter.format(Math.round(seconds / size), unit);
    }
  }
  return formatter.format(Math.round(seconds), "second");
}

/** "Samstag" for a `YYYY-MM-DD` value, in the chosen language. */
export function weekdayName(dateValue) {
  const [year, month, day] = String(dateValue).split("-").map(Number);
  return new Date(year, month - 1, day).toLocaleDateString(i18n.locale, { weekday: "long" });
}


export function dateTime(iso) {
  if (!iso) {
    return "";
  }
  return new Date(iso).toLocaleString(i18n.locale, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

/** A rhythm as text: "weekly", "every 3 days", "when needed". */
export function intervalLabel(seconds) {
  if (seconds < 0) {
    return t("chores.interval.on_demand");
  }
  for (const { key, seconds: size } of INTERVAL_UNITS) {
    if (seconds % size === 0) {
      const count = seconds / size;
      return count === 1 ? t(`chores.interval.every_${key}`) : t(`chores.interval.every_n_${key}`, { count });
    }
  }
  return t("chores.interval.every_n_hour", { count: Math.round(seconds / HOUR) });
}

/** Split an interval into the largest unit that divides it evenly. */
export function splitInterval(seconds) {
  for (const { key, seconds: size } of INTERVAL_UNITS) {
    if (seconds % size === 0) {
      return { unit: key, count: seconds / size };
    }
  }
  return { unit: "hour", count: Math.max(1, Math.round(seconds / HOUR)) };
}

export function unitSeconds(unit) {
  return INTERVAL_UNITS.find((entry) => entry.key === unit)?.seconds ?? DAY;
}


/** "12,34 €" — integer cents in the household currency, never a float. */
export function money(cents, currency) {
  const amount = (cents ?? 0) / 100;
  try {
    return new Intl.NumberFormat(i18n.locale, { style: "currency", currency }).format(amount);
  } catch {
    // An unknown currency code must not blank out the whole screen.
    return `${amount.toFixed(2)} ${currency ?? ""}`.trim();
  }
}

/**
 * "12,34" or "12.34" → 1234 cents, anything else → null.
 *
 * Parsed digit by digit instead of through `parseFloat`: money is counted in cents, and
 * a binary fraction has no business anywhere near a shared bill.
 */
export function parseAmount(text) {
  const compact = String(text ?? "").replace(/\s/g, "");
  // Keep only the last separator, so "1.234,50" works as well as "1234,50".
  const normalised = compact.replace(/[.,](?=[^.,]*[.,])/g, "");
  const match = normalised.match(/^(\d+)(?:[.,](\d{1,2}))?$/);
  if (!match) {
    return null;
  }
  const fraction = (match[2] ?? "").padEnd(2, "0");
  return Number(match[1]) * 100 + Number(fraction);
}

/** `YYYY-MM-DD` of today in local time — what a date input expects. */
export function today() {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

/** "6. August 2026" for a `YYYY-MM-DD` value, in the chosen language. */
export function dateLabel(value) {
  if (!value) {
    return "";
  }
  const [year, month, day] = String(value).split("-").map(Number);
  return new Date(year, month - 1, day).toLocaleDateString(i18n.locale, { dateStyle: "medium" });
}

/**
 * The even split the server would calculate: leftover cents go to the lowest member ids.
 *
 * Mirrored here only to preview the division while the form is still open — the server
 * remains the authority, and the two rules are kept identical on purpose.
 */
export function splitEvenly(amountCents, userIds) {
  const ordered = [...new Set(userIds)].sort((a, b) => a - b);
  if (ordered.length === 0) {
    return [];
  }
  const base = Math.floor(amountCents / ordered.length);
  const remainder = amountCents - base * ordered.length;
  return ordered.map((id, index) => ({
    user_id: id,
    share_cents: base + (index < remainder ? 1 : 0),
  }));
}

/**
 * The display name of a member id — also for people who are no longer in the household.
 *
 * A deleted account carries no name any more, so it shows as "Former member 7". The
 * number is the member id: it stays the same in every view, which is what keeps two
 * departed people apart when the books still say who owes what.
 */
export function memberLabel(id, provided = null) {
  const person = store.household?.members.find((entry) => entry.id === id);
  if (person?.first_name) {
    return person.first_name;
  }
  // The kitty sends the names of people who have left but still owe or are owed
  // something. Nobody else may be named — the server decides that, not this function.
  const named = provided?.find((entry) => entry.user_id === id);
  if (named?.first_name) {
    return named.first_name;
  }
  return t("common.former_member_numbered", { number: id });
}


/** "John Meier" — the full name, falling back to whatever is known. */
export function fullName(person) {
  if (!person) {
    return "";
  }
  return [person.first_name, person.last_name].filter(Boolean).join(" ");
}
