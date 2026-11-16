import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'
import { Composer } from './components/Composer.tsx'
import { ExchangeView, type TurnActions } from './components/Exchange.tsx'
import { Header } from './components/Header.tsx'
import { Presenter } from './components/Presenter.tsx'
import {
  PRESENTER_STORAGE_KEY, presenterCommand, presenterFromUrl, readStored, reducePresenter, saveStored,
} from './demo/presenter.ts'
import { SCRIPT, type Step } from './demo/script.ts'
import { followUpFrom, newTurn, reduce, type Exchange, type Thread, type Turn } from './exchanges.ts'
import type { Choice } from './labels.ts'
import { createAgentClient, loadSession } from './live/client.ts'
import type { Question, Session, Variant } from './live/types.ts'

type SessionState =
  | { status: 'loading' }
  | { status: 'ready'; session: Session }
  | { status: 'failed'; message: string }

function messageOf(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback
}

// Presenter mode lives in sessionStorage, so it survives the sign-in redirect; where storage is blocked, it lasts
// until the page reloads.
function storedPresenter(): string | null {
  try {
    return window.sessionStorage.getItem(PRESENTER_STORAGE_KEY)
  } catch {
    return null
  }
}

function storePresenter(value: string): void {
  try {
    window.sessionStorage.setItem(PRESENTER_STORAGE_KEY, value)
  } catch {
    // Not remembered: presenter mode still works on this page.
  }
}

// Once per page: ?presenter=1 or ?presenter=0 wins over what this tab remembered.
function initialPresenter() {
  const restored = readStored(storedPresenter(), SCRIPT.steps.length, Date.now())
  return { on: presenterFromUrl(window.location.search) ?? restored.on, state: restored.state }
}

