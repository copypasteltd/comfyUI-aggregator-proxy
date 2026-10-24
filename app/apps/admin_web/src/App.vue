<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Connection, DataBoard, Key, List, Document, SwitchButton, Share, User, Warning } from '@element-plus/icons-vue'
import { useSession } from './session'
import { readableError } from './api'
import { useSessionActivity } from './sessionActivity'

const session = useSession(), route = useRoute(), router = useRouter()
useSessionActivity()
const username = ref(''), password = ref(''), busy = ref(false), error = ref('')
const links = computed(() => [
  { path: '/', name: '运行总览', icon: DataBoard, permission: 'queues:read' },
  { path: '/channels', name: 'Windows 渠道', icon: Connection, permission: 'channels:read' },
  { path: '/worker-nodes', name: 'Windows 节点', icon: Connection, permission: 'nodes:read' },
  { path: '/spec-isolations', name: '规格隔离', icon: Warning, permission: 'channels:read' },
  { path: '/jobs', name: '任务管理', icon: List, permission: 'jobs:read' },
  { path: '/workflows', name: 'Workflow 管理', icon: Share, permission: 'workflows:read' },
  { path: '/models', name: '模型与路由', icon: Share, permission: 'models:read' },
  { path: '/model-artifacts', name: '模型文件目录', icon: Document, permission: 'models:read' },
  { path: '/keys', name: 'API 密钥', icon: Key, permission: 'keys:manage' },
  { path: '/scopes', name: '租户与项目', icon: User, permission: 'scopes:read' },
  { path: '/content', name: '内容管理', icon: Document, permission: 'content:read' },
  { path: '/gateways', name: '网关接入', icon: Connection, permission: 'keys:manage' },
  { path: '/operations', name: '操作中心', icon: List, permission: 'operations:read' },
  { path: '/alerts', name: '运行告警', icon: Warning, permission: 'operations:read' },
  { path: '/audit', name: '审计事件', icon: Document, permission: 'audit:read' },
  { path: '/users', name: '管理账号', icon: User, permission: 'users:manage' },
].filter(item => session.allows(item.permission)))
async function signIn() {
  busy.value = true; error.value = ''
  const target = route.path
  try { await session.login(username.value, password.value); password.value = ''; await router.replace({ path: target, query: {}, hash: '' }) }
  catch (e) { error.value = readableError(e) }
  finally { busy.value = false }
}
async function signOut() { try { await session.logout() } catch (e) { error.value = readableError(e) } }
function expired(event: Event) { const hadSession = !!session.info; session.clear(); password.value = ''; if (hadSession) error.value = event instanceof CustomEvent && typeof event.detail === 'string' ? event.detail : '会话已失效，请重新登录' }
onMounted(async () => {
  window.addEventListener('session-expired', expired)
  try { await session.refresh() } catch (e) { error.value = readableError(e); session.ready = true }
})
onUnmounted(() => window.removeEventListener('session-expired', expired))
</script>

<template>
  <div v-if="!session.ready" class="boot" role="status">正在连接控制台…</div>
  <main v-else-if="!session.info" class="login-layout">
    <section class="login-intro"><div class="brand-mark">R<span>/</span>C</div><p class="eyebrow">RENDER CONTROL</p><h1>生成控制台</h1><p>渠道接入、任务调度与访问控制。</p><div class="intro-lines"><span>WINDOWS / COMFYUI</span><span>IMAGE + VIDEO + AUDIO</span></div></section>
    <section class="login-card"><p class="eyebrow">ADMIN ACCESS</p><h2>登录管理后台</h2><p class="muted">使用管理账号进入控制台。</p><el-alert v-if="error" :title="error" type="error" :closable="false" show-icon/>
      <el-form label-position="top" @submit.prevent="signIn"><el-form-item label="用户名"><el-input v-model="username" autocomplete="username" required autofocus size="large"/></el-form-item><el-form-item label="密码"><el-input v-model="password" type="password" autocomplete="current-password" show-password required size="large"/></el-form-item><el-button native-type="submit" type="primary" size="large" :loading="busy" class="full">登录</el-button></el-form>
    </section>
  </main>
  <div v-else class="console-layout">
    <aside class="sidebar"><router-link to="/" class="brand"><span class="brand-mark small">R<span>/</span>C</span><span>生成控制台<small>RENDER CONTROL</small></span></router-link><div class="nav-caption">控制面板</div><nav aria-label="主导航"><router-link v-for="item in links" :key="item.path" :to="item.path" :class="{ active: route.path === item.path || (item.path !== '/' && route.path.startsWith(item.path + '/')) }"><el-icon><component :is="item.icon"/></el-icon>{{ item.name }}</router-link></nav><div class="sidebar-bottom"><span class="status-dot"/> {{ session.info.environment === 'production' ? '生产环境' : '开发环境' }}<small v-if="session.info.storage_backend === 'local_development'">本地存储适配</small></div></aside>
    <div class="workspace"><header class="topbar"><span>控制台 <span class="crumb">/</span> {{ route.meta.title }}</span><div class="account"><span>{{ session.info.user.username }}</span><el-button text :icon="SwitchButton" aria-label="退出登录" @click="signOut">退出</el-button></div></header><div class="page-shell"><el-alert v-if="error" :title="error" type="error" closable @close="error = ''"/><router-view v-if="session.allows(String(route.meta.permission))"/><el-result v-else icon="warning" title="没有此页面的访问权限" sub-title="请联系管理员调整账号角色。"/></div></div>
  </div>
</template>
