const $ = (selector) => document.querySelector(selector);

function fmtBytes(value) {
  const bytes = Number(value || 0);
  const units = ["B", "KB", "MB", "GB", "TB"];
  let current = bytes;
  let index = 0;
  while (current >= 1024 && index < units.length - 1) {
    current /= 1024;
    index += 1;
  }
  return `${current.toFixed(index ? 1 : 0)} ${units[index]}`;
}

function pct(value) {
  return Math.max(0, Math.min(Number(value || 0), 100));
}

function setBar(selector, value) {
  $(selector).style.width = `${pct(value)}%`;
}

function fmtWatts(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "N/A";
  return `${number.toFixed(number >= 100 ? 0 : 1)} W`;
}

function renderGpu(gpu) {
  const hasLimit = Number.isFinite(Number(gpu.power_limit_w)) && Number(gpu.power_limit_w) > 0;
  const powerText = hasLimit ? `${fmtWatts(gpu.power_w)} / ${fmtWatts(gpu.power_limit_w)}` : fmtWatts(gpu.power_w);
  return `<article class="gpu-card">
    <div class="gpu-top">
      <div>
        <span>GPU ${gpu.index}</span>
        <strong>${gpu.name || "GPU"}</strong>
      </div>
      <b>${Math.round(gpu.utilization_percent || 0)}%</b>
    </div>
    <div class="mini-row"><span>VRAM</span><span>${gpu.memory_used_mb || 0} / ${gpu.memory_total_mb || 0} MB</span></div>
    <div class="bar"><span style="width:${pct(gpu.memory_percent)}%"></span></div>
    <div class="gpu-meta">
      <span>${gpu.temperature_c || 0}°C</span>
      <span>${gpu.power_source === "unavailable" ? "N/A" : powerText}</span>
    </div>
  </article>`;
}

function renderDisk(disk) {
  return `<div class="disk-row">
    <div>
      <strong>${disk.mount}</strong>
      <span>${disk.filesystem}</span>
    </div>
    <div class="disk-usage">
      <span>${fmtBytes(disk.used_bytes)} / ${fmtBytes(disk.size_bytes)}</span>
      <div class="bar"><span style="width:${pct(disk.used_percent)}%"></span></div>
    </div>
  </div>`;
}

let hasCompleteDashboard = false;
let pollingDashboard = false;

function setLoading() {
  $("#remoteStatus").textContent = "загрузка";
  $("#remoteStatus").classList.remove("bad");
  $("#remoteStatus").classList.add("loading");
  $("#ollamaStatus").textContent = "загрузка";
  $("#ollamaStatus").classList.remove("bad");
  $("#ollamaStatus").classList.add("loading");
  $("#cpuLoad").textContent = "загрузка...";
  $("#cpuLoad").classList.add("loading-text");
  $("#cpuMeta").textContent = "ожидание ответа сервера";
  setBar("#cpuBar", 0);
  $("#ramUsed").textContent = "загрузка...";
  $("#ramUsed").classList.add("loading-text");
  $("#ramMeta").textContent = "ожидание ответа сервера";
  setBar("#ramBar", 0);
  $("#ollamaVersion").textContent = "загрузка...";
  $("#ollamaVersion").classList.add("loading-text");
  $("#ollamaModels").textContent = "ожидание ответа сервера";
  $("#gpuSummary").textContent = "загрузка...";
  $("#gpuGrid").innerHTML = '<div class="empty loading-box">Загрузка данных GPU...</div>';
  $("#diskList").innerHTML = '<div class="empty loading-box">Загрузка данных дисков...</div>';
  $("#tempLog").textContent = "Загрузка температур...";
}

