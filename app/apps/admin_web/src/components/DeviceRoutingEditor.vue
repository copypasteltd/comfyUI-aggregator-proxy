<script setup lang="ts">
import { computed } from 'vue'
import type { components } from '../generated/api'

type Routing = components['schemas']['DeviceRouting']
type Channel = Extract<components['schemas']['ModelMappingOptions']['data'][number], { kind: 'channel' }>
const props = defineProps<{ modelValue: Routing; channels: Channel[]; disabled: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: Routing] }>()
const members = computed(() => [...new Set(props.channels.map(item => item.gpu_id))].sort().map(id => ({ id, names: props.channels.filter(item => item.gpu_id === id).map(item => item.name).join('、') })))
function policy(value: 'first_ready' | 'balanced_ready') { emit('update:modelValue', { policy: value, weights: value === 'balanced_ready' ? { ...props.modelValue.weights } : {} }) }
function weight(id: string, value: number | undefined) { if (value != null && Number.isInteger(value) && value >= 1 && value <= 1000) emit('update:modelValue', { policy: 'balanced_ready', weights: { ...props.modelValue.weights, [id]: value } }) }
</script>

<template>
  <el-form-item label="设备调度策略" required>
    <el-select :model-value="modelValue.policy ?? 'first_ready'" aria-label="设备调度策略" :disabled="disabled" @update:model-value="policy">
      <el-option label="就绪设备领取（原策略）" value="first_ready"/>
      <el-option label="就绪设备加权均衡" value="balanced_ready"/>
    </el-select>
    <p class="form-help">均衡按物理 GPU 分配；同卡的多个渠道合并计算。保存后须重新发布，已接收任务继续使用原池。</p>
  </el-form-item>
  <template v-if="modelValue.policy === 'balanced_ready'">
    <p class="form-help">仅在已批准、空闲且可达的设备之间分配。权重是相对份额：2:1 表示持续同等就绪时约两倍分配数；不代表完成速度或并发数。</p>
    <div v-for="member in members" :key="member.id" class="device-weight">
      <div><strong>{{ member.names }}</strong><span class="id-line">{{ member.id }}</span></div>
      <el-input-number :model-value="modelValue.weights?.[member.id] ?? 1" :min="1" :max="1000" :precision="0" :step="1" :aria-label="'设备权重 ' + member.id" :disabled="disabled" @update:model-value="weight(member.id, $event)"/>
    </div>
    <p v-if="!members.length" class="form-help" role="status">选择并核对映射渠道后显示物理 GPU 权重。</p>
  </template>
</template>

<style scoped>
.device-weight { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 12px 0; border-bottom: 1px solid var(--rc-line); }
.device-weight > div { min-width: 0; overflow-wrap: anywhere; }
.device-weight .el-input-number { flex-shrink: 0; }
@media(max-width: 500px) { .device-weight { align-items: flex-start; flex-direction: column; gap: 8px; } }
</style>
