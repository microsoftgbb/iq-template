// Test helper: the renderer's output contract. Only these tags and attributes, links only to http(s) in a new tab,
// every element closed in order, and no other angle brackets.
import assert from 'node:assert/strict'

const ALLOWED: Record<string, readonly string[]> = {
  p: [], ul: [], ol: [], li: [], strong: [], em: [], code: [], br: [],
  a: ['href', 'target', 'rel'],
  button: ['type', 'class', 'data-marker', 'title'],
}

export function assertSafe(html: string): void {
  const open: string[] = []
  for (const [, closing, name, attributes] of html.matchAll(/<(\/?)([^\s/>]+)([^>]*)>/g)) {
    assert.ok(Object.hasOwn(ALLOWED, name), `<${name}> in ${html}`)
    if (closing) {
      assert.equal(open.pop(), name, html)
      continue
    }
    assert.equal(attributes.replace(/\s+[\w-]+="[^"<>]*"/g, ''), '', `attributes of <${name}> in ${html}`)
    for (const [, attribute] of attributes.matchAll(/([\w-]+)="/g)) assert.ok(ALLOWED[name].includes(attribute), `${attribute} on <${name}>`)
    if (name === 'a') assert.match(attributes, /^ href="https?:\/\/[^"]+" target="_blank" rel="noopener noreferrer"$/)
    if (name === 'button') assert.match(attributes, /^ type="button" class="cite cite-(?:fabric|foundry|web|work)" data-marker="(?:Web|W|F|D)\d+" title="[A-Za-z0-9 ]+"$/)
    if (name !== 'br') open.push(name)
  }
  assert.deepEqual(open, [], html)
  // The text between the allowed tags holds no angle bracket of its own.
  for (const text of html.split(/<\/?[a-z]+(?: [^<>]*)?>/)) assert.doesNotMatch(text, /[<>]/, html)
}
