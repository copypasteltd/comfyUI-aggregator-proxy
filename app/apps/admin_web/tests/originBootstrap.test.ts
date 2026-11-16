import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'
import { webcrypto } from 'node:crypto'

function browser() {
  const sent: any[] = []
  class Element { tagName = 'IMG'; setAttribute() {} }
  class XHR {
    headers: Record<string, string> = {}; method = ''; url: any
    open(method: string, url: any) { this.method = method; this.url = url; this.headers = {} }
    setRequestHeader(name: string, value: string) { this.headers[name.toLowerCase()] = value }
    send() { sent.push(this) }
  }
  const storage = { getItem: () => null, setItem() {}, removeItem() {}, length: 0, key: () => null }
  const location = new URL('https://example.test/origin/rtx4090/')
  const window = {
    __originConfig: { prefix: '/origin/rtx4090/', csrf: 'synthetic-csrf', namespace: 'key-device' },
    fetch: async (url: any, options: any) => { sent.push({ url: String(url.url ?? url), options }); return { status: 200 } },
    WebSocket: class {}, localStorage: storage, sessionStorage: storage,
  }
  const scope = { window, location, document: { baseURI: location.href, readyState: 'loading', addEventListener() {} },
    Element, HTMLImageElement: class {}, HTMLMediaElement: class {}, HTMLSourceElement: class {}, HTMLScriptElement: class {}, HTMLLinkElement: class {},
    XMLHttpRequest: XHR, URL, Headers, Request, crypto: webcrypto, setTimeout, clearTimeout }
  vm.runInNewContext(readFileSync(new URL('../public/origin-bootstrap.js', import.meta.url), 'utf8'), scope)
  return { window, sent, XHR }
}

test('native fetch preserves source filenames and supplies guarded write identity', async () => {
  const { window, sent } = browser()
  await window.fetch('/api/userdata/workflows%2F%E4%B8%AD%E6%96%87.json', { method: 'POST', body: '{}' })
  assert.equal(sent[0].url, 'https://example.test/origin/rtx4090/api/userdata/workflows%2F%E4%B8%AD%E6%96%87.json')
  assert.equal(sent[0].options.headers.get('X-Origin-CSRF'), 'synthetic-csrf')
  assert.match(sent[0].options.headers.get('Idempotency-Key'), /^[a-f0-9]{32}$/)
  await window.fetch('/api/userdata/workflows%2Fa.json', { method: 'DELETE', headers: { 'Idempotency-Key': 'original-saved-operation' } })
  assert.equal(sent[1].options.headers.get('Idempotency-Key'), 'original-saved-operation')
  await window.fetch('/api/userdata?dir=workflows', {})
  assert.equal(sent[2].options.headers.has('Idempotency-Key'), false)
  await window.fetch('https://other.test/api/userdata/a.json', { method: 'POST' })
  assert.equal(sent[3].options.headers, undefined)
})

test('native XHR source save and move have CSRF and one modification identity', () => {
  const { XHR } = browser()
  const request = new XHR()
  request.open('POST', '/api/userdata/workflows%2Fa.json/move/workflows%2Fb.json')
  request.send()
  assert.equal(request.headers['x-origin-csrf'], 'synthetic-csrf')
  assert.match(request.headers['idempotency-key'], /^[a-f0-9]{32}$/)
  request.open('DELETE', '/api/userdata/workflows%2Fb.json')
  request.setRequestHeader('Idempotency-Key', 'original-saved-operation')
  request.send()
  assert.equal(request.headers['idempotency-key'], 'original-saved-operation')
})
