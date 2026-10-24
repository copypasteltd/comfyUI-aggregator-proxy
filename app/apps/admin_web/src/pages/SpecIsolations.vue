<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { api, readableError, revisionHeaders, time, unwrap } from '../api'
import type { components } from '../generated/api'
import { useSession } from '../session'

type Row = components['schemas']['SpecIsolationView']
type State = 'all' | 'isolated' | 'watching' | 'clear'
const session = useSession(), snapshot = ref<components['schemas']['SpecIsolationPage'] | null>(null)
const state = ref<State>('isolated'), page = ref(1), loading = ref(false), error = ref(''), updatedAt = ref<number | null>(null)
const selected = ref<Row | null>(null), mode = ref<'isolate' | 'resume'>('resume'), dialogOpen = ref(false), evidence = ref(''), validation = ref(''), busy = ref(false), actionError = ref('')
let controller: AbortController | undefined, timer: ReturnType<typeof setInterval> | undefined, disposed = false, mutationController: AbortController | undefined
let intent = '', token = ''
async function refresh() {
  if (disposed || document.hidden) return
  controller?.abort(); const request = new AbortController(); controller = request; loading.value = true
  try {
    const data = unwrap(await api.GET('/admin/api/v1/spec-isolations', { signal: request.signal, params: { query: { page: page.value, state: state.value } } }))
    if (!request.signal.aborted) { snapshot.value = data; error.value = ''; updatedAt.value = Date.now() / 1000 }
  } catch (e) { if (!request.signal.aborted) error.value = readableError(e) }
  finally { if (controller === request) loading.value = false }
}
function changed(delta = 0) { page.value = delta ? page.value + delta : 1; snapshot.value = null; updatedAt.value = null; void refresh() }
function open(row: Row) {
  selected.value = { ...row }; mode.value = row.is_active ? 'resume' : 'isolate'; evidence.value = ''; validation.value = ''; actionError.value = ''; intent = ''; token = ''; dialogOpen.value = true
}
async function apply() {
  const row = selected.value
  if (!row || busy.value) return
  const body = mode.value === 'isolate' ? { action: 'isolate' as const, expected_revision: row.revision, evidence_id: evidence.value.trim() } : { action: 'resume' as const, expected_revision: row.revision, validation_job_id: validation.value.trim() }
  if (JSON.stringify(body) !== intent) { intent = JSON.stringify(body); token = crypto.randomUUID() }
  busy.value = true; actionError.value = ''; mutationController = new AbortController()
  try {
    unwrap(await api.POST('/admin/api/v1/spec-isolations/{identifier}/actions', { signal: mutationController.signal, params: { path: { identifier: row.id } }, headers: { ...revisionHeaders(row.revision), 'Idempotency-Key': token }, body }))
    if (!disposed) { dialogOpen.value = false; intent = ''; token = ''; ElMessage.success(mode.value === 'resume' ? '规格已恢复派单' : '规格已隔离'); await refresh() }
  } catch (e) { if (!disposed) actionError.value = readableError(e) }
  finally { busy.value = false }
}
function visibility() {
  if (timer) clearInterval(timer); timer = undefined
  if (document.hidden) { controller?.abort(); return }
  void refresh(); timer = setInterval(() => { if (!loading.value) void refresh() }, 5000)
}
const causes = { comfy_oom: '显存不足', comfy_output_invalid: '产物不合格' }
function specification(row: Row) {
  const p = row.specification.parameters as Record<string, unknown>
  return [p.size, p.quality, p.seconds == null ? '' : `${p.seconds} 秒`, p.fps == null ? '' : `${p.fps} fps`, row.specification.reference_count ? `${row.specification.reference_count} 张参考图` : '', row.specification.source_image ? '源图' : '', row.specification.mask ? '蒙版' : '', p.strength == null ? '' : `强度 ${p.strength}`].filter(Boolean).join(' · ')
}
onMounted(() => { visibility(); document.addEventListener('visibilitychange', visibility) })
onUnmounted(() => { disposed = true; controller?.abort(); mutationController?.abort(); if (timer) clearInterval(timer); document.removeEventListener('visibilitychange', visibility) })
</script>

