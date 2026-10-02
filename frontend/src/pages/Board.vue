<template>
  <div>
    <h1 class="brand">本周看板</h1>
    <p class="muted">周卡片网格 · ready 后可封存格位差分包；封存周只读，解封后 force 重生成可与差分包对照</p>

    <div class="week-tabs">
      <button v-for="w in weeks" :key="w.id" class="week-tab"
              :class="{ active: w.id === weekId }" @click="pickWeek(w.id)">
        <span>{{ w.label || ('第' + w.id + '周') }}</span>
        <span class="chip" :class="statusChip(w)">{{ statusText(w) }}</span>
      </button>
    </div>

    <div style="display:flex;gap:8px;margin:12px 0;flex-wrap:wrap">
      <button @click="generate">生成周表</button>
      <button class="ghost" @click="load">刷新</button>
      <button v-if="board && !board.sealed && board.week.status==='ready'" @click="seal">封存差分包</button>
      <button v-if="board && board.sealed" class="ghost" @click="unseal">解封（保留差分包）</button>
      <button class="ghost" @click="toggleDetail" :disabled="!pkgSeal">差分包详情</button>
      <button class="ghost" @click="toggleDiff" :disabled="!pkgSeal">看板对照</button>
    </div>

    <p v-if="board && board.sealed" class="seal-banner">
      🔒 已封存于 {{ fmtTime(board.package.sealed_at) }} · 差分包 {{ board.package.byte_count }} 字节
      · sha256 {{ shortHash(board.package.sha256) }} · 生成 / 对调确认 / 撤销均已锁定
    </p>
    <p v-if="board && !board.sealed && pkgSeal" class="seal-banner warn">
      🔓 已解封可写 · 差分包保持 {{ board.package.byte_count }} 字节不变（{{ shortHash(board.package.sha256) }}）· force 重生成后可用「看板对照」检出漂移格
    </p>
    <p v-if="err" class="err">{{ err }}</p>

    <section v-if="showDetail && detail" class="week-card detail-panel">
      <header>差分包详情（封存瞬间，只读）</header>
      <dl class="seal-dl">
        <dt>封存时刻</dt><dd>{{ fmtTime(detail.sealed_at) }}</dd>
        <dt>周状态</dt><dd>{{ detail.status }}（{{ detail.sealed ? '封存中' : '已解封' }}）</dd>
        <dt>周标签</dt><dd>{{ detail.week_label }}</dd>
        <dt>家庭名（钉死）</dt><dd>{{ detail.household }}</dd>
        <dt>字节数</dt><dd>{{ detail.byte_count }}</dd>
        <dt>sha256</dt><dd class="mono">{{ detail.sha256 }}</dd>
        <dt>格位数</dt><dd>{{ detail.package.cells.length }} 格</dd>
      </dl>
      <p class="muted">改家庭名不会解封，也不会改写本包内容。</p>
    </section>

    <section v-if="showDiff && diff" class="detail-panel">
      <h2 class="brand" style="font-size:16px">看板对照：现网 vs 差分包</h2>
      <p class="muted">
        一致 {{ diff.match_count }} 格 · 不一致 <b>{{ diff.mismatch_count }}</b> 格 ·
        差分包 {{ diff.package_byte_count }} 字节 {{ shortHash(diff.package_sha256) }}
      </p>
      <p v-if="diff.mismatch_count === 0" class="muted">完全一致，现网仍是封存瞬间的看板。</p>
      <ul class="list">
        <li v-for="(m, i) in diff.mismatches" :key="i" class="diff-row">
          <span class="chip" :class="kindClass(m.kind)">{{ kindText(m.kind) }}</span>
          Day{{ m.day }} · {{ m.task_title }}(T{{ m.task_id }})
          <template v-if="m.kind==='member_changed'">
            <span class="coral-text">{{ m.package_member_name }} → {{ m.live_member_name }}</span>
          </template>
          <template v-else-if="m.kind==='only_in_package'">
            <span class="muted">包内 {{ m.package_member_name }}，现网缺格</span>
          </template>
          <template v-else>
            <span class="muted">现网 {{ m.live_member_name }}，包内无此格</span>
          </template>
        </li>
      </ul>
    </section>

    <div class="week-grid">
      <article v-for="d in days" :key="d" class="week-card" :class="{ sealed: board && board.sealed }">
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
import { ref, onMounted } from 'vue'
import { api } from '../api'
const weeks = ref([])
const board = ref(null)
const detail = ref(null)
const diff = ref(null)
const showDetail = ref(false)
const showDiff = ref(false)
const days = [0,1,2,3,4,5,6]
const err = ref('')
const weekId = ref(1)
const pkgSeal = ref(false)

