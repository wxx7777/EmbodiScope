import * as THREE from "/vendor/three.module.min.js";

const state = {
  dataset: null,
  library: null,
  adapters: [],
  profiles: null,
  benchmark: null,
  repairBenchmark: null,
  benchmarkSuite: "fault",
  embodied: null,
  taskReasoning: null,
  reasoningEpisode: null,
  audit: null,
  repair: null,
  batchRepair: { activeJob: null, pollTimer: null },
  workspaceMode: "diagnostics",
  episode: null,
  activeEpisode: null,
  activeSignals: ["ee_x", "ee_y", "ee_z"],
  signalColors: {
    ee_speed: "#147d73", ee_x: "#147d73", ee_y: "#38679b", ee_z: "#b87513", camera_motion: "#7b5ca8",
    force_z: "#c53e3a", gripper: "#5f6966", object_distance: "#cb6e38",
    joint_1: "#147d73", joint_2: "#38679b", joint_3: "#b87513",
    joint_4: "#7b5ca8", joint_5: "#c53e3a", joint_6: "#607d5a",
    state_speed: "#147d73", state_1: "#147d73", state_2: "#38679b", state_3: "#7b5ca8",
    action_1: "#b87513", action_2: "#c53e3a", action_3: "#607d5a", reward: "#7b5ca8"
  },
  signalNames: {
    ee_speed: "末端速度", ee_x: "末端 X", ee_y: "末端 Y", ee_z: "末端 Z", camera_motion: "视觉运动",
    force_z: "Z 轴力", gripper: "夹爪开度", object_distance: "目标距离",
    joint_1: "关节 1", joint_2: "关节 2", joint_3: "关节 3", joint_4: "关节 4", joint_5: "关节 5", joint_6: "关节 6",
    state_speed: "状态速度", state_1: "状态 X", state_2: "状态 Y", state_3: "状态 3",
    action_1: "动作 X", action_2: "动作 Y", action_3: "动作 3", reward: "奖励"
  },
  media: { info: null, start: 0, end: 0, duration: 0 },
  simulation: {
    catalog: null, activeJob: null, pollTimer: null, replay: null, index: 0,
    renderer: null, scene: null, camera: null, world: null, segments: [], joints: [],
    object: null, goal: null, tcp: null, path: null
  },
  recovery: {
    catalog: null, activeJob: null, pollTimer: null, result: null,
    failureReplay: null, recoveredReplay: null, playing: false, resultStale: false,
    benchmarkJob: null, benchmarkPollTimer: null, benchmarkResult: null
  },
  spatial: {
    renderer: null, scene: null, camera: null, world: null, marker: null, progressLine: null,
    data: null, index: 0, playing: false, playbackStartedAt: 0, playbackTime: 0,
    target: new THREE.Vector3(), distance: 1.8, yaw: -0.72, pitch: 0.48,
    dragging: false, pointerX: 0, pointerY: 0
  }
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const format = (value, digits = 1) => Number(value).toFixed(digits);

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const contentType = response.headers.get("content-type") || "";
  const body = contentType.includes("json") ? await response.json() : await response.text();
  if (!response.ok) throw new Error(body.error || body || "请求失败");
  return body;
}

function toast(message, error = false) {
  const node = $("#toast");
  node.textContent = message;
  node.style.background = error ? "#9e302d" : "#17201f";
  node.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { node.hidden = true; }, 3200);
}

const compactNumber = value => Number(value || 0).toLocaleString("zh-CN");
const compactBytes = value => value >= 1024 * 1024 ? `${format(value / 1024 / 1024, 1)} MB` : `${format(value / 1024, 0)} KB`;

async function loadDatasetLibrary() {
  state.library = await api("/api/datasets");
  renderDatasetLibrary();
}

function renderDatasetLibrary() {
  const library = state.library;
  if (!library) return;
  $("#libraryCount").textContent = library.available_count;
  $("#libraryDatasetTotal").textContent = library.available_count;
  $("#libraryEpisodeTotal").textContent = compactNumber(library.episode_count);
  $("#libraryRowTotal").textContent = compactNumber(library.row_count);
  $("#datasetLibraryGrid").innerHTML = library.datasets.map(dataset => `
    <article class="dataset-library-item ${dataset.active ? "active" : ""} ${dataset.featured ? "featured" : ""}">
      <header><span>${escapeHtml(dataset.origin)}</span><b>${escapeHtml(dataset.license)}</b></header>
      <h3>${escapeHtml(dataset.name)}</h3>
      <p>${escapeHtml(dataset.description)}</p>
      <div class="dataset-stats"><span><strong>${compactNumber(dataset.episode_count)}</strong> Episodes</span><span><strong>${compactNumber(dataset.row_count)}</strong> 采样点</span><span><strong>${compactBytes(dataset.size_bytes)}</strong> 本地</span></div>
      <div class="dataset-modalities">${dataset.modalities.map(item => `<i>${escapeHtml(item)}</i>`).join("")}</div>
      <footer>
        ${dataset.source_url ? `<a href="${escapeHtml(dataset.source_url)}" target="_blank" rel="noreferrer">查看来源</a>` : `<span>${escapeHtml(dataset.format)}</span>`}
        <button class="button ${dataset.active ? "secondary" : "primary"} dataset-load" data-dataset="${escapeHtml(dataset.id)}" type="button" ${!dataset.available || dataset.active ? "disabled" : ""}>${dataset.active ? "当前数据" : dataset.available ? "载入数据" : "未安装"}</button>
      </footer>
    </article>`).join("");
  $$(".dataset-load").forEach(button => button.addEventListener("click", () => loadLibraryDataset(button.dataset.dataset)));
}

async function loadLibraryDataset(datasetId) {
  const button = $(`.dataset-load[data-dataset="${CSS.escape(datasetId)}"]`);
  if (button) { button.disabled = true; button.textContent = "正在分析"; }
  try {
    await api("/api/datasets/load", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ dataset_id: datasetId })
    });
    await Promise.all([loadDataset(), loadDatasetLibrary()]);
    setDatasetLibraryModal(false);
    toast(`已载入 ${state.dataset.episode_count} 条 Episode`);
  } catch (error) { toast(error.message, true); }
  finally { if (button) button.disabled = false; }
}

async function loadDataset(preferredEpisode = null) {
  const dataset = await api("/api/dataset");
  state.dataset = dataset;
  state.audit = null;
  state.embodied = null;
  state.taskReasoning = null;
  state.reasoningEpisode = null;
  clearTimeout(state.batchRepair.pollTimer);
  state.batchRepair.activeJob = null;
  $("#batchRepairJob").hidden = true;
  $("#batchRepairResult").hidden = true;
  $("#startBatchRepair").disabled = false;
  renderDataset(dataset);
  const target = preferredEpisode && dataset.episodes.some(item => item.episode_id === preferredEpisode)
    ? preferredEpisode : dataset.episodes[0]?.episode_id;
  if (target) await selectEpisode(target);
  if (state.workspaceMode === "audit") await loadAudit();
}

function renderDataset(dataset) {
  $("#datasetName").textContent = dataset.dataset_name;
  $("#sourceFormat").textContent = dataset.source_format || "DATA";
  $("#sourceAdapter").textContent = dataset.adapter_name || "数据适配器";
  const profileName = dataset.analysis_profile?.name || "通用诊断配置";
  const detail = dataset.source_warnings?.[0] || `${dataset.column_count} 个标准化字段 · ${profileName}`;
  $("#sourceDetail").textContent = detail;
  $("#modalCurrentSource").textContent = `${dataset.adapter_name || "未知适配器"} · ${dataset.dataset_name}`;
  $("#modalSourceWarnings").textContent = dataset.source_warnings?.length ? `${dataset.source_warnings.length} 条导入提示` : "格式已标准化";
  $("#episodeCount").textContent = dataset.episode_count;
  $("#successRate").textContent = dataset.success_rate == null ? "—" : `${format(dataset.success_rate, 0)}%`;
  $("#averageScore").textContent = format(dataset.average_score, 1);
  $("#criticalCount").textContent = dataset.critical_episodes;
  renderEpisodeList();
}

function renderEpisodeList(query = "") {
  const list = $("#episodeList");
  const episodes = state.dataset.episodes.filter(item =>
    item.episode_id.toLowerCase().includes(query.toLowerCase()) || item.primary_cause.includes(query)
  );
  list.innerHTML = episodes.map(item => `
    <button class="episode-item ${item.success_known && !item.success ? "failed" : ""} ${item.episode_id === state.activeEpisode ? "active" : ""}" data-episode="${escapeHtml(item.episode_id)}" type="button">
      <i></i><div><strong>${escapeHtml(item.episode_id)}</strong><small>${escapeHtml(item.primary_cause)}</small></div><span>${format(item.quality_score, 0)}</span>
    </button>`).join("");
  $$(".episode-item").forEach(button => button.addEventListener("click", () => selectEpisode(button.dataset.episode)));
}

async function selectEpisode(episodeId) {
  state.activeEpisode = episodeId;
  state.repair = null;
  resetRepairWorkbench();
  renderEpisodeList($("#episodeSearch").value);
  $("#dashboard").hidden = true;
  $("#loadingState").style.display = "flex";
  try {
    const episode = await api(`/api/episode/${encodeURIComponent(episodeId)}`);
    state.episode = episode;
    chooseDefaultSignals(episode);
    renderEpisode(episode);
    $("#loadingState").style.display = "none";
    $("#dashboard").hidden = false;
    requestAnimationFrame(drawChart);
    if ($("#panel-spatial").classList.contains("active")) requestAnimationFrame(() => renderSpatialScene(true));
  } catch (error) {
    toast(error.message, true);
  }
}

function chooseDefaultSignals(episode) {
  const available = Object.keys(episode.timeseries.signals);
  const preferred = ["ee_x", "ee_y", "ee_z"].filter(key => available.includes(key));
  state.activeSignals = preferred.length ? preferred : available.slice(0, 3);
}

function renderEpisode(episode) {
  $("#headDataset").textContent = episode.dataset_name;
  $("#episodeTitle").textContent = episode.episode_id;
  $("#episodeMeta").textContent = `${episode.rows} 个数据点 · ${format(episode.duration, 2)} 秒 · ${format(episode.sample_rate, 1)} Hz`;
  const status = $("#episodeStatus");
  status.textContent = !episode.success_known ? "结果未标注" : episode.success ? "任务成功" : "任务失败";
  status.className = `status-badge ${episode.success_known && !episode.success ? "failed" : !episode.success_known ? "unknown" : ""}`;
  $("#qualityScore").textContent = format(episode.quality_score, 0);
  $("#qualityGrade").textContent = `质量等级 ${episode.grade}`;
  $("#scoreRing").style.borderColor = scoreColor(episode.quality_score);
  $("#metricMissing").textContent = `${format(episode.metrics.missing_rate, episode.metrics.missing_rate < 1 ? 2 : 1)}%`;
  $("#metricSync").textContent = `${episode.metrics.sync_offset_ms > 0 ? "+" : ""}${format(episode.metrics.sync_offset_ms, 0)} ms`;
  $("#metricJumps").textContent = episode.metrics.joint_jumps;
  $("#metricForce").textContent = episode.metrics.force_spikes;
  $("#metricIssues").textContent = episode.issues.length;
  const criticalCount = episode.issues.filter(issue => issue.severity === "critical").length;
  $("#criticalHint").textContent = `${criticalCount} 个严重问题`;
  setMetricClass("#metricMissing", episode.metrics.missing_rate > 2 ? "alert" : episode.metrics.missing_rate > 0 ? "warn" : "");
  setMetricClass("#metricSync", Math.abs(episode.metrics.sync_offset_ms) >= 150 ? "alert" : Math.abs(episode.metrics.sync_offset_ms) >= 80 ? "warn" : "");
  setMetricClass("#metricJumps", episode.metrics.joint_jumps ? "alert" : "");
  setMetricClass("#metricForce", episode.metrics.force_spikes ? "alert" : "");
  setMetricClass("#metricIssues", criticalCount ? "alert" : episode.issues.length ? "warn" : "");
  $("#issueTabCount").textContent = episode.issues.length;
  renderScores(episode.scores);
  renderRootCauses(episode.root_causes, episode.success, episode.success_known);
  renderTimeline(episode.events, episode.duration);
  renderSignalControls(episode.timeseries.signals);
  renderSync(episode.metrics);
  renderIssues(episode.issues);
  renderReport(episode);
  prepareSpatial(episode);
  prepareMedia(episode);
  $("#chartRange").textContent = `${format(episode.timeseries.timestamp[0], 2)} s — ${format(episode.timeseries.timestamp.at(-1), 2)} s`;
}

function setMetricClass(selector, className) { $(selector).className = className; }
function scoreColor(score) { return score >= 85 ? "#147d73" : score >= 68 ? "#b87513" : "#c53e3a"; }

function renderScores(scores) {
  const names = { completeness: "完整性", temporal: "时序质量", motion: "运动质量", sync: "多模态同步", safety: "安全性" };
  $("#scoreBars").innerHTML = Object.entries(scores).map(([key, value]) => `
    <div class="score-bar-row"><span>${names[key]}</span><div class="score-bar"><i class="${value < 65 ? "bad" : value < 82 ? "warn" : ""}" style="width:${value}%"></i></div><strong>${format(value, 0)}</strong></div>
  `).join("");
}

function renderRootCauses(causes, success, successKnown = true) {
  const node = $("#rootCauses");
  if (!causes.length) {
    node.innerHTML = `<div class="empty-state"><div><strong>${!successKnown ? "未发现质量异常" : success ? "未发现失败模式" : "证据不足"}</strong><span>${!successKnown ? "当前数据集没有提供任务成功标签" : success ? "当前信号未触发严重诊断规则" : "建议补充视觉与控制器日志"}</span></div></div>`;
    return;
  }
  node.innerHTML = `<div class="cause-list">${causes.map((cause, index) => `
    <div class="cause-item"><span class="cause-rank">${index + 1}</span><div><strong>${escapeHtml(cause.label)}</strong><p>${escapeHtml(cause.reason)}</p></div><span class="confidence ${cause.confidence === "中" ? "medium" : ""}">${cause.confidence}置信</span></div>
  `).join("")}</div>`;
}

function renderTimeline(events, duration) {
  const node = $("#eventTimeline");
  let html = `<div class="timeline-axis"></div>`;
  for (let index = 0; index <= 4; index++) {
    const position = index * 25;
    html += `<span class="timeline-tick" style="left:${position}%">${format(duration * index / 4, 1)}s</span>`;
  }
  events.forEach(event => {
    const position = Math.max(0, Math.min(100, event.time / Math.max(duration, .001) * 100));
    html += `<button class="timeline-event ${event.severity}" data-time="${event.time}" style="left:${position}%" type="button"><span>${escapeHtml(event.label)} · ${format(event.time, 2)}s</span></button>`;
  });
  if (!events.length) html += `<div class="empty-state"><div><strong>时间轴干净</strong><span>未定位到异常事件</span></div></div>`;
  node.innerHTML = html;
  $$(".timeline-event").forEach(button => button.addEventListener("click", () => {
    if (state.media.info?.available) seekMedia(Number(button.dataset.time));
    activateTab("signals");
    drawChart(Number(button.dataset.time));
  }));
}

function renderSignalControls(signals) {
  const keys = Object.keys(signals);
  $("#signalControls").innerHTML = keys.map(key => `<button class="signal-control ${state.activeSignals.includes(key) ? "active" : ""}" data-signal="${key}" type="button">${signalName(key)}</button>`).join("");
  $$(".signal-control").forEach(button => button.addEventListener("click", () => {
    const key = button.dataset.signal;
    if (state.activeSignals.includes(key)) {
      if (state.activeSignals.length === 1) return;
      state.activeSignals = state.activeSignals.filter(item => item !== key);
    } else {
      if (state.activeSignals.length >= 4) state.activeSignals.shift();
      state.activeSignals.push(key);
    }
    renderSignalControls(signals);
    drawChart();
  }));
}

function signalName(key) {
  if (state.signalNames[key]) return state.signalNames[key];
  const match = key.match(/^(state|action|joint)_(\d+)$/);
  if (match) return `${{ state: "状态", action: "动作", joint: "关节" }[match[1]]} ${match[2]}`;
  return key;
}

function prepareMedia(episode) {
  const video = $("#datasetVideo");
  video.pause();
  state.media.info = episode.media || { available: false };
  state.media.start = Number(episode.media?.start || 0);
  state.media.end = Number(episode.media?.end || 0);
  state.media.duration = Number(episode.media?.duration || 0);
  $("#mediaTabState").textContent = episode.media?.available ? "RGB" : "—";
  $("#mediaFeature").textContent = episode.media?.feature || "RGB";
  $("#mediaScrubber").max = Math.max(.01, state.media.duration);
  $("#mediaScrubber").value = 0;
  $("#mediaTime").textContent = "0.00 s";
  $("#mediaProgress").textContent = `0.00 / ${format(state.media.duration, 2)} s`;
  if (!episode.media?.available) {
    video.hidden = true;
    $("#mediaEmpty").hidden = false;
    video.removeAttribute("src");
    video.load();
    return;
  }
  video.hidden = false;
  $("#mediaEmpty").hidden = true;
  const source = new URL(episode.media.url, location.href).href;
  if (video.src !== source) video.src = source;
  const initialize = () => { if (video.readyState >= 1) video.currentTime = state.media.start; };
  if (video.readyState >= 1) initialize(); else video.addEventListener("loadedmetadata", initialize, { once: true });
}

