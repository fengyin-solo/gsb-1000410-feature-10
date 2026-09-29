<template>
  <section class="page" data-module="vehicle">
    <header class="page-head">
      <div>
        <h2>车辆调度管理</h2>
        <p class="page-desc">维护冷藏车辆，围绕车辆编号、车牌号、车型类别、温层能力做登记、筛选与状态流转；机组状态由制冷机组模块统一回写。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记冷藏车辆</button>
        <button class="btn" type="button" @click="exportRows">导出车辆调度清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <label class="filter-item">
        <span>车辆状态</span>
        <select v-model="statusFilter">
          <option value="">全部</option>
          <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>机组状态</span>
        <select v-model="reeferStatusFilter">
          <option value="">全部</option>
          <option value="运行">运行</option>
          <option value="怠速">怠速</option>
          <option value="故障">故障</option>
          <option value="保养中">保养中</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">
            <span v-if="column === '制冷机组状态'" :class="['status-tag', statusClass(row[column])]">{{ row[column] || '—' }}</span>
            <span v-else>{{ row[column] ?? '—' }}</span>
          </td>
          <td class="row-actions">
            <button class="link" type="button" @click="openDetail(row)">车辆详情</button>
            <button class="link" type="button" @click="runAction('派发出车', row)">派发出车</button>
            <button class="link" type="button" @click="runAction('收车归队', row)">收车归队</button>
            <button class="link" type="button" @click="runAction('报修车辆', row)">报修车辆</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无车辆调度数据，可先登记冷藏车辆</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条车辆调度记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 车辆详情 -->
    <div v-if="detailOpen" class="modal-mask" @click.self="detailOpen = false">
      <div class="modal">
        <h3>车辆详情 · {{ detail['车辆编号'] }}</h3>
        <dl class="detail-list">
          <div v-for="field in detailFields" :key="field">
            <dt>{{ field }}</dt>
            <dd>{{ detail[field] ?? '—' }}</dd>
          </div>
        </dl>
        <p v-if="formError" class="error-text">{{ formError }}</p>
        <div class="modal-actions">
          <button class="btn primary" type="button" @click="detailOpen = false">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/vehicle'
const columns = ["车辆编号", "车牌号", "车型类别", "温层能力", "制冷机组型号", "制冷机组状态", "最近保养结论", "上次维保日", "当前位置", "车辆状态"]
const detailFields = ["车辆编号", "车牌号", "车型类别", "温层能力", "制冷机组型号", "制冷机组状态", "最近保养结论", "上次维保日", "当前位置", "车辆状态"]
const statuses = ["空闲", "已派单", "执行中", "维修中", "停运"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const formError = ref('')
const filters = ref<Record<string, string>>({})
const statusFilter = ref('')
const reeferStatusFilter = ref('')
const filterFields = ["车辆编号", "车牌号", "车型类别"]

const stats = computed(() => [
  { label: "空闲车辆", value: rows.value.filter(r => r['车辆状态'] === '空闲').length },
  { label: "执行中车辆", value: rows.value.filter(r => r['车辆状态'] === '执行中').length },
  { label: "机组保养中", value: rows.value.filter(r => r['制冷机组状态'] === '保养中').length },
])

const detailOpen = ref(false)
const detail = ref<Record<string, string | number | null>>({})

function statusClass(status: unknown): string {
  if (status === '运行' || status === '空闲') return 'tag-ok'
  if (status === '保养中' || status === '维修中') return 'tag-warn'
  if (status === '故障') return 'tag-err'
  return ''
}

function resetFilters() {
  filters.value = {}
  statusFilter.value = ''
  reeferStatusFilter.value = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '冷藏车辆登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload?.ok) {
      throw new Error(payload?.message || '车辆调度动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '车辆调度操作失败'
  }
}

async function openDetail(row: Row) {
  formError.value = ''
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}`)
    if (!response.ok) {
      const payload = await response.json().catch(() => null)
      throw new Error(payload?.detail || '车辆详情读取失败')
    }
    detail.value = await response.json()
    detailOpen.value = true
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '车辆详情读取失败'
    await reload()
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(filters.value)) {
    if (value) query.set(key, value)
  }
  if (statusFilter.value) query.set('status', statusFilter.value)
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) {
      throw new Error('冷藏车辆列表读取失败')
    }
    const payload = await response.json()
    let items: Row[] = payload.items ?? []
    // 机组状态过滤在后端 projection 之上做二次筛选，保证与机组列表口径一致
    if (reeferStatusFilter.value) {
      items = items.filter(row => row['制冷机组状态'] === reeferStatusFilter.value)
    }
    rows.value = items
    total.value = payload.total ?? items.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '车辆调度列表读取失败'
  }
}

onMounted(reload)
</script>
