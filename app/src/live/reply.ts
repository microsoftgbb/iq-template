// Reads the agents' Markdown replies (an answer with source markers, then Sources and Steps) and renders their
// Markdown as safe HTML, with each marker as a chip in its IQ's colour.
import { IQ_NAMES } from './protocol.ts'
import type { IqKey, Marker, ParsedReply, SourceLine } from './types.ts'

// [D1], [F1 §2], [Web1], [W1]: a source prefix, its number and an optional section.
export const MARKER = /\[(Web|W|F|D)(\d+)(?:\s*§\s*([\w.-]+))?\]/
export const MARKER_KINDS: Readonly<Record<string, IqKey>> = { D: 'fabric', F: 'foundry', Web: 'web', W: 'work' }
const IQ_ORDER: readonly IqKey[] = ['fabric', 'foundry', 'work', 'web']
const markerPattern = () => new RegExp(MARKER.source, 'g')

// Sources or Steps alone on a line: plain, bold, as a heading or with a trailing colon.
const SECTION = /^ {0,3}(?:#{1,6}[ \t]+)?[*_]{0,2}[ \t]*(sources|steps)[ \t]*:?[ \t]*[*_]{0,2}[ \t]*:?[ \t]*$/i
const LIST_ITEM = /^[ \t]*(?:[-*+]|\d{1,3}[.)])[ \t]+/
const RULE = /^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$/
// A source line starts with its marker; agents sometimes drop the brackets in this list.
const SOURCE = /^[ \t]*(?:(?:[-*+]|\d{1,3}[.)])[ \t]+)?(?:\*\*|__)?\[?(Web|W|F|D)(\d+)(?:\s*§\s*[\w.-]+)?\]?(?:\*\*|__)?(?=[\s:|·–—-]|$)[ \t]*(?:[:|·–—-][ \t]*)?(.*)$/
const HTTP = /^https?:\/\/\S+$/i
const BARE_URL = /https?:\/\/[^\s<>()[\]]+/i
// A link target may hold one level of parentheses.
const TEXT_LINK = /\[([^\]]*)\]\(((?:[^()\s]|\([^()\s]*\))+)\)/g

type Section = 'sources' | 'steps'

function lines(text: string): string[] {
  return text.replace(/\r\n?/g, '\n').split('\n')
}

// A section that only lists, so an answer that happens to contain a Sources or Steps line keeps its text.
function listOnly(section: string[]): boolean {
  return section.every((line) => !line.trim() || RULE.test(line) || LIST_ITEM.test(line) || SOURCE.test(line) || /^[ \t]{2,}\S/.test(line))
}

function split(all: string[]): { answer: string[] } & Record<Section, string[]> {
  const headings = all.flatMap((line, index) => {
    const match = SECTION.exec(line)
    return match ? [{ index, kind: match[1].toLowerCase() as Section }] : []
  })
  const parts = { answer: all, sources: [] as string[], steps: [] as string[] }
  const last = headings.at(-1)
  if (!last) return parts
  parts.answer = all.slice(0, last.index)
  parts[last.kind] = all.slice(last.index + 1)
  const other = headings.findLast((heading) => heading.kind !== last.kind)
  if (other && listOnly(all.slice(other.index + 1, last.index))) {
    parts.answer = all.slice(0, other.index)
    parts[other.kind] = all.slice(other.index + 1, last.index)
  }
  return parts
}

function withoutTrailingRules(answer: string[]): string[] {
  let end = answer.length
  while (end > 0 && (!answer[end - 1].trim() || RULE.test(answer[end - 1]))) end -= 1
  return answer.slice(0, end)
}

// Unique by marker and section, in order of first appearance.
function markersIn(answer: string): Marker[] {
  const found: Marker[] = []
  const seen = new Set<string>()
  for (const match of answer.matchAll(markerPattern())) {
    const id = match[1] + match[2]
    const section = match[3]
    const key = section ? `${id}§${section}` : id
    if (seen.has(key)) continue
    seen.add(key)
    found.push({ id, kind: MARKER_KINDS[match[1]], ...(section ? { section } : {}) })
  }
  return found
}

