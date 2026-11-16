// npm run rehearse: asks the demo script's questions (src/demo/script.json) as you, one at a time, and checks each
// reply against the script; see docs/runbook.md.
//
//   npm run rehearse                                  every step with a question, each of its agents, twice
//   npm run rehearse -- --step live-facts --runs 3    chosen steps
//   npm run rehearse -- --variant with_iq             one agent
//   npm run rehearse -- --with-work-iq                also the steps that need Work IQ
//   npm run rehearse -- --compare a.json b.json       two results files side by side
//
// It reads the deployment's settings from app/.env.local (terraform output app_settings) and asks live agents through
// the server's own Foundry client, as the Azure CLI's signed-in user, with a token for https://ai.azure.com that stays
// in memory: never run it as a template check. Results hold the replies, which can hold mail, so they go to the
// repository's ignored .artifacts/rehearsal/: never commit them.
import { execFile } from 'node:child_process'
import { chmod, mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { promisify } from 'node:util'
import { SCRIPT, TOOL_NAMES } from '../src/demo/script.ts'
import { readServerConfig } from './config.ts'
import {
  HELP, UsageError, compare, exitCode, parseCommand, readResults, rehearse, select, summarize, type Results,
} from './rehearsal.ts'

const ROOT = path.resolve(import.meta.dirname, '../..')
const OUTPUT = path.join(ROOT, '.artifacts', 'rehearsal')
const FOUNDRY_SCOPE = 'https://ai.azure.com/.default'
// npm runs the script in app/: files on the command line are relative to where npm was invoked.
const INVOKED = process.env.INIT_CWD ?? process.cwd()

async function azureToken(): Promise<string> {
  try {
    const { stdout } = await promisify(execFile)('az', ['account', 'get-access-token', '--scope', FOUNDRY_SCOPE,
      '--query', 'accessToken', '--output', 'tsv'], { timeout: 60_000 })
    const token = stdout.trim()
    if (/^[\w-]+\.[\w-]+\.[\w-]+$/.test(token)) return token
  } catch {
    // Reported below, without the CLI's output.
  }
  throw new Error('Azure CLI could not get a Foundry token for you: sign in with az login as the presenter, then rehearse again.')
}

async function results(file: string): Promise<Results> {
  try {
    return readResults(JSON.parse(await readFile(path.resolve(INVOKED, file), 'utf8')), file)
  } catch (error) {
    throw error instanceof UsageError ? error : new UsageError(`${file} could not be read as a results file.`)
  }
}

async function save(rehearsed: Results): Promise<string> {
  await mkdir(OUTPUT, { recursive: true, mode: 0o700 })
  await chmod(OUTPUT, 0o700)
  const file = path.join(OUTPUT, `${rehearsed.at.replace(/[:.]/g, '-')}.json`)
  await writeFile(file, `${JSON.stringify(rehearsed, null, 2)}\n`, { mode: 0o600, flag: 'wx' })
  return path.relative(INVOKED, file)
}

async function main(argv: string[]): Promise<number> {
  const command = parseCommand(argv)
  if (command.kind === 'help') {
    console.log(HELP)
    return 0
  }
  if (command.kind === 'compare') {
    const [first, second] = await Promise.all(command.files.map(results))
    console.log(compare(first, second, [path.basename(command.files[0]), path.basename(command.files[1])]))
    return 0
  }
  const selection = select(SCRIPT, command.options)
  const { config, problems } = readServerConfig(process.env)
  if (!config) {
    throw new UsageError(`Write app/.env.local from terraform -chdir=infra output -json app_settings first (app/README.md):\n- ${problems.join('\n- ')}`)
  }
  const rehearsed = await rehearse(selection, command.options.runs, {
    config,
    token: azureToken,
    progress: (text) => process.stderr.write(text),
    onTool: (call) => {
      const name = Object.hasOwn(TOOL_NAMES, call.server) ? TOOL_NAMES[call.server] : call.server
      if (call.state !== 'running') process.stderr.write(`${name}${call.state === 'failed' ? ' (failed)' : ''} `)
    },
  })
  console.log(summarize(rehearsed))
  console.log(`Saved ${await save(rehearsed)}: it holds the replies, which can hold mail, so never commit it.`)
  return exitCode(rehearsed)
}

main(process.argv.slice(2)).then((code) => {
  process.exitCode = code
}, (error: unknown) => {
  console.error(error instanceof Error ? error.message : String(error))
  process.exitCode = error instanceof UsageError ? 2 : 1
})
