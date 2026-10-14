import { useState, type FormEvent } from 'react'
import { getContext } from '@microsoft/managed-apps/app'
import { getUser } from '@microsoft/managed-apps/auth'
import { MicrosoftCopilotStudioService } from '../../generated/services/MicrosoftCopilotStudioService'
import { STUDIO_AGENT_SCHEMA } from './config'
import { connectionManagerUrl, studioDiagnostic, studioFailure, studioProof, studioReply } from './studio'
import './App.css'

// Required by ExecuteCopilotAsyncV2, but unused because the call waits for the reply.
const NOTIFICATION_URL = 'https://notificationurlplaceholder'
const LOCALE = 'en-US'

interface Result {
  tone: 'ready' | 'answer' | 'warning' | 'error'
  title: string
  lines?: string[]
  link?: string
  detail?: string
  elapsed?: number
}

class Failure extends Error {
  detail?: string

  constructor(message: string, raw?: unknown) {
    super(message)
    if (raw !== undefined) this.detail = JSON.stringify(studioDiagnostic(raw), null, 2)
  }
}

async function target() {
  if (!STUDIO_AGENT_SCHEMA) {
    throw new Failure('Set STUDIO_AGENT_SCHEMA in app/src/config.ts to the agent schema name.')
  }
  const [user, context] = await Promise.all([getUser(), getContext()])
  if (!user.objectId || !user.tenantId || !context.app.environmentId) {
    throw new Failure('Open this app through the signed-in Managed Runtime player.')
  }
  return { tenantId: user.tenantId, environmentId: context.app.environmentId }
}

async function send(message: string, environmentId: string, conversationId?: string) {
  const response = await MicrosoftCopilotStudioService.ExecuteCopilotAsyncV2(
    STUDIO_AGENT_SCHEMA,
    { message, locale: LOCALE, notificationUrl: NOTIFICATION_URL },
    conversationId,
    environmentId,
  )
  if (!response.success) throw new Failure(studioFailure(response.error?.message))
  const raw: unknown = response.data
  return raw
}

function connectionNeeded(tenantId: string, environmentId: string, conversationId: string): Result {
  return {
    tone: 'warning',
    title: 'The agent is waiting for input this app cannot show, usually your connection consent.',
    lines: ['Open the connection manager, connect each source, then try again.'],
    link: connectionManagerUrl(tenantId, environmentId, STUDIO_AGENT_SCHEMA, conversationId),
  }
}

function parse<T>(raw: unknown, parser: (value: unknown) => T): T {
  try {
    return parser(raw)
  } catch (error) {
    throw new Failure(error instanceof Error ? error.message : 'Studio returned an unreadable reply.', raw)
  }
}

function App() {
  const [question, setQuestion] = useState('')
  const [conversationId, setConversationId] = useState<string>()
  const [pending, setPending] = useState(false)
  const [result, setResult] = useState<Result>(STUDIO_AGENT_SCHEMA
    ? { tone: 'ready', title: 'Ask a question, or check the agent connection first.' }
    : { tone: 'warning', title: 'Set STUDIO_AGENT_SCHEMA in app/src/config.ts.' })

  async function run(work: () => Promise<Result>) {
    setPending(true)
    const started = performance.now()
    let outcome: Result
    try {
      outcome = await work()
    } catch (error) {
      outcome = {
        tone: 'error',
        title: error instanceof Error ? error.message : 'The request failed.',
        detail: error instanceof Failure ? error.detail : undefined,
      }
    }
    setResult({ ...outcome, elapsed: (performance.now() - started) / 1000 })
    setPending(false)
  }

  function ask(event: FormEvent) {
    event.preventDefault()
    void run(async () => {
      const { tenantId, environmentId } = await target()
      const reply = parse(await send(question.trim(), environmentId, conversationId), studioReply)
      if (reply.status === 'needs-input') {
        setConversationId(undefined)
        return connectionNeeded(tenantId, environmentId, reply.conversationId)
      }
      setConversationId(reply.conversationId)
      return { tone: 'answer', title: 'Answer', lines: reply.responses }
    })
  }

  function check() {
    void run(async () => {
      const { tenantId, environmentId } = await target()
      const challenge = `connection proof ${crypto.randomUUID()}`
      const raw = await send(challenge, environmentId)
      const proof = parse(raw, (value) => studioProof(value, challenge))
      if (proof.status === 'needs-input') return connectionNeeded(tenantId, environmentId, proof.conversationId)
      return proof.status === 'responded'
        ? { tone: 'ready', title: 'Connected: the published agent returned this check\'s fresh challenge.' }
        : { tone: 'warning', title: 'Studio accepted the message but the agent returned no reply.', detail: new Failure('', raw).detail }
    })
  }

  function restart() {
    setConversationId(undefined)
    setResult({ tone: 'ready', title: 'New conversation.' })
  }

  return (
    <main>
      <header>
        <span className="eyebrow">Scenario assistant</span>
        <h1>Ask about live operations and project documents.</h1>
        <p>
          Answers come from the project's Copilot Studio agent. It reads the Fabric IQ ontology and the
          Foundry IQ knowledge base with your own connections.
        </p>
      </header>
      <form className="panel" onSubmit={ask}>
        <label htmlFor="question">Question</label>
        <textarea
          id="question"
          rows={3}
          maxLength={2000}
          value={question}
          placeholder="Which assets are in maintenance, and what does the procedure say to check first?"
          onChange={(event) => setQuestion(event.target.value)}
        />
        <div className="actions">
          <button className="primary" type="submit" disabled={pending || !question.trim()}>
            {pending ? 'Waiting for the agent…' : 'Ask'}
          </button>
          <button type="button" disabled={pending || !conversationId} onClick={restart}>New conversation</button>
          <button type="button" disabled={pending} onClick={check}>Check connection</button>
        </div>
        <p className="hint">
          {conversationId ? 'Follow-up questions continue this conversation. ' : ''}
          An answer can take up to two minutes.
        </p>
      </form>
      <section className={`panel result ${result.tone}`} aria-live="polite">
        <h2>{result.title}</h2>
        {result.lines?.map((line, index) => <p key={index} className="answer">{line}</p>)}
        {result.link && <a href={result.link} target="_blank" rel="noreferrer">{result.link}</a>}
        {result.detail && <pre>{result.detail}</pre>}
        {result.elapsed !== undefined && <p className="meta">{result.elapsed.toFixed(1)} s</p>}
      </section>
    </main>
  )
}

export default App
