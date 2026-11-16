// The scorer and runner behind `npm run rehearse` (server/rehearse.ts). It asks the demo script's questions
// (src/demo/script.json) through the server's own Foundry client, one at a time, as the person rehearsing, and scores
// each reply against the script's checks. The checks support the presenter's judgement; they are not a verdict on
// wording (docs/runbook.md).
import { randomUUID } from 'node:crypto'
import { parseArgs } from 'node:util'
import { TOOL_NAMES, VARIANT_IQS, WORK_IQ, type Checks, type DemoScript, type Step } from '../src/demo/script.ts'
import { IQ_NAMES, SERVER_IQ, isVariant, type ToolCall, type TurnOutcome } from '../src/live/protocol.ts'
import { MARKER_KINDS, parseReply } from '../src/live/reply.ts'
import type { Variant } from '../src/live/types.ts'
import type { ServerConfig } from './config.ts'
import { askAgent, type AgentRun } from './foundry.ts'

export const RESULTS_FORMAT = 'iq-template.rehearsal-results.v1'
// A turn slower than this is too slow to present, though still inside the app's 225-second turn limit.
export const SLOW_MS = 120_000
export const DEFAULT_RUNS = 2
export const MAX_RUNS = 5

export const HELP = `npm run rehearse [-- options]: ask the demo script's questions as you, one at a time, and check each reply.

  --step <id>        only this step (repeatable); default: every step with a question
  --variant <name>   only this agent: with_iq or without_iq
  --runs <n>         ask each question n times, 1 to ${MAX_RUNS} (default ${DEFAULT_RUNS})
  --with-work-iq     include the steps that need Work IQ
  --compare <a> <b>  show two results files side by side

It reads app/.env.local and asks live agents as the Azure CLI's signed-in user. Results go to the ignored
.artifacts/rehearsal/: they hold the replies, so never commit them. Exit codes: 0 every turn passed, 1 a check
failed, 2 usage error, 3 a tool needs your consent first.`

export class UsageError extends Error {}

export interface Options {
  // Step ids; empty for every step with a question.
  steps: string[]
  variant?: Variant
  runs: number
  withWorkIq: boolean
}

export type Command =
  | { kind: 'rehearse'; options: Options }
  | { kind: 'compare'; files: [string, string] }
  | { kind: 'help' }

export function parseCommand(argv: string[]): Command {
  let parsed
  try {
    parsed = parseArgs({
      args: argv,
      allowPositionals: true,
      options: {
        step: { type: 'string', multiple: true },
        variant: { type: 'string' },
        runs: { type: 'string' },
        'with-work-iq': { type: 'boolean' },
        compare: { type: 'boolean' },
        help: { type: 'boolean', short: 'h' },
      },
    })
  } catch (error) {
    throw new UsageError(`${error instanceof Error ? error.message : String(error)}\n\n${HELP}`)
  }
  const { values, positionals } = parsed
  if (values.help) return { kind: 'help' }
  if (values.compare) {
    if (positionals.length !== 2 || values.step || values.variant || values.runs || values['with-work-iq']) {
      throw new UsageError('--compare takes exactly two results files and no other option.')
    }
    return { kind: 'compare', files: [positionals[0], positionals[1]] }
  }
  if (positionals.length) throw new UsageError(`Unexpected argument: ${positionals[0]}\n\n${HELP}`)
  const runs = values.runs === undefined ? DEFAULT_RUNS : Number(values.runs)
  if (!Number.isInteger(runs) || runs < 1 || runs > MAX_RUNS) throw new UsageError(`--runs must be a whole number from 1 to ${MAX_RUNS}.`)
  const variant = values.variant
  if (variant !== undefined && !isVariant(variant)) throw new UsageError('--variant must be with_iq or without_iq.')
  return {
    kind: 'rehearse',
    options: { steps: values.step ?? [], ...(variant ? { variant } : {}), runs, withWorkIq: Boolean(values['with-work-iq']) },
  }
}

// One agent's turn at one step, with what its answer must show.
export interface Planned {
  step: Step
  variant: Variant
  checks: Checks
}

export interface Selection {
  planned: Planned[]
  skipped: { step: string; reason: string }[]
}

