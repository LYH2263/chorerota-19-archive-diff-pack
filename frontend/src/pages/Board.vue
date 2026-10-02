<template>
  <div>
    <h1 class="brand">本周看板</h1>
    <p class="muted">周卡片网格 · round-robin 落位后可去「对调」申请交换</p>

    <div class="week-strip">
      <div v-for="w in weeks" :key="w.id" class="week-card week-tab"
           :class="{ active: w.id === weekId }" @click="pick(w.id)">
        <b>{{ w.label }}</b>
        <span class="chip" :class="{ coral: w.status === 'sealed' }">{{ w.status }}</span>
        <p v-if="w.pack_id" class="muted">包 v{{ w.pack_version }} · {{ short(w.pack_checksum) }}</p>
      </div>
    </div>

    <div style="display:flex;gap:8px;margin:12px 0;flex-wrap:wrap">
      <button :disabled="sealed" @click="generate(false)">生成周表</button>
      <button v-if="needForce" @click="generate(true)">强制重生成</button>
      <button v-if="week.status === 'ready'" @click="seal">封存</button>
      <button v-if="sealed" class="ghost" @click="unseal">解封</button>
      <button v-if="pack" class="ghost" @click="loadDiff">对照差分包</button>
      <button class="ghost" @click="load">刷新</button>
    </div>
    <p v-if="sealed" class="muted">已封存：生成 / 对调确认 / 撤销均被拒绝，解封后恢复。</p>
    <p v-if="err" class="err">{{ err }}</p>

    <section v-if="pack" class="week-card" style="margin-bottom:12px">
      <header>差分包 · 封存瞬间数据</header>
      <p>v{{ pack.version }} · {{ pack.sealed_at }} · {{ pack.cell_count }} 格</p>
      <p class="muted">checksum {{ pack.checksum }}</p>
      <button class="ghost" @click="toggleDetail">{{ packDetail ? '收起详情' : '包详情' }}</button>
      <div v-if="packDetail" style="margin-top:8px">
        <span v-for="cell in packDetail.cells" :key="cell.day + '-' + cell.task_id" class="chip">
          D{{ cell.day }}·{{ taskTitle(cell.task_id) }} → {{ memberName(cell.member_id) }}
        </span>
      </div>
    </section>

    <section v-if="diff" class="week-card" style="margin-bottom:12px">
      <header>对照 · 现网 vs 包 v{{ diff.pack.version }}（{{ short(diff.pack.checksum) }}）</header>
      <p v-if="!diff.mismatch_count" class="muted">全部格位一致</p>
      <ul v-else class="list">
        <li v-for="m in diff.mismatches" :key="m.day + '-' + m.task_id">
          Day {{ m.day }} · {{ taskTitle(m.task_id) }}：
          <span v-if="m.kind === 'changed'">封存 {{ memberName(m.sealed_member_id) }} → 现网 {{ memberName(m.live_member_id) }}</span>
          <span v-else-if="m.kind === 'missing_live'">现网缺格（封存为 {{ memberName(m.sealed_member_id) }}）</span>
          <span v-else>现网多格（{{ memberName(m.live_member_id) }}）</span>
        </li>
      </ul>
    </section>

    <div class="week-grid">
      <article v-for="d in days" :key="d" class="week-card">
        <header>Day {{ d }}</header>
        <div v-for="a in byDay(d)" :key="a.id">
          <span class="chip">{{ a.task_title }}</span>
          <span class="chip coral">{{ a.member_name }}</span>
        </div>
        <p v-if="!byDay(d).length" class="muted">空</p>
      </article>
    </div>
  </div>
</template>
<script setup>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api'
const weeks = ref([])
const weekId = ref(1)
const assigns = ref([])
const pack = ref(null)
const packDetail = ref(null)
const diff = ref(null)
const needForce = ref(false)
const err = ref('')
const members = ref({})
const tasks = ref({})
const days = [0,1,2,3,4,5,6]
const week = computed(() => weeks.value.find(w => w.id === weekId.value) || {})
const sealed = computed(() => week.value.status === 'sealed')
function byDay(d) { return assigns.value.filter(a => a.day === d) }
function short(s) { return (s || '').slice(0, 10) }
function memberName(id) { return id == null ? '—' : (members.value[id] || '#' + id) }
function taskTitle(id) { return tasks.value[id] || 'T' + id }
async function loadBoard() {
  const b = await api('/weeks/' + weekId.value + '/board')
  assigns.value = b.assignments || []
  pack.value = b.pack
}
async function load() {
  err.value = ''
  try {
    weeks.value = await api('/weeks')
    if (!weeks.value.some(w => w.id === weekId.value) && weeks.value.length) weekId.value = weeks.value[0].id
    const ms = await api('/members'); members.value = Object.fromEntries(ms.map(m => [m.id, m.name]))
    const ts = await api('/tasks'); tasks.value = Object.fromEntries(ts.map(t => [t.id, t.title]))
    await loadBoard()
  } catch (e) { err.value = e.message }
}
function pick(id) {
  weekId.value = id
  packDetail.value = null; diff.value = null; needForce.value = false; err.value = ''
  loadBoard().catch(e => { err.value = e.message })
}
async function generate(force) {
  err.value = ''
  try {
    await api('/weeks/' + weekId.value + '/generate', { method: 'POST', body: JSON.stringify(force ? { force: true } : {}) })
    needForce.value = false; diff.value = null
    await load()
  } catch (e) {
    if (String(e.message).includes('already_generated')) needForce.value = true
    else err.value = e.message
  }
}
async function seal() {
  err.value = ''
  try { await api('/weeks/' + weekId.value + '/seal', { method: 'POST', body: '{}' }); await load() }
  catch (e) { err.value = e.message }
}
async function unseal() {
  err.value = ''
  try { await api('/weeks/' + weekId.value + '/unseal', { method: 'POST', body: '{}' }); await load() }
  catch (e) { err.value = e.message }
}
async function toggleDetail() {
  err.value = ''
  if (packDetail.value) { packDetail.value = null; return }
  try { packDetail.value = await api('/weeks/' + weekId.value + '/pack') }
  catch (e) { err.value = e.message }
}
async function loadDiff() {
  err.value = ''
  try { diff.value = await api('/weeks/' + weekId.value + '/diff') }
  catch (e) { err.value = e.message }
}
onMounted(load)
</script>
