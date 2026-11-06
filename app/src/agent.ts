// Parses the hosted agent's Responses protocol results without trusting their shape.
export const PROOF_PREFIX = 'AGENT_PROOF|'

export interface ToolCall {
  name: string
  status?: string
}

export interface AgentTurn {
  responseId: string
  text: string
  tools: ToolCall[]
  consentLink?: string
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function errorText(value: unknown): string | undefined {
  if (!record(value)) return undefined
  const error = record(value.error) ? value.error : value
  return typeof error.message === 'string' && error.message.trim() ? error.message.trim() : undefined
}

export function agentTurn(value: unknown): AgentTurn {
  if (!record(value)) throw new Error('The agent did not return a response object.')
  if (value.status !== 'completed') {
    const detail = errorText(value)
    const status = typeof value.status === 'string' ? value.status : 'missing a status'
    throw new Error(`The agent response is ${status}${detail ? `: ${detail}` : '.'}`)
  }
  if (typeof value.id !== 'string' || !value.id) throw new Error('The agent response has no ID.')
  if (!Array.isArray(value.output)) throw new Error('The agent response has no output items.')
  const texts: string[] = []
  const tools: ToolCall[] = []
  let consentLink: string | undefined
  for (const item of value.output) {
    if (!record(item) || typeof item.type !== 'string') continue
    if (item.type === 'message' && item.role === 'assistant' && Array.isArray(item.content)) {
      for (const part of item.content) {
        if (record(part) && part.type === 'output_text' && typeof part.text === 'string') texts.push(part.text)
      }
    } else if (item.type === 'oauth_consent_request' && typeof item.consent_link === 'string') {
      consentLink = item.consent_link
    } else if (item.type.endsWith('_call') && typeof item.name === 'string') {
      tools.push({ name: item.name, ...(typeof item.status === 'string' ? { status: item.status } : {}) })
    }
  }
  const text = texts.join('\n\n').trim()
  if (!text && !consentLink) throw new Error('The agent completed without an answer.')
  return { responseId: value.id, text, tools, ...(consentLink ? { consentLink } : {}) }
}

// Progress while an answer streams: the text so far and each tool call as it starts and returns.
export interface StreamProgress {
  text: string
  tools: ToolCall[]
}

type StreamStep = { done: false } | { done: true, turn: AgentTurn }

// Server-sent event data in arrival order. Comments such as keep-alives are skipped, and the
// incomplete tail stays in `rest` until the next chunk arrives.
export function sseEvents(buffer: string): { events: unknown[], rest: string } {
  const blocks = buffer.split(/\r?\n\r?\n/)
  const rest = blocks.pop() ?? ''
  const events: unknown[] = []
  for (const block of blocks) {
    const data = block.split(/\r?\n/)
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.slice(5).replace(/^ /, ''))
      .join('\n')
    if (!data || data === '[DONE]') continue
    try {
      events.push(JSON.parse(data))
    } catch {
      // A malformed event is ignored; only the final response decides the answer.
    }
  }
  return { events, rest }
}

// Apply one Responses streaming event. The final response is still validated by agentTurn.
export function applyEvent(progress: StreamProgress & { calls: Record<string, number> }, event: unknown): StreamStep {
  if (!record(event) || typeof event.type !== 'string') return { done: false }
  const item = record(event.item) ? event.item : undefined
  switch (event.type) {
    case 'response.output_text.delta':
      if (typeof event.delta === 'string') progress.text += event.delta
      break
    case 'response.output_item.added':
      if (item && typeof item.type === 'string' && item.type.endsWith('_call') && typeof item.name === 'string') {
        if (typeof item.call_id === 'string') progress.calls[item.call_id] = progress.tools.length
        progress.tools.push({ name: item.name, status: 'running' })
      }
      break
    case 'response.output_item.done':
      if (item?.type === 'function_call_output' && typeof item.call_id === 'string' && item.call_id in progress.calls) {
        progress.tools[progress.calls[item.call_id]].status = 'done'
      } else if (item && typeof item.type === 'string' && item.type.endsWith('_call') && item.type !== 'function_call') {
        const index = progress.tools.findIndex((tool) => tool.name === item.name && tool.status === 'running')
        if (index >= 0) progress.tools[index].status = typeof item.status === 'string' ? item.status : 'done'
      }
      break
    case 'response.completed':
    case 'response.failed':
    case 'response.incomplete':
      return { done: true, turn: agentTurn(event.response) }
    case 'error':
      throw new Error(errorText(event) ?? 'The agent stream failed.')
  }
  return { done: false }
}

// Read a streamed answer, reporting progress as events arrive, and return the validated final turn.
export async function readStream(body: ReadableStream<Uint8Array>,
  onProgress: (progress: StreamProgress) => void): Promise<AgentTurn> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  const progress = { text: '', tools: [] as ToolCall[], calls: {} as Record<string, number> }
  let buffer = ''
  try {
    for (;;) {
      const { value, done } = await reader.read()
      buffer += done ? `${decoder.decode()}\n\n` : decoder.decode(value, { stream: true })
      const { events, rest } = sseEvents(buffer)
      buffer = rest
      for (const event of events) {
        const step = applyEvent(progress, event)
        if (step.done) return step.turn
      }
      if (done) break
      onProgress({ text: progress.text, tools: progress.tools.map((tool) => ({ ...tool })) })
    }
  } finally {
    void reader.cancel().catch(() => {})
  }
  throw new Error('The answer stopped before the agent finished. Ask again.')
}

export function proofMatches(turn: AgentTurn, challenge: string): boolean {
  return turn.text === PROOF_PREFIX + challenge
}

export function failureMessage(status: number, body: unknown): string {
  const detail = errorText(body)
  const reasons: Record<number, string> = {
    401: 'The agent did not accept your sign-in. Sign out and sign in again.',
    403: 'You are not allowed to use this agent. Ask for the Foundry Agent Consumer role on the project.',
    404: 'The agent is not deployed at the configured endpoint.',
    408: 'The agent did not answer in time. Ask again; a cold ontology query can be slow.',
    429: 'The agent is busy. Wait a moment and ask again.',
  }
  const reason = reasons[status] ?? (status >= 500
    ? 'The agent failed to answer.'
    : `The agent request failed with HTTP ${status}.`)
  return detail ? `${reason} (${detail})` : reason
}

export function redact(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(redact)
  if (!record(value)) return value
  return Object.fromEntries(Object.entries(value).map(([key, entry]) => [
    key,
    /token|authorization|secret|password|credential|cookie|signature|claims/i.test(key) ? '[redacted]' : redact(entry),
  ]))
}
