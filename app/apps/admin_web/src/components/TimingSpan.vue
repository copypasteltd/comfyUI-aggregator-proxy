<script setup lang="ts">
import { computed } from 'vue'
import type { components } from '../generated/api'
import { durationLabel } from '../timing'
const props = defineProps<{ value?: components['schemas']['DurationView'] | null }>()
const note = computed(() => props.value?.status === 'open' ? '截至快照 · 尚无结束记录' : props.value?.status === 'complete' ? '已记录起止时间' : props.value?.reason === 'unmeasured' ? '未独立测量' : props.value?.reason === 'clock_order' ? '时间记录不一致' : '缺少阶段记录')
</script>
<template><span class="timing-span" :class="value?.status"><strong class="mono">{{ value?.status === 'unknown' ? '未知' : durationLabel(value?.milliseconds) }}</strong><small>{{ note }}</small></span></template>
<style scoped>
.timing-span { display: inline-flex; flex-direction: column; gap: 5px; }
strong { font-size: 13px; font-weight: 500; color: #173346; }
small { color: #617482; font-size: 11px; }
.open strong { color: #087e8b; } .unknown strong { color: #617482; }
</style>
