// replay.js — player engine
let frames = [], metadata = null, teams = [];
let playing = false, playSpeed = 1;
let currentTime = 0, lastRafTime = null, rafId = null;

async function init() {
  const params = new URLSearchParams(location.search);
  const sessionId = params.get('session');
  if (!sessionId) {
    await showSessionList();
    return;
  }
  document.getElementById('player').style.display = 'flex';
  await loadRecording(sessionId);
}

async function loadRecording(sessionId) {
  // fetch metadata
  let metaResp;
  try {
    metaResp = await fetch('/api/recordings/' + sessionId + '/metadata');
  } catch (e) {
    showError('无法连接到服务器: ' + e.message);
    return;
  }
  if (!metaResp.ok) {
    showError('未找到场次: ' + sessionId + ' (HTTP ' + metaResp.status + ')');
    return;
  }
  metadata = await metaResp.json();

  document.querySelector('.session-name').textContent = '场次: ' + sessionId;
  document.querySelector('.session-type-badge').textContent = metadata.session_type || '';
  teams = metadata.teams || [];

  // fetch global teams list for display names
  try {
    const tr = await fetch('/api/teams');
    if (tr.ok) teams = await tr.json();
  } catch (e) {
    // fallback to metadata teams
  }

  // stream telemetry
  showLoading('加载遥测数据...');
  frames = [];
  let resp;
  try {
    resp = await fetch('/api/recordings/' + sessionId + '/telemetry');
  } catch (e) {
    hideLoading();
    showError('加载遥测失败: ' + e.message);
    return;
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const lines = buf.split('\n');
    buf = lines.pop();
    for (const line of lines) {
      if (line.trim()) {
        try { frames.push(JSON.parse(line)); } catch (e) { /* skip malformed */ }
      }
    }
    updateLoadProgress(frames.length);
  }
  if (buf.trim()) {
    try { frames.push(JSON.parse(buf)); } catch (e) { /* skip */ }
  }

  hideLoading();

  if (frames.length === 0) {
    showError('遥测数据为空');
    return;
  }

  const duration = metadata.duration_sim || frames[frames.length - 1].t || 0;
  metadata.duration_sim = duration;

  const timeline = document.getElementById('timeline');
  timeline.max = duration;
  timeline.step = 0.064;
  timeline.value = 0;
  currentTime = 0;

  const canvas = document.getElementById('minimap');
  drawTrackBackground(canvas);
  renderFrame(0);
  setupControls();

  // Show final rankings immediately if metadata has them
  if (metadata.final_rankings && metadata.final_rankings.length) {
    // Will be shown when playback ends; also add a button
    addFinalRankingsButton();
  }
}

function findFrameIndex(t) {
  if (frames.length === 0) return 0;
  let lo = 0, hi = frames.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (frames[mid].t <= t) lo = mid; else hi = mid - 1;
  }
  return lo;
}

function renderFrame(idx) {
  const frame = frames[idx];
  if (!frame) return;

  const canvas = document.getElementById('minimap');
  const ctx = canvas.getContext('2d');
  drawTrackBackground(canvas);
  drawCars(ctx, frame, canvas.width, canvas.height);
  updateLeaderboard(document.getElementById('leaderboard-panel'), frame, teams, metadata);
  updateEventLog(frame);

  const t = frame.t;
  const dur = metadata.duration_sim;
  document.getElementById('time-display').textContent = formatTimeLong(t) + ' / ' + formatTimeLong(dur);
  document.getElementById('timeline').value = t;
}

function formatTimeLong(t) {
  const mins = Math.floor(t / 60);
  const secs = Math.floor(t % 60);
  const cs = Math.floor((t % 1) * 100);
  return String(mins).padStart(2, '0') + ':' + String(secs).padStart(2, '0') + '.' + String(cs).padStart(2, '0');
}

function rafCallback(now) {
  if (!playing) return;
  if (lastRafTime !== null) {
    const elapsed = (now - lastRafTime) / 1000;
    currentTime = Math.min(currentTime + elapsed * playSpeed, metadata.duration_sim);
  }
  lastRafTime = now;
  renderFrame(findFrameIndex(currentTime));

  if (currentTime >= metadata.duration_sim) {
    playing = false;
    document.getElementById('btn-play').textContent = '▶';
    document.getElementById('btn-play').title = '从头播放';
    if (metadata.final_rankings && metadata.final_rankings.length) {
      showFinalRankings(document.getElementById('leaderboard-panel'), metadata.final_rankings, teams);
    }
    return;
  }
  rafId = requestAnimationFrame(rafCallback);
}

