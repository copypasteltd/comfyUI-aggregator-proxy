<script setup lang="ts">
import { computed, ref } from 'vue'
import { ExactNumber, UnfinishedNumber, numericText, parseExactJson, type JsonObject, type JsonValue } from '../workflowJson'
const props = defineProps<{ modelValue: JsonObject; disabled: boolean; label: string }>()
const emit = defineEmits<{ 'update:modelValue': [JsonObject]; dirty: [] }>()
const page = ref(1), error = ref(''), newKey = ref('')
const entries = computed(() => Object.entries(props.modelValue))
const shown = computed(() => entries.value.slice((page.value - 1) * 50, page.value * 50))
function supported(value: JsonValue) { return value instanceof ExactNumber || value instanceof UnfinishedNumber || ['string', 'number', 'boolean'].includes(typeof value) }
function kind(value: JsonValue) { return value instanceof ExactNumber || value instanceof UnfinishedNumber || typeof value === 'number' ? 'number' : typeof value }
function update(key: string, value: JsonValue) { if (props.disabled) return; emit('update:modelValue', { ...props.modelValue, [key]: value }); emit('dirty') }
function remove(key: string) { if (props.disabled) return; const values = { ...props.modelValue }; delete values[key]; emit('update:modelValue', values); emit('dirty'); page.value = Math.max(1, Math.min(page.value, Math.ceil((entries.value.length - 1) / 50))); error.value = '' }
function add() { if (props.disabled) return; const key = newKey.value.trim(); if (!key || Object.hasOwn(props.modelValue, key)) { error.value = '节点输入名称不能为空或重复'; return }; page.value = Math.floor(entries.value.length / 50) + 1; update(key, ''); newKey.value = ''; error.value = '' }
function setKind(key: string, next: string) { update(key, next === 'boolean' ? false : next === 'number' ? new ExactNumber('0') : ''); error.value = '' }
function numeric(key: string, text: string) { try { const value = parseExactJson(text, 8192); if (!(value instanceof ExactNumber)) throw new Error(); update(key, value); error.value = '' } catch { update(key, new UnfinishedNumber(text)); error.value = '固定参数的数字格式无效，请修正后应用' } }
</script>
<template>
  <div class="scalar-values"><p class="form-help">{{ label }}：按节点编号.输入名固定标量值。</p><el-alert v-if="error" :title="error" type="error" :closable="false"/>
    <div v-for="[key, value] in shown" :key="key" class="scalar-row">
      <el-input :model-value="key" :aria-label="label + ' 节点输入 ' + key" readonly/>
      <el-select v-if="supported(value)" :model-value="kind(value)" :aria-label="label + ' 类型 ' + key" :disabled="disabled" @update:model-value="setKind(key, $event)"><el-option label="文本" value="string"/><el-option label="数字" value="number"/><el-option label="布尔" value="boolean"/></el-select>
      <el-input v-if="kind(value) === 'number'" :model-value="numericText(value)" :aria-label="label + ' 数值 ' + key" :disabled="disabled" @update:model-value="numeric(key, $event)"/>
      <el-select v-else-if="kind(value) === 'boolean'" :model-value="value" :aria-label="label + ' 布尔 ' + key" :disabled="disabled" @update:model-value="update(key, $event)"><el-option label="true" :value="true"/><el-option label="false" :value="false"/></el-select>
      <el-input v-else-if="supported(value)" :model-value="typeof value === 'string' ? value : ''" :aria-label="label + ' 文本 ' + key" :disabled="disabled" @update:model-value="update(key, $event)"/>
      <p v-else class="form-help">此值不是标量，原数据已保留；请在完整 JSON 中修正。</p>
      <el-button :disabled="disabled" @click="remove(key)">移除</el-button>
    </div>
    <p class="form-help">更换目标请移除后重新添加；已有目标中的值可直接修改。</p><div class="scalar-row"><el-input v-model="newKey" :aria-label="label + ' 新固定参数目标'" placeholder="节点编号.输入名" :disabled="disabled"/><el-button :disabled="disabled" @click="add">添加固定参数</el-button></div>
    <div v-if="entries.length > 50" class="pagination"><el-button :disabled="page === 1" @click="page--">参数上一页</el-button><span>{{ page }} / {{ Math.ceil(entries.length / 50) }}</span><el-button :disabled="page * 50 >= entries.length" @click="page++">参数下一页</el-button></div>
  </div>
</template>
<style scoped>
.scalar-values { padding-top: 12px; }
.scalar-row { display: grid; grid-template-columns: 1.1fr 90px 1fr auto; gap: 8px; margin-bottom: 12px; }
@media (max-width: 600px) { .scalar-row { grid-template-columns: minmax(0, 1fr) 90px; } }
</style>
