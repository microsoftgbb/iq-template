import { useId, useState, type FormEvent, type KeyboardEvent } from 'react'
import { CHOICES, CHOICE_HINTS, LEGEND, type Choice } from '../labels.ts'
import { IQ_NAMES, QUESTION_LIMIT } from '../live/protocol.ts'
import { SAMPLES } from '../samples.ts'

interface ComposerProps {
  // Why asking is unavailable, such as before sign-in; undefined when the user can ask.
  blocked?: string
  onAsk: (question: string, choice: Choice) => void
}

export function Composer({ blocked, onAsk }: ComposerProps) {
  const [text, setText] = useState('')
  const [choice, setChoice] = useState<Choice>('with_iq')
  const id = useId()
  const question = text.trim()
  const submit = (event?: FormEvent) => {
    event?.preventDefault()
    if (blocked || !question) return
    onAsk(question, choice)
    setText('')
  }
  // Enter asks; Shift+Enter starts a new line.
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      submit()
    }
  }
  return (
    <section className="composer" aria-labelledby={`${id}-label`}>
      <form onSubmit={submit}>
        <label id={`${id}-label`} htmlFor={`${id}-question`} className="composer-label">
          Ask about the sample’s sites and assets
        </label>
        <textarea
          id={`${id}-question`}
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={onKeyDown}
          maxLength={QUESTION_LIMIT}
          rows={3}
          placeholder="Which assets are in maintenance, and at which site?"
          aria-describedby={`${id}-hint`}
        />
        <div className="composer-bar">
          <fieldset className="segmented">
            <legend className="visually-hidden">Which agent to ask</legend>
            {CHOICES.map((option) => (
              <label key={option.value}>
                <input
                  type="radio"
                  name={`${id}-variant`}
                  value={option.value}
                  checked={choice === option.value}
                  onChange={() => setChoice(option.value)}
                />
                <span>{option.label}</span>
              </label>
            ))}
          </fieldset>
          <span className="count" aria-hidden="true">{text.length} / {QUESTION_LIMIT}</span>
          <button type="submit" className="primary" disabled={Boolean(blocked) || !question}>Ask</button>
        </div>
        <p id={`${id}-hint`} className={blocked ? 'hint blocked' : 'hint'}>{blocked ?? CHOICE_HINTS[choice]}</p>
      </form>
      <div className="samples">
        <h2 id={`${id}-samples`}>Sample questions</h2>
        <ul aria-labelledby={`${id}-samples`}>
          {SAMPLES.map((sample) => (
            <li key={sample.question}>
              <button type="button" className="chip" disabled={Boolean(blocked)} onClick={() => onAsk(sample.question, choice)}>
                {sample.question}
                {sample.note && <span className="chip-note">{sample.note}</span>}
              </button>
            </li>
          ))}
        </ul>
      </div>
      <dl className="legend" aria-label="Source markers">
        {LEGEND.map((entry) => (
          <div key={entry.marker}>
            <dt><span className={`cite cite-${entry.iq}`}>{entry.marker}</span></dt>
            <dd><strong>{IQ_NAMES[entry.iq]}</strong> {entry.what}</dd>
          </div>
        ))}
      </dl>
    </section>
  )
}