<template>
  <div class="page-heading"><div><p class="eyebrow">GENERATION / SPECIFICATIONS</p><h1>规格隔离</h1><p class="muted">暂停反复失败的生成规格，验证修复后恢复派单。</p></div><el-button :icon="Refresh" :loading="loading" @click="refresh">刷新隔离状态</el-button></div>
  <el-alert v-if="error" :title="error + (snapshot ? ' · 当前展示最后一次成功快照' : '')" type="error" :closable="false" show-icon/>
  <div class="snapshot-line"><span>更新于 {{ time(updatedAt) }}</span><span>每 5 秒刷新</span></div>
  <section class="panel"><div class="toolbar"><el-select v-model="state" aria-label="隔离状态" style="width: 220px" @change="changed()"><el-option label="已隔离" value="isolated"/><el-option label="观察中" value="watching"/><el-option label="正常 / 已恢复" value="clear"/><el-option label="全部状态" value="all"/></el-select></div>
    <el-table :data="snapshot?.data ?? []" v-loading="loading" table-layout="auto" :empty-text="!snapshot ? (error ? '读取失败，请刷新重试' : '正在读取隔离状态…') : '当前筛选没有规格记录'">
      <el-table-column label="状态" width="120"><template #default="{ row }"><el-tag :type="row.is_active ? 'danger' : row.failure_count ? 'warning' : 'success'">{{ row.is_active ? '已隔离' : row.failure_count ? '观察中' : row.cleared_validation_job_id ? '已恢复' : '正常' }}</el-tag></template></el-table-column>
      <el-table-column label="GPU / 发布版本" min-width="300"><template #default="{ row }"><span class="mono">{{ row.gpu_id }}</span><span class="id-line">{{ row.release_id }}</span></template></el-table-column>
      <el-table-column label="受影响规格" min-width="240"><template #default="{ row }"><strong>{{ specification(row) }}</strong><span class="id-line">{{ row.specification.task_type }}</span><span class="id-line">{{ row.spec_digest.slice(0, 16) }}…</span></template></el-table-column>
      <el-table-column label="失败记录" min-width="225"><template #default="{ row }"><span>{{ row.failure_count }} 次 · {{ row.last_error_code ? causes[row.last_error_code as keyof typeof causes] : '人工调查' }}</span><span class="id-line">最近失败 {{ time(row.last_failure_at) }}</span><router-link v-if="row.last_failure_job_id" :to="{ path: '/jobs', query: { job: row.last_failure_job_id } }" class="id-line">查看失败任务</router-link></template></el-table-column>
      <el-table-column label="处置记录" min-width="225"><template #default="{ row }"><span>{{ row.reason === 'repeated_failure' ? '重复失败触发' : row.reason === 'manual_investigation' ? '人工隔离' : '—' }}</span><span v-if="row.isolated_at" class="id-line">隔离于 {{ time(row.isolated_at) }}</span><router-link v-if="row.cleared_validation_job_id" :to="{ path: '/jobs', query: { job: row.cleared_validation_job_id } }" class="id-line">查看恢复验证任务</router-link></template></el-table-column>
      <el-table-column v-if="session.allows('channels:write')" label="操作" width="135" fixed="right"><template #default="{ row }"><el-button text :type="row.is_active ? 'primary' : 'danger'" @click="open(row)">{{ row.is_active ? '验证后恢复' : '人工隔离' }}</el-button></template></el-table-column>
    </el-table>
    <div class="pagination"><el-button size="small" :disabled="page === 1 || loading" @click="changed(-1)">上一页</el-button><span>第 {{ page }} 页</span><el-button size="small" :disabled="!snapshot?.has_more || loading" @click="changed(1)">下一页</el-button></div>
  </section>
  <p class="muted">同一物理 GPU 与 Workflow 版本内共享隔离。两次确认的显存不足或产物失败会触发隔离；恢复需要隔离后创建、同规格的成功验证任务。</p>
  <el-dialog v-model="dialogOpen" :title="mode === 'resume' ? '验证后恢复规格' : '人工隔离规格'" width="min(580px, 94vw)" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy">
    <p v-if="selected" class="muted">{{ specification(selected) }} · {{ selected.gpu_id }}</p>
    <el-alert v-if="actionError" :title="actionError" type="error" :closable="false" show-icon/>
    <el-form label-position="top" @submit.prevent="apply"><el-form-item v-if="mode === 'resume'" label="成功验证任务 ID" for="spec-validation"><el-input id="spec-validation" v-model="validation" placeholder="job_…" maxlength="36" :disabled="busy"/><p class="muted">先在 Workflow 管理中对该渠道、版本和规格完成显式验证，再填入任务编号。验证结果须在 24 小时内，设备和渠道配置仍一致。</p></el-form-item><el-form-item v-else label="调查记录编号" for="spec-evidence"><el-input id="spec-evidence" v-model="evidence" placeholder="例如 incident-20261023" maxlength="128" :disabled="busy"/><p class="muted">使用 8～128 位字母、数字及 . _ : -，便于审计查找。</p></el-form-item><div class="actions"><el-button :disabled="busy" @click="dialogOpen = false">返回</el-button><el-button native-type="submit" :type="mode === 'resume' ? 'primary' : 'danger'" :loading="busy">{{ mode === 'resume' ? '验证并恢复' : '确认隔离' }}</el-button></div></el-form>
  </el-dialog>
</template>
