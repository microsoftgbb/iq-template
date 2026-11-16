// One question as one agent turn, without the browser, so it runs under Node tests: client.ts supplies the call to
// the app server, which asks the variant's Foundry agent as the signed-in user. It never rejects: every outcome, a
// cancelled turn included, is a TurnResult.
import { iqOf, type ToolCall, type TurnOutcome } from './protocol.ts'
import { parseReply } from './reply.ts'
import type { IqKey, Question, TurnResult, Variant } from './types.ts'

export const CANCELLED = 'cancelled'

// One question to the variant's agent, as its own Foundry run. cid labels the request and its trace.
export type Send = (question: Question, cid: string, signal: AbortSignal, onTool: (call: ToolCall) => void) => Promise<TurnOutcome>

// Value-free by construction: no question or answer text, and no user or Foundry identifiers.
export interface TurnLog {
  event: 'app.turn'
  correlationId: string
  variant: Variant
  followUp: boolean
  outcome: TurnResult['status'] | 'cancelled'
  elapsedMs: number
  usedIqs?: IqKey[]
  counts?: { markers: number; sources: number; steps: number }
  tools?: { iq: IqKey | 'other'; state: ToolCall['state']; ms?: number }[]
}

export interface TurnOptions {
  question: Question
  send: Send
  signal?: AbortSignal
  onTool?: (call: ToolCall) => void
  clock?: () => number
  newId?: () => string
  log?: (record: TurnLog) => void
}

function messageOf(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback
}

// The calls still running when a turn stops never finished.
function settled(calls: Iterable<ToolCall>): ToolCall[] {
  return [...calls].map((call) => (call.state === 'running' ? { ...call, state: 'failed' as const, error: 'The call did not finish.' } : call))
}

function fromOutcome(outcome: TurnOutcome, elapsedMs: number): TurnResult {
  const common = { elapsedMs, ...(outcome.responseId ? { responseId: outcome.responseId } : {}), toolCalls: outcome.toolCalls }
  switch (outcome.status) {
    case 'answered': {
      const raw = outcome.text.trim()
      if (!raw) return { status: 'failed', message: 'The agent finished without an answer. Ask again.', ...common }
      return { status: 'answered', reply: parseReply(raw), raw, ...common }
    }
    case 'needs-consent':
      if (!/^https:\/\//i.test(outcome.consentUrl)) return { status: 'failed', message: 'A tool asked for consent through an unsafe link.', ...common }
      return { status: 'needs-consent', consentUrl: outcome.consentUrl, ...(outcome.server ? { server: outcome.server } : {}), ...common }
    case 'failed':
      return { status: 'failed', message: outcome.message, ...(outcome.code ? { code: outcome.code } : {}), ...common }
  }
}

// Each turn is its own Foundry run with a fresh correlation ID. After an abort it resolves at once as cancelled;
// the server stops the run when the browser goes away.
export async function runTurn(options: TurnOptions): Promise<TurnResult> {
  const clock = options.clock ?? (() => performance.now())
  const started = clock()
  const elapsed = () => Math.max(0, Math.round(clock() - started))
  const correlationId = (options.newId ?? (() => crypto.randomUUID()))()
  const controller = new AbortController()
  const calls = new Map<string, ToolCall>()
  const cancel = (): TurnResult => ({
    status: 'failed', message: 'Stopped before the agent answered.', code: CANCELLED, elapsedMs: elapsed(), toolCalls: settled(calls.values()),
  })
  const onTool = (call: ToolCall) => {
    if (controller.signal.aborted) return
    calls.set(call.id, call)
    options.onTool?.(call)
  }
  const attempt = async (): Promise<TurnResult> => {
    try {
      const outcome = await options.send(options.question, correlationId, controller.signal, onTool)
      // A stream that broke off reports no calls: keep the ones the browser saw.
      return fromOutcome(outcome.toolCalls.length || !calls.size ? outcome : { ...outcome, toolCalls: settled(calls.values()) }, elapsed())
    } catch (error) {
      return { status: 'failed', message: messageOf(error, 'The question could not be sent.'), elapsedMs: elapsed(), toolCalls: settled(calls.values()) }
    }
  }
  let result: TurnResult
  const { signal } = options
  if (signal?.aborted) {
    result = cancel()
  } else if (!signal) {
    result = await attempt()
  } else {
    let stop = () => {}
    const aborted = new Promise<TurnResult>((resolve) => {
      const listener = () => {
        const stopped = cancel()
        controller.abort()
        resolve(stopped)
      }
      signal.addEventListener('abort', listener, { once: true })
      stop = () => signal.removeEventListener('abort', listener)
    })
    try {
      result = await Promise.race([attempt(), aborted])
    } finally {
      stop()
    }
  }
  options.log?.({
    event: 'app.turn',
    correlationId,
    variant: options.question.variant,
    followUp: Boolean(options.question.previousResponseId),
    outcome: result.status === 'failed' && result.code === CANCELLED ? 'cancelled' : result.status,
    elapsedMs: result.elapsedMs,
    ...(result.status === 'answered'
      ? {
          usedIqs: [...result.reply.usedIqs],
          counts: { markers: result.reply.markers.length, sources: result.reply.sources.length, steps: result.reply.steps.length },
        }
      : {}),
    ...(result.toolCalls.length
      ? { tools: result.toolCalls.map((call) => ({ iq: iqOf(call.server) ?? 'other', state: call.state, ...(call.ms === undefined ? {} : { ms: call.ms }) })) }
      : {}),
  })
  return result
}
