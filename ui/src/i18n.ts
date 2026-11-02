/**
 * The app's own strings. A bundle cannot read the dashboard's catalogs (those
 * belong to the host and change with it), so every locale the dashboard ships is
 * bundled here and looked up against the host's active language.
 *
 * The catalogs (`ui/i18n/<locale>.json`) are the `apps.papyrus.*` subtree copied
 * from Kiro Crew's dashboard catalogs, plus the handful of shared dashboard
 * strings the page used (a resize grip's label, the confirm dialog's Cancel, the
 * script names), under the SAME key paths, so every call site reads as it did in
 * the built-in.
 *
 * Lookup order: the exact locale (`pt`, `zh-CN`), then its base language, then
 * English, then the key itself. A numeric `count` selects the plural form the
 * locale's rules name (`_one`, `_few`, `_many`, `_other`), as the dashboard's own
 * catalogs are keyed. `{{name}}` placeholders are filled from `vars`; one with no
 * value is left as written.
 *
 * `t` reads the language at call time. The root component subscribes to the
 * host's language changes (`useLanguageGeneration`), and its re-render reaches
 * every child, so a language switch repaints the page without a reload.
 */

import { activeLocale } from '@kirocrew/app-sdk'
import bn from '../i18n/bn.json'
import de from '../i18n/de.json'
import en from '../i18n/en.json'
import es from '../i18n/es.json'
import fr from '../i18n/fr.json'
import hi from '../i18n/hi.json'
import it from '../i18n/it.json'
import ja from '../i18n/ja.json'
import ko from '../i18n/ko.json'
import pt from '../i18n/pt.json'
import ru from '../i18n/ru.json'
import zhCN from '../i18n/zh-CN.json'

interface Catalog {
  [key: string]: string | Catalog
}

export const CATALOGS: Readonly<Record<string, Catalog>> = {
  bn,
  de,
  en,
  es,
  fr,
  hi,
  it,
  ja,
  ko,
  pt,
  ru,
  'zh-CN': zhCN,
}

const own = (obj: object, key: string) => Object.prototype.hasOwnProperty.call(obj, key)

function lookup(catalog: Catalog | undefined, key: string): string | undefined {
  let node: string | Catalog | undefined = catalog
  for (const part of key.split('.')) {
    if (node === undefined || typeof node === 'string' || !own(node, part)) return undefined
    node = node[part]
  }
  return typeof node === 'string' ? node : undefined
}

/** The catalogs to try for `locale`, most specific first, always ending in English. */
export function localeChain(locale: string): string[] {
  const chain: string[] = []
  const add = (candidate: string) => {
    if (own(CATALOGS, candidate) && !chain.includes(candidate)) chain.push(candidate)
  }
  add(locale)
  add(locale.split('-')[0])
  add('en')
  return chain
}

export function currentLocale(): string {
  try {
    const locale = activeLocale()
    return typeof locale === 'string' && locale ? locale : 'en'
  } catch {
    return 'en'
  }
}

const PLACEHOLDER = /\{\{\s*(\w+)\s*\}\}/g

function pluralForm(locale: string, count: number): string {
  try {
    return new Intl.PluralRules(locale).select(count)
  } catch {
    return count === 1 ? 'one' : 'other'
  }
}

export type Vars = Record<string, string | number>

export function t(key: string, vars?: Vars): string {
  const count = vars && typeof vars.count === 'number' ? vars.count : undefined
  for (const locale of localeChain(currentLocale())) {
    const catalog = CATALOGS[locale]
    let text: string | undefined
    if (count !== undefined) {
      text = lookup(catalog, `${key}_${pluralForm(locale, count)}`) ?? lookup(catalog, `${key}_other`)
    }
    text = text ?? lookup(catalog, key)
    if (text !== undefined) {
      return text.replace(PLACEHOLDER, (whole, name: string) =>
        vars && own(vars, name) ? String(vars[name]) : whole,
      )
    }
  }
  return key
}

/** The dashboard's name for this helper, so the ported call sites read unchanged. */
export const i18nT = t
