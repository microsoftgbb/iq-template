// POST /api/turns: one question to one of the two agents, as the signed-in user. The server checks the user's token
// before it reads anything else, accepts only { cid, variant, question, previousResponseId? }, asks the variant's
// Foundry agent with the user's own token and streams each tool call and then the outcome as newline-delimited JSON.
// The Foundry call runs in an invoke_agent span. Nothing the user asked or the agent answered is logged.
import type { IncomingMessage, ServerResponse } from 'node:http'
import { QUESTION_LIMIT, iqOf, parseTurnRequest, type TurnEvent, type TurnOutcome } from '../src/live/protocol.ts'
import type { Variant } from '../src/live/types.ts'
import type { Verifier } from './auth.ts'
import type { ServerConfig } from './config.ts'
import { askAgent, type AgentRun } from './foundry.ts'
import { RequestError, readJson, sendJson } from './http.ts'
import { traceAgentRun } from './telemetry.ts'

// Value-free by construction: no question, answer, tool argument, token or user identifier.
export interface ServerTurnLog {
  event: 'app.turn'
  correlationId: string
  variant: Variant
  followUp: boolean
  outcome: TurnOutcome['status']
  code?: string
  elapsedMs: number
  responseId?: string
  tools: { iq: string; state: string; ms?: number }[]
}

export interface TurnDependencies {
  config: ServerConfig
  verify: Verifier
  ask?: (run: AgentRun) => Promise<TurnOutcome>
  log?: (record: ServerTurnLog) => void
  heartbeatMs?: number
  clock?: () => number
}

// A question of QUESTION_LIMIT characters, each escaped as \uXXXX, with room for the other fields.
const BODY_LIMIT = 16_384
const BAD_REQUEST = `Send { cid, variant, question, previousResponseId? }: a lowercase UUID, with_iq or without_iq, a question of 1 to ${QUESTION_LIMIT} characters and, for a follow-up, Foundry's response ID.`

export function createTurnHandler(dependencies: TurnDependencies) {
  const { config, verify } = dependencies
  const ask = dependencies.ask ?? askAgent
  const log = dependencies.log ?? ((record: ServerTurnLog) => console.info(JSON.stringify(record)))
  const clock = dependencies.clock ?? Date.now
  return async (req: IncomingMessage, res: ServerResponse): Promise<void> => {
    const verified = await verify(req.headers.authorization)
    if (!verified.ok) {
      sendJson(res, 401, { message: verified.message }, { 'WWW-Authenticate': 'Bearer' })
      return
    }
    let body: unknown
    try {
      body = await readJson(req, BODY_LIMIT)
    } catch (error) {
      const status = error instanceof RequestError ? error.status : 400
      sendJson(res, status, { message: error instanceof Error ? error.message : 'The request could not be read.' })
      return
    }
    const request = parseTurnRequest(body)
    if (!request) {
      sendJson(res, 400, { message: BAD_REQUEST })
      return
    }
    res.writeHead(200, { 'Content-Type': 'application/x-ndjson; charset=utf-8', 'Cache-Control': 'no-store' })
    res.flushHeaders()
    const write = (event: TurnEvent) => {
      if (!res.writableEnded && !res.destroyed) res.write(`${JSON.stringify(event)}\n`)
    }
    const gone = new AbortController()
    res.on('close', () => {
      if (!res.writableFinished) gone.abort()
    })
    // Keeps proxies from closing a quiet connection while a slow tool runs.
    const heartbeat = setInterval(() => write({ type: 'heartbeat' }), dependencies.heartbeatMs ?? 15_000)
    const started = clock()
    const { cid, variant, question, previousResponseId } = request
    const agent = config.agents[variant]
    const followUp = previousResponseId !== undefined
    let outcome: TurnOutcome
    try {
      outcome = await traceAgentRun({ agent, endpoint: config.projectEndpoint, variant, cid, followUp }, (traceTool) => ask({
        endpoint: config.projectEndpoint,
        agent,
        token: verified.token,
        question,
        ...(followUp ? { previousResponseId } : {}),
        metadata: { cid, variant },
        signal: gone.signal,
        timeoutMs: config.turnTimeoutMs,
        onTool: (call) => {
          traceTool(call)
          write({ type: 'tool', call })
        },
      }))
    } catch {
      outcome = { status: 'failed', message: 'The app server could not complete the question. Ask again.', toolCalls: [] }
    } finally {
      clearInterval(heartbeat)
    }
    write({ type: 'result', outcome })
    res.end()
    log({
      event: 'app.turn',
      correlationId: cid,
      variant,
      followUp,
      outcome: outcome.status,
      ...(outcome.status === 'failed' && outcome.code ? { code: outcome.code } : {}),
      elapsedMs: Math.max(0, Math.round(clock() - started)),
      ...(outcome.responseId ? { responseId: outcome.responseId } : {}),
      tools: outcome.toolCalls.map((call) => ({ iq: iqOf(call.server) ?? 'other', state: call.state, ...(call.ms === undefined ? {} : { ms: call.ms }) })),
    })
  }
}
