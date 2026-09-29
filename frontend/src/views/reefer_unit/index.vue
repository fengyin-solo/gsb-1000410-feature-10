<template>
  <section class="page" data-module="reefer_unit">
    <header class="page-head">
      <div>
        <h2>制冷机组管理</h2>
        <p class="page-desc">维护制冷设备，围绕机组编号、所属车辆、机组型号、设定温度做登记、筛选与状态流转；保养结论登记后自动回写车辆调度。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记制冷设备</button>
        <button class="btn" type="button" @click="exportRows">导出制冷机组清单</button>
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
        <span>机组状态</span>
        <select v-model="statusFilter">
          <option value="">全部</option>
          <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
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
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="runAction('安排保养', row)">安排保养</button>
            <button class="link" type="button" @click="openMaintenance(row)">登记保养</button>
            <button class="link" type="button" @click="openDetail(row)">保养详情</button>
            <button
              v-if="row['机组状态'] !== '保养中'"
              class="link"
              type="button"
              @click="runAction('停机检查', row)"
            >
              停机检查
            </button>
            <button
              v-if="row['机组状态'] !== '保养中'"
              class="link"
              type="button"
              @click="runAction('复位故障', row)"
            >
              复位故障
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无制冷机组数据，可先登记制冷设备</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条制冷机组记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 保养登记 -->
    <div v-if="maintenanceOpen" class="modal-mask" @click.self="closeMaintenance">
      <div class="modal">
        <h3>保养登记 · {{ maintenanceForm.机组编号 }}</h3>
        <p class="modal-sub">当前机组状态：{{ maintenanceForm.机组状态 }} ／ 所属车辆：{{ maintenanceForm.所属车辆 }}</p>
        <label class="form-item">
          <span>保养结论 <em>*</em></span>
          <textarea v-model="maintenanceForm.保养结论" rows="3" placeholder="例如：更换皮带与冷媒，试机工况正常"></textarea>
        </label>
        <label class="form-item">
          <span>保养人员</span>
          <input v-model="maintenanceForm.保养人员" placeholder="保养负责人" />
        </label>
        <label class="form-item">
          <span>保养日期</span>
          <input v-model="maintenanceForm.保养日期" type="date" />
        </label>
        <label class="form-item">
          <span>备注</span>
          <input v-model="maintenanceForm.备注" placeholder="选填" />
        </label>
        <p v-if="formError" class="error-text">{{ formError }}</p>
        <div class="modal-actions">
          <button class="btn ghost" type="button" :disabled="submitting" @click="closeMaintenance">取消</button>
          <button class="btn primary" type="button" :disabled="submitting" @click="submitMaintenance">
            {{ submitting ? '提交中…' : '提交保养结论' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 保养详情 -->
    <div v-if="detailOpen" class="modal-mask" @click.self="detailOpen = false">
      <div class="modal">
        <h3>保养详情 · {{ detail.机组编号 }}</h3>
        <dl class="detail-list">
          <div><dt>机组状态</dt><dd>{{ detail.机组状态 || '—' }}</dd></div>
          <div><dt>所属车辆</dt><dd>{{ detail.所属车辆 || '—' }}</dd></div>
          <div><dt>保养开始</dt><dd>{{ detail.保养开始时间 || '—' }}</dd></div>
          <div><dt>保养日期</dt><dd>{{ detail.保养日期 || '—' }}</dd></div>
          <div><dt>保养人员</dt><dd>{{ detail.保养人员 || '—' }}</dd></div>
          <div><dt>保养结论</dt><dd>{{ detail.保养结论 || '—' }}</dd></div>
          <div><dt>备注</dt><dd>{{ detail.备注 || '—' }}</dd></div>
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

const ENDPOINT = '/api/reefer_unit'
const columns = ["机组编号", "所属车辆", "机组型号", "设定温度", "回风温度", "运转时长", "上次保养日", "机组状态", "最近保养结论"]
const statuses = ["运行", "怠速", "故障", "保养中"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const statusFilter = ref('')
const filterFields = ["机组编号", "所属车辆", "机组型号"]

const stats = computed(() => [
  { label: "运行机组", value: rows.value.filter(r => r['机组状态'] === '运行').length },
  { label: "故障机组", value: rows.value.filter(r => r['机组状态'] === '故障').length },
  { label: "保养中机组", value: rows.value.filter(r => r['机组状态'] === '保养中').length },
])

// 保养登记表单
const maintenanceOpen = ref(false)
const submitting = ref(false)
const formError = ref('')
const maintenanceForm = ref<{ id: number | null; 机组编号: string; 机组状态: string; 所属车辆: string; 保养结论: string; 保养人员: string; 保养日期: string; 备注: string }>({
  id: null, 机组编号: '', 机组状态: '', 所属车辆: '', 保养结论: '', 保养人员: '', 保养日期: '', 备注: '',
})

// 保养详情
const detailOpen = ref(false)
const detail = ref<Record<string, string | null>>({})

function resetFilters() {
  filters.value = {}
  statusFilter.value = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '制冷设备登记入口尚未接入审批流'
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
      throw new Error(payload?.message || '制冷机组动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '制冷机组操作失败'
  }
}

async function openMaintenance(row: Row) {
  formError.value = ''
  errorMessage.value = ''
  // 登记前先核对保养详情：查询报错时不能继续占用设备（后端会自愈释放）
  try {
    const response = await request(`${ENDPOINT}/${row.id}/maintenance`)
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(payload?.detail || '保养详情查询失败')
    }
    if (payload?.机组状态 !== '保养中' || row['机组状态'] !== '保养中') {
      throw new Error('该机组当前不在保养中，请先安排保养')
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : '保养详情查询失败'
    errorMessage.value = `无法开始保养登记：${message}；设备占用已释放`
    await reload()
    return
  }

  maintenanceForm.value = {
    id: Number(row.id),
    机组编号: String(row['机组编号'] ?? ''),
    机组状态: String(row['机组状态'] ?? ''),
    所属车辆: String(row['所属车辆'] ?? ''),
    保养结论: '',
    保养人员: '',
    保养日期: new Date().toISOString().slice(0, 10),
    备注: '',
  }
  maintenanceOpen.value = true
}

function closeMaintenance() {
  if (submitting.value) return
  maintenanceOpen.value = false
}

async function submitMaintenance() {
  formError.value = ''
  if (!maintenanceForm.value.保养结论.trim()) {
    formError.value = '保养结论为必填项，缺失结论无法完成登记'
    return
  }
  submitting.value = true
  try {
    const response = await request(`${ENDPOINT}/${maintenanceForm.value.id}/maintenance`, {
      method: 'POST',
      body: JSON.stringify({
        保养结论: maintenanceForm.value.保养结论,
        保养人员: maintenanceForm.value.保养人员,
        保养日期: maintenanceForm.value.保养日期,
        备注: maintenanceForm.value.备注,
      }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload?.ok) {
      throw new Error(payload?.message || '保养登记未生效')
    }
    maintenanceOpen.value = false
    await reload()
  } catch (error) {
    // 登记失败（含并发抢占、回滚）：关闭弹窗并刷新，后端保证不会留下半条记录
    formError.value = error instanceof Error ? error.message : '保养登记失败，已回滚'
    maintenanceOpen.value = false
    errorMessage.value = formError.value
    await reload()
  } finally {
    submitting.value = false
  }
}

async function openDetail(row: Row) {
  formError.value = ''
  errorMessage.value = ''
  detail.value = {}
  try {
    const response = await request(`${ENDPOINT}/${row.id}/maintenance`)
    if (!response.ok) {
      const payload = await response.json().catch(() => null)
      // 页面查询报错不能继续占用设备：提示并刷新列表（后端已自愈释放）
      throw new Error(payload?.detail || '保养详情读取失败')
    }
    detail.value = await response.json()
    detailOpen.value = true
  } catch (error) {
    errorMessage.value = `保养详情查询失败：${error instanceof Error ? error.message : '未知错误'}；设备占用已释放`
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
      throw new Error('制冷设备列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '制冷机组列表读取失败'
  }
}

onMounted(reload)
</script>
