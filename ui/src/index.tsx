/**
 * The bundle's entry: AppHost lazy-imports this module and renders its default
 * export as the app's page.
 *
 * Two things happen here once, for the whole page. The host's permission-scoped
 * API client becomes the transport every PPTX Maker call goes through
 * (`api.setTransport`), and the host's language generation is subscribed to,
 * which repaints the page on a language switch: `t` reads the active locale at
 * call time, and this component's re-render reaches every child.
 */

import { useAppApi, useLanguageGeneration } from '@kirocrew/app-sdk'
import { setTransport } from './api'
import PptxMakerPage from './PptxMakerPage'

export default function PptxMakerApp() {
  useLanguageGeneration()
  // Set during render, before any child's query runs: the client is stable for
  // the app's lifetime, and a child's first fetch must already use it.
  setTransport(useAppApi())
  return (
    <div className="flex flex-col flex-1 min-h-0 h-full">
      <PptxMakerPage />
    </div>
  )
}
