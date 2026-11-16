import { STATE_LABELS, duration } from '../labels.ts'
import { IQ_NAMES, iqOf, type ToolCall } from '../live/protocol.ts'

// The tool calls Foundry reported for one turn, as they start and finish.
export function ToolTimeline({ calls }: { calls: readonly ToolCall[] }) {
  return (
    <ol className="timeline" aria-label="Tool calls">
      {calls.map((call) => {
        const iq = iqOf(call.server)
        return (
          <li key={call.id} className={`tool tool-${call.state}`}>
            <span className="tool-icon" aria-hidden="true" />
            <span className={`iq-badge iq-${iq ?? 'other'}`}>{iq ? IQ_NAMES[iq] : call.server || 'Tool'}</span>
            <code className="tool-name">{call.tool}</code>
            <span className="tool-state">
              {STATE_LABELS[call.state]}
              {call.ms !== undefined && <> · <span className="tool-ms">{duration(call.ms)}</span></>}
            </span>
            {call.error && <p className="tool-error">{call.error}</p>}
            {call.arguments && (
              <details className="tool-args">
                <summary>Query</summary>
                <code>{call.arguments}</code>
              </details>
            )}
          </li>
        )
      })}
    </ol>
  )
}
