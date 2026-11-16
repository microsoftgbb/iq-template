// The interface between the UI (src/App.tsx, src/components) and the live agent layer (src/live). Both sides
// compile against it.
import type { ToolCall } from './protocol.ts'

export type { ToolCall } from './protocol.ts'

// The two Foundry agents the app asks: the same instructions, with the IQ tools and without any tool.
export type Variant = 'with_iq' | 'without_iq'
export type IqKey = 'fabric' | 'foundry' | 'web' | 'work'

export interface Marker {
  // As written in the reply: 'D1', 'F2', 'Web1', 'W1'.
  id: string
  kind: IqKey
  // '2' for [F1 §2].
  section?: string
}

export interface SourceLine {
  id: string
  kind: IqKey
  // The line after the marker, as plain text.
  text: string
  url?: string
}

export interface ParsedReply {
  // The Markdown before the Sources and Steps sections.
  answer: string
  sources: SourceLine[]
  steps: string[]
  // Unique, in order of first appearance in the answer.
  markers: Marker[]
  // The IQs cited in the answer or its sources, always ordered fabric, foundry, work, web.
  usedIqs: IqKey[]
}

export interface Identity {
  name: string
}

export interface Question {
  variant: Variant
  text: string
  // Foundry's ID of the answer this question follows up.
  previousResponseId?: string
}

// responseId is Foundry's ID for the agent run, which its traces show and a follow-up continues; toolCalls are the
// MCP calls Foundry reported. needs-consent carries the link a tool's server asked the user to open first. code is
// 'unauthorized' when the app server refused the sign-in, 'timeout' or 'cancelled' when the turn stopped.
export type TurnResult =
  | { status: 'answered'; reply: ParsedReply; raw: string; elapsedMs: number; responseId?: string; toolCalls: ToolCall[] }
  | { status: 'needs-consent'; consentUrl: string; server?: string; elapsedMs: number; responseId?: string; toolCalls: ToolCall[] }
  | { status: 'failed'; message: string; code?: string; elapsedMs: number; responseId?: string; toolCalls: ToolCall[] }

export interface AgentClient {
  readonly mode: 'live' | 'mock'
  // Never rejects. onTool receives each tool call as Foundry starts and finishes it, while the turn runs.
  ask(question: Question, signal?: AbortSignal, onTool?: (call: ToolCall) => void): Promise<TurnResult>
}

export interface Session {
  readonly mode: 'live' | 'mock'
  // The signed-in user; undefined until someone signs in in this tab.
  readonly identity?: Identity
  // Shown in the header, such as mock mode's label; only mock builds have one.
  readonly notice?: string
  signIn(): Promise<void>
  signOut(): Promise<void>
}
