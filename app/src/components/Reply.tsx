import { useMemo, useRef, type MouseEvent } from 'react'
import { IQ_NAMES } from '../live/protocol.ts'
import { renderInline, renderMarkdown } from '../live/reply.ts'
import type { ParsedReply } from '../live/types.ts'

const sourceElement = (turnId: string, marker: string) => `${turnId}-source-${marker}`

// An answer: its Markdown, rendered safely with a chip for each source marker, then the Sources and Steps it listed.
// A chip opens Sources at the line it cites.
export function Reply({ reply, turnId }: { reply: ParsedReply; turnId: string }) {
  const sources = useRef<HTMLDetailsElement>(null)
  const answer = useMemo(() => renderMarkdown(reply.answer), [reply.answer])
  const steps = useMemo(() => reply.steps.map((step) => renderInline(step)), [reply.steps])
  const first = new Map<string, number>()
  reply.sources.forEach((source, index) => {
    if (!first.has(source.id)) first.set(source.id, index)
  })
  const showSource = (event: MouseEvent<HTMLElement>) => {
    const marker = (event.target as Element).closest('button.cite')?.getAttribute('data-marker')
    if (!marker || !first.has(marker)) return
    if (sources.current) sources.current.open = true
    document.getElementById(sourceElement(turnId, marker))?.focus()
  }
  return (
    <div className="reply" onClick={showSource}>
      <div className="answer" dangerouslySetInnerHTML={{ __html: answer }} />
      {reply.sources.length > 0 && (
        <details className="sources" ref={sources}>
          <summary>Sources <span className="count">{reply.sources.length}</span></summary>
          <ol>
            {reply.sources.map((source, index) => (
              <li key={index} id={first.get(source.id) === index ? sourceElement(turnId, source.id) : undefined} tabIndex={-1}>
                <span className={`cite cite-${source.kind}`} title={IQ_NAMES[source.kind]}>{source.id}</span>
                <span className="visually-hidden">{IQ_NAMES[source.kind]}: </span>
                <span className="source-text">{source.text}</span>
                {source.url && (
                  <a href={source.url} target="_blank" rel="noopener noreferrer">
                    Open<span className="visually-hidden"> {source.id} in a new tab</span>
                  </a>
                )}
              </li>
            ))}
          </ol>
        </details>
      )}
      {steps.length > 0 && (
        <details className="steps">
          <summary>Steps <span className="count">{steps.length}</span></summary>
          <ol>
            {steps.map((html, index) => <li key={index} dangerouslySetInnerHTML={{ __html: html }} />)}
          </ol>
        </details>
      )}
    </div>
  )
}