function seekMedia(localTime) {
  if (!state.media.info?.available) return;
  const local = Math.max(0, Math.min(state.media.duration, Number(localTime) || 0));
  const video = $("#datasetVideo");
  video.currentTime = state.media.start + local;
  $("#mediaScrubber").value = local;
  $("#mediaTime").textContent = `${format(local, 2)} s`;
  $("#mediaProgress").textContent = `${format(local, 2)} / ${format(state.media.duration, 2)} s`;
}

function renderSync(metrics) {
  const offset = metrics.sync_offset_ms;
  const node = $(".sync-explainer");
  const abnormal = Math.abs(offset) >= 80 && metrics.sync_confidence >= .35;
  node.classList.toggle("alert", abnormal);
  $("#syncTitle").textContent = abnormal ? `检测到 ${Math.abs(offset).toFixed(0)} ms 时间错位` : "同步状态良好";
  $("#syncDescription").textContent = abnormal
    ? `视觉运动信号相对机器人状态${offset > 0 ? "滞后" : "超前"}，建议在导出阶段校正 ${Math.abs(offset).toFixed(0)} ms 后重新评测。相关置信度 ${metrics.sync_confidence.toFixed(2)}。`
    : `视觉变化与机器人末端运动处于同一时间基准，估计偏移 ${offset.toFixed(0)} ms。`;
}

function renderIssues(issues) {
  const counts = {
    critical: issues.filter(item => item.severity === "critical").length,
    warning: issues.filter(item => item.severity === "warning").length,
    info: issues.filter(item => item.severity === "info").length
  };
  $("#issueSummary").innerHTML = `
    <div class="issue-chip"><i style="background:#c53e3a"></i><div><strong>${counts.critical}</strong><span>严重问题</span></div></div>
    <div class="issue-chip"><i style="background:#b87513"></i><div><strong>${counts.warning}</strong><span>警告问题</span></div></div>
    <div class="issue-chip"><i style="background:#38679b"></i><div><strong>${counts.info}</strong><span>提示信息</span></div></div>
    <div class="issue-chip note">按严重级别与任务风险排序</div>`;
  const labels = { critical: "严重", warning: "警告", info: "提示" };
  $("#issueTableBody").innerHTML = issues.length ? issues.map(issue => `
    <tr><td><span class="severity ${issue.severity}">${labels[issue.severity]}</span></td>
    <td><strong class="issue-title">${escapeHtml(issue.title)}</strong><span class="issue-evidence">${escapeHtml(issue.description)}${issue.evidence ? `<br>证据：${escapeHtml(issue.evidence)}` : ""}</span></td>
    <td class="time-range">${issue.start_time == null ? "全局" : `${format(issue.start_time, 2)} — ${format(issue.end_time ?? issue.start_time, 2)} s`}</td>
    <td>${escapeHtml(issue.recommendation)}</td></tr>`).join("") : `<tr><td colspan="4"><div class="empty-state"><div><strong>没有需要处理的问题</strong><span>当前 episode 通过全部诊断规则</span></div></div></td></tr>`;
}

function resetRepairWorkbench() {
  const button = $("#generateRepair");
  if (!button) return;
  button.disabled = false;
  button.textContent = "生成清洗方案";
  $("#repairLoading").hidden = true;
  $("#repairResult").hidden = true;
}

function repairValue(value) {
  if (value == null) return "缺失";
  if (typeof value === "number") return Math.abs(value) >= 100 ? value.toFixed(2) : value.toFixed(5).replace(/0+$/, "").replace(/\.$/, "");
  return String(value);
}

function renderRepair(result) {
  state.repair = result;
  const summary = result.summary;
  $("#repairRetained").textContent = `${format(summary.retained_rate, 1)}%`;
  $("#repairRetainedRows").textContent = `${summary.retained_rows} / ${summary.source_rows} 行可用`;
  $("#repairModified").textContent = compactNumber(summary.modified_rows);
  $("#repairQuarantined").textContent = compactNumber(summary.quarantined_rows);
  $("#repairSegments").textContent = compactNumber(summary.segment_count);
  $("#repairIssueDelta").textContent = `${summary.before_issue_count} → ${summary.after_issue_count}`;
  $("#repairStatus").textContent = result.status === "ready" ? "通过训练质量门" : "风险片段已隔离，仍需复核";
  $("#repairActionCount").textContent = `${result.actions.length} ACTIONS`;
  const kindLabels = { correction: "数值校正", segmentation: "片段切分", quarantine: "风险隔离" };
  $("#repairActions").innerHTML = result.actions.length ? result.actions.map(action => `
    <article class="repair-action">
      <span class="repair-kind ${action.kind}">${kindLabels[action.kind] || escapeHtml(action.kind)}</span>
      <div><strong>${escapeHtml(action.title)}</strong><p title="${escapeHtml(action.description)}">${escapeHtml(action.description)}</p></div>
      <small>${compactNumber(action.row_count)} 行${action.cells ? ` · ${compactNumber(action.cells)} 单元格` : ""}</small>
    </article>`).join("") : `<div class="empty-state"><div><strong>无需执行清洗动作</strong><span>当前 Episode 可保留原始数据</span></div></div>`;

  const sourceHash = result.provenance.source_sha256;
  const artifactHash = result.provenance.artifact_sha256;
  $("#repairSourceHash").textContent = sourceHash;
  $("#repairSourceHash").title = sourceHash;
  $("#repairArtifactHash").textContent = artifactHash;
  $("#repairArtifactHash").title = artifactHash;
  $("#repairPolicy").textContent = `短缺口上限 ${result.policy.short_gap_limit} 个采样点；不补造时间样本，真实物理事件保持原值，训练入口由 quality_valid 控制。`;
  $("#downloadRepairCsv").href = result.downloads.csv;
  $("#downloadRepairManifest").href = result.downloads.manifest;

  $("#repairPreviewBody").innerHTML = result.preview_rows.length ? result.preview_rows.map(row => {
    const changes = Object.entries(row.changes).map(([column, values]) => `<code>${escapeHtml(column)}: ${escapeHtml(repairValue(values.before))} → ${escapeHtml(repairValue(values.after))}</code>`).join("");
    return `<tr><td>${row.source_row}</td><td>${format(row.timestamp, 3)} s</td><td><span class="${row.quality_valid ? "repair-row-valid" : "repair-row-invalid"}">${row.quality_valid ? "可训练" : "已隔离"}</span></td><td>${row.actions.map(code => escapeHtml(code)).join("<br>")}</td><td>${changes || "仅添加质量标记，原始测量未修改"}</td></tr>`;
  }).join("") : `<tr><td colspan="5" class="audit-empty-row">没有数值变化或风险隔离记录</td></tr>`;
  $("#repairPreviewState").textContent = result.preview_truncated ? `前 ${result.preview_rows.length} 行 / 已截断` : `${result.preview_rows.length} ROWS`;
  $("#repairResult").hidden = false;
}

async function generateRepair() {
  if (!state.activeEpisode) return;
  const button = $("#generateRepair");
  button.disabled = true;
  button.textContent = "正在生成";
  $("#repairLoading").hidden = false;
  $("#repairResult").hidden = true;
  try {
    const result = await api(`/api/repair/${encodeURIComponent(state.activeEpisode)}`);
    renderRepair(result);
    button.textContent = "重新生成";
    toast("清洗方案与来源指纹已生成");
  } catch (error) {
    button.textContent = "生成清洗方案";
    toast(error.message, true);
  } finally {
    button.disabled = false;
    $("#repairLoading").hidden = true;
  }
}

function renderReport(episode) {
  const labels = { completeness: "完整性", temporal: "时序质量", motion: "运动质量", sync: "多模态同步", safety: "安全性" };
  $("#reportPreview").innerHTML = `
    <h1>EmbodiScope 具身数据质量诊断报告</h1>
    <p>数据集：${escapeHtml(episode.dataset_name)}　 Episode：${escapeHtml(episode.episode_id)}　任务结果：${!episode.success_known ? "未标注" : episode.success ? "成功" : "失败"}</p>
    <h2>诊断结论</h2><p>综合质量分 <strong>${episode.quality_score} / 100（${episode.grade}）</strong>，共发现 ${episode.issues.length} 个问题，其中 ${episode.issues.filter(item => item.severity === "critical").length} 个严重问题。</p>
    <h2>质量维度</h2><table><thead><tr><th>维度</th><th>得分</th><th>状态</th></tr></thead><tbody>${Object.entries(episode.scores).map(([key, value]) => `<tr><td>${labels[key]}</td><td>${value}</td><td>${value >= 85 ? "良好" : value >= 65 ? "需关注" : "不合格"}</td></tr>`).join("")}</tbody></table>
    <h2>主要发现</h2>${episode.issues.length ? episode.issues.slice(0, 4).map((issue, index) => `<h3>${index + 1}. ${escapeHtml(issue.title)}</h3><p>${escapeHtml(issue.description)} 建议：${escapeHtml(issue.recommendation)}</p>`).join("") : "<p>未发现超过当前阈值的质量异常。</p>"}
    <h2>失败根因</h2>${episode.root_causes.length ? `<ol>${episode.root_causes.map(cause => `<li><strong>${escapeHtml(cause.label)}</strong>（${cause.confidence}置信）：${escapeHtml(cause.reason)}</li>`).join("")}</ol>` : "<p>当前 episode 未发现明确失败模式。</p>"}`;
}

function prepareSpatial(episode) {
  stopSpatialPlayback();
  const signals = episode.timeseries.signals;
  const timestamps = episode.timeseries.timestamp;
  const hasTrajectory = ["ee_x", "ee_y", "ee_z"].every(key => Array.isArray(signals[key]));
  const points = [];
  if (hasTrajectory) {
    for (let index = 0; index < timestamps.length; index++) {
      const values = [signals.ee_x[index], signals.ee_y[index], signals.ee_z[index]];
      if (values.every(value => value != null && Number.isFinite(Number(value)))) {
        points.push({ values: values.map(Number), time: Number(timestamps[index]), sourceIndex: index });
      }
    }
  }
  state.spatial.data = { points, events: episode.events || [] };
  state.spatial.index = 0;
  $("#spatialScrubber").max = String(Math.max(0, points.length - 1));
  $("#spatialScrubber").value = "0";
  $("#spatialEmpty").hidden = points.length > 1;
  $("#spatialPlay").disabled = points.length <= 1;
  updateSpatialReadout();
}

function initSpatialScene() {
  if (state.spatial.renderer) return;
  const canvas = $("#spatialCanvas");
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0xeef2ef);
  scene.fog = new THREE.Fog(0xeef2ef, 3.5, 8);
  const camera = new THREE.PerspectiveCamera(42, 1, 0.01, 100);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x718079, 2.15));
  const keyLight = new THREE.DirectionalLight(0xffffff, 2.3);
  keyLight.position.set(3, 5, 4);
  scene.add(keyLight);
  const grid = new THREE.GridHelper(4, 20, 0xaab7b1, 0xd7ded9);
  grid.material.transparent = true;
  grid.material.opacity = 0.7;
  scene.add(grid);
  scene.add(new THREE.AxesHelper(0.32));
  const world = new THREE.Group();
  scene.add(world);
  state.spatial.renderer = renderer;
  state.spatial.scene = scene;
  state.spatial.camera = camera;
  state.spatial.world = world;

  canvas.addEventListener("pointerdown", event => {
    state.spatial.dragging = true;
    state.spatial.pointerX = event.clientX;
    state.spatial.pointerY = event.clientY;
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointermove", event => {
    if (!state.spatial.dragging) return;
    state.spatial.yaw -= (event.clientX - state.spatial.pointerX) * 0.008;
    state.spatial.pitch = Math.max(-0.05, Math.min(1.45, state.spatial.pitch + (event.clientY - state.spatial.pointerY) * 0.006));
    state.spatial.pointerX = event.clientX;
    state.spatial.pointerY = event.clientY;
    updateSpatialCamera();
    renderSpatialFrame();
  });
  canvas.addEventListener("pointerup", event => {
    state.spatial.dragging = false;
    if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
  });
  canvas.addEventListener("wheel", event => {
    event.preventDefault();
    state.spatial.distance = Math.max(0.35, Math.min(12, state.spatial.distance * Math.exp(event.deltaY * 0.0012)));
    updateSpatialCamera();
    renderSpatialFrame();
  }, { passive: false });
}

function clearSpatialWorld() {
  const world = state.spatial.world;
  if (!world) return;
  while (world.children.length) {
    const child = world.children.pop();
    child.geometry?.dispose();
    if (Array.isArray(child.material)) child.material.forEach(material => material.dispose());
    else child.material?.dispose();
  }
  state.spatial.marker = null;
  state.spatial.progressLine = null;
}

function spatialVector(point) {
  return new THREE.Vector3(point.values[0], point.values[2], -point.values[1]);
}

function renderSpatialScene(resetCamera = false) {
  initSpatialScene();
  resizeSpatialRenderer();
  clearSpatialWorld();
  const points = state.spatial.data?.points || [];
  if (points.length <= 1) {
    renderSpatialFrame();
    return;
  }
  const vectors = points.map(spatialVector);
  const bounds = new THREE.Box3().setFromPoints(vectors);
  bounds.getCenter(state.spatial.target);
  const size = new THREE.Vector3();
  bounds.getSize(size);
  const maxDimension = Math.max(size.x, size.y, size.z, 0.2);
  if (resetCamera) state.spatial.distance = Math.max(0.75, maxDimension * 3.25);

  const pathGeometry = new THREE.BufferGeometry().setFromPoints(vectors);
  const pathMaterial = new THREE.LineBasicMaterial({ color: 0x147d73, transparent: true, opacity: 0.42 });
  state.spatial.world.add(new THREE.Line(pathGeometry, pathMaterial));
  const progressGeometry = new THREE.BufferGeometry().setFromPoints(vectors.slice(0, 2));
  const progressMaterial = new THREE.LineBasicMaterial({ color: 0x0b5f57 });
  const progressLine = new THREE.Line(progressGeometry, progressMaterial);
  state.spatial.world.add(progressLine);
  state.spatial.progressLine = progressLine;

  const marker = new THREE.Mesh(
    new THREE.SphereGeometry(Math.max(maxDimension * 0.034, 0.012), 24, 16),
    new THREE.MeshStandardMaterial({ color: 0xb87513, roughness: 0.35, metalness: 0.08 })
  );
  state.spatial.world.add(marker);
  state.spatial.marker = marker;
  const startMarker = new THREE.Mesh(
    new THREE.SphereGeometry(Math.max(maxDimension * 0.018, 0.007), 16, 12),
    new THREE.MeshStandardMaterial({ color: 0x147d73 })
  );
  startMarker.position.copy(vectors[0]);
  state.spatial.world.add(startMarker);

  for (const event of state.spatial.data.events) {
    const eventTime = Number(event.time);
    const nearest = points.reduce(
      (best, point, index) => Math.abs(point.time - eventTime) < best.distance ? { index, distance: Math.abs(point.time - eventTime) } : best,
      { index: 0, distance: Infinity }
    );
    const eventMarker = new THREE.Mesh(
      new THREE.OctahedronGeometry(Math.max(maxDimension * 0.026, 0.01)),
      new THREE.MeshStandardMaterial({ color: event.severity === "critical" ? 0xc53e3a : 0xb87513, roughness: 0.45 })
    );
    eventMarker.position.copy(vectors[nearest.index]);
    state.spatial.world.add(eventMarker);
  }
  updateSpatialCamera();
  setSpatialIndex(Math.min(state.spatial.index, points.length - 1), false);
  renderSpatialFrame();
}

function updateSpatialCamera() {
  const { camera, target, distance, yaw, pitch } = state.spatial;
  if (!camera) return;
  const horizontal = Math.cos(pitch) * distance;
  camera.position.set(
    target.x + Math.sin(yaw) * horizontal,
    target.y + Math.sin(pitch) * distance,
    target.z + Math.cos(yaw) * horizontal
  );
  camera.lookAt(target);
}

function resizeSpatialRenderer() {
  const renderer = state.spatial.renderer;
  const viewport = $("#spatialViewport");
  if (!renderer || !viewport.clientWidth || !viewport.clientHeight) return;
  const width = viewport.clientWidth;
  const height = viewport.clientHeight;
  renderer.setSize(width, height, false);
  state.spatial.camera.aspect = width / height;
  state.spatial.camera.updateProjectionMatrix();
}

function renderSpatialFrame() {
  if (!state.spatial.renderer) return;
  state.spatial.renderer.render(state.spatial.scene, state.spatial.camera);
}