function setDashboardError(message) {
  const text = message || "сервер не вернул полный набор метрик";
  hasCompleteDashboard = true;
  $("#remoteStatus").textContent = "ошибка";
  $("#remoteStatus").classList.remove("loading");
  $("#remoteStatus").classList.add("bad");
  $("#ollamaStatus").textContent = "нет данных";
  $("#ollamaStatus").classList.remove("loading");
  $("#ollamaStatus").classList.add("bad");
  $("#cpuLoad").textContent = "нет данных";
  $("#cpuLoad").classList.remove("loading-text");
  $("#cpuMeta").textContent = text;
  $("#ramUsed").textContent = "нет данных";
  $("#ramUsed").classList.remove("loading-text");
  $("#ramMeta").textContent = text;
  $("#ollamaVersion").textContent = "нет данных";
  $("#ollamaVersion").classList.remove("loading-text");
  $("#ollamaModels").textContent = text;
  $("#gpuSummary").textContent = "нет данных";
  $("#gpuGrid").innerHTML = `<div class="empty">${text}</div>`;
  $("#diskList").innerHTML = `<div class="empty">${text}</div>`;
  $("#tempLog").textContent = text;
}

async function loadDashboard() {
  const response = await fetch("/api/dashboard", { cache: "no-store" });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function render(data) {
  const remote = data.remote || {};
  const cpu = remote.cpu || {};
  const memory = remote.memory || {};
  const gpus = remote.gpus || [];
  const ollama = data.ollama || {};
  if (!remote.ok) {
    setDashboardError(remote.error || "сервер не вернул метрики");
    return;
  }
  hasCompleteDashboard = true;
  $("#remoteStatus").textContent = remote.ok ? remote.host || "server ok" : "server error";
  $("#remoteStatus").classList.remove("loading");
  $("#remoteStatus").classList.toggle("bad", !remote.ok);
  $("#ollamaStatus").textContent = ollama.ok ? "ollama ok" : "ollama offline";
  $("#ollamaStatus").classList.remove("loading");
  $("#ollamaStatus").classList.toggle("bad", !ollama.ok);

  $("#cpuLoad").textContent = `${Math.round(cpu.load_percent || 0)}%`;
  $("#cpuLoad").classList.remove("loading-text");
  $("#cpuMeta").textContent = `${cpu.cores || 0} cores · load ${Number(cpu.load_1m || 0).toFixed(2)} / ${Number(cpu.load_5m || 0).toFixed(2)} / ${Number(cpu.load_15m || 0).toFixed(2)}`;
  setBar("#cpuBar", cpu.load_percent || 0);

  $("#ramUsed").textContent = `${Math.round(memory.used_percent || 0)}%`;
  $("#ramUsed").classList.remove("loading-text");
  $("#ramMeta").textContent = `${fmtBytes(memory.used_bytes)} / ${fmtBytes(memory.total_bytes)} · свободно ${fmtBytes(memory.available_bytes)}`;
  setBar("#ramBar", memory.used_percent || 0);

  $("#ollamaVersion").textContent = ollama.ok ? "online" : "offline";
  $("#ollamaVersion").classList.remove("loading-text");
  $("#ollamaModels").textContent = (ollama.models || []).length
    ? (ollama.models || []).map((model) => `${model.name} · VRAM ${fmtBytes(model.size_vram || 0)}`).join(" | ")
    : "нет загруженных моделей";

  $("#gpuSummary").textContent = gpus.length ? `${gpus.length} GPU` : remote.error || "GPU не найдены";
  $("#gpuGrid").innerHTML = gpus.length ? gpus.map(renderGpu).join("") : '<div class="empty">Нет данных от nvidia-smi</div>';
  $("#diskList").innerHTML = (remote.disks || []).length ? remote.disks.map(renderDisk).join("") : '<div class="empty">Нет данных по дискам</div>';
  $("#tempLog").textContent = (remote.temperatures || []).join("\n") || "нет данных";
}

function updateClock() {
  $("#lastUpdate").textContent = new Date().toLocaleTimeString("ru-RU");
}

async function tick() {
  if (pollingDashboard) return;
  pollingDashboard = true;
  if (!hasCompleteDashboard) setLoading();
  try {
    render(await loadDashboard());
  } catch (error) {
    if (!hasCompleteDashboard) setDashboardError(error.message);
    else {
      $("#remoteStatus").textContent = "dashboard error";
      $("#remoteStatus").classList.remove("loading");
      $("#remoteStatus").classList.add("bad");
    }
  } finally {
    pollingDashboard = false;
  }
}

setLoading();
updateClock();
tick();
setInterval(updateClock, 1000);
setInterval(tick, 1000);
