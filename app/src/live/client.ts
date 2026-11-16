// The app's only route to the agents: its own server's /api/turns, called with the signed-in user's Foundry token.
// The server asks the variant's Foundry agent as that user. Only this module loads the browser sign-in
// (live/auth.ts), so the modules it uses stay testable under Node. Mock mode serves canned answers instead.
import { CONFIG_PATH, TURN_PATH, isPublicConfig, type PublicConfig, type TurnRequest } from './protocol.ts'
import { readTurnStream } from './stream.ts'
import { runTurn, type Send, type TurnLog } from './turn.ts'
import type { AgentClient, Session } from './types.ts'

// One value-free record per turn: its correlation ID is the request's cid, which the server's log and trace carry.
const log = (record: TurnLog) => console.info(JSON.stringify(record))

async function publicConfig(): Promise<PublicConfig> {
  const response = await fetch(CONFIG_PATH, { headers: { Accept: 'application/json' }, cache: 'no-store' })
  if (!response.ok) throw new Error(`The app server's sign-in settings are unavailable (HTTP ${response.status}).`)
  const value: unknown = await response.json()
  if (!isPublicConfig(value)) throw new Error('The app server returned invalid sign-in settings.')
  return value
}

async function startSession(): Promise<Session> {
  if (import.meta.env.MODE === 'mock') {
    const { MOCK_SESSION } = await import('../mock/client.ts')
    return MOCK_SESSION
  }
  const auth = await import('./auth.ts')
  const identity = await auth.startSignIn(await publicConfig())
  return { mode: 'live', ...(identity ? { identity } : {}), signIn: auth.signIn, signOut: auth.signOut }
}

let pending: Promise<Session> | undefined

// Once per page: finishes a sign-in that redirected back here, but sends nobody to Entra until they choose to sign
// in. A failed attempt is forgotten, so the next call tries again.
export function loadSession(): Promise<Session> {
  if (!pending) {
    const attempt = startSession()
    pending = attempt
    attempt.catch(() => {
      if (pending === attempt) pending = undefined
    })
  }
  return pending
}

// ask never rejects. Each call is a new Foundry run of the question's agent variant, continuing previousResponseId
// when set; after an abort it resolves at once as cancelled (live/turn.ts) and the server stops that run.
export function createAgentClient(): AgentClient {
  if (import.meta.env.MODE === 'mock') {
    const mock = import('../mock/client.ts').then(({ createMockClient }) => createMockClient({ log }))
    return { mode: 'mock', ask: async (question, signal, onTool) => (await mock).ask(question, signal, onTool) }
  }
  const send: Send = async (question, cid, signal, onTool) => {
    const { accessToken } = await import('./auth.ts')
    const request: TurnRequest = {
      cid,
      variant: question.variant,
      question: question.text,
      ...(question.previousResponseId ? { previousResponseId: question.previousResponseId } : {}),
    }
    const response = await fetch(TURN_PATH, {
      method: 'POST',
      headers: { Authorization: `Bearer ${await accessToken()}`, 'Content-Type': 'application/json', Accept: 'application/x-ndjson' },
      body: JSON.stringify(request),
      cache: 'no-store',
      signal,
    })
    return readTurnStream(response, onTool)
  }
  return {
    mode: 'live',
    ask: (question, signal, onTool) => runTurn({ question, send, signal, onTool, log }),
  }
}