function setSpatialIndex(index, render = true) {
  const points = state.spatial.data?.points || [];
  if (!points.length) return;
  const nextIndex = Math.max(0, Math.min(points.length - 1, Number(index) || 0));
  state.spatial.index = nextIndex;
  $("#spatialScrubber").value = String(nextIndex);
  if (state.spatial.marker) state.spatial.marker.position.copy(spatialVector(points[nextIndex]));
  if (state.spatial.progressLine) {
    state.spatial.progressLine.geometry.dispose();
    state.spatial.progressLine.geometry = new THREE.BufferGeometry().setFromPoints(points.slice(0, Math.max(2, nextIndex + 1)).map(spatialVector));
  }
  updateSpatialReadout();
  if (render) renderSpatialFrame();
}

function updateSpatialReadout() {
  const points = state.spatial.data?.points || [];
  const point = points[state.spatial.index];
  $("#spatialProgress").textContent = points.length ? `${state.spatial.index + 1} / ${points.length}` : "0 / 0";
  if (!point) {
    $("#spatialTime").textContent = "0.00 s";
    $("#spatialPosition").textContent = "X — · Y — · Z —";
    return;
  }
  $("#spatialTime").textContent = `${point.time.toFixed(2)} s`;
  $("#spatialPosition").textContent = `X ${point.values[0].toFixed(3)} · Y ${point.values[1].toFixed(3)} · Z ${point.values[2].toFixed(3)}`;
}

function toggleSpatialPlayback() {
  if (state.spatial.playing) {
    stopSpatialPlayback();
    return;
  }
  const points = state.spatial.data?.points || [];
  if (points.length <= 1) return;
  if (state.spatial.index >= points.length - 1) setSpatialIndex(0);
  state.spatial.playing = true;
  state.spatial.playbackStartedAt = performance.now();
  state.spatial.playbackTime = points[state.spatial.index].time;
  $("#spatialPlay").textContent = "■";
  $("#spatialPlay").title = "暂停";
  requestAnimationFrame(stepSpatialPlayback);
}

function stepSpatialPlayback(now) {
  if (!state.spatial.playing) return;
  const points = state.spatial.data?.points || [];
  const targetTime = state.spatial.playbackTime + (now - state.spatial.playbackStartedAt) / 1000;
  let index = state.spatial.index;
  while (index + 1 < points.length && points[index + 1].time <= targetTime) index++;
  setSpatialIndex(index);
  if (index >= points.length - 1) stopSpatialPlayback();
  else requestAnimationFrame(stepSpatialPlayback);
}

function stopSpatialPlayback() {
  state.spatial.playing = false;
  const button = $("#spatialPlay");
  if (button) {
    button.textContent = "▶";
    button.title = "播放";
  }
}

function setSpatialView(view) {
  const poses = {
    perspective: { yaw: -0.72, pitch: 0.48 },
    top: { yaw: 0, pitch: 1.43 },
    side: { yaw: 1.56, pitch: 0.18 }
  };
  Object.assign(state.spatial, poses[view] || poses.perspective);
  $$(".spatial-view-button").forEach(button => button.classList.toggle("active", button.dataset.view === view));
  updateSpatialCamera();
  renderSpatialFrame();
}

function drawChart(focusTime = null) {
  if (!state.episode || $("#panel-signals").classList.contains("active") === false && focusTime == null) return;
  const canvas = $("#signalChart");
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(rect.width * dpr); canvas.height = Math.round(rect.height * dpr);
  const ctx = canvas.getContext("2d"); ctx.scale(dpr, dpr);
  const width = rect.width, height = rect.height, pad = { left: 48, right: 15, top: 18, bottom: 29 };
  const times = state.episode.timeseries.timestamp;
  const selected = state.activeSignals.map(key => ({ key, values: state.episode.timeseries.signals[key] })).filter(item => item.values);
  ctx.clearRect(0, 0, width, height); ctx.font = "10px Segoe UI";
  ctx.strokeStyle = "#e5e9e6"; ctx.fillStyle = "#7b8581"; ctx.lineWidth = 1;
  for (let index = 0; index <= 5; index++) {
    const y = pad.top + (height - pad.top - pad.bottom) * index / 5;
    ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(width - pad.right, y); ctx.stroke();
  }
  for (let index = 0; index <= 6; index++) {
    const x = pad.left + (width - pad.left - pad.right) * index / 6;
    ctx.beginPath(); ctx.moveTo(x, pad.top); ctx.lineTo(x, height - pad.bottom); ctx.stroke();
    const label = (times[0] + (times.at(-1) - times[0]) * index / 6).toFixed(1) + "s";
    ctx.fillText(label, x - 10, height - 8);
  }
  selected.forEach(signal => {
    const finite = signal.values.filter(value => value != null && Number.isFinite(value));
    if (!finite.length) return;
    let min = Math.min(...finite), max = Math.max(...finite);
    const margin = (max - min || 1) * .12; min -= margin; max += margin;
    ctx.strokeStyle = state.signalColors[signal.key] || "#17201f"; ctx.lineWidth = 1.7; ctx.beginPath();
    signal.values.forEach((value, index) => {
      if (value == null) return;
      const x = pad.left + (width - pad.left - pad.right) * index / Math.max(1, signal.values.length - 1);
      const y = pad.top + (height - pad.top - pad.bottom) * (1 - (value - min) / (max - min));
      index === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.stroke();
  });
  selected.forEach((signal, index) => {
    const x = pad.left + index * 118; ctx.fillStyle = state.signalColors[signal.key] || "#17201f"; ctx.fillRect(x, 2, 13, 3);
    ctx.fillStyle = "#4f5a56"; ctx.fillText(signalName(signal.key), x + 18, 7);
  });
  if (focusTime != null) {
    const x = pad.left + (width - pad.left - pad.right) * (focusTime - times[0]) / (times.at(-1) - times[0]);
    ctx.strokeStyle = "#c53e3a"; ctx.setLineDash([4, 3]); ctx.beginPath(); ctx.moveTo(x, pad.top); ctx.lineTo(x, height - pad.bottom); ctx.stroke(); ctx.setLineDash([]);
  }
  canvas.chartGeometry = { pad, width, height, times, selected };
}

function handleChartMove(event) {
  const canvas = $("#signalChart"), geometry = canvas.chartGeometry;
  if (!geometry) return;
  const rect = canvas.getBoundingClientRect();
  const x = event.clientX - rect.left;
  if (x < geometry.pad.left || x > geometry.width - geometry.pad.right) { $("#chartTooltip").hidden = true; return; }
  const ratio = (x - geometry.pad.left) / (geometry.width - geometry.pad.left - geometry.pad.right);
  const index = Math.max(0, Math.min(geometry.times.length - 1, Math.round(ratio * (geometry.times.length - 1))));
  const tooltip = $("#chartTooltip");
  tooltip.innerHTML = `<strong>${geometry.times[index].toFixed(3)} s</strong><br>${geometry.selected.map(signal => `<span style="color:${state.signalColors[signal.key] || "#17201f"}">●</span> ${signalName(signal.key)}: ${signal.values[index] == null ? "缺失" : Number(signal.values[index]).toFixed(4)}`).join("<br>")}`;
  tooltip.style.left = `${Math.min(rect.width - 150, Math.max(5, x + 12))}px`;
  tooltip.style.top = `${Math.max(10, event.clientY - rect.top - 20)}px`;
  tooltip.hidden = false;
}

function activateTab(name) {
  $$(".tab").forEach(tab => tab.classList.toggle("active", tab.dataset.tab === name));
  $$(".tab-panel").forEach(panel => panel.classList.toggle("active", panel.id === `panel-${name}`));
  if (name === "signals") requestAnimationFrame(() => drawChart());
  if (name === "spatial") requestAnimationFrame(() => renderSpatialScene(true));
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]);
}

async function uploadFile(file) {
  if (!file) return;
  if (file.size > 25 * 1024 * 1024) return toast("文件不能超过 25 MB", true);
  const button = $("#uploadButton");
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "正在解析";
  try {
    const buffer = await file.arrayBuffer();
    const bytes = new Uint8Array(buffer);
    let binary = "";
    const chunkSize = 0x8000;
    for (let offset = 0; offset < bytes.length; offset += chunkSize) {
      binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
    }
    const contentBase64 = btoa(binary);
    await api("/api/upload", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename: file.name, content_base64: contentBase64 })
    });
    toast(`已载入 ${file.name}`);
    await Promise.all([loadDataset(), loadDatasetLibrary()]);
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.textContent = original; $("#fileInput").value = ""; }
}

async function loadAdapters() {
  const response = await api("/api/adapters");
  state.adapters = response.adapters;
  $("#adapterGrid").innerHTML = state.adapters.map(adapter => `
    <article class="adapter-item ${adapter.available ? "available" : "unavailable"}">
      <div class="adapter-item-head"><span>${adapter.available ? "AVAILABLE" : "OPTIONAL"}</span><i></i></div>
      <h3><a href="${escapeHtml(adapter.project_url)}" target="_blank" rel="noreferrer">${escapeHtml(adapter.name)}</a></h3>
      <p>${escapeHtml(adapter.description)}</p>
      <div class="adapter-formats">${adapter.formats.map(format => escapeHtml(format)).join("<br>")}</div>
      <div class="adapter-license"><span>${escapeHtml(adapter.dependency)}</span><span>${escapeHtml(adapter.license)}</span></div>
    </article>`).join("");
}

function setAdapterModal(open) {
  $("#adapterModal").hidden = !open;
  document.body.style.overflow = open ? "hidden" : "";
}

function setDatasetLibraryModal(open) {
  $("#datasetLibraryModal").hidden = !open;
  document.body.style.overflow = open ? "hidden" : "";
}

const issueCodeName = code => ({
  SENSOR_DESYNC: "视觉与状态不同步", FORCE_SPIKE: "异常接触力", JOINT_JUMP: "关节突跳",
  TIMESTAMP_GAP: "采样时间缺口", NON_MONOTONIC_TIME: "时间戳非单调", FRAME_DROP: "连续视觉丢帧",
  ROBOT_STUCK: "执行卡滞", GRASP_SLIP: "抓取滑脱", MISSING_VALUES: "传感器数据缺失",
  WORKSPACE_OUTLIER: "工作空间越界"
}[code] || code);

async function loadAudit() {
  $("#auditLoading").hidden = false;
  $("#auditResults").hidden = true;
  try {
    state.audit = await api("/api/audit");
    renderAudit(state.audit);
    $("#auditResults").hidden = false;
  } finally {
    $("#auditLoading").hidden = true;
  }
}

function renderAudit(audit) {
  $("#auditAdapter").textContent = `${audit.provenance.adapter_name} · ${audit.provenance.source_format}`;
  $("#auditDatasetName").textContent = audit.dataset_name;
  $("#auditSourceHash").textContent = `SHA-256 ${audit.provenance.source_sha256}`;
  $("#auditSourceHash").title = audit.provenance.source_sha256;
  $("#auditEpisodes").textContent = compactNumber(audit.episode_count);
  $("#auditRows").textContent = `${compactNumber(audit.row_count)} 采样点`;
  $("#auditReady").textContent = compactNumber(audit.training_ready_episodes);
  $("#auditReadyRate").textContent = `${format(audit.training_ready_episodes / Math.max(1, audit.episode_count) * 100, 1)}% 通过训练质量门`;
  $("#auditAverage").textContent = format(audit.average_score, 1);
  $("#auditCritical").textContent = compactNumber(audit.critical_episodes);
  $("#auditIssueTypes").textContent = compactNumber(audit.issue_code_counts.length);

  const issueFilter = $("#auditIssueFilter");
  const previousIssue = issueFilter.value;
  issueFilter.innerHTML = `<option value="all">全部故障</option><option value="clean">无显著异常</option>${audit.issue_code_counts.map(item => `<option value="${escapeHtml(item.code)}">${escapeHtml(issueCodeName(item.code))}</option>`).join("")}`;
  issueFilter.value = [...issueFilter.options].some(option => option.value === previousIssue) ? previousIssue : "all";

  const gradeMax = Math.max(1, ...Object.values(audit.grade_distribution));
  $("#auditGradeDistribution").innerHTML = ["A", "B", "C", "D"].map(grade => `
    <div class="grade-row grade-${grade}"><b>${grade}</b><div><i style="width:${audit.grade_distribution[grade] / gradeMax * 100}%"></i></div><span>${audit.grade_distribution[grade]}</span></div>`).join("");

  const dimensionNames = { completeness: "完整性", temporal: "时序", motion: "运动", sync: "同步", safety: "安全" };
  $("#auditDimensions").innerHTML = Object.entries(audit.average_dimension_scores).map(([key, value]) => `
    <div class="dimension-row"><span>${dimensionNames[key]}</span><div><i class="${value < 65 ? "bad" : value < 82 ? "warn" : ""}" style="width:${value}%"></i></div><strong>${format(value, 0)}</strong></div>`).join("");

  const issueMax = Math.max(1, ...audit.issue_code_counts.map(item => item.episode_count));
  $("#auditFaultTotal").textContent = `${audit.issue_code_counts.length} TYPES`;
  $("#auditIssueDistribution").innerHTML = audit.issue_code_counts.length ? audit.issue_code_counts.map(item => `
    <div class="audit-issue-row" title="${escapeHtml(item.code)} · ${escapeHtml(item.title)}"><span>${escapeHtml(issueCodeName(item.code))}</span><div><i class="${item.severity}" style="width:${item.episode_count / issueMax * 100}%"></i></div><strong>${item.episode_count}</strong></div>`).join("") : `<div class="empty-state"><div><strong>未聚合到故障类型</strong><span>全部 Episode 通过当前诊断阈值</span></div></div>`;
  renderAuditTable();
}

function renderAuditTable() {
  if (!state.audit) return;
  const query = $("#auditSearch").value.trim().toLowerCase();
  const grade = $("#auditGradeFilter").value;
  const issue = $("#auditIssueFilter").value;
  const sort = $("#auditSort").value;
  const episodes = state.audit.episodes.filter(item => {
    const searchable = `${item.episode_id} ${item.primary_cause} ${item.issue_codes.join(" ")}`.toLowerCase();
    return (!query || searchable.includes(query))
      && (grade === "all" || item.grade === grade)
      && (issue === "all" || issue === "clean" && !item.issue_codes.length || item.issue_codes.includes(issue));
  });
  episodes.sort((left, right) => {
    if (sort === "score-desc") return right.quality_score - left.quality_score;
    if (sort === "critical-desc") return right.critical_count - left.critical_count || left.quality_score - right.quality_score;
    if (sort === "episode-asc") return left.episode_id.localeCompare(right.episode_id, "zh-CN", { numeric: true });
    return left.quality_score - right.quality_score;
  });
  $("#auditFilterSummary").textContent = `显示 ${episodes.length} / ${state.audit.episode_count} 条记录`;
  $("#auditTableBody").innerHTML = episodes.length ? episodes.map(item => {
    const resultLabel = !item.success_known ? "结果未标注" : item.success ? "任务成功" : "任务失败";
    const finding = item.issue_codes.length ? item.issue_codes.slice(0, 2).map(issueCodeName).join(" / ") : "未发现显著异常";
    const status = item.critical_count ? `${item.critical_count} 个严重问题` : item.quality_score >= 78 ? "训练就绪" : "建议复核";
    return `<tr>
      <td class="audit-episode"><strong>${escapeHtml(item.episode_id)}</strong><small>${resultLabel}</small></td>
      <td><div class="audit-score"><b>${format(item.quality_score, 0)}</b><span class="audit-grade ${item.grade}">${item.grade}</span></div></td>
      ${["completeness", "temporal", "motion", "sync", "safety"].map(key => `<td class="dimension-score">${format(item.scores[key], 0)}</td>`).join("")}
      <td class="audit-finding"><strong title="${escapeHtml(finding)}">${escapeHtml(finding)}</strong><small>${escapeHtml(status)}</small></td>
      <td><button class="audit-open" type="button" data-episode="${escapeHtml(item.episode_id)}">打开诊断</button></td>
    </tr>`;
  }).join("") : `<tr><td colspan="9" class="audit-empty-row">没有符合当前筛选条件的 Episode</td></tr>`;
  $$(".audit-open").forEach(button => button.addEventListener("click", () => openAuditEpisode(button.dataset.episode)));
}

async function openAuditEpisode(episodeId) {
  setWorkspaceMode("diagnostics");
  await selectEpisode(episodeId);
  activateTab("overview");
}

function renderBatchRepairJob(job) {
  state.batchRepair.activeJob = job;
  $("#batchRepairJob").hidden = false;
  $("#batchRepairJobId").textContent = job.id;
  $("#batchRepairMessage").textContent = job.error || job.message;
  const progress = Math.round(Number(job.progress || 0) * 100);
  $("#batchRepairProgressLabel").textContent = `${progress}%`;
  $("#batchRepairProgressBar").style.width = `${progress}%`;
  $("#cancelBatchRepair").hidden = !["queued", "running"].includes(job.status);
  $("#startBatchRepair").disabled = ["queued", "running"].includes(job.status);
  if (job.status === "completed" && job.result) renderBatchRepairResult(job);
}

