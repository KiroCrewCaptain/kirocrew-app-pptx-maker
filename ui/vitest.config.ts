import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// Root-relative: Vite resolves a leading `/` against this directory.
const stub = (name: string) => `/tests/stubs/${name}`

// The two host SDK modules exist only inside a running dashboard (its import map
// serves them). Tests resolve them to light local stubs instead, and a test that
// needs a different host shape overrides them with `vi.mock`. No network.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: [
      { find: /^@kirocrew\/app-sdk\/ui$/, replacement: stub('app-sdk-ui.tsx') },
      { find: /^@kirocrew\/app-sdk$/, replacement: stub('app-sdk.ts') },
    ],
  },
  test: {
    environment: 'jsdom',
    include: ['tests/**/*.test.{ts,tsx}'],
    setupFiles: ['tests/setup.ts'],
    restoreMocks: true,
    testTimeout: 10_000,
  },
})
