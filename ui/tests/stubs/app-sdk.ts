/**
 * A light stand-in for the host's `@kirocrew/app-sdk`, at App Kit level 1.
 *
 * Only the names the bundle uses, with the host's documented behaviour where a
 * test depends on it. The `stub*` / `resetStubSdk` exports are test controls; no
 * host module has them. A test that needs an older host replaces this module
 * with `vi.mock`.
 *
 * `useAppApi().raw` is a same-origin `fetch` that throws the host's scoped error
 * shape (`status` + raw `body`) on a non-2xx, so a test that stubs `fetch` drives
 * the page through the same transport the dashboard hands it.
 */

import { vi } from 'vitest'
import type { KeyboardEvent } from 'react'
import type { AppApi, ChatLaunchOptions, DateOptions, NumberOptions } from '@kirocrew/app-sdk'

let locale = 'en'

/** The host's active language for the next lookups. */
export function stubLocale(next: string): void {
  locale = next
}

export const stubOpenChat = vi.fn<(opts?: ChatLaunchOptions) => void>()

export function resetStubSdk(): void {
  locale = 'en'
  stubOpenChat.mockReset()
}

export function activeLocale(): string {
  return locale
}

export function useLanguageGeneration(): number {
  return 0
}

export function fmtNumber(value: number, options?: NumberOptions): string {
  if (!Number.isFinite(value)) return '—'
  return new Intl.NumberFormat(locale, options).format(value)
}

export function fmtDateTime(value: Date | number | string, options?: DateOptions): string {
  return new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short', ...options }).format(new Date(value))
}

export function compareText(a: string, b: string): number {
  return new Intl.Collator(locale, { numeric: true, sensitivity: 'base' }).compare(a, b)
}

export const APP_SDK_LEVEL = 1

class ScopedError extends Error {
  status: number
  body: string
  constructor(status: number, body: string) {
    super(`API ${status}: ${body}`)
    this.name = 'AppApiError'
    this.status = status
    this.body = body
  }
}

async function raw(path: string, init?: RequestInit): Promise<Response> {
  const r = await fetch(path, { ...init })
  if (!r.ok) {
    const text = await r.text().catch(() => r.statusText)
    throw new ScopedError(r.status, text)
  }
  return r
}

const unstubbed = () => Promise.reject(new Error('stub app-sdk: only raw() is used'))

const API: AppApi = { raw, request: unstubbed, get: unstubbed, post: unstubbed, put: unstubbed, del: unstubbed }

export function useAppApi(): AppApi {
  return API
}

export function useChatLauncher(): { openChat: (opts?: ChatLaunchOptions) => void } {
  return { openChat: stubOpenChat }
}

export function useImeGuard() {
  return {
    bindEnter: ({ onEnter, onEscape, onBlur }: { onEnter?: () => void; onEscape?: () => void; onBlur?: () => void }) => ({
      onKeyDown: (e: KeyboardEvent<HTMLElement>) => {
        if (e.nativeEvent.isComposing) return
        if (e.key === 'Enter') {
          e.preventDefault()
          onEnter?.()
        } else if (e.key === 'Escape') {
          onEscape?.()
        }
      },
      onBlur: () => onBlur?.(),
    }),
  }
}