function renderBatchRepairResult(job) {
  const summary = job.result.summary;
  $("#batchRepairResult").hidden = false;
  $("#batchRepairRetained").textContent = `${format(summary.retained_rate, 1)}%`;
  $("#batchRepairRows").textContent = `${compactNumber(summary.retained_rows)} / ${compactNumber(summary.source_rows)} 行可用`;
  $("#batchRepairEpisodes").textContent = compactNumber(summary.episode_count);
  $("#batchRepairModified").textContent = compactNumber(summary.modified_rows);
  $("#batchRepairQuarantined").textContent = compactNumber(summary.quarantined_rows);
  $("#downloadBatchPackage").href = job.result.downloads.package;
  $("#downloadBatchParquet").href = job.result.downloads.parquet;
  $("#downloadBatchManifest").href = job.result.downloads.manifest;
}

async function startBatchRepair() {
  const button = $("#startBatchRepair");
  button.disabled = true;
  $("#batchRepairResult").hidden = true;
  try {
    const job = await api("/api/batch-repair/run", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}"
    });
    renderBatchRepairJob(job);
    pollBatchRepair(job.id);
  } catch (error) {
    button.disabled = false;
    toast(error.message, true);
  }
}

async function pollBatchRepair(jobId) {
  clearTimeout(state.batchRepair.pollTimer);
  try {
    const job = await api(`/api/batch-repair/status/${encodeURIComponent(jobId)}`);
    renderBatchRepairJob(job);
    if (["queued", "running"].includes(job.status)) {
      state.batchRepair.pollTimer = setTimeout(() => pollBatchRepair(jobId), 450);
      return;
    }
    $("#startBatchRepair").disabled = false;
    if (job.status === "completed") toast("整套训练数据包已生成");
    else toast(job.error || "批量清洗未完成", true);
  } catch (error) {
    $("#startBatchRepair").disabled = false;
    toast(error.message, true);
  }
}

async function cancelBatchRepair() {
  const job = state.batchRepair.activeJob;
  if (!job) return;
  try {
    await api("/api/batch-repair/cancel", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ job_id: job.id })
    });
    toast("已请求取消批量清洗作业");
  } catch (error) { toast(error.message, true); }
}

async function loadBatchRepairStatus() {
  try {
    const response = await api("/api/batch-repair/status");
    const job = response.jobs.find(item => item.dataset_name === state.dataset?.dataset_name);
    if (!job) return;
    renderBatchRepairJob(job);
    if (["queued", "running"].includes(job.status)) pollBatchRepair(job.id);
  } catch (error) {
    toast(error.message, true);
  }
}

function embodiedStatusLabel(status) {
  return { ready: "训练就绪", review: "建议复核", blocked: "数据契约阻断" }[status] || "等待评估";
}

function renderEmbodied(result) {
  state.embodied = result;
  const status = $("#embodiedStatus");
  status.className = `embodied-status ${result.status}`;
  status.querySelector("strong").textContent = embodiedStatusLabel(result.status);
  status.querySelector("span").textContent = `${result.dataset.episode_count} Episodes · Observation → Action → World`;
  $("#embodiedDatasetName").textContent = result.dataset_name || "当前数据集";
  $("#embodiedScore").textContent = format(result.score, 1);
  $("#embodiedReadyRate").textContent = `${format(result.ready_rate, 1)}%`;
  $("#embodiedActionEpisodes").textContent = `${result.episodes.filter(item => item.action_source === "explicit").length} / ${result.episodes.length}`;
  $("#embodiedContactEpisodes").textContent = `${result.episodes.filter(item => item.metrics.contact_ratio > 0).length} / ${result.episodes.length}`;
  $("#embodiedReviewEpisodes").textContent = compactNumber(result.status_counts.review + result.status_counts.blocked);

  const dimensions = Object.entries(result.dimension_names).map(([key, label]) => [key, label, result.dimension_averages[key]]);
  $("#embodiedDimensionBars").innerHTML = dimensions.map(([key, label, value]) => `
    <div class="embodied-dimension-row"><span>${escapeHtml(label)}</span><div><i class="${value < 60 ? "bad" : value < 78 ? "warn" : ""}" style="width:${value}%"></i></div><strong>${format(value, 0)}</strong><small>${key === "controllability" ? "动作对下一状态的响应相关性" : key === "contact_grounding" ? "物理接触证据" : "Episode 平均分"}</small></div>
  `).join("");

  $("#embodiedBlockerCount").textContent = `${result.top_blockers.length} TYPES`;
  $("#embodiedBlockers").innerHTML = result.top_blockers.length ? result.top_blockers.map(item => `
    <div class="embodied-blocker-row"><span>${escapeHtml(item.code)}</span><div><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.detail)}</small></div><b>${item.episode_count} EP</b></div>
  `).join("") : `<div class="embodied-empty">当前数据集没有闭环契约缺口。</div>`;

  $("#embodiedTableBody").innerHTML = result.episodes.map(item => {
    const blocker = item.blockers[0]?.title || "无主要缺口";
    const response = item.metrics.action_state_correlation == null ? "—" : format(item.metrics.action_state_correlation, 2);
    const actionClass = item.action_source === "explicit" ? "explicit" : "proxy";
    return `<tr>
      <td><strong>${escapeHtml(item.episode_id)}</strong><small>${item.rows} 行 · ${format(item.duration, 1)} s</small></td>
      <td><span class="embodied-status-pill ${item.status}">${embodiedStatusLabel(item.status)}</span></td>
      <td><b class="embodied-score">${format(item.score, 0)}</b></td>
      <td><span class="embodied-action-pill ${actionClass}">${escapeHtml(item.action_label)}</span></td>
      <td>${response}</td>
      <td>${item.metrics.response_lag_ms == null ? "—" : `${format(item.metrics.response_lag_ms, 0)} ms`}</td>
      <td>${item.metrics.phase_count} 类 / ${item.metrics.phase_transitions} 次切换</td>
      <td>${format(item.metrics.contact_ratio, 1)}%</td>
      <td class="embodied-finding" title="${escapeHtml(blocker)}">${escapeHtml(blocker)}</td>
    </tr>`;
  }).join("");
  $("#embodiedSources").innerHTML = result.protocol.sources.map(source => `
    <a href="${escapeHtml(source.url)}" target="_blank" rel="noreferrer"><strong>${escapeHtml(source.name)}</strong><span>${escapeHtml(source.idea)}</span></a>
  `).join("");
  $("#embodiedResults").hidden = false;
}

async function loadEmbodiedEvaluation() {
  $("#embodiedLoading").hidden = false;
  try {
    renderEmbodied(await api("/api/embodied"));
  } catch (error) {
    $("#embodiedStatus").className = "embodied-status blocked";
    $("#embodiedStatus strong").textContent = "评估失败";
    toast(error.message, true);
  } finally {
    $("#embodiedLoading").hidden = true;
  }
}

function reasoningStatusLabel(status) {
  return {
    verified: "任务闭环",
    degraded: "证据降级",
    recoverable: "可局部恢复",
    blocked: "任务图阻断",
  }[status] || "等待推理";
}

function reasoningStepLabel(status) {
  return {
    completed: "效果成立",
    degraded: "证据不足",
    failed: "约束失败",
    not_observed: "未观察",
  }[status] || status;
}

function renderReasoningEpisode(episodeId) {
  const result = state.taskReasoning;
  if (!result) return;
  const episode = result.episodes.find(item => item.episode_id === episodeId) || result.episodes[0];
  if (!episode) return;
  state.reasoningEpisode = episode.episode_id;
  $("#reasoningEpisodeSelect").value = episode.episode_id;
  $("#reasoningEpisodeSummary").textContent = `${episode.episode_id} · ${episode.rows} 行 · ${format(episode.duration, 1)} s · ${reasoningStatusLabel(episode.status)}`;
  $("#reasoningProgressLabel").textContent = `${format(episode.task_progress, 0)}% TASK PROGRESS`;

  $("#reasoningGraph").innerHTML = episode.trace.map((step, index) => `
    <article class="reasoning-step ${step.status}">
      <span>${String(index + 1).padStart(2, "0")}</span>
      <b>${escapeHtml(step.name)}</b>
      <strong>${escapeHtml(step.label)}</strong>
      <small>${step.start_time == null ? "未观察到阶段" : `${format(step.start_time, 2)}–${format(step.end_time, 2)} s`}</small>
      <i>${reasoningStepLabel(step.status)}</i>
    </article>
  `).join("");

  const violation = episode.first_violation;
  const violationNode = $("#reasoningViolation");
  if (violation) {
    violationNode.className = `reasoning-violation ${violation.severity || "critical"}`;
    violationNode.innerHTML = `
      <span>FIRST VIOLATED INVARIANT</span>
      <code>${escapeHtml(violation.predicate)}</code>
      <strong>${escapeHtml(violation.predicate_label)}</strong>
      <p>${escapeHtml(violation.skill)}${violation.time == null ? " · 全局诊断" : ` · ${format(violation.time, 2)} s`} · ${escapeHtml(violation.issue_code)}</p>
      <small>${escapeHtml(violation.evidence || violation.title || "物理约束未满足")}</small>`;
  } else {
    violationNode.className = "reasoning-violation verified";
    violationNode.innerHTML = `
      <span>ALL INVARIANTS SATISFIED</span>
      <code>TASK_COMPLETE</code>
      <strong>任务技能链完整闭合</strong>
      <p>5 / 5 技能效果成立</p>
      <small>未发现需要触发恢复规划的前置条件或安全不变量。</small>`;
  }

  const causal = episode.causal_chain.length ? episode.causal_chain : ["连续信号完成谓词落地", "技能前置条件全部满足", "末端任务结果确认成功"];
  $("#reasoningCausalChain").innerHTML = causal.map((item, index) => `
    <div><span>${String(index + 1).padStart(2, "0")}</span><strong>${escapeHtml(item)}</strong></div>
  `).join("");

  $("#reasoningRecoveryCount").textContent = `${episode.recovery_plan.length} OPERATORS`;
  $("#reasoningRecoveryList").innerHTML = episode.recovery_plan.length ? episode.recovery_plan.map(step => `
    <div class="reasoning-recovery-step ${step.kind}">
      <span>${String(step.index).padStart(2, "0")}</span>
      <div><code>${escapeHtml(step.operator)}</code><strong>${escapeHtml(step.label)}</strong><small>${escapeHtml(step.rationale)}</small></div>
      <b>${escapeHtml({ safety: "安全", control: "控制", perception: "感知", planning: "规划", verification: "验证" }[step.kind] || step.kind)}</b>
    </div>
  `).join("") : `<div class="reasoning-clean"><strong>无需恢复</strong><span>该 Episode 的任务图已完整闭合。</span></div>`;

  const evidenceRows = [];
  episode.trace.forEach(step => {
    if (step.status === "not_observed") {
      evidenceRows.push(`<tr><td><strong>${escapeHtml(step.name)}</strong><small>${escapeHtml(step.label)}</small></td><td>阶段</td><td>—</td><td><span class="predicate-pill unknown">Unknown</span></td><td>轨迹中未观察到该技能阶段</td><td>—</td></tr>`);
      return;
    }
    [["前置条件", step.preconditions], ["预期效果", step.effects]].forEach(([relation, predicates]) => {
      predicates.forEach(predicate => evidenceRows.push(`<tr>
        <td><strong>${escapeHtml(step.name)}</strong><small>${escapeHtml(step.label)}</small></td>
        <td>${relation}</td>
        <td><code>${escapeHtml(predicate.key)}</code><small>${escapeHtml(predicate.label)}</small></td>
        <td><span class="predicate-pill ${predicate.state}">${predicate.state === "true" ? "True" : predicate.state === "false" ? "False" : "Unknown"}</span></td>
        <td>${escapeHtml(predicate.evidence)}</td>
        <td>${escapeHtml({ high: "高", medium: "中", low: "低" }[predicate.confidence] || predicate.confidence)}</td>
      </tr>`));
    });
  });
  $("#reasoningEvidenceBody").innerHTML = evidenceRows.join("");
  $$("#reasoningEpisodeBody tr").forEach(row => row.classList.toggle("active", row.dataset.episode === episode.episode_id));
}

function renderTaskReasoning(result) {
  state.taskReasoning = result;
  $("#reasoningDatasetName").textContent = result.dataset_name || "当前数据集";
  $("#reasoningVerified").textContent = `${result.status_counts.verified} / ${result.dataset.episode_count}`;
  $("#reasoningRecoverable").textContent = `${result.status_counts.recoverable} / ${result.dataset.episode_count}`;
  $("#reasoningGrounding").textContent = `${format(result.summary.average_grounding_coverage, 1)}%`;
  $("#reasoningPlanHealth").textContent = `${format(result.summary.average_plan_health, 1)}%`;
  $("#reasoningRecoverySteps").textContent = compactNumber(result.summary.recovery_steps);
  const status = $("#reasoningStatus");
  status.className = `reasoning-status ${result.status_counts.blocked ? "blocked" : result.status_counts.recoverable ? "recoverable" : "verified"}`;
  status.querySelector("strong").textContent = `${result.status_counts.recoverable} 条失败可局部恢复`;
  status.querySelector("span").textContent = `${result.operators.length} Operators · ${Object.keys(result.predicate_labels).length} Predicates`;

  $("#reasoningEpisodeSelect").innerHTML = result.episodes.map(item => `<option value="${escapeHtml(item.episode_id)}">${escapeHtml(item.episode_id)} · ${reasoningStatusLabel(item.status)}</option>`).join("");
  $("#reasoningEpisodeBody").innerHTML = result.episodes.map(item => {
    const violation = item.first_violation;
    return `<tr data-episode="${escapeHtml(item.episode_id)}">
      <td><button class="reasoning-episode-link" type="button" data-episode="${escapeHtml(item.episode_id)}">${escapeHtml(item.episode_id)}</button><small>${item.rows} 行 · ${format(item.duration, 1)} s</small></td>
      <td><span class="reasoning-status-pill ${item.status}">${reasoningStatusLabel(item.status)}</span></td>
      <td>${format(item.task_progress, 0)}%</td>
      <td>${format(item.plan_health, 0)}%</td>
      <td>${format(item.grounding_coverage, 0)}%</td>
      <td>${violation ? `<code>${escapeHtml(violation.predicate)}</code><small>${escapeHtml(violation.predicate_label)}</small>` : "—"}</td>
      <td>${violation ? escapeHtml(violation.skill) : "—"}</td>
      <td>${item.recovery_plan.length}</td>
    </tr>`;
  }).join("");
  $$(".reasoning-episode-link").forEach(button => button.addEventListener("click", () => renderReasoningEpisode(button.dataset.episode)));
  $("#reasoningSources").innerHTML = result.protocol.sources.map(source => `
    <a href="${escapeHtml(source.url)}" target="_blank" rel="noreferrer"><strong>${escapeHtml(source.name)}</strong><span>${escapeHtml(source.idea)}</span></a>
  `).join("");
  const preferred = result.episodes.some(item => item.episode_id === state.reasoningEpisode)
    ? state.reasoningEpisode
    : result.episodes.find(item => item.status === "recoverable")?.episode_id || result.episodes[0]?.episode_id;
  if (preferred) renderReasoningEpisode(preferred);
  $("#reasoningResults").hidden = false;
}

async function loadTaskReasoning() {
  $("#reasoningLoading").hidden = false;
  try {
    renderTaskReasoning(await api("/api/task-reasoning"));
  } catch (error) {
    $("#reasoningStatus").className = "reasoning-status blocked";
    $("#reasoningStatus strong").textContent = "推理失败";
    toast(error.message, true);
  } finally {
    $("#reasoningLoading").hidden = true;
  }
}

async function loadRecoveryCatalog() {
  const [catalog, status, benchmarkStatus] = await Promise.all([
    api("/api/recovery/catalog"),
    api("/api/recovery/status"),
    api("/api/recovery-benchmark/status")
  ]);
  state.recovery.catalog = catalog;
  const select = $("#recoveryScenario");
  select.innerHTML = catalog.scenarios.map(item => `
    <option value="${escapeHtml(item.id)}">${escapeHtml(item.scenario_name)} · ${escapeHtml(item.name)}</option>
  `).join("");
  if (catalog.scenarios.some(item => item.id === "collision")) select.value = "collision";
  renderRecoveryProtocol();
  $("#recoverySources").innerHTML = catalog.sources.map(source => `
    <a href="${escapeHtml(source.url)}" target="_blank" rel="noreferrer">
      <strong>${escapeHtml(source.name)}</strong><span>${escapeHtml(source.idea)}</span>
    </a>
  `).join("");
  const runtime = catalog.runtime;
  $("#runRecovery").disabled = !runtime.available;
  $("#runRecoveryBenchmark").disabled = !runtime.available;
  const statusNode = $("#recoveryStatus");
  statusNode.className = "recovery-status";
  statusNode.querySelector("strong").textContent = runtime.available ? "恢复运行时就绪" : "恢复运行时不可用";
  statusNode.querySelector("span").textContent = runtime.available
    ? `ManiSkill ${runtime.mani_skill_version || "3"} · ${runtime.sim_backend} / ${runtime.render_backend}`
    : runtime.error;
  const active = status.jobs.find(job => ["queued", "running"].includes(job.status));
  const completed = status.jobs.find(job => job.status === "completed" && job.result);
  if (active) {
    state.recovery.activeJob = active;
    renderRecoveryJob(active);
    $("#runRecovery").disabled = true;
    pollRecoveryJob(active.id);
  } else if (completed) {
    state.recovery.activeJob = completed;
    renderRecoveryJob(completed);
    await loadRecoveryResult(completed);
  }
  const activeBenchmark = benchmarkStatus.jobs.find(job => ["queued", "running"].includes(job.status));
  const completedBenchmark = benchmarkStatus.jobs.find(job => job.status === "completed" && job.result);
  if (activeBenchmark) {
    state.recovery.benchmarkJob = activeBenchmark;
    renderRecoveryBenchmarkJob(activeBenchmark);
    $("#runRecoveryBenchmark").disabled = true;
    pollRecoveryBenchmark(activeBenchmark.id);
  } else if (completedBenchmark) {
    state.recovery.benchmarkJob = completedBenchmark;
    renderRecoveryBenchmarkJob(completedBenchmark);
    renderRecoveryBenchmarkResult(completedBenchmark.result);
  }
}

