// One question to one Foundry prompt agent, as the signed-in user: the Responses API on the project endpoint, with
// the agent referenced by name and the user's own Foundry token, streamed so each MCP tool call is reported as it
// starts and finishes. A follow-up continues the previous response. Foundry runs the agent's tools; the server only
// relays what the run reports. The request carries the active span's W3C trace context, which Foundry's server-side
// trace of the run continues.
import { context, propagation } from '@opentelemetry/api'
import { ARGUMENTS_LIMIT, type ToolCall, type TurnOutcome } from '../src/live/protocol.ts'
import { readSse } from './sse.ts'

export interface AgentRun {
  endpoint: string
  agent: string
  token: string
  question: string
  // Foundry's ID of the response this question follows up.
  previousResponseId?: string
  // Value-free labels Foundry stores with the run: the cid and the variant.
  metadata: Record<string, string>
  // Aborted when the browser goes away.
  signal: AbortSignal
  timeoutMs: number
  onTool: (call: ToolCall) => void
  fetch?: typeof fetch
  clock?: () => number
}

type Json = Record<string, unknown>

function record(value: unknown): value is Json {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function shorten(value: unknown, limit: number): string | undefined {
  if (typeof value !== 'string' || !value) return undefined
  const text = value.replace(/\s+/g, ' ').trim()
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text
}

function httpsUrl(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined
  try {
    const url = new URL(value)
    return url.protocol === 'https:' ? url.href : undefined
  } catch {
    return undefined
  }
}

function errorText(value: unknown): string | undefined {
  if (typeof value === 'string') return value
  if (record(value)) return [value.code, value.message].filter((part) => typeof part === 'string' && part).join(': ') || undefined
  return undefined
}

function failed(message: string, toolCalls: ToolCall[] = [], extra: { code?: string; responseId?: string } = {}): TurnOutcome {
  return { status: 'failed', message, toolCalls, ...extra }
}

// What a refused request means for the user, without echoing anything but the service's short message.
async function httpFailure(response: Response, agent: string): Promise<TurnOutcome> {
  let detail: string | undefined
  let code: string | undefined
  try {
    const body: unknown = await response.json()
    const error = record(body) && record(body.error) ? body.error : body
    if (record(error)) {
      detail = shorten(error.message, 300)
      code = typeof error.code === 'string' ? error.code : undefined
    }
  } catch {
    detail = undefined
  }
  const status = response.status
  const extra = { code: code ?? `HTTP ${status}` }
  if (status === 401) return failed('Foundry did not accept your sign-in. Sign in again.', [], extra)
  if (status === 403) {
    return failed('Your account may not use these agents: it needs the Foundry User role on the Foundry project.', [], extra)
  }
  if (status === 404) return failed(`Foundry has no agent ${agent}. Publish the agents (deploy.py up, agent phase), then ask again.`, [], extra)
  if (status === 429) return failed('Foundry is throttling the agent model (HTTP 429). Wait a moment, then ask again.', [], extra)
  if (status >= 500) return failed(`Foundry answered HTTP ${status}. Ask again.`, [], extra)
  return failed(`Foundry refused the question (HTTP ${status})${detail ? `: ${detail}` : '.'}`, [], extra)
}

// The run's state, from its stream events.
class Run {
  readonly calls = new Map<string, ToolCall>()
  readonly started = new Map<string, number>()
  readonly listed = new Map<string, number>()
  responseId?: string
  consent?: { url: string; server?: string }
  final?: Json
  streamError?: { message: string; code?: string }
  deltas: string[] = []
  done = false
  private readonly clock: () => number
  private readonly onTool: (call: ToolCall) => void

  constructor(clock: () => number, onTool: (call: ToolCall) => void) {
    this.clock = clock
    this.onTool = onTool
  }

  apply(type: string, data: Json): void {
    const item = record(data.item) ? data.item : undefined
    switch (type) {
      case 'response.created':
      case 'response.in_progress':
        if (record(data.response) && typeof data.response.id === 'string') this.responseId = data.response.id
        return
      case 'response.output_item.added':
        if (item?.type === 'mcp_call') this.start(item)
        return
      case 'response.output_item.done':
        if (item?.type === 'mcp_call') this.finish(item)
        else if (item?.type === 'oauth_consent_request') this.ask(item)
        else if (item?.type === 'mcp_list_tools' && typeof item.server_label === 'string') {
          this.listed.set(item.server_label, Array.isArray(item.tools) ? item.tools.length : 0)
        }
        return
      case 'response.output_text.delta':
        if (typeof data.delta === 'string') this.deltas.push(data.delta)
        return
      case 'response.completed':
      case 'response.incomplete':
      case 'response.failed':
        if (record(data.response)) {
          this.final = data.response
          if (typeof data.response.id === 'string') this.responseId = data.response.id
        }
        this.done = true
        return
      case 'error':
        this.streamError = {
          message: shorten(errorText(data.error) ?? data.message, 300) ?? 'Foundry reported an error.',
          ...(typeof data.code === 'string' ? { code: data.code } : {}),
        }
        this.done = true
        return
      default:
    }
  }

  private start(item: Json): void {
    const id = typeof item.id === 'string' ? item.id : `call-${this.calls.size + 1}`
    if (this.calls.has(id)) return
    this.started.set(id, this.clock())
    this.report({ id, server: String(item.server_label ?? ''), tool: String(item.name ?? ''), state: 'running' })
  }

  private finish(item: Json): void {
    const id = typeof item.id === 'string' ? item.id : `call-${this.calls.size + 1}`
    const began = this.started.get(id)
    const error = shorten(errorText(item.error), 300)
    const args = shorten(item.arguments, ARGUMENTS_LIMIT)
    this.report({
      id, server: String(item.server_label ?? ''), tool: String(item.name ?? ''), state: error ? 'failed' : 'completed',
      ...(began === undefined ? {} : { ms: Math.max(0, Math.round(this.clock() - began)) }),
      ...(args ? { arguments: args } : {}),
      ...(error ? { error } : {}),
    })
  }

  private ask(item: Json): void {
    const url = httpsUrl(item.consent_link)
    if (url && !this.consent) this.consent = { url, ...(typeof item.server_label === 'string' ? { server: item.server_label } : {}) }
  }

  private report(call: ToolCall): void {
    this.calls.set(call.id, call)
    this.onTool(call)
  }

  toolCalls(): ToolCall[] {
    return [...this.calls.values()].map((call) => (call.state === 'running' ? { ...call, state: 'failed' as const, error: 'The call did not finish.' } : call))
  }

  // A consent link that a tool's error carries instead of a consent item, as Fabric IQ's CONSENT_REQUIRED can.
  private consentFromErrors(): { url: string; server?: string } | undefined {
    for (const call of this.calls.values()) {
      if (call.state !== 'failed' || !call.error || !/consent/i.test(call.error)) continue
      const url = httpsUrl(/https:\/\/[^\s"'<>]+/.exec(call.error)?.[0])
      if (url) return { url, server: call.server }
    }
    return undefined
  }

  outcome(): TurnOutcome {
    const toolCalls = this.toolCalls()
    const extra = this.responseId ? { responseId: this.responseId } : {}
    const consent = this.consent ?? this.consentFromErrors()
    if (consent) return { status: 'needs-consent', consentUrl: consent.url, ...(consent.server ? { server: consent.server } : {}), ...extra, toolCalls }
    if (this.streamError) return failed(`Foundry stopped the run: ${this.streamError.message}`, toolCalls, { ...extra, ...(this.streamError.code ? { code: this.streamError.code } : {}) })
    const final = this.final
    if (!final) return failed('Foundry closed the stream before the answer finished. Ask again.', toolCalls, extra)
    if (final.status === 'failed') {
      const error = record(final.error) ? final.error : {}
      const code = typeof error.code === 'string' ? error.code : undefined
      return failed(`The agent run failed${typeof error.message === 'string' ? `: ${shorten(error.message, 300)}` : '.'}`, toolCalls, { ...extra, ...(code ? { code } : {}) })
    }
    if (final.status === 'incomplete') {
      const details = record(final.incomplete_details) ? final.incomplete_details : {}
      const reason = typeof details.reason === 'string' ? details.reason : 'unknown'
      return failed(`The agent's answer was cut off (${reason}). Ask again.`, toolCalls, { ...extra, code: reason })
    }
    const text = answerText(final) || this.deltas.join('')
    if (!text.trim()) return failed('The agent finished without an answer. Ask again.', toolCalls, extra)
    return { status: 'answered', text, ...extra, toolCalls }
  }

  // Servers whose tool list came back empty: the agent's allowed tools don't match what the server offers.
  emptyServers(): string[] {
    return [...this.listed.entries()].filter(([, count]) => count === 0).map(([server]) => server)
  }
}

// The assistant's message text, in output order.
export function answerText(response: Json): string {
  const parts: string[] = []
  for (const item of Array.isArray(response.output) ? response.output : []) {
    if (!record(item) || item.type !== 'message' || (item.role !== undefined && item.role !== 'assistant')) continue
    for (const content of Array.isArray(item.content) ? item.content : []) {
      if (record(content) && content.type === 'output_text' && typeof content.text === 'string') parts.push(content.text)
    }
  }
  return parts.join('\n\n')
}

export async function askAgent(run: AgentRun): Promise<TurnOutcome> {
  const fetcher = run.fetch ?? fetch
  const clock = run.clock ?? Date.now
  const timeout = AbortSignal.timeout(run.timeoutMs)
  const signal = AbortSignal.any([run.signal, timeout])
  const state = new Run(clock, run.onTool)
  const stopped = (): TurnOutcome => (timeout.aborted
    ? failed(`The agent didn't finish within ${Math.round(run.timeoutMs / 1000)} seconds. Ask again; a cold Fabric IQ query can be slow.`, state.toolCalls(), { code: 'timeout', ...(state.responseId ? { responseId: state.responseId } : {}) })
    : failed('Cancelled: the answer was no longer needed.', state.toolCalls(), { code: 'cancelled' }))
  const headers: Record<string, string> = {
    Authorization: `Bearer ${run.token}`,
    'Content-Type': 'application/json',
    Accept: 'text/event-stream',
    ...(run.metadata.cid ? { 'x-ms-client-request-id': run.metadata.cid } : {}),
  }
  // traceparent, and tracestate when set: a no-op until telemetry registers a propagator.
  propagation.inject(context.active(), headers)
  let response: Response
  try {
    response = await fetcher(`${run.endpoint}/openai/v1/responses`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        agent_reference: { type: 'agent_reference', name: run.agent },
        input: run.question,
        ...(run.previousResponseId ? { previous_response_id: run.previousResponseId } : {}),
        metadata: run.metadata,
        stream: true,
      }),
      signal,
    })
  } catch {
    return signal.aborted ? stopped() : failed('Foundry could not be reached. Check the network, then ask again.')
  }
  if (!response.ok) return httpFailure(response, run.agent)
  if (!response.body) return failed('Foundry sent no answer stream. Ask again.')
  try {
    for await (const event of readSse(response.body)) {
      if (event.data === '[DONE]') break
      let data: unknown
      try {
        data = JSON.parse(event.data)
      } catch {
        continue
      }
      if (!record(data)) continue
      state.apply(typeof data.type === 'string' ? data.type : event.event, data)
      if (state.done) break
    }
  } catch {
    if (signal.aborted) return stopped()
    return failed('The answer stream from Foundry broke off. Ask again.', state.toolCalls(), state.responseId ? { responseId: state.responseId } : {})
  }
  const outcome = state.outcome()
  const empty = state.emptyServers()
  if (outcome.status === 'answered' && empty.length) {
    // Keep the answer, but say plainly which tool offered none of its allowed operations.
    return { ...outcome, toolCalls: [...outcome.toolCalls, ...empty.map((server) => ({ id: `list-${server}`, server, tool: 'tools/list', state: 'failed' as const, error: 'The server offered none of the allowed tools.' }))] }
  }
  return outcome
}
