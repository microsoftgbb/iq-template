// The demo script (script.json): the single source of presenter mode's steps and notes, mock mode's replies and the
// rehearsal's checks (npm run rehearse). readScript validates the JSON and types it; it throws on anything presenter
// mode, the mock or the rehearsal couldn't use. The tests and vite.config.ts load this module, so a broken script fails
// the tests and every build, the image's included, not a session.
import { ARGUMENTS_LIMIT, QUESTION_LIMIT, SERVER_IQ, isVariant } from '../live/protocol.ts'
import { MARKER_KINDS, parseReply } from '../live/reply.ts'
import type { IqKey, Variant } from '../live/types.ts'
import data from './script.json' with { type: 'json' }

export const SCRIPT_FORMAT = 'iq-template.demo.v1'
export const IQS: readonly IqKey[] = ['fabric', 'foundry', 'web', 'work']
export const WORK_IQ = 'work_iq'

// The with-IQ agent's tools by MCP server label, named as their files in agents/scenario-with-iq/tools/.
export const TOOL_NAMES: Readonly<Record<string, string>> = {
  fabric_iq_ontology: 'FabricIQOntology',
  foundry_iq_knowledge: 'FoundryIQKnowledge',
  web_iq_public_context: 'WebIQPublicContext',
  work_iq_mail: 'WorkIQMail',
}

// The IQs each agent can reach: the with-IQ agent has every IQ's tool (Work IQ's only when enabled), the other none.
export const VARIANT_IQS: Readonly<Record<Variant, readonly IqKey[]>> = { with_iq: IQS, without_iq: [] }

export interface Fact {
  fact: string
  // The fact passes when the answer contains any of these, ignoring case.
  anyOf: string[]
}

export interface Checks {
  keyFacts: Fact[]
  mustNot: Fact[]
  // Marker prefixes the answer must use: D, F, Web or W.
  markers: string[]
  // Tools that must run, as TOOL_NAMES names them.
  tools: string[]
  // What only a person can check, such as a Web IQ query without private names.
  manual: string[]
}

export interface ScriptedTool {
  server: string
  tool: string
  arguments: string
}

export interface Scripted {
  // Markdown in the agents' reply format.
  reply: string
  // The tool calls mock mode replays before the reply.
  tools: ScriptedTool[]
}

// One agent a step asks, with what its answer must show and its scripted reply.
export interface Ask {
  variant: Variant
  checks: Checks
  scripted: Scripted
}

export interface Step {
  id: string
  title: string
  iq: IqKey[]
  say: string
  do: string
  land: string
  setup?: string
  requires?: typeof WORK_IQ
  // Absent for a talk-only step, which asks nothing.
  question?: string
  // The agents to ask, in order; two means a side-by-side comparison.
  asks: Ask[]
}

export interface DemoScript {
  format: typeof SCRIPT_FORMAT
  title: string
  steps: Step[]
}

type Json = Record<string, unknown>

const STEP_ID = /^[a-z0-9]+(?:-[a-z0-9]+)*$/
const TOP_KEYS = new Set(['format', 'title', 'description', 'steps'])
const STEP_KEYS = new Set(['id', 'title', 'iq', 'say', 'do', 'land', 'setup', 'requires', 'question', 'variants', 'checks', 'scripted'])
const CHECK_KEYS = new Set(['keyFacts', 'mustNot', 'markers', 'tools', 'manual'])
const FACT_KEYS = new Set(['fact', 'anyOf'])
const SCRIPTED_KEYS = new Set(['reply', 'tools'])
const TOOL_KEYS = new Set(['server', 'tool', 'arguments'])
const IQ_OF_TOOL: Readonly<Record<string, IqKey>> = Object.fromEntries(
  Object.entries(TOOL_NAMES).map(([server, name]) => [name, SERVER_IQ[server]]),
)