function recoveryBenchmarkPercent(metric) {
  return `${format(Number(metric?.rate || 0) * 100, 0)}%`;
}

function renderRecoveryBenchmarkJob(job) {
  $("#recoveryBenchmarkJob").hidden = false;
  $("#recoveryBenchmarkJobId").textContent = job.id;
  $("#recoveryBenchmarkMessage").textContent = job.error || job.message;
  const percent = Math.round(Number(job.progress || 0) * 100);
  $("#recoveryBenchmarkProgressLabel").textContent = `${percent}%`;
  $("#recoveryBenchmarkProgressBar").style.width = `${percent}%`;
  $("#cancelRecoveryBenchmark").hidden = !["queued", "running"].includes(job.status);
  const status = $("#recoveryBenchmarkStatus");
  if (["queued", "running"].includes(job.status)) {
    status.className = "recovery-benchmark-status running";
    status.querySelector("strong").textContent = "批测运行中";
    status.querySelector("small").textContent = `${percent}% · ${job.message}`;
  }
}

async function startRecoveryBenchmark() {
  const button = $("#runRecoveryBenchmark");
  button.disabled = true;
  $("#recoveryBenchmarkResults").hidden = true;
  try {
    const job = await api("/api/recovery-benchmark/run", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        seed_count: Number($("#recoveryBenchmarkSeeds").value),
        base_seed: Number($("#recoveryBenchmarkBaseSeed").value),
        horizon: Number($("#recoveryBenchmarkHorizon").value)
      })
    });
    state.recovery.benchmarkJob = job;
    state.recovery.benchmarkResult = null;
    renderRecoveryBenchmarkJob(job);
    pollRecoveryBenchmark(job.id);
  } catch (error) {
    button.disabled = !state.recovery.catalog?.runtime?.available;
    toast(error.message, true);
  }
}

async function pollRecoveryBenchmark(jobId) {
  clearTimeout(state.recovery.benchmarkPollTimer);
  try {
    const job = await api(`/api/recovery-benchmark/status/${encodeURIComponent(jobId)}`);
    state.recovery.benchmarkJob = job;
    renderRecoveryBenchmarkJob(job);
    if (["queued", "running"].includes(job.status)) {
      state.recovery.benchmarkPollTimer = setTimeout(() => pollRecoveryBenchmark(jobId), 700);
      return;
    }
    $("#runRecoveryBenchmark").disabled = !state.recovery.catalog?.runtime?.available;
    $("#cancelRecoveryBenchmark").hidden = true;
    if (job.status === "completed") {
      const result = job.result || await api(`/api/recovery-benchmark/result/${encodeURIComponent(job.id)}`);
      renderRecoveryBenchmarkResult(result);
      toast("RecoveryBench 多场景统计评测已完成");
    } else {
      const status = $("#recoveryBenchmarkStatus");
      status.className = "recovery-benchmark-status failed";
      status.querySelector("strong").textContent = job.status === "cancelled" ? "批测已取消" : "批测失败";
      status.querySelector("small").textContent = job.error || job.message;
      toast(job.error || job.message, true);
    }
  } catch (error) {
    $("#runRecoveryBenchmark").disabled = !state.recovery.catalog?.runtime?.available;
    toast(error.message, true);
  }
}

async function cancelRecoveryBenchmark() {
  const job = state.recovery.benchmarkJob;
  if (!job) return;
  try {
    await api("/api/recovery-benchmark/cancel", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: job.id })
    });
    toast("已请求取消 RecoveryBench");
  } catch (error) { toast(error.message, true); }
}

function renderRecoveryBenchmarkResult(result) {
  state.recovery.benchmarkResult = result;
  const summary = result.summary;
  const ci = summary.task_recovery.ci95;
  $("#recoveryBenchmarkResults").hidden = false;
  $("#recoveryBenchmarkTaskRate").textContent = recoveryBenchmarkPercent(summary.task_recovery);
  $("#recoveryBenchmarkTaskCI").textContent = `Wilson 95% CI ${format(ci.lower * 100, 1)}–${format(ci.upper * 100, 1)}%`;
  $("#recoveryBenchmarkEpisodeSafety").textContent = recoveryBenchmarkPercent(summary.episode_safety);
  $("#recoveryBenchmarkPostSafety").textContent = recoveryBenchmarkPercent(summary.post_intervention_safety);
  $("#recoveryBenchmarkTrigger").textContent = recoveryBenchmarkPercent(summary.online_trigger_coverage);
  $("#recoveryBenchmarkPairs").textContent = recoveryBenchmarkPercent(summary.pair_integrity);
  $("#recoveryBenchmarkLatency").textContent = summary.recovery_latency_s.p95 == null ? "—" : `${format(summary.recovery_latency_s.p95, 2)} s`;
  $("#recoveryBenchmarkPath").textContent = summary.path_overhead_m.mean == null ? "—" : `${summary.path_overhead_m.mean >= 0 ? "+" : ""}${format(summary.path_overhead_m.mean, 3)} m`;
  $("#recoveryBenchmarkOperators").textContent = summary.operator_completion_rate.mean == null ? "—" : `${format(summary.operator_completion_rate.mean, 0)}%`;
  $("#recoveryBenchmarkTrialCount").textContent = `${summary.trials} PAIRED TRIALS`;
  $("#recoveryBenchmarkScenarioBody").innerHTML = result.per_scenario.map(item => {
    const itemCI = item.task_recovery.ci95;
    return `<tr>
      <td><strong>${escapeHtml(item.scenario_name)}</strong><code>${escapeHtml(item.scenario)}</code></td>
      <td>${item.trials}</td>
      <td class="${item.task_recovery.rate === 1 ? "passed" : "failed"}">${recoveryBenchmarkPercent(item.task_recovery)}</td>
      <td>${format(itemCI.lower * 100, 1)}–${format(itemCI.upper * 100, 1)}%</td>
      <td class="${item.episode_safety.rate === 1 ? "passed" : "failed"}">${recoveryBenchmarkPercent(item.episode_safety)}</td>
      <td class="${item.post_intervention_safety.rate === 1 ? "passed" : "failed"}">${recoveryBenchmarkPercent(item.post_intervention_safety)}</td>
      <td>${recoveryBenchmarkPercent(item.online_trigger_coverage)}</td>
      <td>${item.recovery_latency_s.mean == null ? "—" : `${format(item.recovery_latency_s.mean, 2)} s`}</td>
    </tr>`;
  }).join("");
  const scenarioNames = Object.fromEntries(result.per_scenario.map(item => [item.scenario, item.scenario_name]));
  $("#recoveryBenchmarkMatrixHead").innerHTML = `<tr><th>Seed</th>${result.protocol.scenarios.map(scenario => `<th>${escapeHtml(scenarioNames[scenario] || scenario)}</th>`).join("")}</tr>`;
  $("#recoveryBenchmarkMatrixBody").innerHTML = result.matrix.map(row => `<tr><td><strong>${row.seed}</strong></td>${result.protocol.scenarios.map(scenario => {
    const cell = row.scenarios[scenario];
    if (!cell) return "<td>—</td>";
    const stateClass = !cell.task_recovery ? "failed" : cell.episode_safety ? "safe" : "recovered-unsafe";
    return `<td><div class="recovery-benchmark-cell ${stateClass}"><strong>${cell.task_recovery ? "RECOVERED" : "FAILED"}</strong><small>EP ${cell.episode_safety ? "SAFE" : "UNSAFE"} · POST ${cell.post_intervention_safety ? "SAFE" : "UNSAFE"}</small><span>${cell.recovery_latency == null ? "—" : `${format(cell.recovery_latency, 2)} s`}</span></div></td>`;
  }).join("")}</tr>`).join("");
  const excluded = result.protocol.excluded_seeds || [];
  const exclusionNode = $("#recoveryBenchmarkExclusions");
  exclusionNode.hidden = excluded.length === 0;
  exclusionNode.innerHTML = excluded.length ? `<strong>样本准入排除 ${excluded.length} 个候选 seed</strong>${excluded.map(item => `<span>seed ${item.seed} · ${escapeHtml(item.scenario || "preflight")} · ${item.failure_rows || 0} frames · 未出现受控故障签名</span>`).join("")}` : "";
  const status = $("#recoveryBenchmarkStatus");
  const recovered = summary.task_recovery.rate === 1;
  const episodeSafe = summary.episode_safety.rate === 1;
  status.className = `recovery-benchmark-status ${recovered && episodeSafe ? "passed" : recovered ? "warning" : "failed"}`;
  status.querySelector("strong").textContent = recovered ? "恢复鲁棒性已验证" : "恢复鲁棒性不足";
  status.querySelector("small").textContent = episodeSafe ? "全部 Episode 安全" : "存在已恢复但不安全的 Episode";
  $("#downloadRecoveryBenchmark").hidden = false;
  $("#downloadRecoveryBenchmark").href = result.downloads?.json || `/api/recovery-benchmark/result/${encodeURIComponent(state.recovery.benchmarkJob?.id || "")}`;
  $("#downloadRecoveryBenchmark").download = `embodiscope-${state.recovery.benchmarkJob?.id || "recovery-benchmark"}.json`;
}

function renderRecoveryProtocol() {
  const catalog = state.recovery.catalog;
  if (!catalog) return;
  const scenario = catalog.scenarios.find(item => item.id === $("#recoveryScenario").value) || catalog.scenarios[0];
  if (!scenario) return;
  $("#recoveryHorizon").value = String(scenario.recommended_steps || 140);
  $("#recoveryProtocolDescription").textContent = `${scenario.description} 首因谓词：${scenario.predicate}。`;
}

function markRecoveryResultStale() {
  const result = state.recovery.result;
  if (!result) return;
  const changed = $("#recoveryScenario").value !== result.scenario
    || Number($("#recoverySeed").value) !== Number(result.seed)
    || Number($("#recoveryHorizon").value) !== Number(result.horizon);
  if (!changed) return;
  pauseRecoveryPlayback();
  state.recovery.resultStale = true;
  $("#recoveryResults").hidden = true;
  $("#recoveryEmpty").hidden = false;
  $("#recoveryEmptyTitle").textContent = "实验配置已更改";
  $("#recoveryEmptyMessage").textContent = "旧结果已隐藏，运行新的配对实验后再显示质量门。";
  const statusNode = $("#recoveryStatus");
  statusNode.className = "recovery-status";
  statusNode.querySelector("strong").textContent = "等待新实验";
  statusNode.querySelector("span").textContent = `旧结果：${result.scenario_name} · seed ${result.seed} · ${result.horizon} steps`;
}

function handleRecoveryScenarioChange() {
  renderRecoveryProtocol();
  markRecoveryResultStale();
}

async function startRecovery() {
  const button = $("#runRecovery");
  button.disabled = true;
  pauseRecoveryPlayback();
  try {
    const job = await api("/api/recovery/run", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        scenario: $("#recoveryScenario").value,
        seed: Number($("#recoverySeed").value),
        horizon: Number($("#recoveryHorizon").value)
      })
    });
    state.recovery.activeJob = job;
    state.recovery.result = null;
    state.recovery.failureReplay = null;
    state.recovery.recoveredReplay = null;
    state.recovery.resultStale = false;
    $("#recoveryEmpty").hidden = true;
    $("#recoveryResults").hidden = true;
    const statusNode = $("#recoveryStatus");
    statusNode.className = "recovery-status";
    statusNode.querySelector("strong").textContent = "配对实验运行中";
    statusNode.querySelector("span").textContent = "先运行失败组，再在相同条件下执行恢复组";
    renderRecoveryJob(job);
    pollRecoveryJob(job.id);
  } catch (error) {
    button.disabled = !state.recovery.catalog?.runtime?.available;
    toast(error.message, true);
  }
}

function renderRecoveryJob(job) {
  $("#recoveryJob").hidden = false;
  $("#recoveryJob").classList.toggle("completed", job.status === "completed");
  $("#recoveryJobId").textContent = job.id;
  $("#recoveryJobMessage").textContent = job.error || job.message;
  const percent = Math.round((job.progress || 0) * 100);
  $("#recoveryProgressLabel").textContent = `${percent}%`;
  $("#recoveryProgressBar").style.width = `${percent}%`;
  const action = $("#cancelRecovery");
  const running = ["queued", "running"].includes(job.status);
  const completed = job.status === "completed";
  action.hidden = !running && !completed;
  action.textContent = running ? "取消实验" : "查看可视化";
  action.classList.toggle("view-result", completed);
}

function scrollRecoveryResults() {
  const results = $("#recoveryResults");
  if (results.hidden || $("#recoveryPage").hidden) return;
  results.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function pollRecoveryJob(jobId) {
  clearTimeout(state.recovery.pollTimer);
  try {
    const job = await api(`/api/recovery/status/${encodeURIComponent(jobId)}`);
    state.recovery.activeJob = job;
    renderRecoveryJob(job);
    if (["queued", "running"].includes(job.status)) {
      state.recovery.pollTimer = setTimeout(() => pollRecoveryJob(jobId), 450);
      return;
    }
    $("#runRecovery").disabled = !state.recovery.catalog?.runtime?.available;
    if (job.status === "completed") {
      await loadRecoveryResult(job);
      scrollRecoveryResults();
      toast("配对恢复实验已完成");
    } else {
      $("#recoveryEmpty").hidden = false;
      const statusNode = $("#recoveryStatus");
      statusNode.className = "recovery-status failed";
      statusNode.querySelector("strong").textContent = job.status === "cancelled" ? "实验已取消" : "实验执行失败";
      statusNode.querySelector("span").textContent = job.error || job.message;
      toast(job.error || job.message || "恢复实验未完成", true);
    }
  } catch (error) {
    $("#runRecovery").disabled = !state.recovery.catalog?.runtime?.available;
    toast(error.message, true);
  }
}

async function cancelRecovery() {
  const job = state.recovery.activeJob;
  if (!job) return;
  try {
    await api("/api/recovery/cancel", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: job.id })
    });
    toast("已请求取消恢复实验");
  } catch (error) { toast(error.message, true); }
}

async function handleRecoveryJobAction() {
  const job = state.recovery.activeJob;
  if (!job) return;
  if (job.status !== "completed") {
    await cancelRecovery();
    return;
  }
  if (!state.recovery.result) {
    try {
      await loadRecoveryResult(job);
    } catch (error) {
      toast(error.message, true);
      return;
    }
  }
  scrollRecoveryResults();
}

function recoveryValue(value, unit) {
  if (unit === "bool") return value ? "true" : "false";
  const digits = unit === "m" ? 4 : unit === "s" ? 3 : 1;
  return `${format(value, digits)} ${unit}`;
}

function recoveryDelta(item) {
  if (item.unit === "bool") {
    if (item.failure === item.recovered) return "UNCHANGED";
    return item.recovered ? "RESTORED" : "REGRESSED";
  }
  const delta = Number(item.recovered) - Number(item.failure);
  const digits = item.unit === "m" ? 4 : item.unit === "s" ? 3 : 1;
  return `${delta >= 0 ? "+" : ""}${format(delta, digits)} ${item.unit}`;
}