function setupControls() {
  const btnPlay = document.getElementById('btn-play');
  btnPlay.disabled = false;

  btnPlay.onclick = () => {
    if (playing) {
      playing = false;
      btnPlay.textContent = '▶';
      if (rafId) cancelAnimationFrame(rafId);
    } else {
      // If at end, restart
      if (currentTime >= metadata.duration_sim) {
        currentTime = 0;
        recentEvents = [];
      }
      playing = true;
      lastRafTime = null;
      btnPlay.textContent = '⏸';
      rafId = requestAnimationFrame(rafCallback);
    }
  };

  document.getElementById('btn-rewind').onclick = () => {
    playing = false;
    if (rafId) cancelAnimationFrame(rafId);
    document.getElementById('btn-play').textContent = '▶';
    currentTime = 0;
    recentEvents = [];
    renderFrame(0);
  };

  document.getElementById('timeline').oninput = (e) => {
    currentTime = parseFloat(e.target.value);
    renderFrame(findFrameIndex(currentTime));
  };

  document.querySelectorAll('.speed-btn').forEach(btn => {
    btn.onclick = () => {
      playSpeed = parseFloat(btn.dataset.speed);
      document.querySelectorAll('.speed-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
    };
  });
}

async function showSessionList() {
  const listEl = document.getElementById('session-list');
  listEl.style.display = 'block';
  listEl.innerHTML = '<div class="loading-msg">加载场次列表...</div>';

  let sessions;
  try {
    const resp = await fetch('/api/recordings');
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    sessions = await resp.json();
  } catch (e) {
    listEl.innerHTML = '<div class="error-msg">加载失败: ' + e.message + '</div>';
    return;
  }

  if (!sessions.length) {
    listEl.innerHTML = '<h2>AI Racer 回放</h2><p class="empty-msg">暂无录制场次</p>';
    return;
  }

  const typeLabels = {
    qualifying: '排位赛',
    group_race: '分组赛',
    semi: '半决赛',
    final: '决赛',
    test: '测试'
  };

  listEl.innerHTML = '<h2>选择场次回放</h2>' + sessions.map(s => {
    const typeLabel = typeLabels[s.session_type] || s.session_type || '未知';
    const finishBadge = s.finish_reason
      ? `<span class="finish-badge finish-${s.finish_reason}">${s.finish_reason}</span>`
      : '';
    const teamsStr = s.teams && s.teams.length
      ? s.teams.slice(0, 4).join(', ') + (s.teams.length > 4 ? ` +${s.teams.length - 4}` : '')
      : '';
    return `<div class="session-card">
      <div class="session-info">
        <div class="session-id">${s.session_id}</div>
        <div class="session-meta">
          <span class="type-badge">${typeLabel}</span>
          ${s.recorded_at ? `<span class="session-time">${s.recorded_at}</span>` : ''}
          ${finishBadge}
        </div>
        ${teamsStr ? `<div class="session-teams">参赛: ${teamsStr}</div>` : ''}
      </div>
      <a class="btn-play-session" href="?session=${encodeURIComponent(s.session_id)}">▶ 播放</a>
    </div>`;
  }).join('');
}

let recentEvents = [];
function updateEventLog(frame) {
  if (frame.events && frame.events.length > 0) {
    for (const ev of frame.events) {
      let msg = '';
      if (ev.type === 'lap_complete') {
        msg = `[${formatTime(frame.t)}] ${ev.team_id} 完成第${ev.lap_number}圈 ${parseFloat(ev.lap_time).toFixed(3)}s`;
      } else if (ev.type === 'collision') {
        msg = `[${formatTime(frame.t)}] ${ev.team_id} 碰撞 (${ev.severity === 'major' ? '重' : '轻'})`;
      } else if (ev.type === 'leader_finished') {
        msg = `[${formatTime(frame.t)}] ${ev.team_id} 率先完赛，宽限60s`;
      } else if (ev.type === 'race_end') {
        msg = `[${formatTime(frame.t)}] 比赛结束`;
      } else if (ev.type === 'powerup_pick') {
        msg = `[${formatTime(frame.t)}] ${ev.team_id} 获得加速包`;
      } else if (ev.type === 'timeout') {
        msg = `[${formatTime(frame.t)}] ${ev.team_id} 超时警告`;
      }
      if (msg) {
        recentEvents.unshift(msg);
        if (recentEvents.length > 8) recentEvents.pop();
      }
    }
  }
  const logEl = document.getElementById('event-log');
  if (recentEvents.length === 0) {
    logEl.textContent = '暂无事件';
    logEl.style.color = '#555';
  } else {
    logEl.style.color = '';
    logEl.innerHTML = recentEvents.map((ev, i) =>
      `<span class="ev-item${i === 0 ? ' ev-new' : ''}">${ev}</span>`
    ).join('<span class="ev-sep"> · </span>');
  }
}

function formatTime(t) {
  return String(Math.floor(t / 60)).padStart(2, '0') + ':' + String(Math.floor(t % 60)).padStart(2, '0');
}

function addFinalRankingsButton() {
  const controls = document.getElementById('controls');
  if (document.getElementById('btn-final')) return;
  const btn = document.createElement('button');
  btn.id = 'btn-final';
  btn.textContent = '最终排名';
  btn.style.marginLeft = '8px';
  btn.onclick = () => {
    showFinalRankings(document.getElementById('leaderboard-panel'), metadata.final_rankings, teams);
  };
  controls.appendChild(btn);
}

function showError(msg) {
  const player = document.getElementById('player');
  const errEl = document.createElement('div');
  errEl.className = 'error-banner';
  errEl.textContent = msg;
  player.prepend(errEl);
}

function showLoading(msg) {
  const existing = document.getElementById('loading-overlay');
  if (existing) existing.remove();
  document.body.insertAdjacentHTML('beforeend',
    `<div id="loading-overlay">
      <div class="loading-spinner"></div>
      <div class="loading-msg">${msg}</div>
      <div id="load-progress"></div>
    </div>`
  );
}

function updateLoadProgress(n) {
  const el = document.getElementById('load-progress');
  if (el) el.textContent = '已加载 ' + n + ' 帧';
}

function hideLoading() {
  const el = document.getElementById('loading-overlay');
  if (el) el.remove();
}

window.addEventListener('load', init);
