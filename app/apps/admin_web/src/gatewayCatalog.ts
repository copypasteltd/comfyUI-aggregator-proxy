import type { components } from './generated/api'
import { isRecord, parseExactJson, safeInteger, stringifyExactJson, type JsonValue } from './workflowJson.ts'

type Release = components['schemas']['CatalogReleaseView']
type Rule = components['schemas']['CatalogRule']
type Model = components['schemas']['ModelView']
// Display/edit projections of the frozen JSON; writes use generated API DTOs.
export type CatalogRow = {
  alias: string; task: string; defaults: string; rule: Rule
  bindings: { id: string; revision: number; project: string; release: string; channels: string[] }[]
}
export type DraftCatalogRow = CatalogRow & { groups: string }
export const taskNames: Record<string, string> = { text_to_image: '文生图', image_to_image: '图生图', references_to_image: '多参考图生图', text_to_video: '文生视频', references_to_video: '多参考图生视频', text_to_audio: 'Workflow 音频' }

function text(value: JsonValue | undefined): string { if (typeof value !== 'string') throw new Error('冻结目录字段无效'); return value }
function texts(value: JsonValue | undefined): string[] { if (!Array.isArray(value) || value.some(item => typeof item !== 'string')) throw new Error('冻结目录列表无效'); return value as string[] }
function ordered(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(ordered)
  if (isRecord(value)) return Object.fromEntries(Object.keys(value).sort().map(key => [key, ordered(value[key])]))
  return value
}
export function exactSummary(value: unknown): string { return stringifyExactJson(ordered(value)) }

export function catalogRows(release: Release | null): CatalogRow[] {
  if (!release) return []
  const parsed = parseExactJson(release.configuration_json, 512 * 1024)
  if (!isRecord(parsed) || parsed.format_version !== 'gateway-catalog-v1' || parsed.gateway_id !== release.gateway_id || parsed.instance_id !== release.instance_id || safeInteger(parsed.revision, 1, 2147483647) !== release.revision || !Array.isArray(parsed.models) || parsed.models.length > 256) throw new Error('冻结目录主体或版本无效')
  return parsed.models.map(value => {
    if (!isRecord(value) || !Array.isArray(value.model_bindings) || !isRecord(value.defaults)) throw new Error('冻结目录映射无效')
    const bindings = value.model_bindings.map(binding => {
      if (!isRecord(binding)) throw new Error('冻结目录绑定无效')
      return { id: text(binding.model_id), revision: safeInteger(binding.revision, 1, 2147483647), project: text(binding.project_id), release: text(binding.release_id), channels: texts(binding.channel_ids) }
    })
    return { alias: text(value.model), task: text(value.task_type), defaults: exactSummary(value.defaults), bindings, rule: { usage_account_id: text(value.usage_account_id), group_ids: texts(value.group_ids), price_version: text(value.price_version), unit_price: text(value.unit_price), model_bindings: bindings.map(binding => ({ model_id: binding.id, expected_revision: binding.revision })) } }
  })
}
export function modelRow(model: Model): DraftCatalogRow {
  return { alias: model.alias, task: model.task_type, defaults: exactSummary(model.defaults), bindings: [{ id: model.id, revision: model.revision, project: model.project_id, release: model.release_id, channels: [...model.channel_ids] }], rule: { usage_account_id: '', group_ids: [], price_version: '', unit_price: '1.00000000', model_bindings: [{ model_id: model.id, expected_revision: model.revision }] }, groups: '' }
}
export function positiveCatalogId(value: string): boolean { return /^[1-9][0-9]{0,18}$/.test(value) && BigInt(value) <= 9223372036854775807n }
export function priceText(value: string): string {
  if (!/^(?:0|[1-9][0-9]{0,5}|1000000)(?:\.[0-9]{1,8})?$/.test(value)) throw new Error('单价须为大于零、不超过 1000000 的金额，最多八位小数')
  const [whole, fraction = ''] = value.split('.')
  if (!/[1-9]/.test(value) || whole === '1000000' && /[1-9]/.test(fraction)) throw new Error('单价须为大于零、不超过 1000000 的金额')
  return whole + '.' + fraction.padEnd(8, '0')
}
export function publicationRules(rows: DraftCatalogRow[]): Rule[] {
  let bindingCount = 0, groupCount = 0
  const ids = new Set<string>(), aliases = new Set<string>()
  if (rows.length > 256) throw new Error('目录最多包含 256 个模型别名')
  const result = rows.map(row => {
    const groups = row.groups.split(/[\s,，]+/).filter(Boolean), account = row.rule.usage_account_id.trim(), version = row.rule.price_version.trim()
    if (!positiveCatalogId(account) || groups.length < 1 || groups.length > 1024 || groups.some(id => !positiveCatalogId(id)) || new Set(groups).size !== groups.length) throw new Error(row.alias + '：账户及分组 ID 必须是有效且不重复的正整数')
    if (!/^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,63}$/.test(version)) throw new Error(row.alias + '：请填写有效的价格版本（最多 64 字符）')
    if (aliases.has(row.alias) || row.rule.model_bindings.length < 1 || row.rule.model_bindings.length > 64) throw new Error(row.alias + '：模型别名重复或项目绑定数量无效')
    aliases.add(row.alias)
    for (const binding of row.rule.model_bindings) { if (ids.has(binding.model_id)) throw new Error('同一公开模型不能重复绑定'); ids.add(binding.model_id) }
    bindingCount += row.rule.model_bindings.length; groupCount += groups.length
    return { usage_account_id: account, group_ids: groups, price_version: version, unit_price: priceText(row.rule.unit_price.trim()), model_bindings: row.rule.model_bindings.map(binding => ({ ...binding })) }
  })
  if (bindingCount > 512 || groupCount > 4096) throw new Error('目录最多包含 512 个项目绑定、4096 个分组引用')
  return result
}
export function catalogDiff(before: CatalogRow[], after: CatalogRow[]) {
  const a = new Map(before.map(row => [row.alias, row])), b = new Map(after.map(row => [row.alias, row]))
  return [...new Set([...a.keys(), ...b.keys()])].sort().map(alias => {
    const previous = a.get(alias), next = b.get(alias)
    return { alias, previous, next, change: !previous ? '新增' : !next ? '移除' : exactSummary(previous) === exactSummary(next) ? '未变' : '修改' }
  })
}
