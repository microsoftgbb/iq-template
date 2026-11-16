// The UI's words for variants, choices, tool states and durations, and what each turn announces to screen readers.
import type { Turn } from './exchanges.ts'
import { IQ_NAMES, iqOf } from './live/protocol.ts'
import { CANCELLED } from './live/turn.ts'
import type { IqKey, ToolCall, Variant } from './live/types.ts'

export type Choice = Variant | 'both'

export const CHOICES: readonly { value: Choice; label: string }[] = [
  { value: 'with_iq', label: 'With IQ' },
  { value: 'without_iq', label: 'Without IQ' },
  { value: 'both', label: 'Ask both' },
]

export const CHOICE_HINTS: Readonly<Record<Choice, string>> = {
  with_iq: 'The agent with its IQ tools: the live ontology, approved documents, public context and, when enabled, your mail.',
  without_iq: 'The same agent and instructions without any tool: it says what it can’t confirm.',
  both: 'Both agents side by side, each in its own run.',
}

export const VARIANT_LABELS: Readonly<Record<Variant, string>> = { with_iq: 'With IQ', without_iq: 'Without IQ' }

export const VARIANT_DETAILS: Readonly<Record<Variant, string>> = {
  with_iq: 'The agent with its IQ tools',
  without_iq: 'The same agent, no tools',
}

// What each marker letter cites, in the order the agents' instructions list them.
export const LEGEND: readonly { marker: string; iq: IqKey; what: string }[] = [
  { marker: 'D', iq: 'fabric', what: 'live ontology data' },
  { marker: 'F', iq: 'foundry', what: 'approved documents' },
  { marker: 'Web', iq: 'web', what: 'public context' },
  { marker: 'W', iq: 'work', what: 'your mail' },
]

export const STATE_LABELS: Readonly<Record<ToolCall['state'], string>> = { running: 'Running', completed: 'Done', failed: 'Failed' }

export function iqName(server: string | undefined): string | undefined {
  const iq = server ? iqOf(server) : undefined
  return iq ? IQ_NAMES[iq] : undefined
}

export function duration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)} ms`
  return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`
}

// A short, polite update for the turn's live region: each tool event, then the outcome.
export function announcement(turn: Turn, label: string): string {
  const result = turn.result
  if (turn.running || !result) {
    const last = turn.toolCalls.at(-1)
    if (!last) return `${label}: asking the agent.`
    const name = iqName(last.server) ?? last.tool
    return `${label}: ${name} ${last.state === 'running' ? 'started' : STATE_LABELS[last.state].toLowerCase()}.`
  }
  switch (result.status) {
    case 'answered':
      return `${label}: answer ready.`
    case 'needs-consent':
      return `${label}: ${iqName(result.server) ?? 'a tool'} needs your consent.`
    case 'failed':
      return result.code === CANCELLED ? `${label}: stopped.` : `${label}: ${result.message}`
  }
}
