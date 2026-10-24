export function durationLabel(milliseconds: number | null | undefined) {
  if (milliseconds == null) return '未知'
  if (milliseconds < 1000) return `${milliseconds} 毫秒`
  if (milliseconds < 60000) return `${(milliseconds / 1000).toFixed(1)} 秒`
  const seconds = Math.floor(milliseconds / 1000)
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`
  return `${Math.floor(seconds / 3600)} 时 ${Math.floor(seconds % 3600 / 60)} 分`
}
