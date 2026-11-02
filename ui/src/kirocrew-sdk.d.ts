/**
 * Types for the two host modules this bundle imports. They are not on npm: the
 * dashboard's import map serves them at runtime (`/vendor/*.mjs`), so only the
 * names this app uses are declared, with the shapes the App Kit documents.
 * Component props are declared as loosely as the call sites need: the host owns
 * the components, and a prop it does not know is ignored at runtime.
 *
 * A name a host added at App Kit level 1 is typed `| undefined` here. That keeps
 * every use of it behind the feature check in `hostSdk.ts`: an older host's
 * module has no such export, and a NAMED import of a missing export fails the
 * whole bundle at link time, so those names are read off a namespace import.
 */

declare module '@kirocrew/app-sdk' {
  export interface AppApi {
    raw(path: string, init?: RequestInit): Promise<Response>
    request<T = unknown>(path: string, init?: RequestInit): Promise<T>
    get<T = unknown>(path: string, init?: RequestInit): Promise<T>
    post<T = unknown>(path: string, body?: unknown, init?: RequestInit): Promise<T>
    put<T = unknown>(path: string, body?: unknown, init?: RequestInit): Promise<T>
    del<T = unknown>(path: string, init?: RequestInit): Promise<T>
  }

  /** A scoped non-2xx: `body` is the raw response text. */
  export interface AppApiError extends Error {
    readonly status: number
    readonly body: string
  }

  export interface ChatLaunchOptions {
    agent?: string
    message?: string
    slotKey?: string
    autoSend?: boolean
    /** Level 1. An older host ignores it and opens a plain new chat. */
    memoryMode?: 'persistent' | 'incognito' | 'temporary'
    /** Level 1. An older host ignores it. */
    sessionFolder?: false
  }

  export interface ImeGuard {
    /** Spread onto an input: Enter calls `onEnter` unless an IME composition owns the key. */
    bindEnter<E extends HTMLElement = HTMLInputElement>(opts: {
      onEnter?: () => void
      onEscape?: () => void
      onBlur?: () => void
    }): Pick<
      import('react').HTMLAttributes<E>,
      'onKeyDown' | 'onCompositionStart' | 'onCompositionEnd' | 'onFocus' | 'onBlur'
    >
  }

  export type NumberOptions = Omit<Intl.NumberFormatOptions, 'localeMatcher'>
  export type DateOptions = Omit<Intl.DateTimeFormatOptions, 'localeMatcher'>

  export function useAppApi(): AppApi
  export function useChatLauncher(): { openChat: (opts?: ChatLaunchOptions) => void }
  export function useImeGuard(): ImeGuard
  export function useLanguageGeneration(): number
  export function activeLocale(): string
  export function fmtNumber(value: number, options?: NumberOptions): string
  export function fmtDateTime(value: Date | number | string, options?: DateOptions): string
  export function compareText(a: string, b: string): number

  // Level 1 (feature-detect before use).
  export const APP_SDK_LEVEL: number | undefined
}

declare module '@kirocrew/app-sdk/ui' {
  import type {
    ButtonHTMLAttributes,
    ComponentType,
    ForwardRefExoticComponent,
    HTMLAttributes,
    InputHTMLAttributes,
    ReactNode,
    RefAttributes,
  } from 'react'

  type Loose = { className?: string; children?: ReactNode; [prop: string]: unknown }

  export const Btn: ForwardRefExoticComponent<
    ButtonHTMLAttributes<HTMLButtonElement> & { danger?: boolean; primary?: boolean } & RefAttributes<HTMLButtonElement>
  >
  export const SendBtn: ForwardRefExoticComponent<
    ButtonHTMLAttributes<HTMLButtonElement> & RefAttributes<HTMLButtonElement>
  >
  export const Input: ForwardRefExoticComponent<
    InputHTMLAttributes<HTMLInputElement> & RefAttributes<HTMLInputElement>
  >
  export const SearchInput: ComponentType<InputHTMLAttributes<HTMLInputElement> & Loose>

  export interface Segment {
    key: string
    label: ReactNode
  }
  export const SegmentedControl: ComponentType<{
    segments: Segment[]
    value: string
    onChange: (key: string) => void
    layoutId?: string
    collapse?: boolean
    className?: string
  }>
  export const MarkdownRenderer: ComponentType<{ content: string } & Loose>
  export const Card: ComponentType<HTMLAttributes<HTMLDivElement> & Loose>
  export const CardTitle: ComponentType<Loose>
  export const ContentSkeleton: ComponentType<Loose & { rows?: number }>
  export const StatCard: ComponentType<{ label: ReactNode; value: ReactNode; accent?: boolean } & Loose>
  export const EmptyState: ComponentType<{
    icon: ReactNode
    title: string
    subtitle?: string
    action?: ReactNode
  } & Loose>
  export const PageHeader: ComponentType<{ title: ReactNode; subtitle?: string; actions?: ReactNode } & Loose>
  export const Clickable: ComponentType<HTMLAttributes<HTMLElement> & Loose>
  export const InfoTip: ComponentType<{ text: string } & Loose>
  export const ErrorNotice: ComponentType<{
    message?: string | null
    className?: string
    title?: string
    onDismiss?: () => void
    askAgent?: boolean
  } & Loose>
  export const Modal: ComponentType<{
    open: boolean
    onClose: () => void
    title?: ReactNode
    maxWidth?: number
    footer?: ReactNode
    children?: ReactNode
  }>

  export interface SelectProps {
    options: string[]
    optionLabels?: string[]
    value: string
    onChange: (value: string) => void
    clearLabel?: string
    disabled?: boolean
    'aria-label'?: string
    title?: string
    className?: string
  }

  // Level 1 (read through hostSdk.ts only).
  export const Select: ComponentType<SelectProps> | undefined
  export const guideCaution: Readonly<Record<string, string>> | undefined
}
