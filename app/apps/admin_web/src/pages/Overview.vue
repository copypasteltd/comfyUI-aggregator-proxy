<script setup lang="ts">
import { computed } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { api, time, unwrap } from '../api'
import { queueStates, useQueueRefresh } from '../queue'
import QueueFilters from '../components/QueueFilters.vue'
import OperationalStatistics from '../components/OperationalStatistics.vue'

const { value: snapshot, error, loading, connected, page, filters, refresh, apply, goPage } = useQueueRefresh(async (query, signal) => unwrap(await api.GET('/admin/api/v1/queues/snapshot', { signal, params: { query } })), true)
const gpuRows = computed(() => {
  const result = new Map<string, { id: string; channels: string[]; occupied: boolean; uncertain: boolean }>()
  for (const c of snapshot.value?.channels ?? []) {
    const item = result.get(c.gpu_id) ?? { id: c.gpu_id, channels: [], occupied: false, uncertain: false }
    item.channels.push(c.name); item.occupied ||= c.gpu_occupied; item.uncertain ||= c.gpu_occupancy_uncertain; result.set(c.gpu_id, item)
  }
  return [...result.values()]
})
</script>

<template>
  <div class="page-heading"><div><p class="eyebrow">OPERATIONS / OVERVIEW</p><h1>运行总览</h1><p class="muted">全局任务状态与物理 GPU 占用</p></div><el-button :icon="Refresh" :loading="loading" @click="refresh">刷新快照</el-button></div>
  <QueueFilters @apply="apply"/>
  <el-alert v-if="error" :title="error + (snapshot ? ' · 当前展示最后一次成功快照' : '')" type="error" :closable="false" show-icon/>
  <div class="snapshot-line"><span><i class="status-dot" :class="{ amber: !connected }"/> {{ connected ? '更新连接正常' : '每 5 秒轮询' }}</span><span>快照时间 {{ time(snapshot?.snapshot_at) }}</span></div>
  <div class="metric-grid retry-metrics"><section class="metric"><span>全局未分配任务</span><strong>{{ snapshot?.global_unassigned_queued ?? '—' }}</strong><small>符合筛选条件的唯一任务</small></section><section class="metric"><span>等待内部重试</span><strong>{{ snapshot?.states.retry_wait ?? 0 }}</strong><small>到期后进入分配，暂未占用生成槽位</small></section><section class="metric"><span>未结束任务</span><strong>{{ snapshot?.unfinished ?? '—' }}</strong><small>符合筛选，含生成、上传与核对</small></section><section class="metric"><span>占用中的物理 GPU</span><strong>{{ snapshot?.occupied_physical_gpus ?? '—' }}</strong><small>实际全局总量，同卡多渠道合并计数</small></section></div>
  <section class="panel"><div class="panel-heading"><h2>物理 GPU 占用</h2><span class="muted">当前渠道页的实际生成槽位</span></div><div v-if="gpuRows.length" class="gpu-grid"><article v-for="gpu in gpuRows" :key="gpu.id" class="gpu-lane" :class="{ occupied: gpu.occupied, uncertain: gpu.uncertain }"><div><span class="mono">{{ gpu.id.slice(-8) }}</span><el-tag :type="gpu.uncertain ? 'warning' : gpu.occupied ? 'primary' : 'info'" size="small">{{ gpu.uncertain ? '待核对' : gpu.occupied ? '占用中' : '未分配' }}</el-tag></div><p>{{ gpu.channels.join(' / ') }}</p><div class="slot-track"><span :style="{ width: gpu.occupied ? '100%' : '0%' }"/></div></article></div><el-empty v-else description="当前筛选下暂无渠道，可调整条件或管理 Windows 渠道。" :image-size="64"><router-link to="/channels"><el-button type="primary">管理渠道</el-button></router-link></el-empty></section>
  <p v-if="snapshot?.dispatch_policy" class="form-help" role="note">调度按租户预计服务成本分配等份额；同租户优先级每等待 {{ snapshot.dispatch_policy.aging_step_seconds }} 秒提升一档，最多 100。等候至少 {{ snapshot.dispatch_policy.overdue_tenant_seconds }} 秒的合格租户优先轮转。服务成本为估计值，不代表收费或准确开始时间。</p>
  <p class="form-help">任务计数按筛选条件统计；GPU 与 ComfyUI 队列显示实际资源总量。<span v-for="(count, state) in snapshot?.states ?? {}" :key="state" class="queue-state-count">{{ queueStates[state] ?? state }} {{ count }}</span></p>
  <section class="panel"><div class="panel-heading"><h2>各渠道队列</h2><span class="table-note">共享候选可能重叠，不能相加</span></div><el-table :data="snapshot?.channels ?? []" empty-text="尚无渠道数据" table-layout="auto"><el-table-column label="渠道" min-width="160"><template #default="{ row }"><router-link :to="{ path: '/channels', query: { channel: row.id } }">{{ row.name }}</router-link></template></el-table-column><el-table-column label="状态" min-width="100"><template #default="{ row }"><el-tag :type="row.observation_status === 'unknown' ? 'warning' : 'success'" size="small">{{ row.observation_status === 'unknown' ? '观测未知' : '观测有效' }}</el-tag></template></el-table-column><el-table-column prop="shared_candidates" label="共享候选" align="right"/><el-table-column prop="assigned_unfinished" label="已分配未结束" align="right"/><el-table-column label="ComfyUI 运行 / 排队" min-width="180" align="right"><template #default="{ row }"><span class="mono">{{ row.comfy_queue_running ?? '未知' }} / {{ row.comfy_queue_pending ?? '未知' }}</span></template></el-table-column><el-table-column label="观测时间" min-width="180"><template #default="{ row }">{{ time(row.queue_observed_at) }}</template></el-table-column></el-table><div class="pagination"><el-button size="small" :disabled="page === 1 || loading" @click="goPage(-1)">上一页</el-button><span>第 {{ page }} 页</span><el-button size="small" :disabled="!snapshot?.channels_has_more || loading || page >= 10000" @click="goPage(1)">下一页</el-button></div></section>
  <OperationalStatistics :filters="filters"/>
</template>

<style scoped>
.retry-metrics { grid-template-columns: repeat(4, minmax(0, 1fr)); }
@media (max-width: 1100px) { .retry-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 600px) { .retry-metrics { grid-template-columns: 1fr; } }
.queue-state-count { display: inline-block; margin: 0 0 6px 12px; color: #173346; }
</style>
