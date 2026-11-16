import { useEffect, useId, useMemo, useRef, useState, type FormEvent } from 'react'
import { SCRIPTED_LABEL, scriptedFallback } from '../demo/presenter.ts'
import { SCRIPT, type Scripted } from '../demo/script.ts'
import { followUpFrom, type Exchange, type Thread, type Turn } from '../exchanges.ts'
import { VARIANT_DETAILS, VARIANT_LABELS, announcement, duration, iqName } from '../labels.ts'
import { QUESTION_LIMIT } from '../live/protocol.ts'
import { parseReply } from '../live/reply.ts'
import { UNAUTHORIZED } from '../live/stream.ts'
import { CANCELLED } from '../live/turn.ts'
import type { TurnResult } from '../live/types.ts'
import { Reply } from './Reply.tsx'
import { ToolTimeline } from './ToolTimeline.tsx'

export interface TurnActions {
  onFollowUp: (thread: Thread, question: string) => void
  onRetry: (thread: Thread, turn: Turn) => void
  onStop: (turn: Turn) => void
  onSignIn: () => void
  // Presenter mode's plan B: shows the step's scripted answer in place of a turn that didn't answer.
  onScripted: (turn: Turn) => void
}

// Whole seconds since the turn started, ticking while it runs.
function useSeconds(startedAt: number): number {
  const [now, setNow] = useState(startedAt)
  useEffect(() => {
    const timer = setInterval(() => setNow(performance.now()), 1000)
    return () => clearInterval(timer)
  }, [])
  return Math.max(0, Math.floor((now - startedAt) / 1000))
}

function Running({ turn, onStop }: { turn: Turn; onStop: () => void }) {
  const seconds = useSeconds(turn.startedAt)
  const toolRunning = turn.toolCalls.some((call) => call.state === 'running')
  return (
    <div className="running">
      {!toolRunning && <span className="spinner" aria-hidden="true" />}
      <span>{toolRunning ? 'Waiting for a tool' : 'Asking the agent'} · {seconds} s</span>
      <button type="button" className="quiet" onClick={onStop}>Stop</button>
    </div>
  )
}

interface OutcomeProps {
  result: TurnResult
  turnId: string
  onRetry: () => void
  onSignIn: () => void
  // Presenter mode's plan B, offered once a step's turn ends without an answer.
  onScripted?: () => void
}

function ScriptedButton({ onScripted }: { onScripted?: () => void }) {
  return onScripted ? <button type="button" className="secondary" onClick={onScripted}>Show scripted answer</button> : null
}

function Outcome({ result, turnId, onRetry, onSignIn, onScripted }: OutcomeProps) {
  switch (result.status) {
    case 'answered':
      return <Reply reply={result.reply} turnId={turnId} />
    case 'needs-consent': {
      const name = iqName(result.server)
      return (
        <div className="notice consent">
          <p>
            <strong>{name ?? 'This tool'} needs your consent.</strong> Authorize it once, in the new tab, to read data
            as you; then ask again.
          </p>
          <div className="actions">
            <a className="button primary" href={result.consentUrl} target="_blank" rel="noopener noreferrer">
              Authorize {name ?? 'tool'} access
            </a>
            <button type="button" className="secondary" onClick={onRetry}>Ask again</button>
            <ScriptedButton onScripted={onScripted} />
          </div>
        </div>
      )
    }
    case 'failed':
      return (
        <div className={result.code === CANCELLED ? 'notice' : 'notice error'}>
          <p>{result.code === CANCELLED ? 'Stopped before the agent answered.' : result.message}</p>
          <div className="actions">
            {result.code === UNAUTHORIZED
              ? <button type="button" className="primary" onClick={onSignIn}>Sign in again</button>
              : <button type="button" className="secondary" onClick={onRetry}>Ask again</button>}
            <ScriptedButton onScripted={onScripted} />
          </div>
        </div>
      )
  }
}

// The script's reply, styled and labelled so nobody mistakes it for a live answer. It takes the focus from the button
// that showed it.
function ScriptedAnswer({ scripted, turnId }: { scripted: Scripted; turnId: string }) {
  const reply = useMemo(() => parseReply(scripted.reply), [scripted.reply])
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => box.current?.focus(), [])
  return (
    <div className="scripted" ref={box} tabIndex={-1}>
      <p className="scripted-label">
        <strong>{SCRIPTED_LABEL}</strong> From the demo script: the agent didn’t answer this turn live.
      </p>
      <Reply reply={reply} turnId={`${turnId}-scripted`} />
    </div>
  )
}

