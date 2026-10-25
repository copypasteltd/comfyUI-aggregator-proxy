<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ArrowLeft, Refresh } from '@element-plus/icons-vue'
import { api, readableError, time, unwrap } from '../api'
import type { components } from '../generated/api'

type Snapshot = components['schemas']['KeyAccessSnapshot']
type Result = 'all' | 'success' | 'denied' | 'error'
const route = useRoute(), snapshot = ref<Snapshot | null>(null), selected = ref<Result>('all')
const error = ref(''), loading = ref(false), cursors = ref<(string | undefined)[]>([undefined]), page = ref(0)
let controller: AbortController | undefined, suspendedPage: number | undefined
const resultLabels = { success: '成功', denied: '拒绝', error: '错误 / 未完成' }
const completionLabels = { completed: '已完成响应', disconnected: '连接中断', failed: '响应未完成' }

async function fetchPage(target = page.value) {
  controller?.abort()
  if (document.hidden) { suspendedPage = target; loading.value = false; return }
  const request = new AbortController(); controller = request
  const key = String(route.params.identifier), result = selected.value
  loading.value = true; suspendedPage = target
  try {
    const data = unwrap(await api.GET('/admin/api/v1/api-keys/{identifier}/access-events', {
      signal: request.signal,
      params: { path: { identifier: key }, query: { result, before: cursors.value[target] } },
    }))
    if (request.signal.aborted || controller !== request) return
    snapshot.value = data; page.value = target
    if (target === 0) cursors.value[0] = data.first_cursor
    error.value = ''
  } catch (e) { if (!request.signal.aborted && controller === request) error.value = readableError(e) }
  finally { if (controller === request) { loading.value = false; if (!request.signal.aborted) suspendedPage = undefined } }
}
function restart() {
  cursors.value = [undefined]; page.value = 0; snapshot.value = null
  error.value = ''; void fetchPage()
}
function next() {
  if (!snapshot.value?.next_cursor || page.value >= 99 || loading.value) return
  cursors.value = [...cursors.value.slice(0, page.value + 1), snapshot.value.next_cursor]
  void fetchPage(page.value + 1)
}
function previous() { if (page.value > 0 && !loading.value) void fetchPage(page.value - 1) }
function visibility() {
  if (document.hidden) { controller?.abort(); loading.value = false }
  else if (suspendedPage !== undefined) void fetchPage(suspendedPage)
}
function bytes(value: number) { return value < 1024 ? `${value} B` : value < 1048576 ? `${(value / 1024).toFixed(1)} KiB` : `${(value / 1048576).toFixed(1)} MiB` }
watch([() => route.params.identifier, selected], restart, { immediate: true })
onMounted(() => document.addEventListener('visibilitychange', visibility))
onUnmounted(() => { controller?.abort(); document.removeEventListener('visibilitychange', visibility) })
</script>

