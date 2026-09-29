const $ = id => document.getElementById(id);
let currentSeries = null;
let selected = new Set();
let searchResults = [];
let availability = new Map();
let qualityErrors = new Map();
const qualityCache = new Map();
let scanToken = 0;
let seriesRequestId = 0;
let scanInProgress = false;
let lastScanSeriesId = '';
let lastScanPreference = '';

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function jsonPost(path, body = {}) {
  return api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
}

function status(id, message, kind = '') {
  const element = $(id);
  element.textContent = message;
  element.className = kind ? `notice ${kind}` : 'hint';
}

function button(id, busy, label) {
  $(id).disabled = busy;
  if (label) $(id).textContent = label;
}

function renderResults() {
  const box = $('results');
  box.replaceChildren();
  if (!searchResults.length) {
    const empty = document.createElement('p');
    empty.className = 'empty';
    empty.textContent = '没有匹配的官方剧目。';
    box.append(empty);
    return;
  }
  for (const item of searchResults) {
    const row = document.createElement('button');
    row.type = 'button';
    row.className = 'result' + (currentSeries?.series_id === item.series_id ? ' active' : '');
    if (item.cover.startsWith('https://')) {
      const image = document.createElement('img');
      image.className = 'cover';
      image.src = item.cover;
      image.alt = '';
      image.loading = 'lazy';
      row.append(image);
    }
    const text = document.createElement('span');
    const title = document.createElement('span');
    title.className = 'result-title';
    title.textContent = item.title;
    const meta = document.createElement('span');
    meta.className = 'result-meta';
    meta.textContent = `${item.episode_count} 集 · ID ${item.series_id}${item.exact ? ' · 完全匹配' : ''}`;
    text.append(title, meta);
    row.append(text);
    row.onclick = () => loadSeries(item.series_id);
    box.append(row);
  }
}

async function search() {
  const query = $('query').value.trim();
  if (!query) return status('searchStatus', '请输入剧名或 series_id。', 'error');
  button('searchBtn', true, '搜索中…');
  try {
    const data = await api(`/api/search?q=${encodeURIComponent(query)}`);
    searchResults = data.items;
    renderResults();
    status('searchStatus', `找到 ${searchResults.length} 个匹配结果，请选择要下载的剧目。`);
  } catch (error) {
    status('searchStatus', error.message, 'error');
  } finally {
    button('searchBtn', false, '搜索');
  }
}

async function loadSeries(seriesId) {
  const requestId = ++seriesRequestId;
  ++scanToken;
  scanInProgress = false;
  currentSeries = null;
  selected.clear();
  availability.clear();
  renderEpisodes();
  status('seriesInfo', '正在读取逐集 ID…');
  try {
    const detail = await api(`/api/series/${encodeURIComponent(seriesId)}`);
    if (requestId !== seriesRequestId) return;
    currentSeries = detail;
    $('range').value = '';
    renderEpisodes();
    renderResults();
    status('seriesInfo', `${currentSeries.title}：官网标注 ${currentSeries.episode_count} 集，已取得 ${currentSeries.episodes.length} 个逐集 ID。网页可看集数 ${currentSeries.accessible_web_count}；实际下载画质和可用性以客户端接口为准。`);
    scanAvailability();
  } catch (error) {
    if (requestId !== seriesRequestId) return;
    currentSeries = null;
    selected.clear();
    renderEpisodes();
    status('seriesInfo', error.message, 'error');
  }
}

function updateEpisodeState(number) {
  const label = $('episodes').querySelector(`[data-number="${number}"]`);
  if (!label) return;
  const state = availability.get(number) || 'checking';
  const checkbox = label.querySelector('input');
  checkbox.disabled = state !== 'available';
  checkbox.checked = selected.has(number);
  label.className = `episode ${state}`;
  label.title = state === 'unavailable' ? '本集没有所选清晰度'
    : state === 'error' ? `检测失败：${qualityErrors.get(number) || '请重新检查'}`
    : state === 'checking' ? '正在检测本集清晰度' : '符合所选清晰度';
}

function renderEpisodes() {
  const box = $('episodes');
  box.replaceChildren();
  if (currentSeries) {
    for (const episode of currentSeries.episodes) {
      const label = document.createElement('label');
      label.className = 'episode checking';
      label.dataset.number = episode.number;
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.checked = selected.has(episode.number);
      checkbox.setAttribute('aria-label', `第 ${episode.number} 集`);
      checkbox.onchange = () => {
        if (availability.get(episode.number) !== 'available') return;
        checkbox.checked ? selected.add(episode.number) : selected.delete(episode.number);
        updateSelection();
      };
      const text = document.createElement('span');
      text.textContent = `${episode.number} 集`;
      label.append(checkbox, text);
      box.append(label);
      updateEpisodeState(episode.number);
    }
  }
  updateSelection();
}