// The turns to ask, in script order: each step with a question, each agent it asks.
export function select(script: DemoScript, options: Options): Selection {
  const asking = script.steps.filter((step) => step.question)
  const known = asking.map((step) => step.id)
  const unknown = options.steps.filter((id) => !known.includes(id))
  if (unknown.length) {
    throw new UsageError(`Unknown or talk-only step: ${unknown.join(', ')}. Steps with a question: ${known.join(', ')}.`)
  }
  const selection: Selection = { planned: [], skipped: [] }
  for (const step of options.steps.length ? asking.filter((entry) => options.steps.includes(entry.id)) : asking) {
    if (step.requires === WORK_IQ && !options.withWorkIq) {
      if (options.steps.includes(step.id)) {
        throw new UsageError(`${step.id} needs Work IQ: add --with-work-iq once Work IQ is enabled and the sample mail is in your mailbox.`)
      }
      selection.skipped.push({ step: step.id, reason: 'needs Work IQ (add --with-work-iq)' })
      continue
    }
    for (const ask of step.asks) {
      if (!options.variant || ask.variant === options.variant) selection.planned.push({ step, variant: ask.variant, checks: ask.checks })
    }
  }
  if (!selection.planned.length) throw new UsageError(`Nothing to ask: no selected step asks ${options.variant ?? 'an agent'}.`)
  return selection
}

// One asked turn. tools and failedTools name the tools Foundry's stream reported, as TOOL_NAMES does.
export interface Reply {
  step: string
  variant: Variant
  run: number
  status: TurnOutcome['status']
  // The reply's Markdown, when the agent answered.
  text?: string
  message?: string
  consentUrl?: string
  server?: string
  elapsedMs: number
  tools: string[]
  failedTools: string[]
  responseId?: string
}

// ok is null for what only a person can check.
export interface Check {
  name: string
  ok: boolean | null
  detail: string
}

export interface Scored extends Reply {
  verdict: 'pass' | 'fail' | 'error' | 'consent'
  checks: Check[]
}

export interface Results {
  format: typeof RESULTS_FORMAT
  at: string
  slowMs: number
  runs: number
  skipped: Selection['skipped']
  replies: Scored[]
}

const prefixOf = (id: string) => id.replace(/\d+$/, '')
// Typographic quotes count as their plain forms: agents write both.
const fold = (text: string) => text.toLowerCase().replace(/[\u2018\u2019\u02bc]/g, "'").replace(/[\u201c\u201d]/g, '"')
const found = (text: string, phrases: readonly string[]) => phrases.some((phrase) => fold(text).includes(fold(phrase)))
// The tools a Steps list names, as each IQ's display name: evidence only when Foundry's stream reported no tool call.
const STEP_TOOLS = Object.entries(TOOL_NAMES).map(([server, name]) => ({ name, pattern: new RegExp(`\\b${IQ_NAMES[SERVER_IQ[server]]}\\b`, 'i') }))

function seconds(ms: number): string {
  return (ms / 1000).toFixed(1)
}

function iqLabel(server: string | undefined): string {
  return server && Object.hasOwn(SERVER_IQ, server) ? IQ_NAMES[SERVER_IQ[server]] : server ?? 'a tool'
}

export function score(checks: Checks, reply: Reply, slowMs = SLOW_MS): Scored {
  if (reply.status === 'needs-consent') {
    return { ...reply, verdict: 'consent', checks: [{ name: 'answer', ok: false, detail: `${iqLabel(reply.server)} needs your consent` }] }
  }
  if (reply.status === 'failed' || !reply.text?.trim()) {
    return { ...reply, verdict: 'error', checks: [{ name: 'answer', ok: false, detail: reply.message ?? 'no answer' }] }
  }
  const parsed = parseReply(reply.text)
  // Facts count only in the answer: Sources and Steps quote titles and queries.
  const missing = checks.keyFacts.filter((fact) => !found(parsed.answer, fact.anyOf)).map((fact) => fact.fact)
  const forbidden = checks.mustNot.filter((fact) => found(parsed.answer, fact.anyOf)).map((fact) => fact.fact)
  const used = new Set(parsed.markers.map((marker) => prefixOf(marker.id)))
  const cited = new Set([...used, ...parsed.sources.map((source) => prefixOf(source.id))])
  const reachable = VARIANT_IQS[reply.variant]
  const absent = checks.markers.filter((marker) => !used.has(marker))
  const foreign = [...cited].filter((marker) => !reachable.includes(MARKER_KINDS[marker]))
  const unsourced = parsed.markers.filter((marker) => !parsed.sources.some((source) => source.id === marker.id)).map((marker) => marker.id)
  // Only when the stream reported no call at all: a call it reported as failed never counts.
  const named = reply.tools.length || reply.failedTools.length ? []
    : STEP_TOOLS.filter((tool) => parsed.steps.some((step) => tool.pattern.test(step))).map((tool) => tool.name)
  const notRun = checks.tools.filter((tool) => !reply.tools.includes(tool) && !named.includes(tool))
  const unexpected = reachable.length ? [] : reply.tools
  const detail = (parts: string[], fallback: string) => parts.filter(Boolean).join('; ') || fallback
  const result: Check[] = [
    { name: 'key facts', ok: !missing.length, detail: missing.length ? missing.join('; ') : `${checks.keyFacts.length} found` },
    { name: 'must not', ok: !forbidden.length, detail: forbidden.length ? forbidden.join('; ') : 'none found' },
    {
      name: 'markers',
      ok: !absent.length && !foreign.length,
      detail: detail([absent.length ? `missing ${absent.join(', ')}` : '', foreign.length ? `not the ${reply.variant} agent's: ${foreign.join(', ')}` : ''],
        [...cited].join(', ') || 'none'),
    },
    {
      name: 'format',
      ok: !unsourced.length && parsed.steps.length > 0,
      detail: detail([unsourced.length ? `no Sources line for ${unsourced.join(', ')}` : '', parsed.steps.length ? '' : 'no Steps list'],
        `${parsed.sources.length} sources, ${parsed.steps.length} steps`),
    },
    {
      name: 'tools',
      ok: !notRun.length && !unexpected.length,
      detail: detail([
        notRun.length ? `not run: ${notRun.join(', ')}` : '',
        unexpected.length ? `unexpected: ${unexpected.join(', ')}` : '',
        reply.failedTools.length ? `failed: ${reply.failedTools.join(', ')}` : '',
        named.some((tool) => checks.tools.includes(tool)) ? 'only the Steps list names them' : '',
      ], reply.tools.join(', ') || 'none'),
    },
    { name: 'time', ok: reply.elapsedMs <= slowMs, detail: `${seconds(reply.elapsedMs)} s (bar ${seconds(slowMs)} s)` },
    ...checks.manual.map((manual) => ({ name: 'by eye', ok: null, detail: manual })),
  ]
  return { ...reply, verdict: result.some((check) => check.ok === false) ? 'fail' : 'pass', checks: result }
}

