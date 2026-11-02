/**
 * Host names added at App Kit level 1, feature-detected.
 *
 * A named import of an export the host's module does not have fails the whole
 * bundle at link time, so a host older than these seams would show a broken page
 * instead of a slightly plainer one. These names are therefore read off namespace
 * imports, each through an `in` check, and each has a local fallback. Names every
 * supported host has are named-imported where they are used.
 */

import * as ui from '@kirocrew/app-sdk/ui'

/** Spread onto a control that removes something; the guide shows its caution line there. */
export const caution: Readonly<Record<string, string>> =
  ('guideCaution' in ui ? ui.guideCaution : undefined) ?? {}