export default function App() {
  const [sessionState, setSessionState] = useState<SessionState>({ status: 'loading' })
  const [problem, setProblem] = useState<string>()
  const [exchanges, dispatch] = useReducer(reduce, [])
  const [initial] = useState(initialPresenter)
  const [presenting, setPresenting] = useState(initial.on)
  const [presenter, present] = useReducer(reducePresenter, initial.state)
  const client = useMemo(() => createAgentClient(), [])
  // The running turns' controllers, by turn: Stop aborts one, and the server then stops its Foundry run.
  const controllers = useRef(new Map<string, AbortController>())

  useEffect(() => {
    let active = true
    loadSession().then(
      (session) => {
        if (active) setSessionState({ status: 'ready', session })
      },
      (error: unknown) => {
        if (active) setSessionState({ status: 'failed', message: messageOf(error, 'Sign-in could not start.') })
      },
    )
    return () => {
      active = false
    }
  }, [])

  useEffect(() => {
    const running = controllers.current
    return () => {
      for (const controller of running.values()) controller.abort()
    }
  }, [])

  const session = sessionState.status === 'ready' ? sessionState.session : undefined
  const blocked = sessionState.status === 'loading' ? 'Connecting to the app server…'
    : sessionState.status === 'failed' ? 'The app can’t ask the agents until sign-in starts.'
      : session?.identity ? undefined : 'Sign in with your Microsoft Entra account to ask the agents.'

  const run = useCallback((turnId: string, question: Question) => {
    const controller = new AbortController()
    controllers.current.set(turnId, controller)
    void client.ask(question, controller.signal, (call) => dispatch({ type: 'tool', turnId, call })).then((result) => {
      if (controllers.current.get(turnId) === controller) controllers.current.delete(turnId)
      dispatch({ type: 'finish', turnId, result })
    })
  }, [client])

  // One question to each agent, side by side in this order; stepId when presenter mode asked it for a script step.
  const start = useCallback((text: string, variants: readonly Variant[], stepId?: string) => {
    const startedAt = performance.now()
    const exchange: Exchange = {
      id: crypto.randomUUID(),
      question: text,
      ...(stepId ? { stepId } : {}),
      threads: variants.map((variant) => ({ id: crypto.randomUUID(), variant, turns: [newTurn(crypto.randomUUID(), text, startedAt)] })),
    }
    dispatch({ type: 'ask', exchange })
    for (const thread of exchange.threads) run(thread.turns[0].id, { variant: thread.variant, text })
  }, [run])

  const ask = useCallback((text: string, choice: Choice) => {
    start(text, choice === 'both' ? ['with_iq', 'without_iq'] : [choice])
  }, [start])

  const askStep = useCallback((step: Step) => {
    if (step.question) start(step.question, step.asks.map((entry) => entry.variant), step.id)
  }, [start])

  useEffect(() => storePresenter(saveStored(presenting, presenter)), [presenting, presenter])

  const current = SCRIPT.steps[presenter.index]
  const stepRunning = exchanges.some((exchange) => exchange.stepId === current.id
    && exchange.threads.some((thread) => thread.turns.some((turn) => turn.running)))
  const canAsk = Boolean(current.question) && !blocked && !stepRunning

  // A presentation clicker's keys, page-wide while presenter mode is on; presenterCommand ignores text fields.
  useEffect(() => {
    if (!presenting) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.repeat) return
      const command = presenterCommand({
        key: event.key, altKey: event.altKey, ctrlKey: event.ctrlKey, metaKey: event.metaKey, isComposing: event.isComposing,
        target: event.target instanceof HTMLElement ? event.target : null,
      })
      if (!command || (command === 'ask' && !canAsk)) return
      event.preventDefault()
      if (command === 'ask') askStep(current)
      else if (command === 'next' || command === 'previous') present({ type: command, at: Date.now() })
      else present({ type: command })
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [presenting, canAsk, current, askStep])

  const authenticate = useCallback((action: 'signIn' | 'signOut') => {
    setProblem(undefined)
    session?.[action]().catch((error: unknown) => setProblem(messageOf(error, 'Microsoft Entra sign-in did not start. Try again.')))
  }, [session])

  const actions = useMemo<TurnActions>(() => ({
    onFollowUp: (thread: Thread, text: string) => {
      const previousResponseId = followUpFrom(thread)
      const turn = newTurn(crypto.randomUUID(), text, performance.now(), previousResponseId)
      dispatch({ type: 'follow-up', threadId: thread.id, turn })
      run(turn.id, { variant: thread.variant, text, ...(previousResponseId ? { previousResponseId } : {}) })
    },
    onRetry: (thread: Thread, turn: Turn) => {
      dispatch({ type: 'restart', turnId: turn.id, startedAt: performance.now() })
      run(turn.id, { variant: thread.variant, text: turn.question, ...(turn.previousResponseId ? { previousResponseId: turn.previousResponseId } : {}) })
    },
    onStop: (turn: Turn) => controllers.current.get(turn.id)?.abort(),
    onSignIn: () => authenticate('signIn'),
    onScripted: (turn: Turn) => dispatch({ type: 'scripted', turnId: turn.id }),
  }), [run, authenticate])

  return (
    <>
      <a className="skip-link" href="#main">Skip to the question</a>
      <Header
        session={session}
        presenting={presenting}
        onSignIn={() => authenticate('signIn')}
        onSignOut={() => authenticate('signOut')}
        onTogglePresenter={() => setPresenting((on) => !on)}
      />
      <main id="main" className="page">
        {sessionState.status === 'failed' && (
          <div className="notice error page-notice" role="alert">
            <p>{sessionState.message}</p>
            <div className="actions">
              <button type="button" className="secondary" onClick={() => window.location.reload()}>Reload</button>
            </div>
          </div>
        )}
        {problem && <div className="notice error page-notice" role="alert"><p>{problem}</p></div>}
        {presenting && (
          <Presenter script={SCRIPT} state={presenter} dispatch={present} blocked={blocked} running={stepRunning} onAsk={askStep} />
        )}
        {!presenting && <Composer blocked={blocked} onAsk={ask} />}
        <section className="exchanges" aria-label="Answers">
          {exchanges.map((exchange) => <ExchangeView key={exchange.id} exchange={exchange} actions={actions} />)}
        </section>
        {presenting && <Composer blocked={blocked} onAsk={ask} />}
      </main>
      <footer className="footer">
        <p>Everything in this sample is fictional. Each question runs a Foundry agent as you.</p>
      </footer>
    </>
  )
}
