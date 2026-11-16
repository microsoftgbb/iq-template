// Mock mode only: `npm run dev:mock` (vite --mode mock). live/client.ts imports this module only behind a literal
// import.meta.env.MODE === 'mock', so production builds never contain it. The demo script's scripted replies
// (demo/script.json) stand in for the agents, through the same turn as a live answer: the tool calls, one consent
// request and the reply.
import { SCRIPT, askOf, stepOfQuestion, type Scripted } from '../demo/script.ts'
import type { ToolCall, TurnOutcome } from '../live/protocol.ts'
import { runTurn, type Send, type TurnLog } from '../live/turn.ts'
import type { AgentClient, Session, Variant } from '../live/types.ts'

export const MOCK_SESSION: Session = {
  mode: 'mock',
  identity: { name: 'Mock user' },
  notice: 'Mock mode · scripted answers, no sign-in',
  signIn: async () => {},
  signOut: async () => {},
}

// Long enough for each running state to show.
export const MOCK_DELAY_MS = 900
export const MOCK_CONSENT_URL = 'https://example.com/mock-consent'
// Mock mode's one consent example: the first question that calls Work IQ mail asks for its consent once.
export const MOCK_CONSENT_SERVER = 'work_iq_mail'

export const MOCK_FALLBACK: Scripted = {
  tools: [],
  reply: "**Mock mode answers only the demo script's questions, as the agents each one asks.** Pick a sample question, or run `npm run dev` against a deployment to ask the live agents.",
}

// The script's reply to this question from this agent, or the fallback.
export function mockReply(question: string, variant: Variant): Scripted {
  return askOf(stepOfQuestion(SCRIPT, question), variant)?.scripted ?? MOCK_FALLBACK
}

function pause(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const done = () => {
      clearTimeout(timer)
      signal.removeEventListener('abort', done)
      resolve()
    }
    const timer = setTimeout(done, ms)
    signal.addEventListener('abort', done, { once: true })
  })
}

export interface MockOptions {
  delayMs?: number
  log?: (record: TurnLog) => void
}

export function createMockClient(options: MockOptions = {}): AgentClient {
  const delayMs = options.delayMs ?? MOCK_DELAY_MS
  // Whether this mock user has "consented" to Work IQ mail: the first question that calls it asks once.
  let consented = false
  const send: Send = async (question, _cid, signal, onTool): Promise<TurnOutcome> => {
    const reply = mockReply(question.text, question.variant)
    const responseId = `resp_mock_${crypto.randomUUID().replaceAll('-', '')}`
    if (!consented && reply.tools.some((tool) => tool.server === MOCK_CONSENT_SERVER)) {
      await pause(delayMs, signal)
      consented = true
      return { status: 'needs-consent', consentUrl: MOCK_CONSENT_URL, server: MOCK_CONSENT_SERVER, responseId, toolCalls: [] }
    }
    const calls: ToolCall[] = []
    for (const [index, tool] of reply.tools.entries()) {
      const id = `mock_call_${index + 1}`
      const started = performance.now()
      onTool({ id, server: tool.server, tool: tool.tool, state: 'running' })
      await pause(delayMs, signal)
      const done: ToolCall = {
        id, server: tool.server, tool: tool.tool, state: 'completed', ms: Math.round(performance.now() - started), arguments: tool.arguments,
      }
      calls.push(done)
      onTool(done)
    }
    await pause(delayMs / 2, signal)
    return { status: 'answered', text: reply.reply, responseId, toolCalls: calls }
  }
  return {
    mode: 'mock',
    ask: (question, signal, onTool) => runTurn({ question, send, signal, onTool, log: options.log }),
  }
}
