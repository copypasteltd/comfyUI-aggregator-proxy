import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createRouter, createWebHistory } from 'vue-router'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import './style.css'
import App from './App.vue'
const Overview = () => import('./pages/Overview.vue')
const Channels = () => import('./pages/Channels.vue')
const WorkerNodes = () => import('./pages/WorkerNodes.vue')
const Keys = () => import('./pages/Keys.vue')
const KeyAccess = () => import('./pages/KeyAccess.vue')
const Workflows = () => import('./pages/Workflows.vue')
const Records = () => import('./pages/Records.vue')
const Audit = () => import('./pages/Audit.vue')
const Models = () => import('./pages/Models.vue')
const Content = () => import('./pages/Content.vue')
const Gateways = () => import('./pages/Gateways.vue')
const Jobs = () => import('./pages/Jobs.vue')
const Users = () => import('./pages/Users.vue')
const ModelArtifacts = () => import('./pages/ModelArtifacts.vue')
const Alerts = () => import('./pages/Alerts.vue')
const SpecIsolations = () => import('./pages/SpecIsolations.vue')
const Scopes = () => import('./pages/Scopes.vue')

const router = createRouter({
  history: createWebHistory('/admin/'),
  routes: [
    { path: '/', component: Overview, meta: { title: '运行总览', permission: 'queues:read' } },
    { path: '/channels', component: Channels, meta: { title: 'Windows 渠道', permission: 'channels:read' } },
    { path: '/worker-nodes', component: WorkerNodes, meta: { title: 'Windows 节点', permission: 'nodes:read' } },
    { path: '/spec-isolations', component: SpecIsolations, meta: { title: '规格隔离', permission: 'channels:read' } },
    { path: '/jobs', component: Jobs, meta: { title: '任务管理', permission: 'jobs:read' } },
    { path: '/workflows', component: Workflows, meta: { title: 'Workflow 管理', permission: 'workflows:read' } },
    { path: '/models', component: Models, meta: { title: '模型与路由', permission: 'models:read' } },
    { path: '/model-artifacts', component: ModelArtifacts, meta: { title: '模型文件目录', permission: 'models:read' } },
    { path: '/keys', component: Keys, meta: { title: 'API 密钥', permission: 'keys:manage' } },
    { path: '/scopes', component: Scopes, meta: { title: '租户与项目', permission: 'scopes:read' } },
    { path: '/keys/:identifier/access', component: KeyAccess, meta: { title: 'Key 访问记录', permission: 'keys:usage' } },
    { path: '/content', component: Content, meta: { title: '内容管理', permission: 'content:read' } },
    { path: '/gateways', component: Gateways, meta: { title: '网关接入', permission: 'keys:manage' } },
    { path: '/operations', component: Records, meta: { title: '操作中心', permission: 'operations:read' } },
    { path: '/alerts', component: Alerts, meta: { title: '运行告警', permission: 'operations:read' } },
    { path: '/audit', component: Audit, meta: { title: '审计事件', permission: 'audit:read' } },
    { path: '/users', component: Users, meta: { title: '管理账号', permission: 'users:manage' } },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
createApp(App).use(createPinia()).use(router).use(ElementPlus, { locale: zhCn }).mount('#app')
