// Presenter mode's state and rules, pure so they run under Node tests: the step the presenter is on, its timers, the
// notes panel, the keys a presentation clicker sends, and when a step's scripted answer may stand in for a live turn.
import type { Turn } from '../exchanges.ts'
import { VARIANT_LABELS } from '../labels.ts'
import type { Variant } from '../live/types.ts'
import { askOf, type DemoScript, type Scripted, type Step } from './script.ts'

// sessionStorage, so presenter mode survives the sign-in redirect but not the browser tab.
export const PRESENTER_STORAGE_KEY = 'iq-template.presenter'
export const SCRIPTED_LABEL = 'Scripted answer, not live'

export interface PresenterState {
  count: number
  index: number
  // Date.now() when the session and the current step started.
  sessionStartedAt: number
  stepStartedAt: number
  // Whether the notes panel shows, and whether its notes are blanked.
  notes: boolean
  blank: boolean
}

export type PresenterAction =
  | { type: 'next' | 'previous'; at: number }
  | { type: 'go'; index: number; at: number }
  | { type: 'restart'; at: number }
  | { type: 'toggle-notes' | 'toggle-blank' }

export type PresenterCommand = 'next' | 'previous' | 'ask' | 'toggle-notes' | 'toggle-blank'

export interface KeyPress {
  key: string
  altKey?: boolean
  ctrlKey?: boolean
  metaKey?: boolean
  isComposing?: boolean
  // The focused element.
  target?: { tagName?: string; isContentEditable?: boolean } | null
}

export function startPresenter(count: number, at: number): PresenterState {
  return { count, index: 0, sessionStartedAt: at, stepStartedAt: at, notes: true, blank: false }
}

function go(state: PresenterState, index: number, at: number): PresenterState {
  const next = Math.min(Math.max(index, 0), state.count - 1)
  return next === state.index ? state : { ...state, index: next, stepStartedAt: at }
}

export function reducePresenter(state: PresenterState, action: PresenterAction): PresenterState {
  switch (action.type) {
    case 'next':
      return go(state, state.index + 1, action.at)
    case 'previous':
      return go(state, state.index - 1, action.at)
    case 'go':
      return go(state, action.index, action.at)
    case 'restart':
      return { ...state, index: 0, sessionStartedAt: action.at, stepStartedAt: action.at }
    case 'toggle-notes':
      return { ...state, notes: !state.notes }
    case 'toggle-blank':
      return { ...state, blank: !state.blank }
  }
}

const TEXT_FIELDS = new Set(['INPUT', 'TEXTAREA', 'SELECT'])
// Elements that Enter already activates.
const ACTIVATED_BY_ENTER = new Set(['BUTTON', 'A', 'SUMMARY'])

// Where the user types: presenter keys never act there, and a new step never takes the focus from there.
export function isTextField(element: KeyPress['target']): boolean {
  return Boolean(element?.isContentEditable || (element?.tagName && TEXT_FIELDS.has(element.tagName.toUpperCase())))
}

// A presentation clicker sends PageDown and PageUp, or the arrow keys. Nothing fires while a text field has focus,
// with a modifier held (the browser's shortcuts), or during an input method composition.
export function presenterCommand(press: KeyPress): PresenterCommand | undefined {
  if (press.altKey || press.ctrlKey || press.metaKey || press.isComposing || isTextField(press.target)) return undefined
  const tag = press.target?.tagName?.toUpperCase()
  switch (press.key) {
    case 'ArrowRight':
    case 'PageDown':
      return 'next'
    case 'ArrowLeft':
    case 'PageUp':
      return 'previous'
    case 'Enter':
      return tag && ACTIVATED_BY_ENTER.has(tag) ? undefined : 'ask'
    case 'n':
    case 'N':
      return 'toggle-notes'
    case 'b':
    case 'B':
      return 'toggle-blank'
    default:
      return undefined
  }
}

// ?presenter=1 turns presenter mode on and ?presenter=0 off; otherwise it stays as this tab last had it.
export function presenterFromUrl(search: string): boolean | undefined {
  const value = new URLSearchParams(search).get('presenter')
  return value === '1' ? true : value === '0' ? false : undefined
}

interface Stored {
  on: boolean
  state: PresenterState
}

export function saveStored(on: boolean, state: PresenterState): string {
  const { index, sessionStartedAt, stepStartedAt, notes } = state
  return JSON.stringify({ on, index, sessionStartedAt, stepStartedAt, notes })
}

// What this tab stored, checked, or presenter mode off at the first step.
export function readStored(value: string | null, count: number, at: number): Stored {
  const fresh = { on: false, state: startPresenter(count, at) }
  let stored: unknown
  try {
    stored = JSON.parse(value ?? 'null')
  } catch {
    return fresh
  }
  if (typeof stored !== 'object' || stored === null) return fresh
  const { on, index, sessionStartedAt, stepStartedAt, notes } = stored as Record<string, unknown>
  const time = (moment: unknown): moment is number => typeof moment === 'number' && Number.isFinite(moment) && moment <= at
  if (typeof on !== 'boolean' || !Number.isInteger(index) || (index as number) < 0 || (index as number) >= count
    || !time(sessionStartedAt) || !time(stepStartedAt) || typeof notes !== 'boolean') return fresh
  return { on, state: { count, index: index as number, sessionStartedAt, stepStartedAt, notes, blank: false } }
}

// m:ss, or h:mm:ss from an hour.
export function elapsed(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / 1000))
  const [hours, minutes] = [Math.floor(seconds / 3600), Math.floor((seconds % 3600) / 60)]
  const pad = (value: number) => String(value).padStart(2, '0')
  return hours ? `${hours}:${pad(minutes)}:${pad(seconds % 60)}` : `${minutes}:${pad(seconds % 60)}`
}

// The hint under a step's notes: what each agent's answer should show, from the script's checks.
export function expectations(step: Step): string[] {
  return step.asks.map(({ variant, checks }) => {
    const parts = [checks.keyFacts.map((fact) => fact.fact).join('; ')]
    if (checks.mustNot.length) parts.push(`Not: ${checks.mustNot.map((fact) => fact.fact).join('; ')}`)
    if (checks.markers.length) parts.push(`Cites ${checks.markers.join(', ')}`)
    parts.push(...checks.manual)
    return `${VARIANT_LABELS[variant]}: ${parts.filter(Boolean).map((part) => part.replace(/\.$/, '')).join('. ')}.`
  })
}

// The scripted reply a presenter may show instead of a live turn: only for the first turn of an agent that the
// presenter asked from a step, once it ended without an answer (failed, timed out, stopped or awaiting consent), and
// only when the script has that agent's reply.
export function scriptedFallback(script: DemoScript, stepId: string | undefined, variant: Variant, turn: Turn, first: boolean): Scripted | undefined {
  if (!stepId || !first || turn.running || !turn.result || turn.result.status === 'answered') return undefined
  return askOf(script.steps.find((step) => step.id === stepId), variant)?.scripted
}
