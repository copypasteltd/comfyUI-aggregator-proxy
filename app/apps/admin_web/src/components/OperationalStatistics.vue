<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api, time, unwrap } from '../api'
import type { components } from '../generated/api'
import { useQueueRefresh, type QueueFilters } from '../queue'
import { durationLabel } from '../timing'
const props = defineProps<{ filters: QueueFilters }>()
type TaskType = NonNullable<components['schemas']['QueueStatistics']['task_type']>
type Bucket = components['schemas']['StatisticsBucket']
const window = ref<'1h' | '24h' | '7d'>('24h'), taskType = ref<TaskType | ''>(''), includeValidation = ref(false)
const taskNames: Record<string, string> = { text_to_image: '文生图', image_to_image: '图生图', references_to_image: '多参考文图生图', text_to_video: '文生视频', references_to_video: '多参考文图生视频', text_to_audio: '音频生成', image_to_3d: '单图生成 3D', other: '其他历史类型' }
const taskOptions = Object.entries(taskNames).filter(([key]) => key !== 'other')
const failureNames: Record<string, string> = { out_of_memory: '显存不足', invalid_output: '产物不符合要求', preparation: '准备失败', execution: '执行失败', other: '其他失败', unclassified: '未分类' }
const outcomes = [{ key: 'succeeded', label: '成功' }, { key: 'failed', label: '失败' }, { key: 'cancelled', label: '取消' }, { key: 'expired', label: '过期' }] as const
const { value: stats, error, loading, refresh, apply } = useQueueRefresh(async (query, signal) => unwrap(await api.GET('/admin/api/v1/queues/statistics', { signal, params: { query: { ...query, window: window.value, task_type: taskType.value || undefined, include_validation: includeValidation.value } } })))
watch([() => props.filters, window, taskType, includeValidation], () => apply(props.filters), { deep: true })
const maximum = computed(() => Math.max(1, ...(stats.value?.buckets.map(bucket => outcomes.reduce((total, item) => total + bucket[item.key], 0)) ?? [])))
const total = (bucket: Bucket) => outcomes.reduce((sum, item) => sum + bucket[item.key], 0)
const barLabel = (bucket: Bucket) => `${time(bucket.started_at)} 至 ${time(bucket.ended_at)}，${outcomes.map(item => `${item.label} ${bucket[item.key]}`).join('，')}`
const number = (value?: number | null) => value == null ? '—' : value.toLocaleString('zh-CN')
</script>
<template>
  <section class="panel li-card statistics-panel" aria-labelledby="statistics-title" :aria-busy="loading">
    <div class="panel-heading"><div><p class="eyebrow li-lab">GENERATION / HISTORY</p><h2 id="statistics-title">生成统计</h2></div><el-button :loading="loading" size="small" @click="refresh">刷新统计</el-button></div>
    <div class="statistics-body">
      <el-form inline class="statistics-controls" @submit.prevent="refresh">
        <el-form-item label="统计窗口"><el-select v-model="window" aria-label="统计窗口"><el-option label="最近 1 小时" value="1h"/><el-option label="最近 24 小时" value="24h"/><el-option label="最近 7 天" value="7d"/></el-select></el-form-item>
        <el-form-item label="生成类型"><el-select v-model="taskType" aria-label="生成类型" clearable placeholder="图片、视频与音频"><el-option v-for="[key, label] in taskOptions" :key="key" :label="label" :value="key"/></el-select></el-form-item>
        <el-form-item><el-checkbox v-model="includeValidation">计入验证任务</el-checkbox></el-form-item>
      </el-form>
      <el-alert v-if="error" :title="error + (stats ? ' · 当前展示最后一次成功统计' : '')" type="error" :closable="false" show-icon/>
      <p class="statistics-snapshot">沿用上方已应用的队列筛选 · 统计快照 {{ time(stats?.snapshot_at) }} · 每 5 秒刷新</p>
      <template v-if="stats">
        <div class="history-metrics">
          <article><span>受理任务</span><strong>{{ number(stats.totals.accepted) }}</strong><small>受理时间在窗口内</small></article>
          <article><span>终态任务</span><strong>{{ number(stats.totals.completed) }}</strong><small>终态时间在窗口内</small></article>
          <article><span>成功率</span><strong>{{ stats.success_rate_bps == null ? '未知' : `${(stats.success_rate_bps / 100).toFixed(2)}%` }}</strong><small>成功 {{ number(stats.totals.succeeded) }} / 失败 {{ number(stats.totals.failed) }}</small></article>
          <article><span>成功任务 P95 总历时</span><strong class="duration-metric">{{ durationLabel(stats.successful_duration.p95_ms) }}</strong><small>P50 {{ durationLabel(stats.successful_duration.p50_ms) }}</small></article>
        </div>
        <div class="trend-heading"><h3>完成趋势</h3><div class="outcome-legend"><span v-for="item in outcomes" :key="item.key"><i :class="item.key"/>{{ item.label }} {{ number(stats.totals[item.key]) }}</span></div></div>
        <p class="form-help">每段 {{ stats.bucket_seconds / 60 }} 分钟，按终态时间计数。成功率只计算成功与失败；没有样本时显示未知。</p>
        <div class="trend-chart" role="group" aria-label="完成趋势，柱形可聚焦查看各时段数量，下方可展开数据表">
          <div v-for="bucket in stats.buckets" :key="bucket.started_at" class="trend-column" tabindex="0" role="img" :aria-label="barLabel(bucket)" :title="barLabel(bucket)">
            <div class="trend-stack" :style="{ height: `${total(bucket) / maximum * 100}%`, minHeight: total(bucket) ? '2px' : '0' }"><span v-for="item in outcomes" :key="item.key" :class="item.key" :style="{ flexGrow: bucket[item.key] }"/></div>
          </div><span v-if="stats.totals.completed === 0" class="chart-empty">当前窗口内暂无终态任务</span>
        </div>
        <div class="trend-axis"><span>{{ time(stats.window_start) }}</span><span>{{ time(stats.window_end) }} 前</span></div>
        <details class="statistics-table"><summary>查看逐时段数据</summary><div class="table-scroll"><table><caption>终态任务分布</caption><thead><tr><th scope="col">时段（含起点、不含终点）</th><th v-for="item in outcomes" :key="item.key" scope="col">{{ item.label }}</th></tr></thead><tbody><tr v-for="bucket in stats.buckets" :key="bucket.started_at"><th scope="row">{{ time(bucket.started_at) }} — {{ time(bucket.ended_at) }}</th><td v-for="item in outcomes" :key="item.key">{{ number(bucket[item.key]) }}</td></tr></tbody></table></div></details>
        <div class="statistics-breakdown">
          <div class="table-scroll"><table><caption>生成类型</caption><thead><tr><th scope="col">类型</th><th scope="col">受理</th><th scope="col">成功</th><th scope="col">失败</th><th scope="col">取消 / 过期</th></tr></thead><tbody><tr v-for="item in stats.task_types" :key="item.task_type"><th scope="row">{{ taskNames[item.task_type] }}</th><td>{{ number(item.accepted) }}</td><td>{{ number(item.succeeded) }}</td><td>{{ number(item.failed) }}</td><td>{{ number(item.cancelled) }} / {{ number(item.expired) }}</td></tr></tbody></table></div>
          <div class="table-scroll"><table><caption>失败分类</caption><thead><tr><th scope="col">分类</th><th scope="col">任务数</th></tr></thead><tbody><tr v-for="item in stats.failure_classes" :key="item.category"><th scope="row">{{ failureNames[item.category] }}</th><td>{{ number(item.count) }}</td></tr></tbody></table></div>
        </div>
        <p class="statistics-note">受理与终态按各自时间计数，可能来自不同批次。成功任务总历时包含排队与恢复：有效样本 {{ number(stats.successful_duration.samples) }}，缺少可靠时间记录 {{ number(stats.successful_duration.missing_samples) }}。{{ stats.include_validation ? '已计入验证任务。' : '未计入验证任务。' }}</p>
      </template>
      <el-empty v-else :description="loading ? '正在读取生成统计' : '尚未取得生成统计'" :image-size="48"/>
    </div>
  </section>
