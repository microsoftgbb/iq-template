import assert from 'node:assert/strict'
import test from 'node:test'
import { renderInline, renderMarkdown } from '../src/live/reply.ts'
import { assertSafe } from './safe-html.ts'

function rendered(markdown: string): string {
  const html = renderMarkdown(markdown)
  assertSafe(html)
  return html
}

const chip = (id: string, kind: string, iq: string, text = id) =>
  `<button type="button" class="cite cite-${kind}" data-marker="${id}" title="${iq} source ${id}">${text}</button>`

test('paragraphs, line breaks, lists, emphasis and code', () => {
  assert.equal(
    rendered('First line\nsecond line\n\n- one\n- two **bold**, *italic*, __strong__, _em_ and `code`\n\n1. first\n2. second'),
    '<p>First line<br>second line</p><ul><li>one</li><li>two <strong>bold</strong>, <em>italic</em>, <strong>strong</strong>, <em>em</em> and <code>code</code></li></ul><ol><li>first</li><li>second</li></ol>',
  )
  assert.equal(rendered('1. one\n\n2. two\n   - a\n   - b\n3. three'), '<ol><li>one</li><li>two<ul><li>a</li><li>b</li></ul></li><li>three</li></ol>')
  assert.equal(rendered('Check:\n- the seal\n- the bearings\n  on Pump B'), '<p>Check:</p><ul><li>the seal</li><li>the bearings<br>on Pump B</li></ul>')
  assert.equal(rendered('demo_asset_002, 2 * 3 * 4 and 48.0 − 25.0'), '<p>demo_asset_002, 2 * 3 * 4 and 48.0 − 25.0</p>')
  assert.equal(rendered(''), '')
})

test('markers become IQ-coloured chips with their section; unknown markers stay text', () => {
  assert.equal(
    rendered('Status [D1], rule [F1 §2] and mail [W1].'),
    `<p>Status ${chip('D1', 'fabric', 'Fabric IQ')}, rule ${chip('F1', 'foundry', 'Foundry IQ', 'F1 §2')} and mail ${chip('W1', 'work', 'Work IQ')}.</p>`,
  )
  const html = rendered('[Web1] [D2] [F3 §4] [W9] `[W8]` [L1] [C1] [U1]')
  for (const [id, kind] of [['Web1', 'web'], ['D2', 'fabric'], ['F3', 'foundry'], ['W9', 'work']]) {
    assert.ok(html.includes(`class="cite cite-${kind}" data-marker="${id}"`), id)
  }
  assert.ok(html.includes('<code>[W8]</code>'), 'no chips inside code')
  assert.ok(html.includes('[L1] [C1] [U1]'), 'markers of other scenarios stay plain text')
})

test('only http and https links survive, opening in a new tab', () => {
  assert.equal(
    rendered('See [the guide](https://example.com/pumps) and [the wiki](https://en.wikipedia.org/wiki/Pump_(disambiguation)).'),
    '<p>See <a href="https://example.com/pumps" target="_blank" rel="noopener noreferrer">the guide</a> and <a href="https://en.wikipedia.org/wiki/Pump_(disambiguation)" target="_blank" rel="noopener noreferrer">the wiki</a>.</p>',
  )
  for (const target of ['javascript:alert(1)', 'JaVaScRiPt:alert(1)', 'data:text/html;base64,PHNjcmlwdD4=', '//evil.example', 'vbscript:msgbox', 'mailto:a@example.org', 'https:']) {
    const html = rendered(`[click](${target})`)
    assert.equal(html, '<p>click</p>', target)
  }
  const quoted = rendered('[x](https://ok.example/a"onmouseover="alert(1))')
  assert.ok(quoted.startsWith('<p><a href="https://ok.example/a&quot;onmouseover=&quot;alert(1)" target="_blank"'), quoted)
})

test('raw HTML, script tags and event handlers are escaped, never passed through', () => {
  assert.equal(rendered('<script>alert(1)</script>'), '<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>')
  assert.equal(rendered('<img src=x onerror=alert(1)>'), '<p>&lt;img src=x onerror=alert(1)&gt;</p>')
  assert.equal(rendered('"><svg onload=alert(1)> & \'quoted\''), '<p>&quot;&gt;&lt;svg onload=alert(1)&gt; &amp; &#39;quoted&#39;</p>')
  assert.equal(rendered('[W1" onclick="x] [F1 §3.2"><script>]'), '<p>[W1&quot; onclick=&quot;x] [F1 §3.2&quot;&gt;&lt;script&gt;]</p>')
  assert.equal(rendered('a \uE0000\uE001 b'), '<p>a 0 b</p>', 'placeholder characters are dropped')
  for (const attack of ['<a href="javascript:alert(1)">x</a>', '<iframe src="https://evil.example"></iframe>', '<style>*{}</style>', '**[x](javascript:alert(1))**', '[[good: x]]']) {
    assert.doesNotMatch(rendered(attack), /<(?!\/?(?:p|strong)\b)/, attack)
  }
})

test('headings, quotes, tables, rules and code fences degrade to the allowed tags', () => {
  assert.equal(
    rendered('# Title\n\n> quoted line\n\n---\n\n| Asset | Status |\n| --- | ---: |\n| Pump B | Maintenance |\n\n```\ncode <b>\n```'),
    '<p><strong>Title</strong></p><p>quoted line</p><ul><li><strong>Asset · Status</strong></li><li>Pump B · Maintenance</li></ul><p><code>code &lt;b&gt;</code></p>',
  )
})

test('renderInline renders one line without a paragraph', () => {
  assert.equal(
    renderInline('Asked the ontology [D1] for **Pump B**\nthen <b>'),
    `Asked the ontology ${chip('D1', 'fabric', 'Fabric IQ')} for <strong>Pump B</strong><br>then &lt;b&gt;`,
  )
})

test('the safety check itself rejects unsafe HTML', () => {
  for (const html of [
    '<img src=x>',
    '<p onclick="x()">a</p>',
    '<a href="javascript:x" target="_blank" rel="noopener noreferrer">x</a>',
    '<a href="https://ok.example">no new tab</a>',
    '<p><strong>x</p></strong>',
    '<p>a > b</p>',
    '<button type="button" class="cite cite-work" data-marker="W1" title="Work IQ source W1" onmouseover="x()">W1</button>',
    '<button type="button" class="cite cite-work" data-marker="W1" title="x&quot;">W1</button>',
    '<mark class="mark-good">x</mark>',
  ]) {
    assert.throws(() => assertSafe(html), assert.AssertionError, `accepted: ${html}`)
  }
})
