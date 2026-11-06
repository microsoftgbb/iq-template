import { useEffect, useState, type FormEvent } from 'react'
import { agentTurn, failureMessage, proofMatches, redact, type ToolCall } from './agent.ts'
import { createAuth, type Auth } from './auth.ts'
import { loadConfig, type AppConfig } from './config.ts'
import './App.css'

interface Result {
  tone: 'ready' | 'answer' | 'warning' | 'error'
  title: string
  lines?: string[]
  tools?: ToolCall[]
  link?: string
  detail?: string
  elapsed?: number
}

class Failure extends Error {
  detail?: string

  constructor(message: string, raw?: unknown) {
    super(message)
    if (raw !== undefined) this.detail = JSON.stringify(redact(raw), null, 2)
  }
}

async function ask(auth: Auth, input: string, previousResponseId?: string) {
  const response = await fetch('/api/responses', {
    method: 'POST',
    headers: { Authorization: `Bearer ${await auth.token()}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ input, ...(previousResponseId ? { previous_response_id: previousResponseId } : {}) }),
  })
  const body: unknown = await response.json().catch(() => undefined)
  if (!response.ok) throw new Failure(failureMessage(response.status, body), body)
  try {
    return agentTurn(body)
  } catch (error) {
    throw new Failure(error instanceof Error ? error.message : 'The agent returned an unreadable reply.', body)
  }
}

function App() {
  const [config, setConfig] = useState<AppConfig>()
  const [auth, setAuth] = useState<Auth>()
  const [question, setQuestion] = useState('')
  const [previousResponseId, setPreviousResponseId] = useState<string>()
  const [pending, setPending] = useState(false)
  const [result, setResult] = useState<Result>({ tone: 'ready', title: 'Loading the app configuration.' })

  useEffect(() => {
    // Development StrictMode mounts twice; both runs share one sign-in client and only the live one updates state.
    let active = true
    void (async () => {
      try {
        const loaded = await loadConfig()
        const session = await createAuth(loaded)
        if (!active) return
        setConfig(loaded)
        setAuth(session)
        setResult(session.user
          ? { tone: 'ready', title: 'Ask a question, or check the agent connection first.' }
          : { tone: 'ready', title: 'Sign in with your work account to ask the agent.' })
      } catch (error) {
        if (active) setResult({ tone: 'error', title: error instanceof Error ? error.message : 'The app could not start.' })
      }
    })()
    return () => {
      active = false
    }
  }, [])

  async function run(work: (session: Auth) => Promise<Result>) {
    if (!auth) return
    setPending(true)
    const started = performance.now()
    let outcome: Result
    try {
      outcome = await work(auth)
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

  function submit(event: FormEvent) {
    event.preventDefault()
    void run(async (session) => {
      const turn = await ask(session, question.trim(), previousResponseId)
      setPreviousResponseId(turn.responseId)
      if (turn.consentLink) {
        return {
          tone: 'warning',
          title: 'A source needs your consent before the agent can use it.',
          lines: ['Open the link, approve access, then ask again.'],
          link: turn.consentLink,
        }
      }
      return { tone: 'answer', title: 'Answer', lines: [turn.text], tools: turn.tools }
    })
  }

  function check() {
    void run(async (session) => {
      const challenge = `connection proof ${crypto.randomUUID()}`
      const turn = await ask(session, challenge)
      return proofMatches(turn, challenge)
        ? { tone: 'ready', title: 'Connected: the hosted agent returned this check\'s fresh challenge as you.' }
        : { tone: 'warning', title: 'The agent answered, but not with the fresh challenge.', lines: [turn.text] }
    })
  }

  function restart() {
    setPreviousResponseId(undefined)
    setResult({ tone: 'ready', title: 'New conversation.' })
  }

  const signedIn = Boolean(auth?.user)

  return (
    <main>
      <header>
        <span className="eyebrow">Scenario assistant</span>
        <h1>Ask about live operations and project documents.</h1>
        <p>
          Answers come from the project's Foundry hosted agent{config ? ` (${config.agentName})` : ''}. It reads the
          Fabric IQ ontology as you and the Foundry IQ knowledge base through its toolbox.
        </p>
        <div className="actions">
          {signedIn
            ? <button type="button" disabled={pending} onClick={() => void auth?.signOut()}>Sign out {auth?.user}</button>
            : <button className="primary" type="button" disabled={!auth} onClick={() => void auth?.signIn()}>Sign in</button>}
        </div>
      </header>
      <form className="panel" onSubmit={submit}>
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
          <button className="primary" type="submit" disabled={pending || !signedIn || !question.trim()}>
            {pending ? 'Waiting for the agent…' : 'Ask'}
          </button>
          <button type="button" disabled={pending || !previousResponseId} onClick={restart}>New conversation</button>
          <button type="button" disabled={pending || !signedIn} onClick={check}>Check connection</button>
        </div>
        <p className="hint">
          {previousResponseId ? 'Follow-up questions continue this conversation. ' : ''}
          A live ontology answer can take a minute.
        </p>
      </form>
      <section className={`panel result ${result.tone}`} aria-live="polite">
        <h2>{result.title}</h2>
        {result.lines?.map((line, index) => <p key={index} className="answer">{line}</p>)}
        {result.link && <a href={result.link} target="_blank" rel="noreferrer">{result.link}</a>}
        {result.tools?.length
          ? <p className="meta">Tools used: {result.tools.map((tool) => tool.name).join(', ')}</p>
          : null}
        {result.detail && <pre>{result.detail}</pre>}
        {result.elapsed !== undefined && <p className="meta">{result.elapsed.toFixed(1)} s</p>}
      </section>
    </main>
  )
}

export default App
