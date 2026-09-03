/**
 * The one place that turns a failure into a sentence somebody can read.
 *
 * Everything the server sends along is English: `message` is meant for logs and for
 * whoever drives the API directly. Putting it on screen would leave every error in one
 * language no matter which one was chosen, so nothing outside this module ever touches
 * it. The chain is: the key the server sent → a general text for the error code → the
 * catch-all. Each step is guaranteed to exist, so a raw key can never reach the screen.
 */

import { ApiError, NetworkError } from "./api.js";
import { t } from "./i18n.js";

/** The reasons `NetworkError` is raised with; anything else falls back to unreachable. */
const NETWORK_REASONS = new Set(["offline", "timeout", "unreachable"]);

export function errorText(error) {
  if (error instanceof ApiError) {
    return error.messageKey ? t(error.messageKey, error.params) : t(`error.${error.code}`);
  }
  if (error instanceof NetworkError) {
    return t(`error.${NETWORK_REASONS.has(error.reason) ? error.reason : "unreachable"}`);
  }
  // A bug in our own code rather than an answer from the server — say so plainly.
  return t("error.internal_error");
}
