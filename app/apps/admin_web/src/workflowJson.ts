// Keep ComfyUI seeds and other numeric tokens intact through local editing and HTTP.
const numberPattern = /-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/y
const wholeNumber = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$/
export const MAX_DRAFT_BYTES = 10 * 1024 * 1024
export class ExactNumber {
  readonly token: string
  constructor(token: string) { if (!wholeNumber.test(token)) throw new Error('JSON 数字格式无效'); this.token = token }
  toJSON(): never { throw new Error('工作流数字需要精确序列化') }
}
export class UnfinishedNumber {
  readonly text: string
  constructor(text: string) { this.text = text }
  toJSON(): never { throw new Error('请先修正未完成的数字') }
}
export type JsonValue = null | boolean | string | number | ExactNumber | UnfinishedNumber | JsonValue[] | JsonObject
export type JsonObject = { [key: string]: JsonValue }
export function isRecord(value: unknown): value is JsonObject { return value !== null && typeof value === 'object' && !Array.isArray(value) && !(value instanceof ExactNumber) && !(value instanceof UnfinishedNumber) }
function invalid(): never { throw new Error('JSON 格式无效，请检查引号、逗号和数字格式') }
function bounded(text: string, bytes: number) { if (text.length > bytes || new TextEncoder().encode(text).byteLength > bytes) throw new Error('发布包超过大小限制（草稿最多 10 MiB）') }
export function parseExactJson(text: string, maxBytes = MAX_DRAFT_BYTES, extraDepth = 0): JsonValue {
  bounded(text, maxBytes)
  let cursor = 0, count = 0
  const white = () => { while (' \t\r\n'.includes(text[cursor] ?? '\0')) cursor++ }
  const string = (): string => {
    const start = cursor++
    while (cursor < text.length) {
      const char = text[cursor++]!
      if (char === '"') { try { return JSON.parse(text.slice(start, cursor)) as string } catch { invalid() } }
      if (char.charCodeAt(0) < 32) invalid()
      if (char === '\\') {
        const escape = text[cursor++]
        if (escape === 'u') { if (!/^[a-fA-F0-9]{4}$/.test(text.slice(cursor, cursor + 4))) invalid(); cursor += 4 }
        else if (escape === undefined || !'"\\/bfnrt'.includes(escape)) invalid()
      }
    }
    return invalid()
  }
  const value = (depth: number): JsonValue => {
    if (++count > 100000 + (extraDepth ? 4096 : 0) || depth > 64 + extraDepth) throw new Error('发布包超过结构限制（最多 100000 项、64 层）')
    white(); const char = text[cursor]
    if (char === '"') return string()
    if (char === '{') {
      cursor++; const result: JsonObject = Object.create(null); white()
      if (text[cursor] === '}') { cursor++; return result }
      while (cursor < text.length) {
        if (text[cursor] !== '"') invalid()
        const key = string(); if (Object.hasOwn(result, key)) throw new Error('JSON 包含重复字段，请先消除重复键')
        white(); if (text[cursor++] !== ':') invalid()
        result[key] = value(depth + 1); white()
        const separator = text[cursor++]; if (separator === '}') return result; if (separator !== ',') invalid(); white()
      }
      return invalid()
    }
    if (char === '[') {
      cursor++; const result: JsonValue[] = []; white()
      if (text[cursor] === ']') { cursor++; return result }
      while (cursor < text.length) {
        result.push(value(depth + 1)); white()
        const separator = text[cursor++]; if (separator === ']') return result; if (separator !== ',') invalid()
      }
      return invalid()
    }
    for (const [literal, result] of [['true', true], ['false', false], ['null', null]] as const) if (text.startsWith(literal, cursor)) { cursor += literal.length; return result }
    numberPattern.lastIndex = cursor
    const match = numberPattern.exec(text)
    if (!match) return invalid()
    cursor = numberPattern.lastIndex
    return new ExactNumber(match[0])
  }
  const result = value(0); white(); if (cursor !== text.length) invalid(); return result
}

export function stringifyExactJson(input: unknown, indent = 0): string {
  const seen = new Set<object>(), parts: string[] = []
  let count = 0, units = 0
  const append = (part: string) => { units += part.length; if (units > MAX_DRAFT_BYTES + 2 * 1024 * 1024) throw new Error('发布包超过大小限制（草稿最多 10 MiB）'); parts.push(part) }
  const write = (value: unknown, depth: number): void => {
    if (++count > 104096 || depth > 68) throw new Error('发布包超过结构限制（最多 100000 项、64 层）')
    if (value instanceof ExactNumber) { append(value.token); return }
    if (value instanceof UnfinishedNumber) throw new Error('数值格式无效，请先修正未完成的数字')
    if (value === null || typeof value === 'string' || typeof value === 'boolean') { append(JSON.stringify(value)); return }
    if (typeof value === 'number' && Number.isFinite(value) && (!Number.isInteger(value) || Number.isSafeInteger(value))) { append(JSON.stringify(value)); return }
    if (typeof value !== 'object' || value === null || seen.has(value)) throw new Error('发布包含无效值或循环引用')
    seen.add(value)
    const array = Array.isArray(value)
    if (!array && ![null, Object.prototype].includes(Object.getPrototypeOf(value))) throw new Error('发布包含不支持的对象')
    if (array && (value.length > 104096 - count || Array.from({ length: value.length }, (_, i) => !Object.hasOwn(value, i)).some(Boolean))) throw new Error('发布包含过大或不连续的数组')
    const entries = array ? value.map((item, i) => [String(i), item] as const) : Object.entries(value)
    append(array ? '[' : '{')
    entries.forEach(([key, item], i) => { if (i) append(','); if (indent) append('\n' + ' '.repeat((depth + 1) * indent)); if (!array) append(JSON.stringify(key) + (indent ? ': ' : ':')); write(item, depth + 1) })
    if (entries.length && indent) append('\n' + ' '.repeat(depth * indent))
    append(array ? ']' : '}'); seen.delete(value)
  }
  write(input, 0)
  const result = parts.join(''); bounded(result, MAX_DRAFT_BYTES + 2 * 1024 * 1024); return result
}

export function exactObject(text: string, response = false): JsonObject {
  const value = parseExactJson(text, MAX_DRAFT_BYTES + (response ? 2 * 1024 * 1024 : 0), response ? 4 : 0)
  if (!isRecord(value)) throw new Error('草稿必须为 JSON 对象')
  return value
}
export function numericText(value: JsonValue | undefined): string { return value instanceof ExactNumber ? value.token : value instanceof UnfinishedNumber ? value.text : typeof value === 'number' ? String(value) : '' }
export function safeInteger(value: JsonValue | undefined, min = 0, max = Number.MAX_SAFE_INTEGER): number {
  const token = numericText(value), result = Number(token)
  if ((!(value instanceof ExactNumber) && typeof value !== 'number') || !/^-?(?:0|[1-9][0-9]*)$/.test(token) || !Number.isSafeInteger(result) || result < min || result > max) throw new Error('数值须为允许范围内的整数')
  return result
}
export function formatExactDraft(value: unknown): string {
  try { const text = stringifyExactJson(value, 2); bounded(text, MAX_DRAFT_BYTES); return text }
  catch { const text = stringifyExactJson(value); bounded(text, MAX_DRAFT_BYTES); return text }
}