async function loadRecoveryResult(job) {
  const result = job.result || await api(`/api/recovery/result/${encodeURIComponent(job.id)}`);
  const [failureReplay, recoveredReplay] = await Promise.all([
    api(result.variants.failure.replay_url),
    api(result.variants.recovered.replay_url)
  ]);
  state.recovery.result = result;
  state.recovery.failureReplay = failureReplay;
  state.recovery.recoveredReplay = recoveredReplay;
  state.recovery.resultStale = false;
  renderRecoveryResult(result);
  const failureVideo = $("#recoveryFailureVideo");
  const recoveredVideo = $("#recoveryRecoveredVideo");
  const failurePoster = result.variants.failure.thumbnail_url
    || result.variants.failure.video_url.replace("/video/", "/thumbnail/");
  const recoveredPoster = result.variants.recovered.thumbnail_url
    || result.variants.recovered.video_url.replace("/video/", "/thumbnail/");
  failureVideo.poster = `${failurePoster}?v=${encodeURIComponent(job.id)}`;
  recoveredVideo.poster = `${recoveredPoster}?v=${encodeURIComponent(job.id)}`;
  failureVideo.src = `${result.variants.failure.video_url}?v=${encodeURIComponent(job.id)}`;
  recoveredVideo.src = `${result.variants.recovered.video_url}?v=${encodeURIComponent(job.id)}`;
  failureVideo.load();
  recoveredVideo.load();
  const duration = Math.max(Number(failureReplay.duration || 0), Number(recoveredReplay.duration || 0));
  $("#recoveryScrubber").max = String(duration);
  renderRecoveryTimeline(result);
  setRecoveryTime(0, true);
}

function renderRecoveryResult(result) {
  if (state.recovery.catalog?.scenarios.some(item => item.id === result.scenario)) {
    $("#recoveryScenario").value = result.scenario;
    $("#recoverySeed").value = String(result.seed);
    $("#recoveryHorizon").value = String(result.horizon);
    renderRecoveryProtocol();
  }
  $("#recoveryEmpty").hidden = true;
  $("#recoveryEmptyTitle").textContent = "等待配对恢复实验";
  $("#recoveryEmptyMessage").textContent = "当前协议覆盖碰撞、夹爪执行失效和抓取滑脱。";
  $("#recoveryResults").hidden = false;
  const taskVerdict = result.verdicts?.task_recovery || { passed: Boolean(result.passed) };
  const episodeVerdict = result.verdicts?.episode_safety || { passed: Number(result.recovered.peak_force) <= 36 };
  const postVerdict = result.verdicts?.post_intervention_safety || { passed: Number(result.metrics.post_recovery_peak_force) <= 36 };
  const statusNode = $("#recoveryStatus");
  statusNode.className = `recovery-status ${taskVerdict.passed && episodeVerdict.passed ? "passed" : taskVerdict.passed ? "warning" : "failed"}`;
  statusNode.querySelector("strong").textContent = taskVerdict.passed
    ? (episodeVerdict.passed ? "任务恢复且整段安全" : "任务已恢复，但整段不安全")
    : "任务恢复失败";
  statusNode.querySelector("span").textContent = `${result.scenario_name} · seed ${result.seed} · ${result.horizon} steps`;
  const verdict = $("#recoveryVerdict");
  verdict.textContent = taskVerdict.passed ? "RECOVERED" : "FAILED";
  verdict.className = taskVerdict.passed ? "passed" : "failed";
  $("#recoveryTaskDelta").textContent = `${result.failure.success ? "SUCCESS" : "FAILURE"} → ${result.recovered.success ? "SUCCESS" : "FAILURE"}`;
  $("#recoveryEpisodeSafety").textContent = episodeVerdict.passed ? "SAFE" : "UNSAFE";
  $("#recoveryEpisodeSafety").className = episodeVerdict.passed ? "passed" : "failed";
  $("#recoveryPostSafety").textContent = postVerdict.passed ? "SAFE" : "UNSAFE";
  $("#recoveryPostSafety").className = postVerdict.passed ? "passed" : "failed";
  $("#recoveryPredicate").textContent = result.metrics.predicate_restored ? "RESTORED" : "FAILED";
  $("#recoveryPredicate").className = result.metrics.predicate_restored ? "passed" : "failed";
  $("#recoveryPredicateLabel").textContent = `${result.predicate} · ${result.predicate_label}`;
  $("#recoveryLatency").textContent = result.metrics.recovery_latency == null ? "—" : `${format(result.metrics.recovery_latency, 2)} s`;
  $("#recoveryPathOverhead").textContent = `${result.metrics.path_overhead >= 0 ? "+" : ""}${format(result.metrics.path_overhead, 3)} m`;
  const overheadRate = `${result.metrics.path_overhead_rate >= 0 ? "+" : ""}${format(result.metrics.path_overhead_rate, 1)}%`;
  $("#recoveryPathOverhead").title = overheadRate;
  $("#recoveryPathOverheadNote").textContent = `${overheadRate} · ${format(result.comparison_window?.duration || 0, 2)} s 共同窗口`;
  $("#recoveryFailureStatus").textContent = result.failure.success ? "SUCCESS" : "FAILED";
  $("#recoveryRecoveredStatus").textContent = result.recovered.success ? "RECOVERED" : "FAILED";
  const completed = result.plan.filter(step => step.status === "completed").length;
  $("#recoveryPlanSummary").textContent = `${result.recovery_name} · ${completed}/${result.plan.length} 算子完成`;
  $("#recoveryOperatorRate").textContent = `${format(result.metrics.operator_completion_rate, 0)}% COMPLETE`;
  $("#recoveryPlanList").innerHTML = result.plan.map(step => `
    <div class="recovery-plan-step ${escapeHtml(step.kind)} ${step.status === "completed" ? "" : "failed"}">
      <span>${String(step.index).padStart(2, "0")}</span>
      <div><code>${escapeHtml(step.operator)}</code><strong>${escapeHtml(step.label)}</strong><small>${step.completed_at == null ? "无有序证据" : `${format(step.completed_at, 2)} s`} · ${escapeHtml(step.evidence || "")}</small></div>
      <b>${step.status === "completed" ? "COMPLETE" : "FAILED"}</b>
    </div>
  `).join("");
  $("#recoveryGates").innerHTML = result.quality_gates.map(gate => `
    <div class="recovery-gate ${gate.passed ? "passed" : ""}">
      <i></i><div><strong>${escapeHtml(gate.label)}</strong><small>${escapeHtml(gate.value)}</small></div>
    </div>
  `).join("");
  $("#recoveryComparisonBody").innerHTML = result.comparison.map(item => `
    <tr><td>${escapeHtml(item.metric)}</td><td>${recoveryValue(item.failure, item.unit)}</td><td>${recoveryValue(item.recovered, item.unit)}</td><td>${recoveryDelta(item)}</td></tr>
  `).join("");
  $("#downloadRecoveryResult").href = result.downloads.json;
  $("#downloadRecoveryResult").download = `embodiscope-${state.recovery.activeJob?.id || "recovery"}.json`;
}

const RECOVERY_EVENT_TYPES = new Set([
  "collision-command", "gripper-failure", "grasp-slip-force",
  "predicate-violated", "recovery-start", "predicate-restored", "recovery-success"
]);

function recoveryTimelineEvents(replay) {
  return (replay?.events || []).filter(event => RECOVERY_EVENT_TYPES.has(event.type));
}

function renderRecoveryEventTrack(target, replay, duration) {
  target.innerHTML = recoveryTimelineEvents(replay).map(event => {
    const position = Math.max(0, Math.min(100, Number(event.time || 0) / Math.max(duration, .001) * 100));
    return `<button class="recovery-event-marker ${escapeHtml(event.type)}" type="button" style="left:${position}%" data-time="${Number(event.time || 0)}" title="${escapeHtml(`${format(event.time || 0, 2)} s · ${event.label}`)}" aria-label="${escapeHtml(event.label)}"></button>`;
  }).join("");
}

function renderRecoveryTimeline(result) {
  const duration = Math.max(
    Number(state.recovery.failureReplay?.duration || 0),
    Number(state.recovery.recoveredReplay?.duration || 0)
  );
  renderRecoveryEventTrack($("#recoveryFailureEvents"), state.recovery.failureReplay, duration);
  renderRecoveryEventTrack($("#recoveryRecoveredEvents"), state.recovery.recoveredReplay, duration);
  $$(".recovery-event-marker").forEach(marker => marker.addEventListener("click", () => {
    pauseRecoveryPlayback();
    setRecoveryTime(Number(marker.dataset.time), true);
  }));
  const trigger = result.trigger || {};
  $("#recoveryTimelineSummary").textContent = trigger.trigger_type
    ? `${trigger.predicate}=false · ${trigger.trigger_type} · ${trigger.evidence}`
    : "历史结果未记录在线触发证据";
  $("#recoveryPairFingerprint").textContent = result.pair_integrity
    ? `PAIR ${result.pair_integrity.fingerprint} · ${result.pair_integrity.passed ? "VERIFIED" : "MISMATCH"}`
    : "PAIR LEGACY";
  scheduleRecoveryEvidenceDraw();
}

function scheduleRecoveryEvidenceDraw() {
  requestAnimationFrame(drawRecoveryEvidence);
  setTimeout(drawRecoveryEvidence, 50);
}

function drawRecoveryEvidence() {
  const canvas = $("#recoveryEvidenceCanvas");
  const failure = state.recovery.failureReplay;
  const recovered = state.recovery.recoveredReplay;
  if (!canvas || !failure || !recovered || canvas.clientWidth < 10) return;
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, width, height);
  const left = 39, right = 12, top = 10, bottom = 25;
  const plotWidth = Math.max(1, width - left - right);
  const plotHeight = Math.max(1, height - top - bottom);
  const duration = Math.max(Number(failure.duration || 0), Number(recovered.duration || 0), .001);
  const maxForce = Math.max(40, ...failure.force.map(Number), ...recovered.force.map(Number)) * 1.08;
  const x = time => left + Number(time) / duration * plotWidth;
  const y = force => top + plotHeight - Number(force) / maxForce * plotHeight;
  context.fillStyle = "#fafcfb";
  context.fillRect(left, top, plotWidth, plotHeight);
  context.strokeStyle = "#e5eae7";
  context.lineWidth = 1;
  [0, .5, 1].forEach(fraction => {
    const yy = top + plotHeight * fraction;
    context.beginPath(); context.moveTo(left, yy); context.lineTo(width - right, yy); context.stroke();
  });
  context.setLineDash([5, 4]);
  context.strokeStyle = "#b87513";
  context.beginPath(); context.moveTo(left, y(36)); context.lineTo(width - right, y(36)); context.stroke();
  context.setLineDash([]);

  const drawTrace = (replay, color) => {
    context.strokeStyle = color;
    context.lineWidth = 1.8;
    context.beginPath();
    replay.force.forEach((value, index) => {
      const px = x(replay.timestamps[index] ?? index / replay.fps);
      const py = y(value);
      if (index === 0) context.moveTo(px, py); else context.lineTo(px, py);
    });
    context.stroke();
  };
  drawTrace(failure, "#c53e3a");
  drawTrace(recovered, "#147d73");

  recoveryTimelineEvents(recovered).filter(event => ["predicate-violated", "recovery-start", "predicate-restored", "recovery-success"].includes(event.type)).forEach(event => {
    context.strokeStyle = event.type === "predicate-violated" ? "#c53e3a" : event.type === "recovery-start" ? "#38679b" : "#147d73";
    context.globalAlpha = .45;
    context.beginPath(); context.moveTo(x(event.time), top); context.lineTo(x(event.time), top + plotHeight); context.stroke();
    context.globalAlpha = 1;
  });
  const cursor = Number($("#recoveryScrubber").value || 0);
  context.strokeStyle = "#38679b";
  context.lineWidth = 2;
  context.beginPath(); context.moveTo(x(cursor), top); context.lineTo(x(cursor), top + plotHeight); context.stroke();
  context.fillStyle = "#71807c";
  context.font = "9px system-ui, sans-serif";
  context.fillText(`${format(maxForce, 0)} N`, 2, top + 8);
  context.fillText("36 N", 5, y(36) + 3);
  context.fillText("0", left - 4, height - 7);
  context.textAlign = "right";
  context.fillText(`${format(duration, 2)} s`, width - right, height - 7);
  context.textAlign = "left";
}

function recoveryFrame(replay, time) {
  if (!replay) return null;
  const timestamps = replay.timestamps || [];
  let index = Math.round(time * Number(replay.fps || 20));
  if (timestamps.length) {
    index = Math.max(0, Math.min(timestamps.length - 1, index));
    while (index + 1 < timestamps.length && Number(timestamps[index + 1]) <= time) index += 1;
  }
  return {
    index,
    time: Number(timestamps[index] ?? time),
    phase: replay.phases?.[index] || "unknown",
    success: Boolean(replay.success_trace?.[index]),
    grasped: Boolean(replay.is_grasped?.[index]),
    force: Number(replay.force?.[index] || 0)
  };
}

function setRecoveryTime(time, seek = false) {
  const max = Number($("#recoveryScrubber").max || 0);
  const value = Math.max(0, Math.min(Number(time || 0), max));
  $("#recoveryScrubber").value = String(value);
  $("#recoveryTime").textContent = `${format(value, 2)} s`;
  const pairs = [
    ["Failure", state.recovery.failureReplay, $("#recoveryFailureVideo")],
    ["Recovered", state.recovery.recoveredReplay, $("#recoveryRecoveredVideo")]
  ];
  pairs.forEach(([label, replay, video]) => {
    const frame = recoveryFrame(replay, value);
    if (!frame) return;
    $(`#recovery${label}Time`).textContent = `${format(frame.time, 2)} s`;
    $(`#recovery${label}Readout`).textContent = `${frame.phase} · success=${frame.success} · grasped=${frame.grasped} · ${format(frame.force, 1)} N`;
    if (seek && Number.isFinite(video.duration) && Math.abs(video.currentTime - value) > .035) video.currentTime = Math.min(value, video.duration || value);
  });
  drawRecoveryEvidence();
}

function pauseRecoveryPlayback() {
  state.recovery.playing = false;
  $("#recoveryFailureVideo").pause();
  $("#recoveryRecoveredVideo").pause();
  $("#recoveryPlay").textContent = "▶";
  $("#recoveryPlay").title = "播放";
}

async function toggleRecoveryPlayback() {
  if (!state.recovery.failureReplay || !state.recovery.recoveredReplay) return;
  if (state.recovery.playing) {
    pauseRecoveryPlayback();
    return;
  }
  const max = Number($("#recoveryScrubber").max || 0);
  if (Number($("#recoveryScrubber").value) >= max - .03) setRecoveryTime(0, true);
  const failureVideo = $("#recoveryFailureVideo");
  const recoveredVideo = $("#recoveryRecoveredVideo");
  const time = Number($("#recoveryScrubber").value || 0);
  failureVideo.currentTime = time;
  recoveredVideo.currentTime = time;
  try {
    await Promise.all([failureVideo.play(), recoveredVideo.play()]);
    state.recovery.playing = true;
    $("#recoveryPlay").textContent = "■";
    $("#recoveryPlay").title = "暂停";
  } catch (error) {
    pauseRecoveryPlayback();
    toast(error.message, true);
  }
}

function setWorkspaceMode(mode) {
  const simulation = mode === "simulation";
  const benchmark = mode === "benchmark";
  const audit = mode === "audit";
  const embodied = mode === "embodied";
  const reasoning = mode === "reasoning";
  const recovery = mode === "recovery";
  state.workspaceMode = mode;
  $("#diagnosticsWorkspace").hidden = simulation || benchmark || audit || embodied || reasoning || recovery;
  $("#simulationPage").hidden = !simulation;
  $("#benchmarkPage").hidden = !benchmark;
  $("#auditPage").hidden = !audit;
  $("#embodiedPage").hidden = !embodied;
  $("#reasoningPage").hidden = !reasoning;
  $("#recoveryPage").hidden = !recovery;
  $("#diagnosticsMode").classList.toggle("active", !simulation && !benchmark && !audit && !embodied && !reasoning && !recovery);
  $("#auditMode").classList.toggle("active", audit);
  $("#simulationMode").classList.toggle("active", simulation);
  $("#benchmarkMode").classList.toggle("active", benchmark);
  $("#embodiedMode").classList.toggle("active", embodied);
  $("#reasoningMode").classList.toggle("active", reasoning);
  $("#recoveryMode").classList.toggle("active", recovery);
  document.body.classList.toggle("simulation-active", simulation || benchmark);
  if (simulation) requestAnimationFrame(() => { resizeSimulationRenderer(); renderSimulationFrame(); });
  if (audit) {
    if (!state.audit) loadAudit().catch(error => toast(error.message, true));
    loadBatchRepairStatus();
  }
  if (embodied && !state.embodied) loadEmbodiedEvaluation();
  if (reasoning && !state.taskReasoning) loadTaskReasoning();
  if (recovery && !state.recovery.catalog) loadRecoveryCatalog().catch(error => toast(error.message, true));
  if (recovery) scheduleRecoveryEvidenceDraw();
}

async function loadProfiles() {
  state.profiles = await api("/api/profiles");
  const options = state.profiles.profiles.map(profile => `<option value="${escapeHtml(profile.profile_id)}">${escapeHtml(profile.name)} · ${escapeHtml(profile.robot)}</option>`).join("");
  $("#benchmarkProfile").innerHTML = options;
  $("#repairBenchmarkProfile").innerHTML = options;
  $("#benchmarkProfile").value = state.profiles.active_profile;
  $("#repairBenchmarkProfile").value = state.profiles.active_profile;
  renderBenchmarkProfileDescription();
}