export interface LiveDependencies {
  config: Pick<ServerConfig, 'projectEndpoint' | 'agents' | 'turnTimeoutMs'>
  // A Foundry token for the person rehearsing, kept in memory only.
  token: () => Promise<string>
  ask?: (run: AgentRun) => Promise<TurnOutcome>
  clock?: () => number
  onTool?: (call: ToolCall) => void
  signal?: AbortSignal
}

// One question to one agent, as the app's server asks it: the same client, agent, endpoint and turn budget. Each run
// is a new conversation.
export async function askOnce(planned: Planned, run: number, dependencies: LiveDependencies): Promise<Reply> {
  const { config } = dependencies
  const ask = dependencies.ask ?? askAgent
  const clock = dependencies.clock ?? Date.now
  const token = await dependencies.token()
  const started = clock()
  const outcome = await ask({
    endpoint: config.projectEndpoint,
    agent: config.agents[planned.variant],
    token,
    question: planned.step.question ?? '',
    metadata: { cid: randomUUID(), variant: planned.variant, step: planned.step.id, purpose: 'rehearsal' },
    signal: dependencies.signal ?? new AbortController().signal,
    timeoutMs: config.turnTimeoutMs,
    onTool: dependencies.onTool ?? (() => {}),
  })
  const names = (state: ToolCall['state']) => [...new Set(outcome.toolCalls.filter((call) => call.state === state)
    .map((call) => (Object.hasOwn(TOOL_NAMES, call.server) ? TOOL_NAMES[call.server] : call.server)))]
  const reply: Reply = {
    step: planned.step.id, variant: planned.variant, run, status: outcome.status, elapsedMs: Math.max(0, Math.round(clock() - started)),
    tools: names('completed'), failedTools: names('failed'), ...(outcome.responseId ? { responseId: outcome.responseId } : {}),
  }
  switch (outcome.status) {
    case 'answered':
      return { ...reply, text: outcome.text }
    case 'needs-consent':
      return { ...reply, consentUrl: outcome.consentUrl, ...(outcome.server ? { server: outcome.server } : {}) }
    case 'failed':
      return { ...reply, message: outcome.code ? `${outcome.code}: ${outcome.message}` : outcome.message }
  }
}

export interface RehearsalDependencies extends LiveDependencies {
  now?: () => Date
  // Progress for the terminal, such as the step being asked.
  progress?: (text: string) => void
}

