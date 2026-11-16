// Mock mode only: `npm run dev:mock` (vite --mode mock). live/client.ts imports this module only behind a literal
// import.meta.env.MODE === 'mock', so production builds never contain it. Canned answers for the sample questions
// stand in for the agents, through the same turn as a live answer: tool calls, one consent request and the reply.
import type { ToolCall, TurnOutcome } from '../live/protocol.ts'
import { runTurn, type Send, type TurnLog } from '../live/turn.ts'
import type { AgentClient, Session, Variant } from '../live/types.ts'
import { SAMPLES } from '../samples.ts'

export const MOCK_SESSION: Session = {
  mode: 'mock',
  identity: { name: 'Mock user' },
  notice: 'Mock mode · canned answers, no sign-in',
  signIn: async () => {},
  signOut: async () => {},
}

// Long enough for each running state to show.
export const MOCK_DELAY_MS = 900
export const MOCK_CONSENT_URL = 'https://example.com/mock-consent'

export interface Canned {
  tools: { server: string; tool: string; arguments: string }[]
  answer: string
  // The tool server whose consent the first ask of this question asks for.
  consent?: string
}

const NO_TOOLS = (what: string): Canned => ({
  tools: [],
  answer: [
    `**I can't confirm ${what}.** This version of the assistant has no tools, so it can't check, and it won't guess.`,
    '',
    'Sources',
    '',
    'Steps',
    '1. Called no tool: this version has none.',
  ].join('\n'),
})

const [maintenance, document, publicContext, mail] = SAMPLES.map((sample) => sample.question)

// By sample question, then variant.
export const MOCK_ANSWERS: Readonly<Record<string, Record<Variant, Canned>>> = {
  [maintenance]: {
    with_iq: {
      tools: [{
        server: 'fabric_iq_ontology', tool: 'ask_ontology',
        arguments: '{"question":"Which Asset rows have the Status value \\"Maintenance\\"? Return each asset name and the name of its site."}',
      }],
      answer: [
        '**Pump B is in maintenance, at North Works.** [D1]',
        '',
        '- Pump B · Status "Maintenance" · located at North Works [D1]',
        '',
        'Sources',
        '- [D1] ScenarioOntology · Asset · demo-asset-002',
        '',
        'Steps',
        '1. Fabric IQ ontology: asked which Asset rows have the Status value "Maintenance", with the name of each one\'s site; it returned Pump B at North Works.',
      ].join('\n'),
    },
    without_iq: NO_TOOLS('which assets are in maintenance or where they are'),
  },
  [document]: {
    with_iq: {
      tools: [{
        server: 'foundry_iq_knowledge', tool: 'knowledge_base_retrieve',
        arguments: '{"queries":["Where do current entity values come from, according to the starter document?"]}',
      }],
      answer: [
        '**From the live ontology, not from the document.** The starter document says to use the ontology source for current entity values and counts [F1].',
        '',
        '- The facts it lists describe the original seed, not current operational status [F1].',
        '',
        'Sources',
        '- [F1] Synthetic asset operations starter knowledge · starter.txt',
        '',
        'Steps',
        '1. Foundry IQ knowledge: asked where current entity values come from; it returned the starter document\'s passage about the ontology source.',
      ].join('\n'),
    },
    without_iq: NO_TOOLS('what the starter document says'),
  },
  [publicContext]: {
    with_iq: {
      tools: [{
        server: 'web_iq_public_context', tool: 'knowledge_base_retrieve',
        arguments: '{"queries":["centrifugal pump maintenance guidance"]}',
      }],
      answer: [
        '**Public context, not company guidance:** general guidance for centrifugal pumps recommends regular checks of seals, bearings, vibration and alignment, with service intervals set by operating hours and conditions [Web1].',
        '',
        'Sources',
        '- [Web1] Centrifugal pump maintenance basics · example.com · https://example.com/centrifugal-pump-maintenance',
        '',
        'Steps',
        '1. Web IQ public context: searched public guidance on centrifugal pump maintenance, without any site or asset name; it returned one general guide.',
      ].join('\n'),
    },
    without_iq: NO_TOOLS('public guidance on centrifugal pump maintenance'),
  },
  [mail]: {
    with_iq: {
      consent: 'work_iq_mail',
      tools: [{
        server: 'work_iq_mail', tool: 'fetch',
        arguments: '{"entityUrls":["/me/messages?$search=\\"Pump B\\"&$select=subject,from,receivedDateTime,webLink,body&$top=10"]}',
      }],
      answer: [
        '**Two recent messages mention Pump B.**',
        '',
        '- The maintenance team reported a worn seal on Pump B and planned its replacement [W1].',
        '- The North Works site lead expects Pump B back in service after the replacement [W2].',
        '',
        'Mail is what people reported or planned, not current status; the live ontology holds that.',
        '',
        'Sources',
        '- [W1] Outlook · Maintenance team · 10 November 2026 · Pump B seal replacement · https://example.com/mail/1',
        '- [W2] Outlook · North Works site lead · 12 November 2026 · Pump B back in service · https://example.com/mail/2',
        '',
        'Steps',
        '1. Work IQ mail: searched your mail for "Pump B"; it returned two messages.',
      ].join('\n'),
    },
    without_iq: NO_TOOLS('what your mail says about Pump B'),
  },
}

const FALLBACK: Canned = {
  tools: [],
  answer: '**Mock mode has canned answers for the sample questions only.** Pick a sample question, or run `npm run dev` against a deployment to ask the live agents.',
}

function canned(question: string, variant: Variant): Canned {
  const key = question.trim().toLowerCase()
  const match = Object.keys(MOCK_ANSWERS).find((sample) => sample.toLowerCase() === key)
  return match ? MOCK_ANSWERS[match][variant] : FALLBACK
}

function pause(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const done = () => {
      clearTimeout(timer)
      signal.removeEventListener('abort', done)
      resolve()
    }
    const timer = setTimeout(done, ms)
    signal.addEventListener('abort', done, { once: true })
  })
}

export interface MockOptions {
  delayMs?: number
  log?: (record: TurnLog) => void
}

export function createMockClient(options: MockOptions = {}): AgentClient {
  const delayMs = options.delayMs ?? MOCK_DELAY_MS
  // The servers this mock user has "consented" to: a question that needs one asks for consent once.
  const consented = new Set<string>()
  const send: Send = async (question, _cid, signal, onTool): Promise<TurnOutcome> => {
    const reply = canned(question.text, question.variant)
    const responseId = `resp_mock_${crypto.randomUUID().replaceAll('-', '')}`
    if (reply.consent && !consented.has(reply.consent)) {
      await pause(delayMs, signal)
      consented.add(reply.consent)
      return { status: 'needs-consent', consentUrl: MOCK_CONSENT_URL, server: reply.consent, responseId, toolCalls: [] }
    }
    const calls: ToolCall[] = []
    for (const [index, tool] of reply.tools.entries()) {
      const id = `mock_call_${index + 1}`
      const started = performance.now()
      onTool({ id, server: tool.server, tool: tool.tool, state: 'running' })
      await pause(delayMs, signal)
      const done: ToolCall = {
        id, server: tool.server, tool: tool.tool, state: 'completed', ms: Math.round(performance.now() - started), arguments: tool.arguments,
      }
      calls.push(done)
      onTool(done)
    }
    await pause(delayMs / 2, signal)
    return { status: 'answered', text: reply.answer, responseId, toolCalls: calls }
  }
  return {
    mode: 'mock',
    ask: (question, signal, onTool) => runTurn({ question, send, signal, onTool, log: options.log }),
  }
}