function byDay(d) { return (board.value?.assignments || []).filter(a => a.day === d) }
function shortHash(h) { return h ? h.slice(0, 10) : '' }
function fmtTime(t) { return t ? t.replace('T', ' ').replace(/\.\d+.*$/, '').replace(/\+.*$/, '').replace(/Z$/, ' UTC') : '' }
function statusText(w) {
  if (w.sealed) return '已封存'
  if (w.has_package && w.status === 'ready') return '已解封'
  return { draft: '草稿', ready: 'ready' }[w.status] || w.status
}
function statusChip(w) { return w.sealed ? 'coral' : (w.has_package ? 'unsealed' : '') }
function kindText(k) { return { member_changed: '换了人', only_in_package: '仅包内', only_in_live: '仅现网' }[k] }
function kindClass(k) { return k === 'member_changed' ? 'coral' : '' }

async function loadWeeks() {
  weeks.value = await api('/weeks')
  if (!weeks.value.find(w => w.id === weekId.value) && weeks.value.length) {
    weekId.value = weeks.value[0].id
  }
}

async function load() {
  err.value = ''
  try {
    await loadWeeks()
    const b = await api('/weeks/' + weekId.value + '/board')
    board.value = b
    pkgSeal.value = !!b.package
    if (showDetail.value) await loadDetail()
    if (showDiff.value) await loadDiff()
  } catch (e) { err.value = e.message }
}

async function loadDetail() {
  detail.value = await api('/weeks/' + weekId.value + '/seal')
}
async function loadDiff() {
  diff.value = await api('/weeks/' + weekId.value + '/diff')
}
async function toggleDetail() {
  showDetail.value = !showDetail.value
  if (showDetail.value && !detail.value) { try { await loadDetail() } catch (e) { err.value = e.message } }
}
async function toggleDiff() {
  showDiff.value = !showDiff.value
  if (showDiff.value && !diff.value) { try { await loadDiff() } catch (e) { err.value = e.message } }
}

async function pickWeek(id) {
  weekId.value = id
  detail.value = null; diff.value = null
  showDetail.value = false; showDiff.value = false
  await load()
}

async function generate() {
  err.value = ''
  const hasCells = (board.value?.assignments || []).length > 0
  if (hasCells) {
    const ok = window.confirm(
      pkgSeal.value
        ? 'force 重生成将覆盖现网看板；差分包字节不变，之后可用「看板对照」列出不一致格。确认？'
        : '现有看板将被覆盖重生成，确认？')
    if (!ok) return
  }
  try {
    await api('/weeks/' + weekId.value + '/generate', {
      method: 'POST', body: JSON.stringify({ force: hasCells }),
    })
    await load()
  } catch (e) { err.value = e.message === 'force_required' ? '该周已有看板，需确认 force 重生成。' : e.message }
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

onMounted(load)
</script>
<style scoped>
.week-tabs { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px; }
.week-tab {
  background: var(--card); color: var(--ink); border: 1px solid var(--line);
  border-radius: 12px; padding: 8px 12px; display: flex; align-items: center; gap: 6px;
}
.week-tab.active { border-color: var(--coral); box-shadow: 0 0 0 1px var(--coral) inset; }
.chip.unsealed { background: #e7e9df; color: #6b6630; }
.seal-banner {
  background: #ffe3de; border: 1px solid #f2b6ad; color: #7a2c21;
  border-radius: 12px; padding: 10px 12px; font-size: 13px;
}
.seal-banner.warn { background: #f1efd8; border-color: #d8d29a; color: #6b6630; }
.week-card.sealed { opacity: .82; border-style: dashed; }
.detail-panel { margin: 12px 0; padding: 14px; }
.seal-dl { display: grid; grid-template-columns: 110px 1fr; gap: 4px 12px; margin: 8px 0; font-size: 13px; }
.seal-dl dt { color: var(--muted); } .seal-dl dd { margin: 0; }
.mono { font-family: ui-monospace, monospace; word-break: break-all; }
.diff-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.coral-text { color: #b33a2c; font-weight: 600; }
</style>