// Each selected step `runs` times, one question at a time, never in parallel; it stops at the first turn that needs
// the user's consent, which only the user can give.
export async function rehearse(selection: Selection, runs: number, dependencies: RehearsalDependencies, slowMs = SLOW_MS): Promise<Results> {
  const results: Results = {
    format: RESULTS_FORMAT, at: (dependencies.now?.() ?? new Date()).toISOString(), slowMs, runs, skipped: selection.skipped, replies: [],
  }
  const steps = [...new Set(selection.planned.map((planned) => planned.step))]
  for (const step of steps) {
    for (let run = 1; run <= runs; run += 1) {
      for (const planned of selection.planned.filter((entry) => entry.step === step)) {
        dependencies.progress?.(`${step.id} ${planned.variant} run ${run}… `)
        const scored = score(planned.checks, await askOnce(planned, run, dependencies), slowMs)
        results.replies.push(scored)
        dependencies.progress?.(`${scored.verdict}\n`)
        if (scored.verdict === 'consent') return results
      }
    }
  }
  return results
}

export function exitCode(results: Results): 0 | 1 | 3 {
  if (results.replies.some((reply) => reply.verdict === 'consent')) return 3
  return results.replies.every((reply) => reply.verdict === 'pass') ? 0 : 1
}

function missingOf(reply: Scored): string {
  return reply.checks.filter((check) => check.ok === false)
    .map((check) => (check.name === 'answer' ? check.detail : `${check.name}: ${check.detail}`)).join('; ')
}

// The results as a table of step, variant, run, verdict, seconds and what was missing, then what to check by eye.
export function summarize(results: Results): string {
  const width = Math.max(4, ...results.replies.map((reply) => reply.step.length))
  const lines = [`${'step'.padEnd(width)}  variant     run  result   seconds  missing`]
  for (const reply of results.replies) {
    lines.push(`${reply.step.padEnd(width)}  ${reply.variant.padEnd(10)}  ${String(reply.run).padStart(3)}  ${reply.verdict.toUpperCase().padEnd(7)}  `
      + `${seconds(reply.elapsedMs).padStart(7)}  ${missingOf(reply)}`.trimEnd())
  }
  const passed = results.replies.filter((reply) => reply.verdict === 'pass').length
  lines.push('', `${passed} of ${results.replies.length} turn(s) passed; a turn passes within ${seconds(results.slowMs)} s.`)
  const manual = new Set(results.replies.flatMap((reply) => reply.checks.filter((check) => check.ok === null)
    .map((check) => `${reply.step} (${reply.variant}): ${check.detail}`)))
  for (const line of manual) lines.push(`Check by eye: ${line}`)
  for (const skip of results.skipped) lines.push(`Skipped ${skip.step}: ${skip.reason}.`)
  for (const reply of results.replies.filter((entry) => entry.verdict === 'consent')) {
    lines.push(`${iqLabel(reply.server)} needs your consent: open ${reply.consentUrl}, accept as yourself, then rehearse again.`)
  }
  return lines.join('\n')
}

export function readResults(value: unknown, name: string): Results {
  const record = (entry: unknown): entry is Record<string, unknown> => typeof entry === 'object' && entry !== null
  const valid = record(value) && value.format === RESULTS_FORMAT && Array.isArray(value.replies)
    && value.replies.every((reply) => record(reply) && typeof reply.step === 'string' && isVariant(reply.variant)
      && typeof reply.verdict === 'string' && typeof reply.elapsedMs === 'number')
  if (!valid) throw new UsageError(`${name} is not an ${RESULTS_FORMAT} file.`)
  return value as unknown as Results
}

function median(values: number[]): number | undefined {
  if (!values.length) return undefined
  const sorted = [...values].sort((a, b) => a - b)
  const middle = Math.floor(sorted.length / 2)
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2
}

// Per step and agent: each results file's passes out of its runs and its median time.
export function compare(first: Results, second: Results, labels: [string, string]): string {
  const keys = [...new Set([...first.replies, ...second.replies].map((reply) => `${reply.step} ${reply.variant}`))]
  const cell = (results: Results, key: string) => {
    const runs = results.replies.filter((reply) => `${reply.step} ${reply.variant}` === key)
    if (!runs.length) return '–'
    const time = median(runs.map((reply) => reply.elapsedMs))
    return `${runs.filter((reply) => reply.verdict === 'pass').length}/${runs.length} pass${time === undefined ? '' : `, ${seconds(time)} s`}`
  }
  const step = Math.max(4, ...keys.map((key) => key.split(' ')[0].length))
  const column = Math.max(labels[0].length, 18)
  const lines = [`${'step'.padEnd(step)}  ${'variant'.padEnd(10)}  ${labels[0].padEnd(column)}  ${labels[1]}`]
  for (const key of keys) {
    const [id, variant] = key.split(' ')
    lines.push(`${id.padEnd(step)}  ${variant.padEnd(10)}  ${cell(first, key).padEnd(column)}  ${cell(second, key)}`)
  }
  return lines.join('\n')
}