<template>
  <div class="page-heading access-heading">
    <div><p class="eyebrow li-lab">ACCESS CONTROL / REQUEST HISTORY</p><h1>Key 访问记录</h1><p class="muted">查看生成与内容 API 的调用结果，核对拒绝原因和中断响应。</p></div>
    <div class="toolbar"><router-link to="/keys"><el-button :icon="ArrowLeft">返回 API 密钥</el-button></router-link><el-button :icon="Refresh" :loading="loading" @click="restart()">刷新记录</el-button></div>
  </div>
  <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon/>
  <el-alert v-if="error && snapshot" title="以下是上次成功取得的记录，请刷新后再确认。" type="warning" :closable="false"/>
  <el-alert v-if="snapshot && (snapshot.capture_health.failed || snapshot.capture_health.overloaded)" title="访问记录可能不完整：当前服务曾发生记录写入失败或容量不足，请核对服务日志。" type="warning" :closable="false" show-icon/>
  <section v-if="snapshot" class="panel li-card">
    <div class="panel-heading access-subject"><div><h2>{{ snapshot.key_name }}</h2><p class="id-line break-id">{{ snapshot.key_id }}</p></div><el-tag :type="snapshot.deleted_at ? 'info' : snapshot.enabled ? 'success' : 'warning'">{{ snapshot.deleted_at ? '已删除 · 历史保留' : snapshot.enabled ? 'Key 已启用' : 'Key 已停用' }}</el-tag></div>
  </section>
  <div class="snapshot-line access-snapshot"><span>{{ snapshot ? `查询截止 ${time(snapshot.snapshot_at)} · 保留 ${snapshot.retention_days} 天` : '尚未取得访问记录' }}</span><span>每页最多 50 条</span></div>
  <section class="panel li-card">
    <div class="panel-heading access-filter"><label for="access-result">调用结果</label><el-select id="access-result" v-model="selected" aria-label="调用结果" style="width: 180px"><el-option label="全部结果" value="all"/><el-option label="成功" value="success"/><el-option label="拒绝" value="denied"/><el-option label="错误 / 未完成" value="error"/></el-select></div>
    <el-table :data="snapshot?.data ?? []" v-loading="loading" :empty-text="loading ? '正在读取访问记录' : error ? '访问记录读取失败，请刷新重试' : snapshot ? '该筛选暂无访问记录' : '尚未取得访问记录'" table-layout="auto">
      <el-table-column label="时间 / 请求 ID" min-width="250"><template #default="{ row }"><span>{{ time(row.started_at) }}</span><span class="id-line break-id">{{ row.request_id }}</span></template></el-table-column>
      <el-table-column label="接口" min-width="260"><template #default="{ row }"><strong class="mono">{{ row.method }}</strong><span class="id-line access-route">{{ row.route }}</span><small class="muted">{{ row.entry === 'gateway' ? '网关接入' : '直接调用' }}</small></template></el-table-column>
      <el-table-column label="结果" min-width="190"><template #default="{ row }"><el-tag :type="row.result === 'success' ? 'success' : row.result === 'denied' ? 'warning' : 'danger'" size="small">{{ row.status_code ?? '无状态码' }} · {{ resultLabels[row.result as keyof typeof resultLabels] }}</el-tag><span v-if="row.error_code" class="id-line">{{ row.error_code }}</span><span v-if="row.completion !== 'completed'" class="id-line">{{ completionLabels[row.completion as keyof typeof completionLabels] }}</span></template></el-table-column>
      <el-table-column label="来源地址" min-width="200"><template #default="{ row }"><span class="mono">{{ row.client_ip ?? row.peer_ip ?? '未知' }}</span><span v-if="row.entry === 'gateway'" class="id-line">网关连接 {{ row.peer_ip ?? '未知' }}</span></template></el-table-column>
      <el-table-column label="耗时 / 传输" min-width="180"><template #default="{ row }"><span class="mono">{{ row.duration_milliseconds }} ms</span><span class="id-line">接收 {{ bytes(row.request_bytes) }} / 发出 {{ bytes(row.response_bytes) }}</span></template></el-table-column>
    </el-table>
    <div class="pagination"><el-button size="small" :disabled="page === 0 || loading" @click="previous">上一页</el-button><span>第 {{ page + 1 }} 页</span><el-button size="small" :disabled="!snapshot?.next_cursor || loading || page >= 99" @click="next">下一页</el-button></div>
  </section>
  <p class="muted access-footnote">记录只保留接口模板和调用元数据。字节计数表示服务已处理的传输量；响应中断时，请结合原任务状态核对。{{ page >= 99 ? '已达到本次浏览上限，请缩小筛选或刷新。' : '' }}</p>
</template>

<style scoped>
.access-subject, .access-filter { flex-wrap: wrap; }
.access-filter { justify-content: flex-start; font-size: 13px; }
.access-snapshot { flex-wrap: wrap; gap: 8px; }
.access-route { overflow-wrap: anywhere; font-size: 11px; }
.access-footnote { line-height: 1.8; }
@media(max-width: 700px) { .access-heading { flex-direction: column; } .access-heading .toolbar { justify-content: flex-start; } }
</style>