function renderBenchmarkProfileDescription() {
  const profile = state.profiles?.profiles.find(item => item.profile_id === $("#benchmarkProfile").value);
  if (!profile) return;
  $("#benchmarkProfileDescription").textContent = `${profile.description} 力阈值下限 ${profile.force_floor} N，关节速度下限 ${profile.joint_velocity_floor} rad/s。`;
}

async function selectBenchmarkProfile(sourceId = "benchmarkProfile") {
  const profileId = $(`#${sourceId}`).value;
  try {
    await api("/api/profile/load", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ profile_id: profileId })
    });
    state.profiles.active_profile = profileId;
    $("#benchmarkProfile").value = profileId;
    $("#repairBenchmarkProfile").value = profileId;
    renderBenchmarkProfileDescription();
    await loadDataset(state.activeEpisode);
    toast("诊断 Profile 已应用到当前数据集");
  } catch (error) { toast(error.message, true); }
}

const percent = value => `${format(Number(value || 0) * 100, 1)}%`;
function renderBenchmark(result) {
  state.benchmark = result;
  const metrics = result.metrics, baseline = result.baseline, comparison = result.comparison, performance = result.performance;
  $("#benchmarkResults").hidden = false;
  $("#exportBenchmark").disabled = false;
  $("#benchmarkStatus").className = "benchmark-status ready";
  $("#benchmarkStatus strong").textContent = `评测完成 · ${result.protocol.sample_count} 条轨迹`;
  $("#benchmarkStatus span").textContent = `${result.protocol.profile.name} · ${result.protocol.seed_count} seeds`;
  $("#benchmarkProtocol").textContent = `${result.protocol.fault_classes} 类故障 × ${result.protocol.intensity_levels} 档强度 × ${result.protocol.seed_count} 个种子，另含 ${result.protocol.seed_count} 条正常轨迹。`;
  $("#benchmarkF1").textContent = percent(metrics.macro_f1);
  $("#benchmarkF1Delta").textContent = `较固定阈值 +${format(comparison.macro_f1_delta * 100, 1)} pp`;
  $("#benchmarkPrecision").textContent = percent(metrics.macro_precision);
  $("#benchmarkRecall").textContent = percent(metrics.macro_recall);
  $("#benchmarkRecallDelta").textContent = `较固定阈值 +${format(comparison.recall_delta * 100, 1)} pp`;
  $("#benchmarkFpr").textContent = percent(metrics.nominal_false_positive_rate);
  $("#benchmarkLatency").textContent = `${format(performance.latency_p95_ms, 1)} ms`;
  $("#benchmarkSamples").textContent = compactNumber(result.protocol.sample_count);
  $("#benchmarkSyncMae").textContent = `${format(performance.sync_offset_mae_ms ?? 0, 1)} ms`;
  $("#benchmarkLocation").textContent = `${format(performance.localization_median_error_ms ?? 0, 1)} ms`;
  $("#benchmarkExact").textContent = percent(metrics.exact_match);
  $("#benchmarkBaseline").textContent = percent(baseline.macro_f1);
  $("#benchmarkBaselineDetail").textContent = `Recall ${percent(baseline.macro_recall)}`;
  $("#benchmarkClassBody").innerHTML = metrics.per_class.map(item => `
    <tr><td><strong>${escapeHtml(issueCodeName(item.code))}</strong><small>${escapeHtml(item.code)}</small></td><td>${item.support}</td><td>${percent(item.precision)}</td><td>${percent(item.recall)}</td><td><b>${percent(item.f1)}</b></td><td><span class="benchmark-pass ${item.f1 < .9 ? "warn" : ""}">${item.f1 >= .9 ? "稳定" : "需关注"}</span></td></tr>`).join("");
  const intensities = ["轻微", "中等", "严重"];
  const faults = [...new Set(result.matrix.map(item => item.fault_id))];
  $("#benchmarkMatrixBody").innerHTML = faults.map(faultId => {
    const rows = result.matrix.filter(item => item.fault_id === faultId);
    const mild = rows.find(item => item.intensity === "轻微");
    return `<tr><td><strong>${escapeHtml(rows[0].fault_name)}</strong><small>${escapeHtml(rows[0].code)}</small></td>${intensities.map(level => {
      const item = rows.find(row => row.intensity === level);
      return `<td><span class="detection-cell ${item.detection_rate >= .95 ? "good" : item.detection_rate >= .75 ? "warn" : "bad"}">${percent(item.detection_rate)}</span><small>${item.value} ${escapeHtml(item.unit)}</small></td>`;
    }).join("")}<td><span class="detection-cell baseline">${percent(mild.baseline_detection_rate)}</span><small>${mild.value} ${escapeHtml(mild.unit)}</small></td></tr>`;
  }).join("");
}

async function runBenchmark() {
  const button = $("#runBenchmark");
  button.disabled = true;
  $("#benchmarkLoading").hidden = false;
  $("#benchmarkResults").hidden = true;
  $("#benchmarkStatus").className = "benchmark-status running";
  $("#benchmarkStatus strong").textContent = "正在执行正式诊断器";
  try {
    const result = await api("/api/benchmark/run", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ seed_count: Number($("#benchmarkSeeds").value) })
    });
    renderBenchmark(result);
    toast("统计评测已完成");
  } catch (error) {
    $("#benchmarkStatus").className = "benchmark-status failed";
    $("#benchmarkStatus strong").textContent = "评测执行失败";
    toast(error.message, true);
  } finally {
    button.disabled = false;
    $("#benchmarkLoading").hidden = true;
  }
}

function exportBenchmark() {
  if (!state.benchmark) return;
  const blob = new Blob([JSON.stringify(state.benchmark, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob), link = document.createElement("a");
  link.href = url; link.download = `embodiscope-faultbench-${state.benchmark.protocol.seed_count}seeds.json`; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function setBenchmarkSuite(suite) {
  const repair = suite === "repair";
  state.benchmarkSuite = suite;
  $("#faultBenchmarkWorkspace").hidden = repair;
  $("#repairBenchmarkWorkspace").hidden = !repair;
  $("#faultBenchmarkSuite").classList.toggle("active", !repair);
  $("#repairBenchmarkSuite").classList.toggle("active", repair);
  const status = $("#benchmarkStatus");
  if (repair && state.repairBenchmark) {
    status.className = `benchmark-status ${state.repairBenchmark.status === "passed" ? "ready" : "failed"}`;
    status.querySelector("strong").textContent = state.repairBenchmark.status === "passed" ? "RepairBench 全部质量门通过" : "RepairBench 需要复核";
    status.querySelector("span").textContent = `EmbodiScope RepairBench ${state.repairBenchmark.protocol.version}`;
  } else if (!repair && state.benchmark) {
    status.className = "benchmark-status ready";
    status.querySelector("strong").textContent = "FaultBench 统计评测完成";
    status.querySelector("span").textContent = `EmbodiScope FaultBench ${state.benchmark.protocol.version}`;
  } else {
    status.className = "benchmark-status";
    status.querySelector("strong").textContent = repair ? "等待运行清洗评测" : "等待运行故障评测";
    status.querySelector("span").textContent = repair ? "EmbodiScope RepairBench 1.0" : "EmbodiScope FaultBench 1.0";
  }
}

function repairMetric(value) {
  const numeric = Number(value || 0);
  return numeric !== 0 && Math.abs(numeric) < .001 ? numeric.toExponential(2) : format(numeric, 4);
}

function renderRepairBenchmark(result) {
  state.repairBenchmark = result;
  const metrics = result.metrics;
  $("#repairBenchmarkResults").hidden = false;
  $("#exportRepairBenchmark").disabled = false;
  $("#repairBenchmarkSuccess").textContent = percent(metrics.repair_success_rate);
  $("#repairBenchmarkRmse").textContent = repairMetric(metrics.reconstruction_rmse);
  $("#repairBenchmarkOvercorrection").textContent = percent(metrics.nominal_overcorrection_rate);
  $("#repairBenchmarkIsolation").textContent = percent(metrics.risk_isolation_recall);
  $("#repairBenchmarkPreservation").textContent = percent(metrics.physical_measurement_preservation);
  $("#repairBenchmarkSamples").textContent = compactNumber(result.protocol.sample_count);
  $("#repairBenchmarkSync").textContent = `${format(metrics.sync_residual_mae_ms, 1)} ms`;
  $("#repairBenchmarkFalseQuarantine").textContent = percent(metrics.nominal_false_quarantine_rate);
  $("#repairBenchmarkRetained").textContent = percent(metrics.average_retained_rate);
  $("#repairBenchmarkLatency").textContent = `${format(result.performance.latency_p95_ms, 1)} ms`;
  $("#repairBenchmarkClassBody").innerHTML = result.per_class.map(item => {
    const evidence = item.reconstruction_rmse != null ? `RMSE ${repairMetric(item.reconstruction_rmse)}`
      : item.preservation_rate != null ? `保持 ${percent(item.preservation_rate)}`
      : item.isolation_recall != null ? `召回 ${percent(item.isolation_recall)}` : "—";
    return `<tr><td><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.action_code)}</small></td><td>${item.support}</td><td><b>${percent(item.success_rate)}</b></td><td>${item.reconstruction_rmse == null ? "—" : repairMetric(item.reconstruction_rmse)}</td><td>${evidence}</td><td>${percent(item.average_retained_rate)}</td></tr>`;
  }).join("");
  $("#repairBenchmarkGates").innerHTML = result.quality_gates.map(gate => `
    <div class="repair-gate-row"><div><span>${escapeHtml(gate.name)}</span><small>${gate.operator} ${gate.name.includes("RMSE") ? repairMetric(gate.threshold) : percent(gate.threshold)}</small></div><b class="${gate.passed ? "" : "failed"}">${gate.passed ? "PASS" : "FAIL"}</b></div>`).join("");
  const strategies = { reconstruction: "数值重建", synchronization: "时间校正", segmentation: "片段切分", isolation: "风险隔离" };
  const intensities = ["轻微", "中等", "严重"];
  const repairIds = [...new Set(result.matrix.map(item => item.repair_id))];
  $("#repairBenchmarkMatrixBody").innerHTML = repairIds.map(repairId => {
    const rows = result.matrix.filter(item => item.repair_id === repairId);
    return `<tr><td><strong>${escapeHtml(rows[0].name)}</strong><small>${escapeHtml(rows[0].mode)}</small></td>${intensities.map(level => {
      const item = rows.find(row => row.intensity === level);
      return `<td><span class="detection-cell ${item.success_rate >= .95 ? "good" : item.success_rate >= .75 ? "warn" : "bad"}">${percent(item.success_rate)}</span><small>${item.value} ${escapeHtml(item.unit)} · 保留 ${percent(item.retained_rate)}</small></td>`;
    }).join("")}<td><span class="benchmark-pass">${strategies[rows[0].mode]}</span></td></tr>`;
  }).join("");
  setBenchmarkSuite("repair");
}

async function runRepairBenchmark() {
  const button = $("#runRepairBenchmark");
  button.disabled = true;
  $("#repairBenchmarkLoading").hidden = false;
  $("#repairBenchmarkResults").hidden = true;
  $("#benchmarkStatus").className = "benchmark-status running";
  $("#benchmarkStatus strong").textContent = "正在执行 RepairBench";
  try {
    const result = await api("/api/repair-benchmark/run", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ seed_count: Number($("#repairBenchmarkSeeds").value) })
    });
    renderRepairBenchmark(result);
    toast("清洗效果统计评测已完成");
  } catch (error) {
    $("#benchmarkStatus").className = "benchmark-status failed";
    $("#benchmarkStatus strong").textContent = "RepairBench 执行失败";
    toast(error.message, true);
  } finally {
    button.disabled = false;
    $("#repairBenchmarkLoading").hidden = true;
  }
}