function record(value: unknown): value is Json {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function text(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0
}

function strings(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(text)
}

function isIq(value: unknown): value is IqKey {
  return typeof value === 'string' && (IQS as readonly string[]).includes(value)
}

function unique(values: readonly string[]): boolean {
  return new Set(values).size === values.length
}

function iqOfMarker(prefix: string): IqKey | undefined {
  return Object.hasOwn(MARKER_KINDS, prefix) ? MARKER_KINDS[prefix] : undefined
}

function iqOfServer(server: string): IqKey | undefined {
  return Object.hasOwn(SERVER_IQ, server) ? SERVER_IQ[server] : undefined
}

function extraKeys(value: Json, allowed: ReadonlySet<string>): string[] {
  return Object.keys(value).filter((key) => !allowed.has(key))
}

// Collects every problem, each with its path, so one run of the tests shows them all.
class Problems {
  readonly list: string[] = []

  add(path: string, problem: string): void {
    this.list.push(`${path}: ${problem}`)
  }

  keys(path: string, value: Json, allowed: ReadonlySet<string>): void {
    const extra = extraKeys(value, allowed)
    if (extra.length) this.add(path, `unknown keys ${extra.join(', ')}`)
  }
}

function readFacts(value: unknown, path: string, problems: Problems): Fact[] {
  if (!Array.isArray(value)) {
    problems.add(path, 'must be a list of { fact, anyOf }')
    return []
  }
  return value.flatMap((entry, index) => {
    const at = `${path}[${index}]`
    if (!record(entry) || !text(entry.fact) || !strings(entry.anyOf) || !entry.anyOf.length) {
      problems.add(at, 'needs a fact and a non-empty anyOf list of phrases')
      return []
    }
    problems.keys(at, entry, FACT_KEYS)
    return [{ fact: entry.fact, anyOf: [...entry.anyOf] }]
  })
}

function readChecks(value: unknown, variant: Variant, step: { iq: readonly IqKey[] }, path: string, problems: Problems): Checks {
  const empty: Checks = { keyFacts: [], mustNot: [], markers: [], tools: [], manual: [] }
  if (!record(value)) {
    problems.add(path, 'must be { keyFacts, mustNot, markers, tools }')
    return empty
  }
  problems.keys(path, value, CHECK_KEYS)
  const checks: Checks = {
    keyFacts: readFacts(value.keyFacts, `${path}.keyFacts`, problems),
    mustNot: readFacts(value.mustNot, `${path}.mustNot`, problems),
    markers: [],
    tools: [],
    manual: [],
  }
  if (!Array.isArray(value.markers) || !value.markers.every((marker) => typeof marker === 'string' && iqOfMarker(marker))) {
    problems.add(`${path}.markers`, `must list marker prefixes from ${Object.keys(MARKER_KINDS).join(', ')}`)
  } else {
    checks.markers = [...value.markers] as string[]
  }
  if (!Array.isArray(value.tools) || !value.tools.every((tool) => typeof tool === 'string' && Object.hasOwn(IQ_OF_TOOL, tool))) {
    problems.add(`${path}.tools`, `must list tools from ${Object.values(TOOL_NAMES).join(', ')}`)
  } else {
    checks.tools = [...value.tools] as string[]
  }
  if (value.manual !== undefined) {
    if (strings(value.manual)) checks.manual = [...value.manual]
    else problems.add(`${path}.manual`, 'must be a list of checks a person makes')
  }
  if (!unique(checks.markers) || !unique(checks.tools)) problems.add(path, 'lists a marker or tool twice')
  const reachable = VARIANT_IQS[variant]
  const used = [...checks.markers.map((marker) => iqOfMarker(marker)), ...checks.tools.map((tool) => IQ_OF_TOOL[tool])]
  for (const iq of used) {
    if (iq && !reachable.includes(iq)) problems.add(path, `expects ${iq}, which the ${variant} agent doesn't have`)
    else if (iq && !step.iq.includes(iq)) problems.add(path, `expects ${iq}, which isn't in the step's iq list`)
  }
  const required = new Set(checks.keyFacts.flatMap((fact) => fact.anyOf.map((phrase) => phrase.toLowerCase())))
  const both = checks.mustNot.flatMap((fact) => fact.anyOf).filter((phrase) => required.has(phrase.toLowerCase()))
  if (both.length) problems.add(path, `both requires and forbids ${both.join(', ')}`)
  return checks
}

function readScripted(value: unknown, variant: Variant, path: string, problems: Problems): Scripted {
  if (!record(value) || !Array.isArray(value.reply) || !value.reply.every((line) => typeof line === 'string')
    || !value.reply.some(text) || !Array.isArray(value.tools)) {
    problems.add(path, 'must be { reply: [Markdown lines], tools: [{ server, tool, arguments }] }')
    return { reply: '', tools: [] }
  }
  problems.keys(path, value, SCRIPTED_KEYS)
  const tools = value.tools.flatMap((tool, index): ScriptedTool[] => {
    const at = `${path}.tools[${index}]`
    if (!record(tool) || typeof tool.server !== 'string' || !iqOfServer(tool.server) || !text(tool.tool)
      || typeof tool.arguments !== 'string' || tool.arguments.length > ARGUMENTS_LIMIT) {
      problems.add(at, `needs a known server label, a tool name and arguments of at most ${ARGUMENTS_LIMIT} characters`)
      return []
    }
    problems.keys(at, tool, TOOL_KEYS)
    return [{ server: tool.server, tool: tool.tool, arguments: tool.arguments }]
  })
  const called = new Set(tools.map((tool) => iqOfServer(tool.server)))
  for (const iq of called) {
    if (iq && !VARIANT_IQS[variant].includes(iq)) problems.add(path, `calls ${iq}, which the ${variant} agent doesn't have`)
  }
  const reply = (value.reply as string[]).join('\n')
  const parsed = parseReply(reply)
  // An agent never cites a tool it didn't call, so a scripted reply cites only its own tools.
  for (const marker of [...parsed.markers, ...parsed.sources]) {
    if (!called.has(marker.kind)) problems.add(path, `cites ${marker.id}, but calls no ${marker.kind} tool`)
  }
  return { reply, tools }
}

function readStep(value: unknown, index: number, problems: Problems): Step | undefined {
  const path = `steps[${index}]`
  if (!record(value)) {
    problems.add(path, 'must be an object')
    return undefined
  }
  const at = `${path} (${typeof value.id === 'string' ? value.id : '?'})`
  problems.keys(at, value, STEP_KEYS)
  if (typeof value.id !== 'string' || !STEP_ID.test(value.id)) problems.add(at, 'id must be a lowercase slug, such as live-facts')
  for (const key of ['title', 'say', 'do', 'land'] as const) {
    if (!text(value[key])) problems.add(at, `needs ${key}`)
  }
  if (value.setup !== undefined && !text(value.setup)) problems.add(at, 'setup must be text')
  const iq = Array.isArray(value.iq) && value.iq.every(isIq) && unique(value.iq) ? [...value.iq] as IqKey[] : undefined
  if (!iq) problems.add(at, `iq must list each of ${IQS.join(', ')} at most once`)
  const step: Step = {
    id: String(value.id), title: String(value.title), iq: iq ?? [], say: String(value.say), do: String(value.do),
    land: String(value.land), asks: [], ...(text(value.setup) ? { setup: value.setup } : {}),
  }
  if (value.question === undefined) {
    for (const key of ['variants', 'checks', 'scripted', 'requires'] as const) {
      if (value[key] !== undefined) problems.add(at, `a talk-only step has no question, so no ${key}`)
    }
    return step
  }
  if (!text(value.question) || value.question !== value.question.trim() || value.question.length > QUESTION_LIMIT) {
    problems.add(at, `question must be trimmed text of at most ${QUESTION_LIMIT} characters`)
  } else {
    step.question = value.question
  }
  if (value.requires !== undefined) {
    if (value.requires === WORK_IQ) step.requires = WORK_IQ
    else problems.add(at, `requires can only be ${WORK_IQ}`)
  }
  const variants = Array.isArray(value.variants) && value.variants.length > 0 && value.variants.every(isVariant)
    && unique(value.variants) ? value.variants : undefined
  if (!variants) {
    problems.add(at, 'variants must list the agents to ask, in order: without_iq, with_iq or both')
    return step
  }
  for (const key of ['checks', 'scripted'] as const) {
    const entries = value[key]
    if (!record(entries)) {
      problems.add(at, `${key} must hold an entry for each variant the step asks`)
      continue
    }
    for (const variant of Object.keys(entries).filter((name) => !variants.includes(name as Variant))) {
      problems.add(at, `${key}.${variant} belongs to a variant the step doesn't ask`)
    }
    for (const variant of variants.filter((name) => !Object.hasOwn(entries, name))) {
      problems.add(at, `${key} has no entry for ${variant}, which the step asks`)
    }
  }
  const checks = record(value.checks) ? value.checks : {}
  const scripted = record(value.scripted) ? value.scripted : {}
  step.asks = variants.map((variant) => ({
    variant,
    checks: readChecks(checks[variant], variant, step, `${at}.checks.${variant}`, problems),
    scripted: readScripted(scripted[variant], variant, `${at}.scripted.${variant}`, problems),
  }))
  // Without Work IQ, the with-IQ agent has no mail tool: a step that needs it must say so, so it can be skipped.
  const mail = step.asks.some((ask) => ask.checks.tools.includes(TOOL_NAMES.work_iq_mail) || ask.checks.markers.includes('W'))
  if (mail && step.requires !== WORK_IQ) problems.add(at, `expects Work IQ mail, so it needs requires: ${WORK_IQ}`)
  if (!mail && step.requires === WORK_IQ) problems.add(at, `requires ${WORK_IQ} but expects no Work IQ mail`)
  return step
}

// The validated, typed script, or an error that lists every problem with its path.
export function readScript(value: unknown): DemoScript {
  const problems = new Problems()
  if (!record(value)) throw new Error('The demo script must be a JSON object.')
  problems.keys('script', value, TOP_KEYS)
  if (value.format !== SCRIPT_FORMAT) problems.add('script', `format must be ${SCRIPT_FORMAT}`)
  if (!text(value.title)) problems.add('script', 'needs a title')
  if (value.description !== undefined && !text(value.description)) problems.add('script', 'description must be text')
  if (!Array.isArray(value.steps) || !value.steps.length) problems.add('script', 'needs at least one step')
  const steps = Array.isArray(value.steps) ? value.steps.flatMap((step, index) => readStep(step, index, problems) ?? []) : []
  const ids = steps.map((step) => step.id)
  const duplicates = ids.filter((id, index) => ids.indexOf(id) !== index)
  if (duplicates.length) problems.add('script', `step ids must be unique: ${[...new Set(duplicates)].join(', ')}`)
  // Mock mode finds a step by its question.
  const questions = steps.flatMap((step) => (step.question ? [step.question.toLowerCase()] : []))
  if (!unique(questions)) problems.add('script', 'questions must be unique')
  if (problems.list.length) throw new Error(`The demo script is invalid:\n- ${problems.list.join('\n- ')}`)
  return { format: SCRIPT_FORMAT, title: String(value.title), steps }
}

export const SCRIPT: DemoScript = readScript(data)

// The step whose question this is, ignoring case and surrounding space.
export function stepOfQuestion(script: DemoScript, question: string): Step | undefined {
  const key = question.trim().toLowerCase()
  return script.steps.find((step) => step.question?.toLowerCase() === key)
}

export function askOf(step: Step | undefined, variant: Variant): Ask | undefined {
  return step?.asks.find((ask) => ask.variant === variant)
}
