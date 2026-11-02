/**
 * A light stand-in for the host's `@kirocrew/app-sdk/ui`, at App Kit level 1:
 * plain elements with the roles and labels the host components render, so a
 * test can find them the way a user would.
 */

import {
  forwardRef,
  type ButtonHTMLAttributes,
  type HTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
} from 'react'
import type { SelectProps } from '@kirocrew/app-sdk/ui'

type Loose = { className?: string; children?: ReactNode; [prop: string]: unknown }

export const Btn = forwardRef<
  HTMLButtonElement,
  ButtonHTMLAttributes<HTMLButtonElement> & { danger?: boolean; primary?: boolean }
>(function Btn({ danger: _danger, primary: _primary, type = 'button', ...rest }, ref) {
  return <button ref={ref} type={type} {...rest} />
})

export const SendBtn = forwardRef<HTMLButtonElement, ButtonHTMLAttributes<HTMLButtonElement>>(
  function SendBtn({ type = 'button', ...rest }, ref) {
    return <button ref={ref} type={type} {...rest} />
  },
)

export const SearchInput = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function SearchInput(props, ref) {
    return <input ref={ref} type="search" {...props} />
  },
)

export function SegmentedControl({
  segments,
  value,
  onChange,
}: {
  segments: Array<{ key: string; label: ReactNode }>
  value: string
  onChange: (key: string) => void
  layoutId?: string
  collapse?: boolean
}) {
  return (
    <div role="tablist">
      {segments.map((seg) => (
        <button
          key={seg.key}
          type="button"
          role="tab"
          aria-selected={seg.key === value}
          onClick={() => onChange(seg.key)}
        >
          {seg.label}
        </button>
      ))}
    </div>
  )
}

export function MarkdownRenderer({ content }: { content: string }) {
  return <div data-testid="markdown">{content}</div>
}

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function Input(props, ref) {
    return <input ref={ref} {...props} />
  },
)

export function Card({ children, className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div className={className} {...rest}>{children}</div>
}

export function CardTitle({ children }: Loose) {
  return <h2>{children}</h2>
}

export function ContentSkeleton() {
  return <div role="status" aria-busy="true" />
}

export function StatCard({ label, value }: Loose) {
  return (
    <div data-testid="stat-card">
      <span data-testid="stat-card-label">{label as ReactNode}</span>
      <span data-testid="stat-card-value">{value as ReactNode}</span>
    </div>
  )
}

export function EmptyState({ title, subtitle, action }: { icon: ReactNode; title: string; subtitle?: string; action?: ReactNode }) {
  return (
    <div>
      <div>{title}</div>
      {subtitle ? <div>{subtitle}</div> : null}
      {action}
    </div>
  )
}

export function PageHeader({ title, subtitle, actions }: { title: ReactNode; subtitle?: string; actions?: ReactNode }) {
  return (
    <header data-testid="page-header">
      <h1 data-testid="page-title">{title}</h1>
      {subtitle ? <p>{subtitle}</p> : null}
      {actions}
    </header>
  )
}

export function Clickable({ children, onClick, className, ...rest }: HTMLAttributes<HTMLElement> & Loose) {
  return (
    <div
      role="button"
      tabIndex={0}
      className={className}
      onClick={onClick}
      onKeyDown={e => {
        // The host's Clickable activates on Enter and Space, like a button.
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          ;(onClick as ((ev: unknown) => void) | undefined)?.(e)
        }
      }}
      {...(rest as HTMLAttributes<HTMLDivElement>)}
    >
      {children}
    </div>
  )
}

export function InfoTip({ text }: { text: string }) {
  return <span title={text}>ⓘ</span>
}

export function ErrorNotice({ message, title, onDismiss }: { message?: string | null; title?: string; onDismiss?: () => void }) {
  if (!message) return null
  return (
    <div role="alert">
      {title ? <strong>{title}</strong> : null}
      {message}
      {onDismiss ? <button type="button" onClick={onDismiss}>Dismiss</button> : null}
    </div>
  )
}

export function Modal({ open, title, footer, children }: { open: boolean; onClose: () => void; title?: ReactNode; footer?: ReactNode; children?: ReactNode }) {
  if (!open) return null
  return (
    <div role="dialog" aria-label={typeof title === 'string' ? title : undefined}>
      <h2>{title}</h2>
      {children}
      {footer}
    </div>
  )
}

/** Stands in for the host's themed dropdown; marked so a test can tell it from the fallback. */
export function Select({ options, optionLabels, value, onChange, clearLabel, disabled, 'aria-label': ariaLabel }: SelectProps) {
  return (
    <select data-host-select="" aria-label={ariaLabel} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
      {clearLabel !== undefined || !options.includes(value) ? <option value="">{clearLabel ?? ''}</option> : null}
      {options.map((o, i) => (
        <option key={o} value={o}>
          {optionLabels?.[i] ?? o}
        </option>
      ))}
    </select>
  )
}

export const guideCaution = { 'data-guide-caution': '' } as const