function exportRepairBenchmark() {
  if (!state.repairBenchmark) return;
  const blob = new Blob([JSON.stringify(state.repairBenchmark, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob), link = document.createElement("a");
  link.href = url; link.download = `embodiscope-repairbench-${state.repairBenchmark.protocol.seed_count}seeds.json`; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function loadSimulationCatalog() {
  const catalog = await api("/api/simulation/catalog");
  state.simulation.catalog = catalog;
  $("#simulationEnv").innerHTML = catalog.environments.map(item => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)} · ${escapeHtml(item.robot)}</option>`).join("");
  const categories = [...new Set(catalog.scenarios.map(item => item.category_name))];
  $("#simulationScenario").innerHTML = categories.map(category => `<optgroup label="${escapeHtml(category)}">${catalog.scenarios.filter(item => item.category_name === category).map(item => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join("")}</optgroup>`).join("");
  $("#simulationScenario").value = "collision";
  renderSimulationScenarioBrief();
  const runtime = $("#simulationRuntime");
  runtime.className = `runtime-badge ${catalog.runtime.available ? "ready" : "failed"}`;
  runtime.querySelector("strong").textContent = catalog.runtime.available
    ? `ManiSkill ${catalog.runtime.mani_skill_version || "3"} · SAPIEN ${catalog.runtime.sapien_version || "3"}`
    : "仿真运行时不可用";
  runtime.querySelector("span").textContent = catalog.runtime.available
    ? `${catalog.runtime.sim_backend} · ${catalog.runtime.render_backend}`
    : catalog.runtime.error;
  $("#runSimulation").disabled = !catalog.runtime.available;
}

function renderSimulationScenarioBrief() {
  const catalog = state.simulation.catalog;
  if (!catalog) return;
  const scenario = catalog.scenarios.find(item => item.id === $("#simulationScenario").value) || catalog.scenarios[0];
  if (!scenario) return;
  $("#simulationScenarioCategory").textContent = scenario.category_name;
  $("#simulationScenarioName").textContent = scenario.name;
  $("#simulationScenarioDescription").textContent = scenario.description;
  $("#simulationExpectedEvidence").innerHTML = scenario.expected.map(item => `<span>${escapeHtml(item)}</span>`).join("");
  $("#simulationScenarioCount").textContent = `${catalog.scenarios.length} SCENARIOS · ${new Set(catalog.scenarios.map(item => item.category)).size} CATEGORIES`;
  $("#simulationSteps").value = String(scenario.recommended_steps || 80);
}

async function startSimulation() {
  const button = $("#runSimulation");
  button.disabled = true;
  try {
    const job = await api("/api/simulation/run", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        env_id: $("#simulationEnv").value,
        scenario: $("#simulationScenario").value,
        seed: Number($("#simulationSeed").value),
        steps: Number($("#simulationSteps").value),
        fps: 20, width: 320, height: 240, record_video: true
      })
    });
    state.simulation.activeJob = job;
    $("#simulationEmpty").hidden = true;
    $("#simulationReplay").hidden = true;
    renderSimulationJob(job);
    pollSimulationJob(job.id);
  } catch (error) {
    button.disabled = false;
    toast(error.message, true);
  }
}

function renderSimulationJob(job) {
  $("#simulationJob").hidden = false;
  $("#simulationJobId").textContent = job.id;
  $("#simulationJobMessage").textContent = job.error || job.message;
  const percent = Math.round((job.progress || 0) * 100);
  $("#simulationProgressLabel").textContent = `${percent}%`;
  $("#simulationProgressBar").style.width = `${percent}%`;
  $("#cancelSimulation").hidden = !["queued", "running"].includes(job.status);
}

async function pollSimulationJob(jobId) {
  clearTimeout(state.simulation.pollTimer);
  try {
    const job = await api(`/api/simulation/status/${encodeURIComponent(jobId)}`);
    state.simulation.activeJob = job;
    renderSimulationJob(job);
    if (["queued", "running"].includes(job.status)) {
      state.simulation.pollTimer = setTimeout(() => pollSimulationJob(jobId), 450);
      return;
    }
    $("#runSimulation").disabled = false;
    if (job.status === "completed") {
      await loadSimulationReplay(job);
      toast("真实仿真与录制已完成");
    } else {
      $("#simulationEmpty").hidden = false;
      toast(job.error || "仿真作业未完成", true);
    }
  } catch (error) {
    $("#runSimulation").disabled = false;
    toast(error.message, true);
  }
}

async function cancelSimulation() {
  const job = state.simulation.activeJob;
  if (!job) return;
  try {
    await api("/api/simulation/cancel", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ job_id: job.id })
    });
    toast("已请求取消仿真作业");
  } catch (error) { toast(error.message, true); }
}

function simulationVector(values) {
  return new THREE.Vector3(Number(values[0]), Number(values[2]), -Number(values[1]));
}

function initSimulationScene() {
  if (state.simulation.renderer) return;
  const canvas = $("#simulationCanvas");
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x101716);
  scene.fog = new THREE.Fog(0x101716, 2.2, 5.5);
  const camera = new THREE.PerspectiveCamera(40, 1, 0.01, 20);
  camera.position.set(1.05, 0.8, 1.25);
  camera.lookAt(-0.15, 0.16, 0);
  scene.add(new THREE.HemisphereLight(0xe8fffb, 0x27332f, 2.3));
  const light = new THREE.DirectionalLight(0xffffff, 2.8); light.position.set(2, 4, 3); scene.add(light);
  const grid = new THREE.GridHelper(2.4, 24, 0x4d625d, 0x293835); grid.position.y = 0; scene.add(grid);
  const table = new THREE.Mesh(new THREE.BoxGeometry(1.45, 0.035, 1.05), new THREE.MeshStandardMaterial({ color: 0x6f5643, roughness: .88 }));
  table.position.set(0, -0.025, 0); scene.add(table);
  const world = new THREE.Group(); scene.add(world);
  const segments = [];
  for (let index = 0; index < 10; index++) {
    const segment = new THREE.Mesh(new THREE.CylinderGeometry(.018, .025, 1, 12), new THREE.MeshStandardMaterial({ color: index % 2 ? 0xdce2e0 : 0x7c8986, roughness: .55, metalness: .18 }));
    world.add(segment); segments.push(segment);
  }
  const joints = [];
  for (let index = 0; index < 11; index++) {
    const joint = new THREE.Mesh(new THREE.SphereGeometry(.031, 14, 10), new THREE.MeshStandardMaterial({ color: 0x273330, roughness: .42, metalness: .25 }));
    world.add(joint); joints.push(joint);
  }
  const object = new THREE.Mesh(new THREE.BoxGeometry(.04, .04, .04), new THREE.MeshStandardMaterial({ color: 0xdc4943, roughness: .48 })); world.add(object);
  const goal = new THREE.Mesh(new THREE.SphereGeometry(.027, 18, 12), new THREE.MeshBasicMaterial({ color: 0x4dc785, wireframe: true })); world.add(goal);
  const tcp = new THREE.Mesh(new THREE.SphereGeometry(.018, 16, 12), new THREE.MeshStandardMaterial({ color: 0xf2b84b, emissive: 0x5c3b08 })); world.add(tcp);
  Object.assign(state.simulation, { renderer, scene, camera, world, segments, joints, object, goal, tcp });
}

function placeSimulationSegment(mesh, start, end) {
  const delta = end.clone().sub(start);
  const length = delta.length();
  mesh.visible = length > 1e-5;
  if (!mesh.visible) return;
  mesh.position.copy(start).add(end).multiplyScalar(.5);
  mesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), delta.normalize());
  mesh.scale.set(1, length, 1);
}

function buildSimulationPath() {
  const simulation = state.simulation;
  if (simulation.path) {
    simulation.world.remove(simulation.path);
    simulation.path.geometry.dispose(); simulation.path.material.dispose();
  }
  const points = simulation.replay.tcp.map(simulationVector);
  simulation.path = new THREE.Line(
    new THREE.BufferGeometry().setFromPoints(points),
    new THREE.LineBasicMaterial({ color: 0x36b9a8, transparent: true, opacity: .7 })
  );
  simulation.world.add(simulation.path);
}

function resizeSimulationRenderer() {
  const simulation = state.simulation;
  const wrap = $(".simulation-canvas-wrap");
  if (!simulation.renderer || !wrap || !wrap.clientWidth || !wrap.clientHeight) return;
  simulation.renderer.setSize(wrap.clientWidth, wrap.clientHeight, false);
  simulation.camera.aspect = wrap.clientWidth / wrap.clientHeight;
  simulation.camera.updateProjectionMatrix();
}

function renderSimulationFrame() {
  if (state.simulation.renderer) state.simulation.renderer.render(state.simulation.scene, state.simulation.camera);
}

function setSimulationIndex(index, seekVideo = false) {
  const simulation = state.simulation;
  const replay = simulation.replay;
  if (!replay?.rows) return;
  const next = Math.max(0, Math.min(replay.rows - 1, Number(index) || 0));
  simulation.index = next;
  $("#simulationScrubber").value = String(next);
  $("#simulationFrameCounter").textContent = `${next + 1} / ${replay.rows}`;
  const time = Number(replay.timestamps[next]);
  $("#replayTime").textContent = `${time.toFixed(2)} s`;
  $("#replayPhase").textContent = replay.phases[next] || "—";
  const tcp = replay.tcp[next], object = replay.object[next], goal = replay.goal[next];
  $("#replayTcp").textContent = `TCP ${tcp.map(value => Number(value).toFixed(3)).join(" / ")}`;
  $("#replayObject").textContent = `OBJ ${object.map(value => Number(value).toFixed(3)).join(" / ")}`;
  const links = replay.links[next].slice(0, 11).map(simulationVector);
  simulation.segments.forEach((segment, item) => placeSimulationSegment(segment, links[item], links[item + 1]));
  simulation.joints.forEach((joint, item) => joint.position.copy(links[item]));
  simulation.object.position.copy(simulationVector(object));
  simulation.goal.position.copy(simulationVector(goal));
  simulation.tcp.position.copy(simulationVector(tcp));
  const force = Number(replay.force[next] || 0), action = Number(replay.action_norm[next] || 0), valid = Boolean(replay.frame_valid[next]);
  const grasped = Boolean(replay.is_grasped?.[next]);
  const gripperCommand = Number(replay.gripper_command?.[next] || 0);
  $("#telemetryForce").textContent = `${force.toFixed(1)} N`;
  $("#telemetryAction").textContent = action.toFixed(2);
  $("#telemetryGrasp").textContent = grasped ? "已抓取" : gripperCommand < -0.5 ? "闭合未抓取" : "未抓取";
  $("#telemetryFrame").textContent = valid ? "有效" : "丢失 / 重复";
  $("#forceMeter").style.width = `${Math.min(100, force / Math.max(1, replay.peak_force) * 100)}%`;
  $("#actionMeter").style.width = `${Math.min(100, action / 2.8 * 100)}%`;
  $("#graspMeter").style.width = grasped ? "100%" : gripperCommand < -0.5 ? "38%" : "8%";
  $("#frameMeter").style.width = valid ? "100%" : "8%";
  const frameState = $("#videoFrameState"); frameState.textContent = valid ? "FRAME OK" : "FRAME DROP"; frameState.classList.toggle("invalid", !valid);
  $$(".simulation-event").forEach(button => button.classList.toggle("active", Math.abs(Number(button.dataset.time) - time) < .08));
  if (seekVideo) {
    const video = $("#simulationVideo");
    if (Math.abs(video.currentTime - time) > .04) video.currentTime = time;
  }
  renderSimulationFrame();
}

async function loadSimulationReplay(job) {
  const replay = await api(job.result.replay_url);
  state.simulation.replay = replay;
  state.simulation.index = 0;
  $("#simulationEmpty").hidden = true;
  $("#simulationReplay").hidden = false;
  $("#replayEnv").textContent = replay.env_id;
  $("#replayScenario").textContent = replay.scenario_name;
  $("#replayFrames").textContent = replay.rows;
  $("#replayPeakForce").textContent = `${Number(replay.peak_force).toFixed(1)} N`;
  $("#replayWallTime").textContent = `${Number(replay.wall_time).toFixed(2)} s`;
  $("#simulationScrubber").max = String(Math.max(0, replay.rows - 1));
  $("#simulationEvents").innerHTML = replay.events.length ? replay.events.map(event => `<button class="simulation-event" type="button" data-time="${Number(event.time)}" title="${escapeHtml(event.source === "diagnosis" ? "诊断检测" : "故障注入")}">${escapeHtml(event.label)}</button>`).join("") : "<span>未产生事件</span>";
  $$(".simulation-event").forEach(button => button.addEventListener("click", () => setSimulationIndex(Math.round(Number(button.dataset.time) * replay.fps), true)));
  const video = $("#simulationVideo");
  video.src = `${job.result.video_url}?v=${encodeURIComponent(job.id)}`;
  video.load();
  initSimulationScene();
  buildSimulationPath();
  requestAnimationFrame(() => { resizeSimulationRenderer(); setSimulationIndex(0); });
}

async function loadSimulationDataset() {
  const job = state.simulation.activeJob;
  if (!job || job.status !== "completed") return;
  const button = $("#loadSimulationDataset"); button.disabled = true;
  try {
    await api("/api/simulation/load", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ job_id: job.id })
    });
    setWorkspaceMode("diagnostics");
    await Promise.all([loadDataset("0"), loadDatasetLibrary()]);
    activateTab("overview");
    toast("仿真轨迹已送入诊断引擎");
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
}

$("#uploadButton").addEventListener("click", () => $("#fileInput").click());
$("#diagnosticsMode").addEventListener("click", () => setWorkspaceMode("diagnostics"));
$("#auditMode").addEventListener("click", () => setWorkspaceMode("audit"));
$("#simulationMode").addEventListener("click", () => setWorkspaceMode("simulation"));
$("#benchmarkMode").addEventListener("click", () => setWorkspaceMode("benchmark"));
$("#embodiedMode").addEventListener("click", () => setWorkspaceMode("embodied"));
$("#reasoningMode").addEventListener("click", () => setWorkspaceMode("reasoning"));
$("#recoveryMode").addEventListener("click", () => setWorkspaceMode("recovery"));
$("#recoveryJob").after($("#recoveryResults"));
$("#runRecovery").addEventListener("click", startRecovery);
$("#cancelRecovery").addEventListener("click", handleRecoveryJobAction);
$("#runRecoveryBenchmark").addEventListener("click", startRecoveryBenchmark);
$("#cancelRecoveryBenchmark").addEventListener("click", cancelRecoveryBenchmark);
$("#recoveryScenario").addEventListener("change", handleRecoveryScenarioChange);
$("#recoverySeed").addEventListener("input", markRecoveryResultStale);
$("#recoveryHorizon").addEventListener("input", markRecoveryResultStale);
$("#recoveryPlay").addEventListener("click", toggleRecoveryPlayback);
$("#recoveryScrubber").addEventListener("input", event => {
  pauseRecoveryPlayback();
  setRecoveryTime(Number(event.target.value), true);
});
$("#recoveryFailureVideo").addEventListener("timeupdate", event => {
  if (!state.recovery.playing) return;
  const recoveredVideo = $("#recoveryRecoveredVideo");
  if (Math.abs(recoveredVideo.currentTime - event.target.currentTime) > .08) recoveredVideo.currentTime = event.target.currentTime;
  setRecoveryTime(event.target.currentTime);
});
$("#recoveryFailureVideo").addEventListener("ended", pauseRecoveryPlayback);
$("#runEmbodiedEvaluation").addEventListener("click", loadEmbodiedEvaluation);
$("#runTaskReasoning").addEventListener("click", loadTaskReasoning);
$("#reasoningEpisodeSelect").addEventListener("change", event => renderReasoningEpisode(event.target.value));
$("#generateRepair").addEventListener("click", generateRepair);
$("#auditSearch").addEventListener("input", renderAuditTable);
$("#auditGradeFilter").addEventListener("change", renderAuditTable);
$("#auditIssueFilter").addEventListener("change", renderAuditTable);
$("#auditSort").addEventListener("change", renderAuditTable);
$("#faultBenchmarkSuite").addEventListener("click", () => setBenchmarkSuite("fault"));
$("#repairBenchmarkSuite").addEventListener("click", () => setBenchmarkSuite("repair"));
$("#benchmarkProfile").addEventListener("change", () => selectBenchmarkProfile("benchmarkProfile"));
$("#repairBenchmarkProfile").addEventListener("change", () => selectBenchmarkProfile("repairBenchmarkProfile"));
$("#runBenchmark").addEventListener("click", runBenchmark);
$("#exportBenchmark").addEventListener("click", exportBenchmark);
$("#runRepairBenchmark").addEventListener("click", runRepairBenchmark);
$("#exportRepairBenchmark").addEventListener("click", exportRepairBenchmark);
$("#startBatchRepair").addEventListener("click", startBatchRepair);
$("#cancelBatchRepair").addEventListener("click", cancelBatchRepair);
$("#runSimulation").addEventListener("click", startSimulation);
$("#simulationScenario").addEventListener("change", renderSimulationScenarioBrief);
$("#cancelSimulation").addEventListener("click", cancelSimulation);
$("#loadSimulationDataset").addEventListener("click", loadSimulationDataset);
$("#simulationScrubber").addEventListener("input", event => {
  $("#simulationVideo").pause();
  setSimulationIndex(Number(event.target.value), true);
});
$("#simulationPlay").addEventListener("click", () => {
  const video = $("#simulationVideo");
  if (!state.simulation.replay) return;
  if (video.paused) {
    if (video.currentTime >= state.simulation.replay.duration - .03) video.currentTime = 0;
    video.play().catch(error => toast(error.message, true));
  } else video.pause();
});
$("#simulationVideo").addEventListener("play", () => { $("#simulationPlay").textContent = "■"; $("#simulationPlay").title = "暂停"; });
$("#simulationVideo").addEventListener("pause", () => { $("#simulationPlay").textContent = "▶"; $("#simulationPlay").title = "播放"; });
$("#simulationVideo").addEventListener("timeupdate", event => {
  const replay = state.simulation.replay;
  if (replay) setSimulationIndex(Math.round(event.target.currentTime * replay.fps));
});
$("#adapterButton").addEventListener("click", () => setAdapterModal(true));
$("#datasetLibraryButton").addEventListener("click", () => setDatasetLibraryModal(true));
$("#closeDatasetLibrary").addEventListener("click", () => setDatasetLibraryModal(false));
$("#datasetLibraryModal").addEventListener("click", event => { if (event.target === $("#datasetLibraryModal")) setDatasetLibraryModal(false); });
$("#closeAdapterModal").addEventListener("click", () => setAdapterModal(false));
$("#adapterModal").addEventListener("click", event => { if (event.target === $("#adapterModal")) setAdapterModal(false); });
document.addEventListener("keydown", event => { if (event.key === "Escape") { setAdapterModal(false); setDatasetLibraryModal(false); } });
$("#fileInput").addEventListener("change", event => uploadFile(event.target.files[0]));
$("#resetButton").addEventListener("click", async () => {
  try { await api("/api/reset", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); await Promise.all([loadDataset(), loadDatasetLibrary()]); toast("已恢复内置演示数据"); }
  catch (error) { toast(error.message, true); }
});
$("#episodeSearch").addEventListener("input", event => renderEpisodeList(event.target.value));
$$(".tab").forEach(tab => tab.addEventListener("click", () => activateTab(tab.dataset.tab)));
$("#downloadReport").addEventListener("click", () => { if (state.activeEpisode) window.location.href = `/api/report/${encodeURIComponent(state.activeEpisode)}`; });
$("#downloadRerun").addEventListener("click", () => {
  if (!state.activeEpisode) return;
  window.location.href = `/api/rerun/${encodeURIComponent(state.activeEpisode)}`;
  toast("正在生成 Rerun 空间记录");
});
$("#spatialPlay").addEventListener("click", toggleSpatialPlayback);
$("#spatialScrubber").addEventListener("input", event => { stopSpatialPlayback(); setSpatialIndex(Number(event.target.value)); });
$("#mediaPlay").addEventListener("click", () => {
  const video = $("#datasetVideo");
  if (!state.media.info?.available) return;
  if (video.paused) {
    if (video.currentTime >= state.media.end - .03 || video.currentTime < state.media.start) seekMedia(0);
    video.play().catch(error => toast(error.message, true));
  } else video.pause();
});
$("#mediaScrubber").addEventListener("input", event => { $("#datasetVideo").pause(); seekMedia(event.target.value); });
$("#datasetVideo").addEventListener("play", () => { $("#mediaPlay").textContent = "■"; $("#mediaPlay").title = "暂停"; });
$("#datasetVideo").addEventListener("pause", () => { $("#mediaPlay").textContent = "▶"; $("#mediaPlay").title = "播放"; });
$("#datasetVideo").addEventListener("timeupdate", event => {
  if (!state.media.info?.available) return;
  const local = event.target.currentTime - state.media.start;
  if (event.target.currentTime >= state.media.end) { event.target.pause(); seekMedia(state.media.duration); return; }
  if (local >= 0) {
    $("#mediaScrubber").value = local;
    $("#mediaTime").textContent = `${format(local, 2)} s`;
    $("#mediaProgress").textContent = `${format(local, 2)} / ${format(state.media.duration, 2)} s`;
  }
});
$$(".spatial-view-button").forEach(button => button.addEventListener("click", () => setSpatialView(button.dataset.view)));
$("#signalChart").addEventListener("mousemove", handleChartMove);
$("#signalChart").addEventListener("mouseleave", () => { $("#chartTooltip").hidden = true; });
if ("ResizeObserver" in window) {
  const recoveryEvidenceObserver = new ResizeObserver(() => {
    if (!$("#recoveryPage").hidden) scheduleRecoveryEvidenceDraw();
  });
  recoveryEvidenceObserver.observe($("#recoveryEvidenceCanvas"));
}
window.addEventListener("resize", () => requestAnimationFrame(() => { drawChart(); resizeSpatialRenderer(); renderSpatialFrame(); resizeSimulationRenderer(); renderSimulationFrame(); drawRecoveryEvidence(); }));

Promise.all([loadAdapters(), loadDatasetLibrary(), loadDataset(), loadSimulationCatalog(), loadRecoveryCatalog(), loadProfiles()]).catch(error => {
  toast(error.message, true); $("#loadingState p").textContent = `加载失败：${error.message}`;
});