function updateSelection() {
  $('selectedCount').textContent = currentSeries ? `已选择 ${selected.size} / ${currentSeries.episodes.length} 集` : '尚未选择剧目';
  $('applyRange').disabled = !currentSeries || scanInProgress;
  $('selectAll').disabled = !currentSeries || scanInProgress;
  $('clearAll').disabled = !currentSeries || selected.size === 0;
  $('probeBtn').disabled = !currentSeries || scanInProgress;
  $('downloadBtn').disabled = !currentSeries || scanInProgress || selected.size === 0;
}

function applyRange() {
  if (!currentSeries || scanInProgress) return;
  const raw = $('range').value.trim();
  if (!raw) return status('seriesInfo', '请输入集数范围，例如 1-10, 15。', 'error');
  const allowed = new Set(currentSeries.episodes.map(item => item.number));
  const next = new Set();
  for (const part of raw.split(/[,，、;；]+/).map(item => item.trim()).filter(Boolean)) {
    const match = part.match(/^(\d+)(?:\s*[-~—]\s*(\d+))?$/);
    if (!match) return status('seriesInfo', `无法识别集数：${part}`, 'error');
    const start = Number(match[1]);
    const end = Number(match[2] || match[1]);
    if (start > end || end - start > 500 || !allowed.has(start) || !allowed.has(end)) {
      return status('seriesInfo', `集数超出该剧可用范围：${part}`, 'error');
    }
    for (let number = start; number <= end; number++) if (allowed.has(number)) next.add(number);
  }
  selected = new Set([...next].filter(number => availability.get(number) === 'available'));
  renderEpisodes();
  status('seriesInfo', `已按范围选择 ${selected.size} 集；${next.size - selected.size} 集不满足所选清晰度或检测失败。`);
}

async function scanAvailability() {
  if (!currentSeries) return;
  const token = ++scanToken;
  const series = currentSeries;
  const preference = $('quality').value;
  scanInProgress = true;
  if (lastScanSeriesId !== series.series_id || lastScanPreference !== preference) selected.clear();
  lastScanSeriesId = series.series_id;
  lastScanPreference = preference;
  availability = new Map(series.episodes.map(item => [item.number, 'checking']));
  qualityErrors = new Map();
  renderEpisodes();
  let next = 0;
  let finished = 0;
  let available = 0;
  let errors = 0;
  const total = series.episodes.length;
  status('qualityStatus', `正在检查 ${$('quality').selectedOptions[0].textContent}：0 / ${total} 集。`);
  async function worker() {
    while (token === scanToken && next < total) {
      const episode = series.episodes[next++];
      try {
        let items = qualityCache.get(episode.video_id);
        if (!items) {
          const data = await jsonPost('/api/qualities', { video_id: episode.video_id });
          items = data.items;
          if (!Array.isArray(items)) throw new Error('客户端未返回画质列表');
          qualityCache.set(episode.video_id, items);
        }
        if (token !== scanToken) return;
        const matches = preference === 'max' ? items.length > 0
          : items.some(item => item.short_edge === Number(preference.slice(5)));
        availability.set(episode.number, matches ? 'available' : 'unavailable');
        if (matches) available++;
        else selected.delete(episode.number);
      } catch (error) {
        if (token !== scanToken) return;
        availability.set(episode.number, 'error');
        qualityErrors.set(episode.number, error.message);
        selected.delete(episode.number);
        errors++;
      }
      finished++;
      updateEpisodeState(episode.number);
      updateSelection();
      if (finished % 5 === 0 || finished === total) {
        status('qualityStatus', `正在检查 ${$('quality').selectedOptions[0].textContent}：${finished} / ${total} 集。`);
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(6, total) }, worker));
  if (token !== scanToken) return;
  scanInProgress = false;
  updateSelection();
  status('qualityStatus', `检查完成：${available} 集符合，${total - available - errors} 集画质不足，${errors} 集检测失败。灰色剧集不可选择。${errors ? '可点“重新检查各集”重试失败集。' : ''}`, errors ? 'error' : 'ok');
}

