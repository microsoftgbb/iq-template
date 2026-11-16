// The page's conversation state, kept pure so the UI only dispatches: each exchange is one question, asked of one
// agent or of both side by side; each thread holds one agent's turns, its follow-ups continuing the last answer.
import type { ToolCall, TurnResult, Variant } from './live/types.ts'

export interface Turn {
  id: string
  question: string
  previousResponseId?: string
  // performance.now() when the turn (re)started.
  startedAt: number
  running: boolean
  toolCalls: ToolCall[]
  result?: TurnResult
}

export interface Thread {
  id: string
  variant: Variant
  turns: Turn[]
}

export interface Exchange {
  id: string
  question: string
  threads: Thread[]
}

export type Action =
  | { type: 'ask'; exchange: Exchange }
  | { type: 'follow-up'; threadId: string; turn: Turn }
  | { type: 'restart'; turnId: string; startedAt: number }
  | { type: 'tool'; turnId: string; call: ToolCall }
  | { type: 'finish'; turnId: string; result: TurnResult }

export function newTurn(id: string, question: string, startedAt: number, previousResponseId?: string): Turn {
  return { id, question, startedAt, running: true, toolCalls: [], ...(previousResponseId ? { previousResponseId } : {}) }
}

// The response a follow-up in this thread continues: the last turn's, once it has answered.
export function followUpFrom(thread: Thread): string | undefined {
  const last = thread.turns.at(-1)
  return last && !last.running && last.result?.status === 'answered' ? last.result.responseId : undefined
}

function mapTurns(state: Exchange[], update: (turn: Turn, thread: Thread) => Turn): Exchange[] {
  return state.map((exchange) => ({
    ...exchange,
    threads: exchange.threads.map((thread) => ({ ...thread, turns: thread.turns.map((turn) => update(turn, thread)) })),
  }))
}

function withCall(calls: readonly ToolCall[], call: ToolCall): ToolCall[] {
  return calls.some((existing) => existing.id === call.id)
    ? calls.map((existing) => (existing.id === call.id ? call : existing))
    : [...calls, call]
}

export function reduce(state: Exchange[], action: Action): Exchange[] {
  switch (action.type) {
    case 'ask':
      return [action.exchange, ...state]
    case 'follow-up':
      return state.map((exchange) => ({
        ...exchange,
        threads: exchange.threads.map((thread) => (thread.id === action.threadId ? { ...thread, turns: [...thread.turns, action.turn] } : thread)),
      }))
    case 'restart':
      return mapTurns(state, (turn) => (turn.id === action.turnId
        ? { id: turn.id, question: turn.question, startedAt: action.startedAt, running: true, toolCalls: [], ...(turn.previousResponseId ? { previousResponseId: turn.previousResponseId } : {}) }
        : turn))
    case 'tool':
      return mapTurns(state, (turn) => (turn.id === action.turnId && turn.running ? { ...turn, toolCalls: withCall(turn.toolCalls, action.call) } : turn))
    case 'finish':
      return mapTurns(state, (turn) => (turn.id === action.turnId
        ? { ...turn, running: false, result: action.result, toolCalls: action.result.toolCalls }
        : turn))
  }
}
