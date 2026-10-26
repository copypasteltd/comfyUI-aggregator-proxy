import { afterEach, describe, expect, it, vi } from 'vitest'
import { webcrypto } from 'node:crypto'
import { ensureRandomUUID } from './randomUuid'

afterEach(() => vi.unstubAllGlobals())

describe('HTTP-compatible request identifiers', () => {
  it('preserves the browser implementation when available', () => {
    const original = vi.fn(() => 'native-uuid')
    const source = { randomUUID: original }
    vi.stubGlobal('crypto', source)
    ensureRandomUUID()
    expect(source.randomUUID).toBe(original)
    expect(original).not.toHaveBeenCalled()
  })

  it('uses secure random bytes for version 4 identifiers when HTTP hides randomUUID', () => {
    const getRandomValues = vi.fn((bytes: Uint8Array) => webcrypto.getRandomValues(bytes))
    vi.stubGlobal('crypto', { getRandomValues })
    ensureRandomUUID()
    const identifiers = Array.from({ length: 128 }, () => crypto.randomUUID())
    expect(identifiers.every(value => /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(value))).toBe(true)
    expect(new Set(identifiers).size).toBe(identifiers.length)
    expect(getRandomValues).toHaveBeenCalledTimes(128)
    const installed = crypto.randomUUID
    ensureRandomUUID()
    expect(crypto.randomUUID).toBe(installed)
  })
})