function plain(text: string): { text: string; url?: string } {
  let url: string | undefined
  let out = text.replace(TEXT_LINK, (_whole, label: string, href: string) => {
    if (!url && HTTP.test(href)) url = href
    return label.trim() || href
  })
  out = out.replace(/<(https?:\/\/[^\s>]+)>/gi, '$1')
  url ??= BARE_URL.exec(out)?.[0].replace(/[.,;:!?'"]+$/, '')
  out = out.replace(/\*\*|__|`/g, '').replace(/\*([^*\s][^*]*?)\*/g, '$1').replace(/\s+/g, ' ').trim()
  return url ? { text: out, url } : { text: out }
}

function sourcesOf(section: string[]): SourceLine[] {
  const sources: SourceLine[] = []
  let current: SourceLine | undefined
  for (const line of section) {
    if (!line.trim() || RULE.test(line)) continue
    const match = SOURCE.exec(line)
    if (match) {
      const { text, url } = plain(match[3])
      const id = match[1] + match[2]
      current = sources.some((source) => source.id === id && source.text === text)
        ? undefined
        : { id, kind: MARKER_KINDS[match[1]], text, ...(url ? { url } : {}) }
      if (current) sources.push(current)
    } else if (current && !LIST_ITEM.test(line)) {
      // A wrapped line continues the source above it.
      const more = plain(line)
      current.text = `${current.text} ${more.text}`.trim()
      if (!current.url && more.url) current.url = more.url
    } else {
      current = undefined
    }
  }
  return sources
}

function stepsOf(section: string[]): string[] {
  const listed = section.some((line) => LIST_ITEM.test(line))
  const steps: string[] = []
  for (const line of section) {
    if (!line.trim() || RULE.test(line)) continue
    const item = LIST_ITEM.exec(line)
    const text = (item ? line.slice(item[0].length) : line).trim()
    if (item || !listed || !steps.length) steps.push(text)
    else steps[steps.length - 1] = `${steps[steps.length - 1]} ${text}`
  }
  return steps.map((step) => step.trim()).filter(Boolean)
}

// Tolerant: without a Sources or Steps heading, the whole reply is the answer.
export function parseReply(raw: string): ParsedReply {
  const parts = split(lines(raw))
  const answer = withoutTrailingRules(parts.answer).join('\n').trim()
  const sources = sourcesOf(parts.sources)
  const markers = markersIn(answer)
  const kinds = new Set<IqKey>([...markers, ...sources].map((item) => item.kind))
  return { answer, sources, steps: stepsOf(parts.steps), markers, usedIqs: IQ_ORDER.filter((iq) => kinds.has(iq)) }
}

// Rendering escapes the whole text first, then adds only its own markup. Finished elements are kept aside as
// tokens, so later patterns can neither see inside them nor split them: every element closes where it opens.
const ESCAPES: Readonly<Record<string, string>> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }
const OPEN = '\uE000'
const CLOSE = '\uE001'
const TOKEN = /\uE000(\d+)\uE001/g
const CODE = /`([^`]+)`/g
const LINK = /\[([^\]\uE000\uE001]+)\]\(((?:[^()\s]|\([^()\s]*\))+)\)/g
const BOLD = /\*\*(?=\S)(.+?)(?<=\S)\*\*|(?<![\w_])__(?=\S)(.+?)(?<=\S)__(?![\w_])/g
const ITALIC = /(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])|(?<![\w_])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![\w_])/g
const FENCE = /^ {0,3}(?:`{3,}|~{3,})/
const QUOTE = /^ {0,3}(?:&gt;[ \t]?)+/
const HEADING = /^ {0,3}#{1,6}[ \t]+(.*?)[ \t]*#*[ \t]*$/
const ITEM = /^( *)([-*+]|\d{1,3}[.)])[ \t]+(.*)$/
const TABLE_ROW = /^ {0,3}\|.*\|[ \t]*$/

type Keep = (html: string) => string

interface List {
  ordered: boolean
  indent: number
  items: { lines: string[]; child?: List }[]
}

function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (char) => ESCAPES[char])
}

function escaped(markdown: string): string {
  return escapeHtml(markdown.replace(/[\uE000\uE001]/g, '').replace(/\r\n?/g, '\n'))
}

function emphasis(text: string, keep: Keep): string {
  const italic = (value: string) => value.replace(ITALIC, (_whole, star?: string, underscore?: string) => keep(`<em>${star ?? underscore}</em>`))
  return italic(text.replace(BOLD, (_whole, star?: string, underscore?: string) => keep(`<strong>${italic(star ?? underscore ?? '')}</strong>`)))
}

function citation(prefix: string, number: string, section?: string): string {
  const id = prefix + number
  const kind = MARKER_KINDS[prefix]
  return `<button type="button" class="cite cite-${kind}" data-marker="${id}" title="${IQ_NAMES[kind]} source ${id}">${id}${section ? ` §${section}` : ''}</button>`
}

// Escaped text in, HTML with token placeholders out.
function inline(text: string, keep: Keep): string {
  let out = text.replace(CODE, (_whole, code: string) => keep(`<code>${code}</code>`))
  out = out.replace(LINK, (_whole, label: string, href: string) => (HTTP.test(href)
    ? keep(`<a href="${href}" target="_blank" rel="noopener noreferrer">${emphasis(label, keep)}</a>`)
    : label))
  out = out.replace(markerPattern(), (_whole, prefix: string, number: string, section?: string) => keep(citation(prefix, number, section)))
  return emphasis(out, keep)
}

function restore(html: string, tokens: readonly string[]): string {
  let out = html
  for (let pass = 0; pass <= tokens.length && out.includes(OPEN); pass += 1) {
    out = out.replace(TOKEN, (_whole, index: string) => tokens[Number(index)] ?? '')
  }
  return out
}

function renderList(list: List, keep: Keep): string {
  const tag = list.ordered ? 'ol' : 'ul'
  const items = list.items.map((item) => `<li>${inline(item.lines.join('<br>'), keep)}${item.child ? renderList(item.child, keep) : ''}</li>`)
  return `<${tag}>${items.join('')}</${tag}>`
}

// A table becomes a list of its rows, the header row in bold.
function renderTable(rows: readonly string[], keep: Keep): string {
  const cells = rows.map((row) => row.trim().replace(/^\||\|$/g, '').split('|').map((cell) => cell.trim()))
  const delimiter = (row?: string[]) => Boolean(row?.length && row.every((cell) => /^:?-+:?$/.test(cell)))
  const items = cells.flatMap((row, index) => {
    if (delimiter(row)) return []
    const text = inline(row.filter(Boolean).join(' · '), keep)
    return [index === 0 && delimiter(cells[1]) ? `<li><strong>${text}</strong></li>` : `<li>${text}</li>`]
  })
  return items.length ? `<ul>${items.join('')}</ul>` : ''
}

// Markdown to HTML that is safe for dangerouslySetInnerHTML: paragraphs, lists, line breaks, bold, italic, code,
// http(s) links and citation chips for markers. Nothing else passes.
export function renderMarkdown(markdown: string): string {
  const tokens: string[] = []
  const keep: Keep = (html) => `${OPEN}${tokens.push(html) - 1}${CLOSE}`
  const html: string[] = []
  let paragraph: string[] = []
  let table: string[] = []
  let list: List | undefined
  let fence: string[] | undefined
  let blank = false

  const flushParagraph = () => {
    if (paragraph.length) html.push(`<p>${inline(paragraph.join('<br>'), keep)}</p>`)
    paragraph = []
  }
  const flushTable = () => {
    if (table.length) html.push(renderTable(table, keep))
    table = []
  }
  const flushList = () => {
    if (list) html.push(renderList(list, keep))
    list = undefined
  }
  const flush = () => {
    flushParagraph()
    flushTable()
    flushList()
  }

  for (const original of escaped(markdown).split('\n')) {
    const line = original.replace(/^\t+/, (tabs) => '    '.repeat(tabs.length))
    if (fence) {
      if (FENCE.test(line)) {
        html.push(`<p><code>${fence.join('<br>')}</code></p>`)
        fence = undefined
      } else {
        fence.push(line.trim())
      }
      continue
    }
    if (FENCE.test(line)) {
      flush()
      fence = []
      continue
    }
    const text = line.replace(QUOTE, '')
    if (!text.trim()) {
      flushParagraph()
      flushTable()
      blank = true
      continue
    }
    const wasBlank = blank
    blank = false
    if (RULE.test(text)) {
      flush()
      continue
    }
    const heading = HEADING.exec(text)
    if (heading) {
      flush()
      if (heading[1]) html.push(`<p><strong>${inline(heading[1], keep)}</strong></p>`)
      continue
    }
    if (TABLE_ROW.test(text)) {
      flushParagraph()
      flushList()
      table.push(text)
      continue
    }
    flushTable()
    const item = ITEM.exec(text)
    if (item) {
      flushParagraph()
      const [, spaces, bullet, content] = item
      const ordered = /\d/.test(bullet)
      const last = list?.items.at(-1)
      if (list && last && spaces.length >= list.indent + 2) {
        last.child ??= { ordered, indent: spaces.length, items: [] }
        last.child.items.push({ lines: [content.trim()] })
      } else if (list && list.ordered === ordered) {
        list.items.push({ lines: [content.trim()] })
      } else {
        flushList()
        list = { ordered, indent: spaces.length, items: [{ lines: [content.trim()] }] }
      }
      continue
    }
    const last = list?.items.at(-1)
    if (list && last && (!wasBlank || /^ {2,}\S/.test(text))) {
      // A continuation line belongs to the deepest open item.
      const target = last.child?.items.at(-1) ?? last
      target.lines.push(text.trim())
      continue
    }
    flushList()
    paragraph.push(text.trim())
  }
  if (fence) html.push(`<p><code>${fence.join('<br>')}</code></p>`)
  flush()
  return restore(html.join(''), tokens)
}

// One line of Markdown without a paragraph, such as a step.
export function renderInline(markdown: string): string {
  const tokens: string[] = []
  const keep: Keep = (html) => `${OPEN}${tokens.push(html) - 1}${CLOSE}`
  const text = escaped(markdown).split('\n').map((line) => line.trim()).filter(Boolean).join('<br>')
  return restore(inline(text, keep), tokens)
}
