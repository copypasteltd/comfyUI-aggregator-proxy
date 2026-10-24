<script setup lang="ts">
import type { components } from '../generated/api'
import { time } from '../api'
import TimingSpan from './TimingSpan.vue'
defineProps<{ detail: components['schemas']['AdminJobDetail'] }>()
const stages = [['elapsed', '本次尝试历时'], ['preparing', '准备'], ['submission', '提交'], ['generation_collection', '生成与收集'], ['upload_verification', '上传与核验'], ['model_loading', '模型加载']] as const
</script>
<template>
  <section class="job-timings" aria-label="任务阶段耗时">
    <div class="timing-heading"><h3>任务阶段耗时</h3><span class="muted">快照 {{ time(detail.timing ? detail.timing.snapshot_at_ms / 1000 : undefined) }}</span></div>
    <dl class="job-spans"><div><dt>任务总历时</dt><dd><TimingSpan :value="detail.timing?.elapsed"/></dd></div><div><dt>首次分配前等待</dt><dd><TimingSpan :value="detail.timing?.queue"/></dd></div></dl>
    <p class="form-help">耗时按已确认的阶段时间计算，包含等待、网络传输和核对时间。生成与收集包含 ComfyUI 内部排队；模型加载与 GPU 实际计算尚未独立测量。各阶段可能缺失，不能简单相加。</p>
    <p v-if="detail.attempts_has_more" class="timing-limit" role="note">展示最近 100 次尝试并保留当前尝试；首次等待按全部历史计算。</p>
    <details v-for="(attempt, index) in detail.attempts" :key="attempt.id" :open="attempt.id === detail.current_attempt_id || (!detail.current_attempt_id && index === detail.attempts.length - 1)">
      <summary><span>尝试 <span class="mono">{{ attempt.id.slice(-8) }}</span></span><span class="muted">{{ attempt.id === detail.current_attempt_id ? '当前尝试' : attempt.closed_at ? '已关闭' : '历史记录' }}</span></summary>
      <dl class="phase-spans"><div v-for="[key, label] in stages" :key="key"><dt>{{ label }}</dt><dd><TimingSpan :value="attempt.timing?.[key]"/></dd></div></dl>
    </details>
  </section>
</template>
<style scoped>
.job-timings { margin: 24px 0; padding: 18px; background: #f7f9fa; border: 1px solid #dce5ea; border-radius: 5px; }
.timing-heading { display: flex; align-items: baseline; flex-wrap: wrap; gap: 8px 18px; justify-content: space-between; }
h3 { margin: 0; font-size: 15px; } dl { display: grid; gap: 18px; margin: 18px 0; }
.job-spans { grid-template-columns: repeat(2, minmax(0, 1fr)); } .phase-spans { grid-template-columns: repeat(3, minmax(0, 1fr)); padding: 0 12px; }
dt { color: #617482; font-size: 12px; margin-bottom: 8px; } dd { margin: 0; }
details { border-top: 1px solid #dce5ea; margin-top: 12px; } summary { cursor: pointer; padding: 14px 0 4px; font-size: 12px; }
summary > span:last-child { margin-left: 12px; font-size: 11px; } .timing-limit { font-size: 12px; color: #926c46; margin-top: 12px; }
@media (max-width: 600px) { .job-timings { padding: 14px; } .phase-spans { grid-template-columns: repeat(2, minmax(0, 1fr)); padding: 0; } }
</style>
