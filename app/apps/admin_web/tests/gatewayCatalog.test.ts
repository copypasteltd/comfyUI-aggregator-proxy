import assert from 'node:assert/strict'
import test from 'node:test'
import { catalogDiff, catalogRows, exactSummary, modelRow, positiveCatalogId, priceText, publicationRules } from '../src/gatewayCatalog.ts'
import type { components } from '../src/generated/api'

const model: components['schemas']['ModelView'] = { id: 'mdl_' + '1'.repeat(32), tenant_id: 'ten_' + '2'.repeat(32), project_id: 'prj_' + '3'.repeat(32), alias: 'demo-image', task_type: 'text_to_image', release_id: 'wfr_' + '4'.repeat(32), channel_ids: ['ch_' + '5'.repeat(32)], defaults: { quality: 'standard', size: '1024x1024' }, revision: 7, published: true }
function draft() { const row = modelRow(model); row.rule.usage_account_id = '9223372036854775807'; row.groups = '1,9223372036854775806'; row.rule.price_version = 'prices-v1'; return row }
function release(configuration_json: string): components['schemas']['CatalogReleaseView'] { return { gateway_id: 'gateway_' + '6'.repeat(32), instance_id: 'demo-instance', revision: 8, digest: 'a'.repeat(64), action: 'publish', source_revision: null, created_at: 1792880000, configuration_json } }
function frozen() {
  return JSON.stringify({ format_version: 'gateway-catalog-v1', gateway_id: 'gateway_' + '6'.repeat(32), instance_id: 'demo-instance', revision: 8, models: [{ model: model.alias, task_type: model.task_type, defaults: model.defaults, usage_account_id: '9223372036854775807', group_ids: ['1'], price_version: 'prices-v1', unit_price: '0.12345678', model_bindings: [{ model_id: model.id, revision: model.revision, project_id: model.project_id, release_id: model.release_id, channel_ids: model.channel_ids }] }] })
}
test('64-bit identifiers and eight-place prices survive the outgoing publication body', () => {
  const row = draft(); row.rule.unit_price = '0.12345678'
  const rules = publicationRules([row]), wire = JSON.parse(JSON.stringify(rules))[0]
  assert.equal(wire.usage_account_id, '9223372036854775807'); assert.deepEqual(wire.group_ids, ['1', '9223372036854775806']); assert.equal(wire.unit_price, '0.12345678'); assert.deepEqual(wire.model_bindings, [{ model_id: model.id, expected_revision: 7 }])
  assert.equal(row.rule.unit_price, '0.12345678')
})
test('rejects noncanonical and out-of-range IDs before they can be rounded', () => {
  for (const id of ['0', '-1', '01', '1e3', ' 1', '9223372036854775808', '99999999999999999999']) assert.equal(positiveCatalogId(id), false)
  assert.equal(positiveCatalogId('9007199254740993'), true); assert.equal(positiveCatalogId('9223372036854775807'), true)
})
test('money bounds use decimal strings without floating-point conversion', () => {
  assert.equal(priceText('0.00000001'), '0.00000001'); assert.equal(priceText('999999.99999999'), '999999.99999999'); assert.equal(priceText('1000000'), '1000000.00000000'); assert.equal(priceText('1.2'), '1.20000000')
  for (const price of ['0', '0.00000000', '-1', '1e3', 'NaN', '01.0', '1.123456789', '1000000.00000001', '1000001']) assert.throws(() => priceText(price))
})
test('publication uses the selected model revision and never mutates the form', () => {
  const row = draft(), snapshot = JSON.stringify(row), rules = publicationRules([row])
  rules[0]!.model_bindings[0]!.expected_revision = 99; rules[0]!.group_ids.push('3')
  assert.equal(JSON.stringify(row), snapshot)
})
test('duplicate bindings, aliases and groups are rejected; an empty catalog is intentional', () => {
  const row = draft(); row.groups = '1,1'; assert.throws(() => publicationRules([row])); row.groups = '1'
  assert.throws(() => publicationRules([row, structuredClone(row)]))
  const second = draft(); second.alias = 'other-alias'; assert.throws(() => publicationRules([row, second]))
  assert.deepEqual(publicationRules([]), [])
})
test('frozen view keeps exact numeric tokens and checks release identity', () => {
  const raw = frozen().replace('"quality":"standard"', '"quality":"standard","seed":18446744073709551615'), rows = catalogRows(release(raw))
  assert.ok(rows[0]!.defaults.includes('18446744073709551615')); assert.equal(rows[0]!.rule.model_bindings[0]!.expected_revision, 7)
  assert.throws(() => catalogRows({ ...release(raw), revision: 9 })); assert.throws(() => catalogRows({ ...release(raw), instance_id: 'other-instance' }))
  assert.throws(() => catalogRows(release(raw.replace('"revision":8', '"revision":8,"revision":8'))))
})
test('diff distinguishes withdrawal, repricing and unchanged mappings without key-order noise', () => {
  const rows = catalogRows(release(frozen())), next = structuredClone(rows)
  assert.equal(catalogDiff(rows, next)[0]!.change, '未变'); next[0]!.rule.unit_price = '0.25000000'; assert.equal(catalogDiff(rows, next)[0]!.change, '修改')
  assert.equal(catalogDiff(rows, [])[0]!.change, '移除'); assert.equal(catalogDiff([], rows)[0]!.change, '新增')
  assert.equal(exactSummary({ b: 1, a: null }), exactSummary({ a: null, b: 1 })); assert.notEqual(exactSummary({ a: false }), exactSummary({ a: 0 }))
})