</template>
<style scoped>
.statistics-body { padding: 20px 22px; } .eyebrow { font-size: 9px; margin-bottom: 5px; }
.statistics-controls { display: flex; gap: 8px 18px; flex-wrap: wrap; align-items: center; } .statistics-controls .el-form-item { margin-right: 0; margin-bottom: 12px; } .statistics-controls .el-select { width: 190px; }
.statistics-snapshot { font-size: 11px; color: var(--rc-text-muted); margin: 2px 0 22px; } .statistics-body > .el-alert { margin-bottom: 14px; }
.history-metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; padding-bottom: 24px; border-bottom: 1px solid var(--rc-line); } .history-metrics article { min-width: 0; border-left: 2px solid var(--rc-line-strong); padding-left: 14px; } .history-metrics article:nth-child(3) { border-color: var(--rc-ink); }
.history-metrics span { font-size: 12px; color: var(--rc-text-muted); } .history-metrics strong { display: block; font: 400 40px/1.2 var(--rc-font-display); margin: 10px 0 6px; color: var(--rc-ink); overflow-wrap: anywhere; } .history-metrics strong.duration-metric { font-family: var(--rc-font-body); font-size: 21px; line-height: 1.85; } .history-metrics small { color: var(--rc-text-muted); font-size: 11px; }
.trend-heading { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; margin-top: 24px; } h3 { font-size: 13px; margin: 0; } .outcome-legend { display: flex; flex-wrap: wrap; gap: 8px 16px; font-size: 11px; color: var(--rc-text-muted); } .outcome-legend i { display: inline-block; width: 8px; height: 8px; margin-right: 6px; }
.succeeded { background: var(--rc-lime); } .failed { background: var(--rc-tomato); } .cancelled { background: var(--rc-chart-muted); } .expired { background: var(--rc-warning); }
.trend-chart { display: flex; gap: 5px; align-items: stretch; height: 160px; margin-top: 18px; position: relative; border-bottom: 1px solid var(--rc-line-strong); background: repeating-linear-gradient(to top, transparent 0, transparent 39px, var(--rc-chart-grid) 39px, var(--rc-chart-grid) 40px); } .trend-column { flex: 1; min-width: 0; display: flex; align-items: flex-end; background: transparent; position: relative; z-index: 1; } .trend-column:hover, .trend-column:focus-visible { background: var(--rc-lime-soft); } .trend-stack { display: flex; flex-direction: column-reverse; width: 100%; } .trend-stack span { flex-basis: 0; min-height: 0; } .chart-empty { position: absolute; inset: 0; display: grid; place-items: center; font-size: 12px; color: var(--rc-text-muted); pointer-events: none; }
.trend-axis { display: flex; gap: 12px; justify-content: space-between; font: 10px/1.6 "Cascadia Mono", Consolas, monospace; color: var(--rc-text-muted); margin-top: 8px; } .statistics-table { margin: 16px 0 22px; font-size: 12px; } summary { cursor: pointer; padding: 8px 0; color: var(--rc-ink); }
.statistics-breakdown { display: grid; grid-template-columns: 1.6fr 1fr; gap: 24px; margin-top: 22px; } .table-scroll { overflow-x: auto; min-width: 0; } table { width: 100%; border-collapse: collapse; font-size: 11px; } caption { text-align: left; font-weight: 600; font-size: 13px; padding: 0 0 12px; } th, td { padding: 10px 8px; border-bottom: 1px solid var(--rc-line); white-space: nowrap; text-align: right; font-variant-numeric: tabular-nums; } thead th { color: var(--rc-text-muted); font-weight: 400; background: var(--rc-canvas); } th:first-child { text-align: left; font-weight: 400; } .statistics-note { color: var(--rc-text-muted); font-size: 11px; line-height: 1.9; margin-top: 18px; }
@media (max-width: 1100px) { .history-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 22px; } .statistics-breakdown { grid-template-columns: 1fr; } }
@media (max-width: 600px) { .statistics-body { padding: 16px; } .statistics-controls .el-form-item { width: 100%; } .statistics-controls .el-select { width: 200px; } .history-metrics { gap: 18px 10px; } .history-metrics article { padding-left: 10px; } .history-metrics strong { font-size: 26px; } .history-metrics strong.duration-metric { font-size: 19px; } .trend-chart { gap: 3px; height: 120px; } .trend-axis { font-size: 9px; } }
</style>
