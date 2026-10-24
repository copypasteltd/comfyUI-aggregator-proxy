import assert from 'node:assert/strict'
import test from 'node:test'
import { ExactNumber, UnfinishedNumber, MAX_DRAFT_BYTES, exactObject, formatExactDraft, isRecord, numericText, parseExactJson, safeInteger, stringifyExactJson } from '../src/workflowJson.ts'

test('64-bit seeds, long decimals and exponents stay numeric and exact in edited HTTP bodies', () => {
  const draft = exactObject('{"workflow_api":{"1":{"inputs":{"seed":18446744073709551615,"negative":-9223372036854775808,"scale":0.12345678901234567890123456789,"tiny":1.234500e-99,"huge":1e400}}},"name":"old"}')
  draft.name = 'edited'
  const body = stringifyExactJson({ expected_revision: 1, draft })
  for (const token of ['18446744073709551615', '-9223372036854775808', '0.12345678901234567890123456789', '1.234500e-99', '1e400']) assert.ok(body.includes(':' + token))
  assert.ok(!body.includes('"token"'))
  assert.equal(safeInteger(exactObject(body).expected_revision, 1), 1)
})

test('prototype-like keys remain ordinary data, including a literal toJSON key', () => {
  const value = exactObject('{"__proto__":{"polluted":true},"constructor":{"prototype":{"admin":true}},"toJSON":"data","a/b~c":"\\u0000\\n字\\\""}')
  assert.equal(Object.getPrototypeOf(value), null)
  assert.equal(({} as Record<string, unknown>).polluted, undefined)
  const round = exactObject(stringifyExactJson(value))
  assert.equal((round.__proto__ as Record<string, unknown>).polluted, true)
  assert.equal(round.toJSON, 'data')
  assert.equal(round['a/b~c'], '\0\n字"')
})

for (const source of ['', '{', '[1,]', '{"a":1,}', '{"a":1 "b":2}', '{"a":1,"a":2}', '{"a":1,"\\u0061":2}', '01', '+1', '1.', '1e', '.5', '0x10', 'NaN', 'Infinity', 'undefined', 'true false', '"\\x20"', '"\\u0xx0"', '"unfinished', '"line\nfeed"', '[,,]', '{a:1}', 'null trailing']) {
  test('rejects invalid or ambiguous JSON: ' + JSON.stringify(source), () => assert.throws(() => parseExactJson(source)))
}

test('JSON whitespace, escapes, arrays and null use JSON semantics', () => {
  const value = parseExactJson(' \r\n\t[true,false,null,"\\/\\b\\f\\r\\t\\u4e2d",0,-0,1.00,1e+3]  ')
  assert.equal(stringifyExactJson(value), '[true,false,null,"/\\b\\f\\r\\t中",0,-0,1.00,1e+3]')
})

test('size limits count UTF-8 bytes and reject oversized input before decoding', () => {
  assert.throws(() => parseExactJson('"' + '字'.repeat(350) + '"', 1024), /大小限制/)
  assert.throws(() => parseExactJson('"' + 'x'.repeat(MAX_DRAFT_BYTES) + '"'), /大小限制/)
})

test('64-level and 100000-value budgets fail explicitly without a partial object', () => {
  assert.throws(() => parseExactJson('['.repeat(65) + '0' + ']'.repeat(65)), /结构限制/)
  const accepted = parseExactJson('[' + Array(99999).fill('0').join(',') + ']') as unknown[]
  assert.equal(accepted.length, 99999)
  assert.throws(() => parseExactJson('[' + Array(100000).fill('0').join(',') + ']'), /结构限制/)
})

test('unsafe native numbers, non-JSON objects, sparse arrays and cycles never silently change values', () => {
  for (const value of [Infinity, NaN, 9007199254740992, undefined, 1n, new Date(), new Map(), Array(3)]) assert.throws(() => stringifyExactJson(value))
  const cyclic: unknown[] = []; cyclic.push(cyclic)
  assert.throws(() => stringifyExactJson(cyclic))
  const shared = { x: true }
  assert.equal(stringifyExactJson([shared, shared]), '[{"x":true},{"x":true}]')
})

test('unfinished numeric fields survive local state while blocking every serialization path', () => {
  const value = new UnfinishedNumber('1e-')
  assert.equal(numericText(value), '1e-'); assert.equal(isRecord(value), false)
  assert.throws(() => stringifyExactJson({ presets: [{ values: { seed: value } }] }), /未完成/)
  assert.throws(() => JSON.stringify(value))
  assert.throws(() => JSON.stringify(new ExactNumber('18446744073709551615')))
})

test('protocol integers must be safe while arbitrary graph integers keep their token', () => {
  assert.throws(() => safeInteger(new ExactNumber('18446744073709551615')))
  assert.throws(() => safeInteger(new ExactNumber('-1')))
  for (const token of ['1.0000000000000001','9007199254740991.1','1e-9000','1e0']) assert.throws(() => safeInteger(new ExactNumber(token)))
  assert.throws(() => safeInteger(new UnfinishedNumber('7')))
  assert.equal(safeInteger(new ExactNumber('7'), 1, 10), 7)
  assert.equal(numericText(new ExactNumber('18446744073709551615')), '18446744073709551615')
})

test('pretty printing falls back to compact JSON rather than dropping a near-limit field', () => {
  const value = { a: 'x'.repeat(MAX_DRAFT_BYTES - 8) }
  const text = formatExactDraft(value)
  assert.ok(new TextEncoder().encode(text).byteLength <= MAX_DRAFT_BYTES)
  assert.equal(exactObject(text).a, value.a)
})
