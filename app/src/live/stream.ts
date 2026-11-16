// Reads the app server's answer to one question: newline-delimited JSON events, the last of them the outcome.
// Plain fetch types only, so it runs under Node tests.
import { parseTurnEvent, type ToolCall, type TurnOutcome } from './protocol.ts'

export const UNAUTHORIZED = 'unauthorized'

function failure(message: string, code?: string): TurnOutcome {
  return { status: 'failed', message, ...(code ? { code } : {}), toolCalls: [] }
}

// The server's JSON error body ({ message }), or a message for the status alone. Never the raw body.
async function errorOutcome(response: Response): Promise<TurnOutcome> {
  let message = ''
  try {
    const body: unknown = await response.json()
    if (typeof body === 'object' && body !== null && typeof (body as { message?: unknown }).message === 'string') {
      message = (body as { message: string }).message.slice(0, 400)
    }
  } catch {
    message = ''
  }
  if (response.status === 401) {
    return failure(message || 'The app server did not accept your sign-in. Sign in again.', UNAUTHORIZED)
  }
  return failure(message || `The app server answered HTTP ${response.status}.`, `HTTP ${response.status}`)
}

export async function readTurnStream(response: Response, onTool: (call: ToolCall) => void): Promise<TurnOutcome> {
  if (!response.ok) return errorOutcome(response)
  if (!response.body) return failure('The app server sent no answer.')
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffered = ''
  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (value) buffered += value
      const lines = buffered.split('\n')
      buffered = done ? '' : lines.pop() ?? ''
      for (const line of lines) {
        if (!line.trim()) continue
        const event = parseTurnEvent(line)
        if (event.type === 'tool') onTool(event.call)
        if (event.type === 'result') return event.outcome
      }
      if (done) return failure('The connection to the app server closed before the answer arrived. Ask again.')
    }
  } finally {
    // On every exit, including an event the protocol rejects: cancelling closes the request, so the server stops the
    // agent run instead of finishing it for nobody.
    await reader.cancel().catch(() => undefined)
  }
}