async function createJob() {
  if (!currentSeries || scanInProgress || !selected.size) return;
  button('downloadBtn', true, '提交中…');
  try {
    const data = await jsonPost('/api/jobs', {
      series_id: currentSeries.series_id,
      episodes: [...selected].sort((a, b) => a - b),
      preference: $('quality').value,
    });
    status('qualityStatus', `任务 ${data.id} 已创建，正在逐集处理。`, 'ok');
    await refreshJobs();
    $('jobsTitle').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (error) {
    status('qualityStatus', error.message, 'error');
  } finally {
    $('downloadBtn').textContent = '下载所选集数';
    updateSelection();
  }
}

async function loadConfig() {
  try {
    const data = await api('/api/config');
    $('downloadDir').value = data.download_dir;
    $('downloadDirDisplay').textContent = data.download_dir;
    $('downloadDirDisplay').title = data.download_dir;
    const message = data.configured
      ? (data.ffmpeg_ready ? '服务就绪' : '未找到 FFmpeg')
      : '配置不可用';
    $('configStatus').textContent = message;
    $('configStatus').className = `settings-state ${data.configured && data.ffmpeg_ready ? 'ok' : 'error'}`;
  } catch (error) {
    $('configStatus').textContent = error.message;
    $('configStatus').className = 'settings-state error';
  }
}

async function saveConfig() {
  button('saveConfig', true, '保存中…');
  try {
    await jsonPost('/api/config', { download_dir: $('downloadDir').value.trim() });
    await loadConfig();
    $('downloadSettings').open = false;
  } catch (error) {
    $('configStatus').textContent = error.message;
    $('configStatus').className = 'settings-state error';
  } finally {
    button('saveConfig', false, '保存');
  }
}

const labels = { queued:'等待', running:'下载中', pausing:'暂停中', paused:'已暂停', interrupted:'已中断', completed:'完成', completed_with_errors:'部分失败', pending:'等待', done:'完成', error:'失败' };

function renderJobs(jobs) {
  const box = $('jobs');
  const openJobs = new Set([...box.querySelectorAll('details[data-job-id]')]
    .filter(item => item.open).map(item => item.dataset.jobId));
  box.replaceChildren();
  if (!jobs.length) {
    box.className = 'empty';
    box.textContent = '暂无任务';
    return;
  }
  box.className = '';
  for (const job of jobs) {
    const card = document.createElement('div');
    card.className = 'job';
    const head = document.createElement('div');
    head.className = 'job-head';
    const title = document.createElement('span');
    title.className = 'job-title';
    title.textContent = job.title;
    const state = document.createElement('span');
    state.textContent = labels[job.status] || job.status;
    head.append(title, state);
    const done = job.episodes.filter(item => item.status === 'done').length;
    const failed = job.episodes.filter(item => item.status === 'error').length;
    const summary = document.createElement('p');
    summary.className = 'small muted';
    summary.textContent = `${done}/${job.episodes.length} 集完成 · ${failed} 集失败 · ${job.created_at}`;
    const progress = document.createElement('div');
    progress.className = 'progress';
    const fill = document.createElement('i');
    fill.style.width = `${Math.round(done / job.episodes.length * 100)}%`;
    progress.append(fill);
    const actions = document.createElement('div');
    actions.className = 'actions';
    if (['queued', 'running'].includes(job.status)) {
      const pause = document.createElement('button');
      pause.className = 'quiet';
      pause.textContent = '暂停';
      pause.onclick = () => jobAction(job.id, 'pause');
      actions.append(pause);
    }
    if (['paused', 'interrupted', 'completed_with_errors'].includes(job.status)) {
      const resume = document.createElement('button');
      resume.className = 'secondary';
      resume.textContent = '继续 / 重试失败集';
      resume.onclick = () => jobAction(job.id, 'resume');
      actions.append(resume);
    }
    const details = document.createElement('details');
    details.dataset.jobId = job.id;
    details.open = openJobs.has(job.id);
    const toggle = document.createElement('summary');
    toggle.textContent = '查看逐集结果';
    const list = document.createElement('ol');
    list.className = 'task-list';
    for (const episode of job.episodes) {
      const item = document.createElement('li');
      item.textContent = `第 ${episode.number} 集：${labels[episode.status] || episode.status} · ${episode.message}`;
      list.append(item);
    }
    details.append(toggle, list);
    card.append(head, summary, progress, actions, details);
    if (job.message) {
      const message = document.createElement('p');
      message.className = 'small';
      message.textContent = job.message;
      card.append(message);
    }
    box.append(card);
  }
}

async function refreshJobs() {
  try {
    const data = await api('/api/jobs');
    renderJobs(data.items);
  } catch (error) {
    $('jobs').textContent = error.message;
  }
}

async function jobAction(id, action) {
  try {
    await jsonPost(`/api/jobs/${id}/${action}`);
    await refreshJobs();
  } catch (error) {
    alert(error.message);
  }
}

$('searchBtn').onclick = search;
$('query').onkeydown = event => { if (event.key === 'Enter') search(); };
$('applyRange').onclick = applyRange;
$('selectAll').onclick = () => { selected = new Set(currentSeries.episodes.filter(item => availability.get(item.number) === 'available').map(item => item.number)); renderEpisodes(); };
$('clearAll').onclick = () => { selected.clear(); renderEpisodes(); };
$('quality').onchange = scanAvailability;
$('probeBtn').onclick = scanAvailability;
$('downloadBtn').onclick = createJob;
$('saveConfig').onclick = saveConfig;
loadConfig();
refreshJobs();
setInterval(refreshJobs, 3000);