function TurnView({ thread, turn, first, stepId, actions }: { thread: Thread; turn: Turn; first: boolean; stepId?: string; actions: TurnActions }) {
  const result = turn.running ? undefined : turn.result
  const fallback = scriptedFallback(SCRIPT, stepId, thread.variant, turn, first)
  return (
    <div className="turn" aria-busy={turn.running}>
      {!first && <p className="follow-up-question"><span>Follow-up</span> {turn.question}</p>}
      {turn.toolCalls.length > 0 && <ToolTimeline calls={turn.toolCalls} />}
      {turn.running && <Running turn={turn} onStop={() => actions.onStop(turn)} />}
      {result && (
        <>
          <Outcome
            result={result}
            turnId={turn.id}
            onRetry={() => actions.onRetry(thread, turn)}
            onSignIn={actions.onSignIn}
            onScripted={fallback && !turn.scripted ? () => actions.onScripted(turn) : undefined}
          />
          {fallback && turn.scripted && <ScriptedAnswer scripted={fallback} turnId={turn.id} />}
          <p className="meta">
            {result.responseId && <span>Response <code>{result.responseId}</code></span>}
            <span>{duration(result.elapsedMs)}</span>
          </p>
        </>
      )}
      <p className="visually-hidden" role="status">{announcement(turn, VARIANT_LABELS[thread.variant])}</p>
    </div>
  )
}

function FollowUp({ thread, onFollowUp }: { thread: Thread; onFollowUp: TurnActions['onFollowUp'] }) {
  const [text, setText] = useState('')
  const id = useId()
  const submit = (event: FormEvent) => {
    event.preventDefault()
    const question = text.trim()
    if (!question) return
    onFollowUp(thread, question)
    setText('')
  }
  return (
    <form className="follow-up" onSubmit={submit}>
      <label htmlFor={id} className="visually-hidden">Follow-up question ({VARIANT_LABELS[thread.variant]})</label>
      <input id={id} type="text" value={text} onChange={(event) => setText(event.target.value)} maxLength={QUESTION_LIMIT} placeholder="Ask a follow-up…" />
      <button type="submit" className="secondary" disabled={!text.trim()}>Follow up</button>
    </form>
  )
}

const STATUS_LABELS: Record<'running' | TurnResult['status'], string> = {
  running: 'Working',
  answered: 'Answered',
  'needs-consent': 'Needs consent',
  failed: 'Not answered',
}

function ThreadCard({ thread, stepId, actions }: { thread: Thread; stepId?: string; actions: TurnActions }) {
  const id = useId()
  const last = thread.turns.at(-1)
  const status = !last || last.running ? 'running' : last.result?.status ?? 'running'
  return (
    <section className={`thread thread-${thread.variant}`} aria-labelledby={id}>
      <header className="thread-head">
        <h3 id={id}>{VARIANT_LABELS[thread.variant]}</h3>
        <span className="thread-detail">{VARIANT_DETAILS[thread.variant]}</span>
        <span className={`state state-${status}`}>{STATUS_LABELS[status]}</span>
      </header>
      {thread.turns.map((turn, index) => (
        <TurnView key={turn.id} thread={thread} turn={turn} first={index === 0} stepId={stepId} actions={actions} />
      ))}
      {followUpFrom(thread) && <FollowUp thread={thread} onFollowUp={actions.onFollowUp} />}
    </section>
  )
}

export function ExchangeView({ exchange, actions }: { exchange: Exchange; actions: TurnActions }) {
  const id = useId()
  const step = exchange.stepId ? SCRIPT.steps.find((entry) => entry.id === exchange.stepId) : undefined
  return (
    <article className="exchange" aria-labelledby={id}>
      {step && <p className="exchange-step">{step.title}</p>}
      <h2 id={id} className="question">{exchange.question}</h2>
      <div className={exchange.threads.length > 1 ? 'threads compare' : 'threads'}>
        {exchange.threads.map((thread) => <ThreadCard key={thread.id} thread={thread} stepId={exchange.stepId} actions={actions} />)}
      </div>
    </article>
  )
}
