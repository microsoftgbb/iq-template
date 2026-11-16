// Server-sent events from a fetch body, as the Responses API streams them: "event:" and "data:" fields, one event
// per blank-line-terminated block. Comments and unknown fields are ignored.
export interface SseEvent {
  event: string
  data: string
}

export async function* readSse(body: ReadableStream<Uint8Array>): AsyncGenerator<SseEvent> {
  const reader = body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  let event = ''
  let data: string[] = []
  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (value) buffer += value
      if (done) buffer += '\n\n'
      let newline = buffer.indexOf('\n')
      while (newline >= 0) {
        const line = buffer.slice(0, newline).replace(/\r$/, '')
        buffer = buffer.slice(newline + 1)
        newline = buffer.indexOf('\n')
        if (!line) {
          if (data.length) yield { event, data: data.join('\n') }
          event = ''
          data = []
          continue
        }
        if (line.startsWith(':')) continue
        const colon = line.indexOf(':')
        const field = colon < 0 ? line : line.slice(0, colon)
        const content = colon < 0 ? '' : line.slice(colon + 1).replace(/^ /, '')
        if (field === 'event') event = content
        else if (field === 'data') data.push(content)
      }
      if (done) return
    }
  } finally {
    // Stops the upstream response too when the caller stops reading early.
    await reader.cancel().catch(() => undefined)
  }
}
