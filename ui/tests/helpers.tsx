import type { ReactElement } from 'react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { render } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

/**
 * Render under a fresh query client: no retries, so a rejected query settles at once.
 * `queryDefaults` merges into the query defaults, the way the dashboard's own test
 * helper takes them (the host's app query client is what serves an installed page).
 */
export function renderWithProviders(
  ui: ReactElement,
  opts: { queryDefaults?: Record<string, unknown> } = {},
) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, ...(opts.queryDefaults ?? {}) },
      mutations: { retry: false },
    },
  })
  return { client, queryClient: client, ...render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>) }
}

/** A source file of this bundle, read for a structural assertion. */
export function readSource(file: string): string {
  return readFileSync(join(__dirname, '..', 'src', file), 'utf-8')
}
