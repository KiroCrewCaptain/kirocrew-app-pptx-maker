/**
 * What changed when the page left the dashboard: its own string catalogs, the
 * host's scoped client as the transport, the chat launcher in place of the
 * dashboard's private session API, and the fallbacks for a host older than App
 * Kit level 1.
 */
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { describe, it, expect, vi } from 'vitest'
import { CATALOGS, localeChain, t } from '../src/i18n'
import { artifactUrl, fetchArtifactText, pptxMakerApi, setTransport } from '../src/api'
import { stubLocale } from './stubs/app-sdk'

const SRC = join(__dirname, '..', 'src')

function sources(): Array<[string, string]> {
  const out: Array<[string, string]> = []
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = join(dir, entry.name)
      if (entry.isDirectory()) walk(path)
      else if (/\.tsx?$/.test(entry.name)) out.push([entry.name, readFileSync(path, 'utf-8')])
    }
  }
  walk(SRC)
  return out
}

describe('the string catalogs', () => {
  const keysOf = (node: object, prefix = ''): string[] =>
    Object.entries(node).flatMap(([k, v]) =>
      typeof v === 'string' ? [prefix + k] : keysOf(v as object, `${prefix}${k}.`))

  it('carries every locale the dashboard ships', () => {
    expect(Object.keys(CATALOGS).sort()).toEqual(
      ['bn', 'de', 'en', 'es', 'fr', 'hi', 'it', 'ja', 'ko', 'pt', 'ru', 'zh-CN'],
    )
  })

  it('has every key the source names, in English and in every other locale', () => {
    const used = new Set<string>()
    for (const [, text] of sources()) {
      for (const m of text.matchAll(/'(apps\.pptxMaker\.[A-Za-z0-9_.]+)'/g)) used.add(m[1])
    }
    expect(used.size).toBeGreaterThan(50)
    for (const [locale, catalog] of Object.entries(CATALOGS)) {
      const keys = new Set(keysOf(catalog))
      const missing = [...used].filter(key => !keys.has(key) && !keys.has(`${key}_other`))
      expect([locale, missing]).toEqual([locale, []])
    }
  })

  it('falls back from a regional locale to its base language, then English', () => {
    expect(localeChain('pt-BR')).toEqual(['pt', 'en'])
    expect(localeChain('xx')).toEqual(['en'])
  })

  it('picks the plural form the locale names', () => {
    stubLocale('en')
    expect(t('apps.pptxMaker.pptxMakerPage.slide_count', { count: 1 })).toBe('1 slide')
    expect(t('apps.pptxMaker.pptxMakerPage.slide_count', { count: 3 })).toBe('3 slides')
    stubLocale('de')
    expect(t('apps.pptxMaker.pptxMakerPage.slide_count', { count: 3 })).not.toBe('3 slides')
  })

  it('shows the key itself for a string no catalog has', () => {
    expect(t('apps.pptxMaker.no_such_key')).toBe('apps.pptxMaker.no_such_key')
  })
})

describe('the transport', () => {
  it('sends every call through the scoped client the root hands it', async () => {
    const raw = vi.fn(async () => new Response(JSON.stringify({ decks: [] }), { status: 200 }))
    setTransport({ raw })
    try {
      await pptxMakerApi.decks()
      await fetchArtifactText('preview/d/specs/brief.md')
      expect(raw.mock.calls.map(([path]) => path)).toEqual([
        '/api/apps/pptx-maker/decks',
        '/api/apps/pptx-maker/preview/d/specs/brief.md',
      ])
    } finally {
      setTransport(null)
    }
  })

  it("reads the backend's error text back out of the scoped client's refusal", async () => {
    setTransport({
      raw: async () => {
        throw Object.assign(new Error('API 409'), {
          status: 409,
          body: JSON.stringify({ error: "style 'brand' already exists", code: 'style_exists' }),
        })
      },
    })
    try {
      await expect(pptxMakerApi.importStyle('brand', '<html></html>')).rejects.toMatchObject({
        name: 'PptxMakerError',
        status: 409,
        message: "style 'brand' already exists",
      })
    } finally {
      setTransport(null)
    }
  })

  it('names the status when the refusal body is not the JSON contract', async () => {
    setTransport({
      raw: async () => {
        throw Object.assign(new Error('API 502'), { status: 502, body: 'Bad Gateway' })
      },
    })
    try {
      await expect(pptxMakerApi.engine()).rejects.toMatchObject({ status: 502, message: 'HTTP 502' })
    } finally {
      setTransport(null)
    }
  })

  it('passes a refusal that is not an HTTP answer through unchanged', async () => {
    const outside = new Error('path is outside this app’s permissions')
    setTransport({ raw: async () => { throw outside } })
    try {
      await expect(pptxMakerApi.decks()).rejects.toBe(outside)
    } finally {
      setTransport(null)
    }
  })

  it('builds artifact links under the granted prefix only', () => {
    expect(artifactUrl('preview/d/output.pptx')).toBe('/api/apps/pptx-maker/preview/d/output.pptx')
    expect(artifactUrl('///preview/d/x.md')).toBe('/api/apps/pptx-maker/preview/d/x.md')
  })
})

describe('no private dashboard surface', () => {
  it('imports only the host modules, its own files and DOMPurify', () => {
    const allowed = new Set([
      'react',
      'lucide-react',
      '@tanstack/react-query',
      '@kirocrew/app-sdk',
      '@kirocrew/app-sdk/ui',
      'dompurify',
    ])
    for (const [name, text] of sources()) {
      for (const m of text.matchAll(/^import[^'"]*['"]([^'"]+)['"]/gm)) {
        const spec = m[1]
        if (spec.startsWith('./') || spec.startsWith('../i18n/')) continue
        expect([name, allowed.has(spec)]).toEqual([name, true])
      }
    }
  })

  it('opens deck chats through the host launcher, not the session API', () => {
    const page = readFileSync(join(SRC, 'PptxMakerPage.tsx'), 'utf-8')
    expect(page).toContain('useChatLauncher')
    expect(page).not.toMatch(/createChatSlot|react-router/)
  })
})

describe('an older host', () => {
  it('drops the caution marker without the level-1 name', async () => {
    vi.resetModules()
    vi.doMock('@kirocrew/app-sdk/ui', async () => {
      const actual = await vi.importActual<Record<string, unknown>>('./stubs/app-sdk-ui')
      const { guideCaution: _g, ...older } = actual
      return older
    })
    try {
      const hostSdk = await import('../src/hostSdk')
      expect(hostSdk.caution).toEqual({})
    } finally {
      vi.doUnmock('@kirocrew/app-sdk/ui')
      vi.resetModules()
    }
  })

  it('carries the caution marker on a level-1 host', async () => {
    const hostSdk = await import('../src/hostSdk')
    expect(hostSdk.caution).toEqual({ 'data-guide-caution': '' })
  })
})
