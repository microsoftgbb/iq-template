import { useEffect, useId, useRef, useState } from 'react'
import { WORK_IQ, type DemoScript, type Step } from '../demo/script.ts'
import { elapsed, expectations, isTextField, type PresenterAction, type PresenterState } from '../demo/presenter.ts'
import { VARIANT_LABELS } from '../labels.ts'
import { IQ_NAMES } from '../live/protocol.ts'

interface PresenterProps {
  script: DemoScript
  state: PresenterState
  dispatch: (action: PresenterAction) => void
  // Why asking is unavailable, such as before sign-in; undefined when the presenter can ask.
  blocked?: string
  // The current step's question is still being answered.
  running: boolean
  onAsk: (step: Step) => void
}

// Ticks every second, starting from `from`, so the timers update without reading the clock while rendering.
function useNow(from: number): number {
  const [now, setNow] = useState(from)
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])
  return Math.max(now, from)
}

function Notes({ step, state, id }: { step: Step; state: PresenterState; id: string }) {
  const expected = expectations(step)
  return (
    <section id={id} className={state.blank ? 'notes blank' : 'notes'} aria-label="Presenter notes" hidden={!state.notes}>
      {state.blank ? <p className="notes-blank">Notes blanked. Press B to show them.</p> : (
        <dl>
          <div><dt>Say</dt><dd>{step.say}</dd></div>
          <div><dt>Do</dt><dd>{step.do}</dd></div>
          <div><dt>Land</dt><dd>{step.land}</dd></div>
          {step.setup && <div><dt>Setup</dt><dd>{step.setup}</dd></div>}
          {expected.length > 0 && (
            <div>
              <dt>Expect</dt>
              <dd><ul>{expected.map((line) => <li key={line}>{line}</li>)}</ul></dd>
            </div>
          )}
        </dl>
      )}
    </section>
  )
}

// Presenter mode: the demo script's steps on a rail, the current step's question and notes, and a timer per step and
// for the session. Keyboard and clicker keys are handled by the page (App.tsx).
export function Presenter({ script, state, dispatch, blocked, running, onAsk }: PresenterProps) {
  const id = useId()
  const now = useNow(state.stepStartedAt)
  const panel = useRef<HTMLElement>(null)
  const title = useRef<HTMLHeadingElement>(null)
  // Enter asks unless a focused control takes it, such as Next, Restart or an answer's opened Sources, so the step's
  // title takes the focus, and comes into view, when presenter mode opens and at every step, from anywhere but a
  // text field. The notes' own toggles hand it back too.
  useEffect(() => {
    const focused = document.activeElement
    if (!(focused instanceof HTMLElement && isTextField(focused))) title.current?.focus()
  }, [state.index, state.sessionStartedAt])
  useEffect(() => {
    const focused = document.activeElement
    if (focused !== title.current && panel.current?.contains(focused)) title.current?.focus({ preventScroll: true })
  }, [state.notes, state.blank])
  const step = script.steps[state.index]
  const last = state.index === script.steps.length - 1
  const asked = step.asks.map((ask) => VARIANT_LABELS[ask.variant])
  return (
    <section className="presenter" aria-labelledby={`${id}-title`} ref={panel}>
      <nav className="rail" aria-label="Demo steps">
        <ol>
          {script.steps.map((entry, index) => (
            <li key={entry.id}>
              <button
                type="button"
                className="rail-step"
                aria-current={index === state.index ? 'step' : undefined}
                onClick={() => {
                  // The current step changes nothing, so no effect moves the focus: hand it to the title here.
                  if (index === state.index) title.current?.focus({ preventScroll: true })
                  else dispatch({ type: 'go', index, at: Date.now() })
                }}
              >
                <span className="rail-number" aria-hidden="true">{index + 1}</span>
                <span className="rail-title">{entry.title}</span>
                {entry.requires === WORK_IQ && <span className="chip-note">needs Work IQ</span>}
              </button>
            </li>
          ))}
        </ol>
      </nav>
      <div className="stage">
        <header className="stage-head">
          <p className="stage-count">Step {state.index + 1} of {script.steps.length}</p>
          <p className="timers">
            <span role="timer" aria-label="Time on this step">{elapsed(now - state.stepStartedAt)}</span>
            <span className="timer-session" role="timer" aria-label="Time in this session">{elapsed(now - state.sessionStartedAt)} session</span>
          </p>
        </header>
        <h2 id={`${id}-title`} className="stage-title" ref={title} tabIndex={-1}>{step.title}</h2>
        {step.iq.length > 0 && (
          <ul className="stage-iqs" aria-label="IQs in this step">
            {step.iq.map((iq) => <li key={iq} className={`iq-badge iq-${iq}`}>{IQ_NAMES[iq]}</li>)}
          </ul>
        )}
        {step.question
          ? <p className="stage-question">{step.question}</p>
          : <p className="hint">Talk only: this step asks no question.</p>}
        {step.requires === WORK_IQ && (
          <p className="notice consent stage-notice">
            <strong>Needs Work IQ.</strong> Skip this step when the deployment leaves Work IQ off (work_iq_enabled).
          </p>
        )}
        <div className="actions">
          {step.question && (
            <button type="button" className="primary" disabled={Boolean(blocked) || running} onClick={() => onAsk(step)}>
              Ask
            </button>
          )}
          <button type="button" className="secondary" disabled={state.index === 0} onClick={() => dispatch({ type: 'previous', at: Date.now() })}>
            Previous
          </button>
          <button type="button" className="secondary" disabled={last} onClick={() => dispatch({ type: 'next', at: Date.now() })}>
            {step.requires === WORK_IQ ? 'Skip' : 'Next'}
          </button>
          <button type="button" className="quiet" aria-controls={`${id}-notes`} aria-expanded={state.notes} onClick={() => dispatch({ type: 'toggle-notes' })}>
            {state.notes ? 'Hide notes' : 'Show notes'}
          </button>
          <button type="button" className="quiet" aria-pressed={state.blank} disabled={!state.notes} onClick={() => dispatch({ type: 'toggle-blank' })}>
            Blank notes
          </button>
          <button type="button" className="quiet" onClick={() => dispatch({ type: 'restart', at: Date.now() })}>Restart</button>
        </div>
        {step.question && (
          <p className={blocked ? 'hint blocked' : 'hint'}>
            {blocked ?? (running ? 'Answering…' : `Asks ${asked.join(' and ')}${asked.length > 1 ? ', side by side' : ''}.`)}
          </p>
        )}
        <Notes step={step} state={state} id={`${id}-notes`} />
        <p className="keys">
          <kbd>→</kbd> or <kbd>Page Down</kbd> next · <kbd>←</kbd> or <kbd>Page Up</kbd> previous · <kbd>Enter</kbd> ask ·{' '}
          <kbd>N</kbd> notes · <kbd>B</kbd> blank
        </p>
      </div>
      <p className="visually-hidden" role="status">Step {state.index + 1} of {script.steps.length}: {step.title}</p>
    </section>
  )
}
