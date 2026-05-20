const USER_ID_KEY = "serplex-user-id";
const LEGACY_USER_ID_KEY = "codex-lite-user-id";
const INVALID_API_KEY_MESSAGE = "Невалидный API-ключ. Требуется новый ключ в настройках.";

function getOrCreateUserId() {
  const existing = localStorage.getItem(USER_ID_KEY) || localStorage.getItem(LEGACY_USER_ID_KEY);
  if (existing) {
    localStorage.setItem(USER_ID_KEY, existing);
    return existing;
  }
  const randomId = globalThis.crypto?.randomUUID ? globalThis.crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const generated = `user-${randomId}`;
  localStorage.setItem(USER_ID_KEY, generated);
  return generated;
}

const state = {
  userId: getOrCreateUserId(),
  config: null,
  sessionId: null,
  currentChat: null,
  chats: [],
  projects: [],
  selectedModel: null,
  fullAccess: false,
  busy: false,
  activeStreams: new Map(),
  currentStreamKey: null,
  streamSeq: 0,
  activeAssistant: null,
  editingIndex: null,
  updateStatus: null,
  checkingUpdate: false,
  installingUpdate: false,
  confirmResolve: null,
  settings: null,
  authInvalid: false,
  attachments: [],
  taskTags: [],
  composerMenuOpen: false,
  taskTagPanelOpen: false,
};

const $ = (selector) => document.querySelector(selector);

const TASK_TAGS = [
  {
    id: "deep_research",
    label: "Глубокое исследование",
    hint: "дольше думать, проверять гипотезы",
    icon: '<path d="M4 19.5V5a2 2 0 0 1 2-2h11v18H6a2 2 0 0 1-2-1.5Z" /><path d="M8 7h5M8 11h6M8 15h4" />',
  },
  {
    id: "web_search",
    label: "Поиск в интернете",
    hint: "искать свежие факты и источники",
    icon: '<path d="M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z" /><path d="M3.6 9h16.8M3.6 15h16.8" /><path d="M12 3a15 15 0 0 1 0 18M12 3a15 15 0 0 0 0 18" />',
  },
  {
    id: "literary",
    label: "Литературный режим",
    hint: "выше температура и живее стиль",
    icon: '<path d="M4 19.5V5a2 2 0 0 1 2-2h7l7 7v9.5a1.5 1.5 0 0 1-1.5 1.5H6a2 2 0 0 1-2-1.5Z" /><path d="M13 3v7h7" /><path d="M8 14h8M8 17h5" />',
  },
  {
    id: "presentation",
    label: "Создание презентаций",
    hint: "структура слайдов, тезисы, визуальная логика",
    icon: '<path d="M4 5h16v10H4Z" /><path d="M12 15v5" /><path d="m8 20 4-5 4 5" />',
  },
  {
    id: "spreadsheet",
    label: "Таблицы",
    hint: "CSV, Excel, формулы и аналитика",
    icon: '<path d="M4 4h16v16H4Z" /><path d="M4 10h16M4 15h16M10 4v16M15 4v16" />',
  },
  {
    id: "ux_review",
    label: "UX/UI-аудит",
    hint: "искать проблемы интерфейса и сценариев",
    icon: '<path d="M4 5h16v12H4Z" /><path d="M8 21h8" /><path d="M12 17v4" /><path d="M8 10h8M8 13h5" />',
  },
  {
    id: "tests",
    label: "Тесты и регрессии",
    hint: "проверки, edge cases, стабильность",
    icon: '<path d="M9 3h6" /><path d="M10 3v5l-5 9a3 3 0 0 0 2.6 4.5h8.8A3 3 0 0 0 19 17l-5-9V3" /><path d="M8 15h8" />',
  },
  {
    id: "refactor",
    label: "Рефакторинг",
    hint: "чистить код без лишней перестройки",
    icon: '<path d="M7 7h10v10H7Z" /><path d="M3 12h4M17 12h4M12 3v4M12 17v4" />',
  },
];

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function classToken(value) {
  return String(value || "").replace(/[^a-zA-Z0-9_-]+/g, " ").trim();
}

function taskTagById(id) {
  return TASK_TAGS.find((tag) => tag.id === id);
}

function setComposerMenu(open) {
  state.composerMenuOpen = Boolean(open);
  if (!state.composerMenuOpen) state.taskTagPanelOpen = false;
  const menu = $("#composerMenu");
  const button = $("#composerMenuButton");
  if (!menu || !button) return;
  menu.classList.toggle("hidden", !state.composerMenuOpen);
  button.setAttribute("aria-expanded", state.composerMenuOpen ? "true" : "false");
  renderTaskTagMenu();
}

function setTaskTagPanel(open) {
  state.taskTagPanelOpen = Boolean(open);
  const panel = $("#taskTagPanel");
  const button = $("#taskTagsButton");
  if (!panel || !button) return;
  panel.classList.toggle("hidden", !state.taskTagPanelOpen);
  button.setAttribute("aria-expanded", state.taskTagPanelOpen ? "true" : "false");
}

function renderTaskTagMenu() {
  const target = $("#taskTagList");
  if (!target) return;
  target.innerHTML = TASK_TAGS.map((tag) => {
    const active = state.taskTags.includes(tag.id) ? " active" : "";
    return `<button class="task-tag${active}" type="button" data-task-tag="${escapeHtml(tag.id)}">
      <svg viewBox="0 0 24 24" aria-hidden="true">${tag.icon}</svg>
      <span>
        <strong>${escapeHtml(tag.label)}</strong>
        <span>${escapeHtml(tag.hint)}</span>
      </span>
    </button>`;
  }).join("");
  setTaskTagPanel(state.taskTagPanelOpen);
}

function renderComposerMeta() {
  const target = $("#composerMeta");
  if (!target) return;
  const tags = state.taskTags
    .map((id) => taskTagById(id))
    .filter(Boolean)
    .map((tag) => `<span class="composer-chip tag" data-chip-tag="${escapeHtml(tag.id)}"><span>${escapeHtml(tag.label)}</span><button type="button" title="Убрать тег" aria-label="Убрать тег" data-remove-tag="${escapeHtml(tag.id)}">×</button></span>`);
  const files = state.attachments
    .map((file, index) => `<span class="composer-chip file" data-chip-file="${index}"><span>${escapeHtml(file.name || basename(file.path))}</span><button type="button" title="Убрать файл" aria-label="Убрать файл" data-remove-file="${index}">×</button></span>`);
  target.innerHTML = [...tags, ...files].join("");
  target.classList.toggle("hidden", !state.taskTags.length && !state.attachments.length);
  renderTaskTagMenu();
}

function clearComposerContext() {
  state.attachments = [];
  state.taskTags = [];
  renderComposerMeta();
}

function toggleTaskTag(id) {
  if (!taskTagById(id)) return;
  state.taskTags = state.taskTags.includes(id)
    ? state.taskTags.filter((item) => item !== id)
    : [...state.taskTags, id];
  renderComposerMeta();
}

async function attachFileFromFolder() {
  $("#chatMeta").textContent = "выбор файла";
  try {
    const data = await api("/api/files/pick", {
      method: "POST",
      body: JSON.stringify({ initial_path: state.config?.workspace || "" }),
    });
    if (!data?.file?.path) {
      $("#chatMeta").textContent = "файл не выбран";
      return;
    }
    const key = pathKey(data.file.path);
    if (!state.attachments.some((item) => pathKey(item.path) === key)) {
      state.attachments = [...state.attachments, data.file];
    }
    renderComposerMeta();
    $("#chatMeta").textContent = "файл добавлен";
  } catch (error) {
    $("#chatMeta").textContent = "ошибка выбора файла";
    pushAssistant(`Ошибка выбора файла: ${error.message}`);
  }
}

async function api(path, options = {}) {
  const headers = {
    "Content-Type": "application/json",
    "X-Serplex-User": state.userId,
    ...(options.headers || {}),
  };
  const response = await fetch(path, {
    ...options,
    headers,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.error || `HTTP ${response.status}`);
    error.status = response.status;
    error.payload = data;
    throw error;
  }
  return data;
}

function basename(path) {
  if (!path) return "Проект";
  const parts = String(path).replaceAll("\\", "/").split("/").filter(Boolean);
  return parts.at(-1) || path;
}

function pathKey(path) {
  return String(path || "").replaceAll("\\", "/").toLowerCase();
}

function shortPath(path, max = 68) {
  if (!path) return "";
  return path.length > max ? `...${path.slice(-(max - 3))}` : path;
}

function formatChatDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function formatMessageCount(count) {
  const value = Number(count || 0);
  const mod10 = value % 10;
  const mod100 = value % 100;
  if (mod10 === 1 && mod100 !== 11) return `${value} сообщение`;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return `${value} сообщения`;
  return `${value} сообщений`;
}

function inlineMarkdown(value) {
  let html = escapeHtml(value);
  const codeParts = [];
  html = html.replace(/`([^`]+)`/g, (_, code) => {
    const index = codeParts.push(`<code>${code}</code>`) - 1;
    return `\u0000CODE${index}\u0000`;
  });
  html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
  html = html.replace(/\*\*([^*\n][\s\S]*?[^*\n])\*\*/g, "<strong>$1</strong>");
  html = html.replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  html = html.replace(/\u0000CODE(\d+)\u0000/g, (_, index) => codeParts[Number(index)] || "");
  return html;
}

function renderTextMarkdown(text) {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  const html = [];
  let paragraph = [];
  let listType = null;
  let listItems = [];

  const flushParagraph = () => {
    if (!paragraph.length) return;
    html.push(`<p>${inlineMarkdown(paragraph.join(" "))}</p>`);
    paragraph = [];
  };
  const flushList = () => {
    if (!listType) return;
    html.push(`<${listType}>${listItems.map((item) => `<li>${inlineMarkdown(item)}</li>`).join("")}</${listType}>`);
    listType = null;
    listItems = [];
  };

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    const trimmed = line.trim();
    if (!trimmed) {
      flushParagraph();
      flushList();
      continue;
    }
    const heading = trimmed.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      flushList();
      const level = Math.min(heading[1].length + 1, 5);
      html.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }
    const bullet = trimmed.match(/^[-*]\s+(.+)$/);
    if (bullet) {
      flushParagraph();
      if (listType && listType !== "ul") flushList();
      listType = "ul";
      listItems.push(bullet[1]);
      continue;
    }
    const numbered = trimmed.match(/^\d+[.)]\s+(.+)$/);
    if (numbered) {
      flushParagraph();
      if (listType && listType !== "ol") flushList();
      listType = "ol";
      listItems.push(numbered[1]);
      continue;
    }
    const quote = trimmed.match(/^>\s+(.+)$/);
    if (quote) {
      flushParagraph();
      flushList();
      html.push(`<blockquote>${inlineMarkdown(quote[1])}</blockquote>`);
      continue;
    }
    flushList();
    paragraph.push(trimmed);
  }
  flushParagraph();
  flushList();
  return html.join("");
}

function renderMarkdown(text) {
  const source = String(text || "");
  const blocks = [];
  let cursor = 0;
  const fence = /```([a-zA-Z0-9_.-]*)[ \t]*\n([\s\S]*?)(```|$)/g;
  let match;
  while ((match = fence.exec(source))) {
    if (match.index > cursor) blocks.push(renderTextMarkdown(source.slice(cursor, match.index)));
    const lang = match[1] ? `<span>${escapeHtml(match[1])}</span>` : "";
    blocks.push(`<pre><div class="code-head">${lang}</div><code>${escapeHtml(match[2])}</code></pre>`);
    cursor = fence.lastIndex;
    if (!match[3]) break;
  }
  if (cursor < source.length) blocks.push(renderTextMarkdown(source.slice(cursor)));
  return blocks.join("") || "";
}

function currentProjectName() {
  return state.config?.workspace ? (state.config?.project_name || basename(state.config?.workspace)) : "Без проекта";
}

function updateProjectChrome() {
  const name = currentProjectName();
  const path = state.config?.workspace || "";
  $("#topProjectName").textContent = name;
  $("#workspaceLabel").textContent = path ? shortPath(path, 84) : "без проекта";
  $("#heroTitle").textContent = path ? `Что будем делать в ${name}?` : "Что спросим без проекта?";
  $("#projectPathInput").value = path;
}

function updateHero() {
  const hasMessages = Boolean(state.currentChat?.messages?.length);
  $("#emptyHero").classList.toggle("hidden", hasMessages || state.activeAssistant);
}

function conversationBox() {
  return document.querySelector(".conversation");
}

function scrollConversationToBottom(behavior = "auto") {
  const box = conversationBox();
  if (!box) return;
  requestAnimationFrame(() => {
    box.scrollTo({ top: box.scrollHeight, behavior });
  });
}

function currentProjectPath() {
  return state.config?.workspace || "";
}

function streamForCurrentChat() {
  const projectPath = currentProjectPath();
  if (state.currentStreamKey && state.activeStreams.has(state.currentStreamKey)) {
    const stream = state.activeStreams.get(state.currentStreamKey);
    if (pathKey(stream.projectPath) === pathKey(projectPath)) return stream;
  }
  if (!state.sessionId) return null;
  for (const stream of state.activeStreams.values()) {
    if (stream.sessionId === state.sessionId && pathKey(stream.projectPath) === pathKey(projectPath)) return stream;
  }
  return null;
}

function isStreamVisible(stream) {
  if (!stream) return false;
  if (pathKey(stream.projectPath) !== pathKey(currentProjectPath())) return false;
  if (stream.key === state.currentStreamKey) return true;
  return Boolean(stream.sessionId && stream.sessionId === state.sessionId);
}

function syncCurrentStreamKey() {
  const stream = streamForCurrentChat();
  state.currentStreamKey = stream?.key || null;
  state.busy = Boolean(stream);
  updateGenerationControls();
}

function updateGenerationControls() {
  const busy = Boolean(streamForCurrentChat());
  state.busy = busy;
  $("#sendPrompt").disabled = busy;
  $("#cancelGeneration").classList.toggle("hidden", !busy);
}

function ensureUpdateButton() {
  if ($("#updateButton")) return;
  const select = $("#modelSelect");
  if (!select) return;
  const button = document.createElement("button");
  button.id = "updateButton";
  button.className = "update-button";
  button.type = "button";
  button.title = "Проверить обновление";
  button.setAttribute("aria-label", "Проверить обновление");
  button.innerHTML = '<svg class="update-check-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12a9 9 0 1 1-2.6-6.4" /><path d="M21 3v6h-6" /></svg><svg class="update-download-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12" /><path d="m7 10 5 5 5-5" /><path d="M5 21h14" /></svg>';
  select.parentElement?.insertBefore(button, select);
}

function renderUpdateButton() {
  const button = $("#updateButton");
  if (!button) return;
  const status = state.updateStatus || {};
  const available = Boolean(status.update_available && (status.download_url || status.installer_url));
  button.classList.toggle("available", available);
  button.classList.toggle("checking", state.checkingUpdate || state.installingUpdate);
  button.disabled = state.checkingUpdate || state.installingUpdate;
  if (state.installingUpdate) {
    button.title = "Устанавливаю обновление";
    button.setAttribute("aria-label", button.title);
  } else if (available) {
    button.title = `Установить обновление ${status.latest_version || ""}`.trim();
    button.setAttribute("aria-label", button.title);
  } else if (status.error) {
    button.title = "Не удалось проверить обновление";
    button.setAttribute("aria-label", button.title);
  } else {
    const version = status.current_version || state.config?.app_version || "";
    button.title = version ? `Проверить обновление, текущая версия ${version}` : "Проверить обновление";
    button.setAttribute("aria-label", button.title);
  }
}

async function installUpdate() {
  if (state.installingUpdate) return;
  state.installingUpdate = true;
  $("#chatMeta").textContent = "устанавливаю обновление";
  renderUpdateButton();
  try {
    const result = await api("/api/update/install", {
      method: "POST",
      body: JSON.stringify({}),
    });
    $("#chatMeta").textContent = "перезапускаюсь";
    pushAssistant(result.message || "Обновление устанавливается. Приложение перезапустится автоматически.");
  } catch (error) {
    state.installingUpdate = false;
    $("#chatMeta").textContent = "ошибка обновления";
    pushAssistant(`Ошибка установки обновления: ${error.message}`);
    renderUpdateButton();
  }
}

async function checkUpdate(silent = false) {
  ensureUpdateButton();
  state.checkingUpdate = true;
  renderUpdateButton();
  try {
    const status = await api("/api/update");
    state.updateStatus = status;
    if (!silent && !status.update_available) {
      $("#chatMeta").textContent = status.ok ? "обновлений нет" : "ошибка обновления";
    }
  } catch (error) {
    state.updateStatus = { ok: false, update_available: false, error: error.message };
    if (!silent) pushAssistant(`Ошибка проверки обновления: ${error.message}`);
  } finally {
    state.checkingUpdate = false;
    renderUpdateButton();
  }
}

function apiKeyRequiredButMissing() {
  return Boolean(state.config?.requires_model_api_key && (!state.config?.model_api_key_configured || state.authInvalid));
}

function isInvalidApiKeyError(error) {
  const message = String(error?.message || "");
  return error?.status === 401 || /невали.*api-?ключ|invalid.*api.*key|missing or invalid|401/i.test(message);
}

function markInvalidApiKey() {
  state.authInvalid = true;
  if (state.config) state.config.model_api_key_configured = false;
  if (state.settings) state.settings.model_api_key_configured = false;
  $("#chatMeta").textContent = "невалидный API-ключ";
  openSettingsModal().catch(() => {});
}

function renderSettings(settings = state.settings || {}) {
  $("#settingsApiKey").value = "";
  $("#settingsClearKey").checked = false;
  $("#settingsAppVersion").textContent = state.config?.app_version || "0.0.0";
  $("#settingsProjectName").textContent = currentProjectName();
  $("#settingsModelName").textContent = state.selectedModel || state.config?.default_model || "-";
  $("#settingsVisionModel").textContent = state.config?.vision_model || "-";
  $("#settingsAccessMode").textContent = state.fullAccess ? "Full Access" : "Sandbox";
  const updateStatus = state.updateStatus;
  $("#settingsUpdateStatus").textContent = updateStatus?.update_available
    ? `Доступна версия ${updateStatus.latest_version || ""}`.trim()
    : updateStatus?.ok
      ? "Обновлений нет"
      : updateStatus?.error
        ? "Ошибка проверки"
        : "Не проверено";
  const keyStatus = $("#settingsKeyStatus");
  const hasKey = Boolean(settings.model_api_key_configured ?? state.config?.model_api_key_configured);
  const requiresKey = Boolean(settings.requires_model_api_key ?? state.config?.requires_model_api_key);
  keyStatus.textContent = state.authInvalid ? "Невалидный API-ключ" : hasKey ? "API-ключ сохранён" : "API-ключ не сохранён";
  keyStatus.classList.toggle("ready", hasKey && !state.authInvalid);
  keyStatus.classList.toggle("warn", state.authInvalid || (!hasKey && requiresKey));
}

async function openSettingsModal() {
  $("#settingsModal").classList.remove("hidden");
  selectSettingsSection(state.authInvalid || apiKeyRequiredButMissing() ? "authorization" : "general");
  renderSettings();
  try {
    state.settings = await api("/api/settings");
    renderSettings(state.settings);
  } catch (error) {
    $("#settingsKeyStatus").textContent = `Ошибка: ${error.message}`;
    $("#settingsKeyStatus").classList.add("warn");
  }
  window.setTimeout(() => $("#settingsApiKey").focus(), 0);
}

function selectSettingsSection(name) {
  const target = name || "general";
  document.querySelectorAll(".settings-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.settingsSection === target);
  });
  document.querySelectorAll(".settings-page").forEach((page) => {
    page.classList.toggle("active", page.dataset.settingsPage === target);
  });
  if (target === "authorization") window.setTimeout(() => $("#settingsApiKey").focus(), 0);
}

function closeSettingsModal() {
  $("#settingsModal").classList.add("hidden");
}

async function saveSettings(event) {
  event.preventDefault();
  const apiKey = $("#settingsApiKey").value.trim();
  const clearKey = $("#settingsClearKey").checked;
  if (!apiKey && !clearKey) {
    closeSettingsModal();
    return;
  }
  const settings = await api("/api/settings", {
    method: "POST",
    body: JSON.stringify({
      model_api_key: apiKey,
      clear_model_api_key: clearKey,
    }),
  });
  state.settings = settings;
  state.authInvalid = false;
  state.config = {
    ...(state.config || {}),
    ollama_base_url: settings.endpoint,
    model_api_key_configured: Boolean(settings.model_api_key_configured),
    requires_model_api_key: Boolean(settings.requires_model_api_key),
  };
  renderSettings(settings);
  closeSettingsModal();
  $("#chatMeta").textContent = settings.model_api_key_configured ? "ключ сохранён" : "ключ удалён";
  await loadModels();
}

function normalizeProjects(projects) {
  const currentPath = state.config?.workspace || "";
  const source = Array.isArray(projects) ? projects : [];
  if (source.length) return source;
  if (!currentPath) {
    return [
      {
        name: "Без проекта",
        path: "",
        exists: true,
        current: true,
        no_project: true,
        chats: state.chats,
      },
    ];
  }
  return [
    {
      name: currentProjectName(),
      path: currentPath,
      exists: true,
      current: true,
      chats: state.chats,
    },
  ];
}

function syncCurrentProjectChats(chats) {
  const currentPath = state.config?.workspace || "";
  const key = pathKey(currentPath);
  let found = false;
  state.projects = normalizeProjects(state.projects).map((project) => {
    if (pathKey(project.path) !== key) return { ...project, current: false };
    found = true;
    return { ...project, current: true, chats };
  });
  if (!found && currentPath) {
    state.projects.push({
      name: currentProjectName(),
      path: currentPath,
      exists: true,
      current: true,
      chats,
    });
  } else if (!found && !currentPath) {
    state.projects.unshift({
      name: "Без проекта",
      path: "",
      exists: true,
      current: true,
      no_project: true,
      chats,
    });
  }
}

async function loadConfig() {
  state.config = await api("/api/config");
  state.projects = normalizeProjects(state.config.projects || state.config.recent_projects || []);
  state.selectedModel = state.config.default_model;
  state.fullAccess = Boolean(state.config.full_access);
  updateProjectChrome();
  updateAccessToggle();
  renderProjectList();
  updateHero();
  await loadModels();
  checkUpdate(true).catch(() => {});
  if (apiKeyRequiredButMissing()) {
    $("#chatMeta").textContent = "невалидный API-ключ";
    openSettingsModal().catch(() => {});
  }
}

async function loadModels() {
  const select = $("#modelSelect");
  const fallback = state.config.default_model;
  try {
    const data = await api("/api/models");
    const names = (data.models || []).map((model) => model.name).filter(Boolean);
    if (!names.includes(fallback)) names.unshift(fallback);
    select.innerHTML = names.map((name) => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join("");
    select.value = fallback;
    state.selectedModel = select.value;
  } catch (_) {
    select.innerHTML = `<option value="${escapeHtml(fallback)}">${escapeHtml(fallback)}</option>`;
    state.selectedModel = fallback;
  }
}

function updateAccessToggle() {
  const button = $("#fullAccessToggle");
  button.classList.toggle("active", state.fullAccess);
  button.setAttribute("aria-pressed", state.fullAccess ? "true" : "false");
}

async function toggleFullAccess() {
  const next = !state.fullAccess;
  const data = await api("/api/access", {
    method: "POST",
    body: JSON.stringify({ full_access: next }),
  });
  state.fullAccess = Boolean(data.full_access);
  updateAccessToggle();
}

function startDraftChat() {
  state.sessionId = null;
  state.currentChat = { id: null, title: "Новый чат", messages: [] };
  state.editingIndex = null;
  state.activeAssistant = null;
  state.currentStreamKey = null;
  clearComposerContext();
  setComposerMenu(false);
  $("#messages").innerHTML = "";
  $("#chatMeta").textContent = "новый чат";
  renderProjectList();
  renderTools([]);
  updateHero();
  updateGenerationControls();
  $("#prompt").focus();
}

function renderProjectList() {
  const list = $("#projectList");
  const query = $("#chatSearch").value.trim().toLowerCase();
  const currentPath = pathKey(state.config?.workspace);
  const projects = normalizeProjects(state.projects);
  const visible = projects
    .map((project) => {
      const projectText = `${project.name || ""} ${project.path || ""}`.toLowerCase();
      const chats = Array.isArray(project.chats) ? project.chats : [];
      const filteredChats = query ? chats.filter((chat) => (chat.title || "").toLowerCase().includes(query)) : chats;
      const projectMatches = !query || projectText.includes(query);
      if (!projectMatches && !filteredChats.length) return null;
      return { ...project, chats: projectMatches ? chats : filteredChats };
    })
    .filter(Boolean);

  if (!visible.length) {
    list.innerHTML = '<div class="empty-side">Ничего не найдено</div>';
    return;
  }

  list.innerHTML = visible
    .map((project) => {
      const activeProject = pathKey(project.path) === currentPath;
      const chats = project.chats || [];
      const chatRows = chats.length
        ? chats
            .map((chat) => {
              const activeChat = activeProject && chat.id === state.sessionId ? " active" : "";
              const date = formatChatDate(chat.updated_at);
              return `<div class="project-chat-row${activeChat}" data-project-path="${escapeHtml(project.path)}" data-chat-id="${escapeHtml(chat.id)}">
                <div class="project-chat-header">
                  <button class="project-chat" type="button">
                    <span class="chat-title">${escapeHtml(chat.title || "Новый чат")}</span>
                    <span class="chat-time">${escapeHtml(date)}</span>
                  </button>
                  <button class="chat-delete" type="button" title="Удалить чат" aria-label="Удалить чат">
                    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12" /></svg>
                  </button>
                </div>
              </div>`;
            })
            .join("")
        : activeProject
          ? '<div class="empty-side nested">Чатов пока нет</div>'
          : "";
      const missing = project.exists === false ? '<span class="project-badge">нет папки</span>' : "";
      const deleteProjectButton = project.no_project
        ? ""
        : `<button class="project-delete" type="button" data-project-path="${escapeHtml(project.path)}" title="Убрать проект из списка" aria-label="Убрать проект из списка">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12" /></svg>
          </button>`;
      return `<section class="project-group${activeProject ? " active" : ""}">
        <div class="project-head-row">
          <button class="project-group-header" data-project-path="${escapeHtml(project.path)}">
            <span class="project-icon">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v8a3 3 0 0 1-3 3H6a3 3 0 0 1-3-3Z" /></svg>
            </span>
            <span class="project-copy">
              <span class="project-name">${escapeHtml(project.name || basename(project.path))}</span>
              <span class="project-path">${escapeHtml(shortPath(project.path, 42))}</span>
            </span>
            ${missing}
          </button>
          ${deleteProjectButton}
        </div>
        <div class="project-chat-list">${chatRows}</div>
      </section>`;
    })
    .join("");
}

async function loadChats(selectFirst = false) {
  const data = await api("/api/chats");
  state.chats = data.chats || [];
  syncCurrentProjectChats(state.chats);
  renderProjectList();
  if (selectFirst && state.chats.length) {
    await loadChat(state.chats[0].id);
  } else if (selectFirst) {
    startDraftChat();
  }
}

async function loadChat(chatId) {
  const chat = await api(`/api/chats/${encodeURIComponent(chatId)}`);
  state.sessionId = chat.id;
  state.currentChat = chat;
  state.editingIndex = null;
  state.activeAssistant = null;
  state.currentStreamKey = null;
  $("#chatMeta").textContent = formatMessageCount(chat.messages?.length || 0);
  renderMessages(chat.messages || []);
  syncCurrentStreamKey();
  renderProjectList();
  renderTools(lastSteps(chat.messages || []));
  updateHero();
}

function lastSteps(messages) {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    if (messages[i].role === "assistant" && messages[i].steps?.length) return messages[i].steps;
  }
  return [];
}

function renderMessages(messages) {
  const box = $("#messages");
  box.innerHTML = "";
  messages.forEach((message, index) => {
    box.appendChild(createMessageNode(message.role, message.content || "", index, message));
  });
  if (state.editingIndex !== null) {
    const editor = box.querySelector(".edit-textarea");
    if (editor) {
      editor.focus();
      editor.selectionStart = editor.value.length;
      editor.selectionEnd = editor.value.length;
    }
  }
  scrollConversationToBottom();
}

function createMessageNode(role, content = "", index = null, message = null) {
  if (role === "user" && index !== null && index === state.editingIndex) {
    return createEditMessageNode(index, content);
  }
  const item = document.createElement("article");
  item.className = `message ${role}`;
  if (index !== null) item.dataset.messageIndex = String(index);
  if (role === "user" && index !== null) {
    const edit = document.createElement("button");
    edit.className = "message-edit";
    edit.type = "button";
    edit.dataset.editIndex = String(index);
    edit.title = "Редактировать вопрос";
    edit.setAttribute("aria-label", "Редактировать вопрос");
    edit.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" /></svg>';
    item.appendChild(edit);
  }
  if (role === "assistant") {
    const panel = createThinkingPanel(message, false);
    if (panel) item.appendChild(panel);
  }
  if (role === "user" && message && (message.task_tags?.length || message.attachments?.length)) {
    const meta = document.createElement("div");
    meta.className = "message-meta";
    const tags = (message.task_tags || [])
      .map((id) => taskTagById(id))
      .filter(Boolean)
      .map((tag) => `<span>${escapeHtml(tag.label)}</span>`);
    const files = (message.attachments || [])
      .map((file) => `<span>${escapeHtml(file.name || basename(file.path))}</span>`);
    meta.innerHTML = [...tags, ...files].join("");
    item.appendChild(meta);
  }
  const body = document.createElement("div");
  body.className = "message-body";
  if (role === "assistant") {
    body.innerHTML = renderMarkdown(content);
  } else {
    body.textContent = content;
  }
  item.appendChild(body);
  return item;
}

function createThinkingPanel(message, open = false) {
  const thoughts = Array.isArray(message?.thoughts) ? message.thoughts : [];
  const steps = Array.isArray(message?.steps) ? message.steps : [];
  if (!thoughts.length && !steps.length) return null;
  const details = document.createElement("details");
  details.className = "thinking-panel";
  details.open = open;
  const body = [
    ...thoughts.map((thought) => `<div class="thinking-entry ${classToken(thought.kind || "draft")}">${renderMarkdown(thought.content || thought.text || "")}</div>`),
    ...steps.map((step) => `<div class="thinking-entry tool ${step.ok ? "ok" : "fail"}">${inlineMarkdown(`${step.ok ? "Готово" : "Ошибка"}: \`${step.tool}\`${step.summary ? ` - ${step.summary}` : ""}`)}</div>`),
  ].join("");
  details.innerHTML = `<summary><span>Ход работы</span><span class="thinking-state">готово</span></summary><div class="thinking-body">${body}</div>`;
  return details;
}

function createEditMessageNode(index, content = "") {
  const item = document.createElement("article");
  item.className = "message user editing";
  item.dataset.messageIndex = String(index);

  const form = document.createElement("form");
  form.className = "edit-form";
  form.dataset.editIndex = String(index);

  const textarea = document.createElement("textarea");
  textarea.className = "edit-textarea";
  textarea.rows = Math.max(2, Math.min(10, String(content).split("\n").length + 1));
  textarea.value = content;

  const actions = document.createElement("div");
  actions.className = "edit-actions";

  const cancel = document.createElement("button");
  cancel.className = "edit-cancel";
  cancel.type = "button";
  cancel.dataset.editCancel = "true";
  cancel.textContent = "Отмена";

  const save = document.createElement("button");
  save.className = "edit-save";
  save.type = "submit";
  save.textContent = "Отправить";

  actions.append(cancel, save);
  form.append(textarea, actions);
  item.appendChild(form);
  return item;
}

function showThinking() {
  const node = createMessageNode("assistant", "");
  const thoughts = document.createElement("details");
  thoughts.className = "thinking-panel";
  thoughts.open = true;
  thoughts.innerHTML = '<summary><span>Ход работы</span><span class="thinking-state">думаю</span></summary><div class="thinking-body"></div>';
  node.insertBefore(thoughts, node.firstChild);
  const body = node.querySelector(".message-body");
  body.innerHTML = '<span class="typing-dots"><span></span><span></span><span></span></span>';
  $("#messages").appendChild(node);
  scrollConversationToBottom();
  state.activeAssistant = {
    node,
    body,
    content: "",
    waiting: true,
    thoughts,
    thoughtBody: thoughts.querySelector(".thinking-body"),
    thoughtState: thoughts.querySelector(".thinking-state"),
    thoughtEntries: [],
  };
  updateHero();
}

function addThinkingEntry(text, kind = "note") {
  if (!state.activeAssistant) showThinking();
  const active = state.activeAssistant;
  const clean = String(text || "").trim();
  if (!clean) return;
  active.thoughtEntries.push({ text: clean, kind });
  active.thoughtBody.innerHTML = active.thoughtEntries
    .map((entry) => `<div class="thinking-entry ${classToken(entry.kind)}">${renderMarkdown(entry.text)}</div>`)
    .join("");
  active.thoughts.classList.remove("empty");
  active.thoughts.open = true;
  scrollConversationToBottom();
}

function moveDraftToThinking(kind = "draft") {
  const active = state.activeAssistant;
  if (!active || !active.content.trim()) return;
  addThinkingEntry(active.content, kind);
  active.content = "";
  active.waiting = true;
  active.body.innerHTML = '<span class="typing-dots"><span></span><span></span><span></span></span>';
}

function appendAssistantToken(token) {
  if (!state.activeAssistant) showThinking();
  const active = state.activeAssistant;
  if (active.waiting) {
    active.body.textContent = "";
    active.waiting = false;
  }
  active.content += token;
  active.body.innerHTML = renderMarkdown(active.content);
  scrollConversationToBottom();
}

function replaceAssistant(content) {
  if (!state.activeAssistant) showThinking();
  if (!content && state.activeAssistant.content.trim()) {
    moveDraftToThinking("draft");
    return;
  }
  state.activeAssistant.content = content || "";
  state.activeAssistant.waiting = false;
  state.activeAssistant.body.innerHTML = renderMarkdown(state.activeAssistant.content);
}

function renderTools(steps) {
  return steps;
}

function parseSseBlock(block) {
  const lines = block.split("\n");
  let event = "message";
  const data = [];
  for (const line of lines) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    if (line.startsWith("data:")) data.push(line.slice(5).trim());
  }
  if (!data.length) return null;
  return { event, data: JSON.parse(data.join("\n")) };
}

async function handleStream(response, stream) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const steps = [];
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const part of parts) {
      if (!part.trim()) continue;
      const message = parseSseBlock(part);
      if (!message) continue;
      const { event, data } = message;
      if (event === "chat") {
        stream.sessionId = data.chat?.id || stream.sessionId;
        if (isStreamVisible(stream)) {
          state.sessionId = stream.sessionId || state.sessionId;
          state.currentStreamKey = stream.key;
        }
        renderProjectList();
      } else if (event === "token") {
        if (isStreamVisible(stream)) appendAssistantToken(data.content || "");
      } else if (event === "replace") {
        if (isStreamVisible(stream)) replaceAssistant(data.content || "");
      } else if (event === "tool_delta") {
        if (isStreamVisible(stream)) moveDraftToThinking("draft");
      } else if (event === "tool_start") {
        if (isStreamVisible(stream)) {
          moveDraftToThinking("draft");
          addThinkingEntry(`Запускаю \`${data.tool}\``, "tool");
          $("#chatMeta").textContent = `${data.tool}...`;
        }
      } else if (event === "tool_end") {
        steps.push(data);
        if (isStreamVisible(stream)) {
          addThinkingEntry(data.summary || `${data.tool} завершен`, data.ok ? "tool ok" : "tool fail");
          renderTools(steps);
        }
      } else if (event === "done") {
        stream.done = true;
        stream.chat = data.chat;
        if (isStreamVisible(stream) && state.activeAssistant?.thoughtEntries?.length) {
          state.activeAssistant.thoughts.open = false;
          if (state.activeAssistant.thoughtState) state.activeAssistant.thoughtState.textContent = "готово";
        }
        if (isStreamVisible(stream)) {
          state.sessionId = data.session_id;
          state.currentChat = data.chat;
          state.editingIndex = null;
          state.activeAssistant = null;
          renderTools(data.steps || steps);
          renderMessages(data.chat?.messages || []);
          $("#chatMeta").textContent = formatMessageCount(data.chat?.messages?.length || 0);
        }
        try {
          await reader.cancel();
        } catch (_) {
          // The server may have already closed the stream.
        }
        return;
      } else if (event === "error") {
        const error = new Error(data.error || "Stream error");
        error.status = data.status;
        throw error;
      }
    }
  }
}

async function runChatStream(payload, stream) {
  const response = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Serplex-User": state.userId },
    body: JSON.stringify(payload),
    signal: stream.controller.signal,
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const error = new Error(data.error || `HTTP ${response.status}`);
    error.status = response.status;
    error.payload = data;
    throw error;
  }
  await handleStream(response, stream);
  await loadChats(false);
}

async function sendPrompt(event) {
  event.preventDefault();
  const prompt = $("#prompt").value.trim();
  if (!prompt || streamForCurrentChat()) return;
  if (apiKeyRequiredButMissing()) {
    pushAssistant(INVALID_API_KEY_MESSAGE);
    markInvalidApiKey();
    return;
  }

  const taskTags = [...state.taskTags];
  const attachments = state.attachments.map((item) => ({ ...item }));
  const stream = {
    key: `stream-${Date.now()}-${++state.streamSeq}`,
    sessionId: state.sessionId,
    projectPath: currentProjectPath(),
    controller: new AbortController(),
    cancelled: false,
  };
  state.activeStreams.set(stream.key, stream);
  state.currentStreamKey = stream.key;
  state.busy = true;
  state.editingIndex = null;
  updateGenerationControls();
  $("#prompt").value = "";
  renderTools([]);
  pushUser(prompt, { task_tags: taskTags, attachments });
  clearComposerContext();
  setComposerMenu(false);
  showThinking();

  try {
    await runChatStream({
      message: prompt,
      session_id: state.sessionId,
      project_path: stream.projectPath,
      model: state.selectedModel,
      task_tags: taskTags,
      attachments,
    }, stream);
  } catch (error) {
    if (stream.cancelled || error.name === "AbortError") {
      if (isStreamVisible(stream)) replaceAssistant("Генерация остановлена.");
    } else if (isInvalidApiKeyError(error)) {
      if (state.activeAssistant && isStreamVisible(stream)) replaceAssistant(INVALID_API_KEY_MESSAGE);
      else pushAssistant(INVALID_API_KEY_MESSAGE);
      markInvalidApiKey();
    } else if (state.activeAssistant && isStreamVisible(stream)) replaceAssistant(`Ошибка: ${error.message}`);
    else pushAssistant(`Ошибка: ${error.message}`);
    if (isStreamVisible(stream)) $("#chatMeta").textContent = stream.cancelled ? "остановлено" : isInvalidApiKeyError(error) ? "невалидный API-ключ" : "ошибка";
  } finally {
    const wasVisible = isStreamVisible(stream);
    state.activeStreams.delete(stream.key);
    if (state.currentStreamKey === stream.key) state.currentStreamKey = null;
    if (wasVisible) state.activeAssistant = null;
    syncCurrentStreamKey();
    updateHero();
  }
}

function startEditMessage(index) {
  if (streamForCurrentChat() || !state.currentChat?.messages?.[index]) return;
  if (state.currentChat.messages[index].role !== "user") return;
  state.editingIndex = index;
  renderMessages(state.currentChat.messages || []);
}

function cancelEditMessage() {
  state.editingIndex = null;
  renderMessages(state.currentChat?.messages || []);
}

async function submitEditMessage(index, text) {
  const prompt = text.trim();
  if (!prompt || streamForCurrentChat() || !state.sessionId || !state.currentChat?.messages?.[index]) return;
  if (state.currentChat.messages[index].role !== "user") return;
  if (apiKeyRequiredButMissing()) {
    pushAssistant(INVALID_API_KEY_MESSAGE);
    markInvalidApiKey();
    return;
  }

  const stream = {
    key: `stream-${Date.now()}-${++state.streamSeq}`,
    sessionId: state.sessionId,
    projectPath: currentProjectPath(),
    controller: new AbortController(),
    cancelled: false,
  };
  state.activeStreams.set(stream.key, stream);
  state.currentStreamKey = stream.key;
  state.busy = true;
  state.editingIndex = null;
  updateGenerationControls();
  renderTools([]);

  const nextMessages = [
    ...state.currentChat.messages.slice(0, index),
    { role: "user", content: prompt, timestamp: new Date().toISOString(), edited: true },
  ];
  state.currentChat = { ...state.currentChat, messages: nextMessages };
  renderMessages(nextMessages);
  showThinking();

  try {
    await runChatStream({
      message: prompt,
      session_id: state.sessionId,
      project_path: stream.projectPath,
      model: state.selectedModel,
      edit_message_index: index,
    }, stream);
  } catch (error) {
    if (stream.cancelled || error.name === "AbortError") {
      if (isStreamVisible(stream)) replaceAssistant("Генерация остановлена.");
    } else if (isInvalidApiKeyError(error)) {
      if (state.activeAssistant && isStreamVisible(stream)) replaceAssistant(INVALID_API_KEY_MESSAGE);
      else pushAssistant(INVALID_API_KEY_MESSAGE);
      markInvalidApiKey();
    } else if (state.activeAssistant && isStreamVisible(stream)) replaceAssistant(`Ошибка: ${error.message}`);
    else pushAssistant(`Ошибка: ${error.message}`);
    if (isStreamVisible(stream)) $("#chatMeta").textContent = stream.cancelled ? "остановлено" : isInvalidApiKeyError(error) ? "невалидный API-ключ" : "ошибка";
  } finally {
    const wasVisible = isStreamVisible(stream);
    state.activeStreams.delete(stream.key);
    if (state.currentStreamKey === stream.key) state.currentStreamKey = null;
    if (wasVisible) state.activeAssistant = null;
    syncCurrentStreamKey();
    updateHero();
  }
}

function pushUser(text, meta = {}) {
  $("#messages").appendChild(createMessageNode("user", text, null, meta));
  scrollConversationToBottom();
  if (state.currentChat) {
    state.currentChat.messages = [...(state.currentChat.messages || []), { role: "user", content: text, ...meta }];
  }
  updateHero();
}

function pushAssistant(text) {
  $("#messages").appendChild(createMessageNode("assistant", text));
  scrollConversationToBottom();
}

function cancelGeneration() {
  const stream = streamForCurrentChat();
  if (!stream) return;
  stream.cancelled = true;
  stream.controller.abort();
  state.activeStreams.delete(stream.key);
  if (state.currentStreamKey === stream.key) state.currentStreamKey = null;
  if (state.activeAssistant) {
    replaceAssistant("Генерация остановлена.");
    state.activeAssistant = null;
  }
  $("#chatMeta").textContent = "остановлено";
  syncCurrentStreamKey();
  updateHero();
}

function confirmDialog({ title, body, confirmText = "Удалить", cancelText = "Отмена", danger = true }) {
  if (state.confirmResolve) state.confirmResolve(false);
  $("#confirmTitle").textContent = title;
  $("#confirmBody").textContent = body;
  $("#confirmAccept").textContent = confirmText;
  $("#confirmCancel").textContent = cancelText;
  $("#confirmAccept").classList.toggle("danger", danger);
  $("#confirmModal").classList.remove("hidden");
  window.setTimeout(() => $("#confirmCancel").focus(), 0);
  return new Promise((resolve) => {
    state.confirmResolve = resolve;
  });
}

function closeConfirmDialog(result = false) {
  $("#confirmModal").classList.add("hidden");
  const resolve = state.confirmResolve;
  state.confirmResolve = null;
  if (resolve) resolve(Boolean(result));
}

function chatTitleForId(chatId, projectPath) {
  const project = normalizeProjects(state.projects).find((item) => pathKey(item.path) === pathKey(projectPath));
  const chat = project?.chats?.find((item) => item.id === chatId);
  return chat?.title || "чат";
}

async function deleteChat(chatId, projectPath) {
  if (!chatId) return;
  const title = chatTitleForId(chatId, projectPath);
  const confirmed = await confirmDialog({
    title: `Удалить чат "${title}"?`,
    body: "История этого чата исчезнет из приложения. Папка проекта и файлы на диске не будут затронуты.",
    confirmText: "Удалить чат",
  });
  if (!confirmed) return;
  const wasCurrent = chatId === state.sessionId && pathKey(projectPath) === pathKey(state.config?.workspace);
  const stream = [...state.activeStreams.values()].find((item) => item.sessionId === chatId && pathKey(item.projectPath) === pathKey(projectPath));
  if (stream) {
    stream.cancelled = true;
    stream.controller.abort();
    state.activeStreams.delete(stream.key);
  }
  await api(`/api/chats/${encodeURIComponent(chatId)}?project_path=${encodeURIComponent(projectPath || "")}`, {
    method: "DELETE",
  });
  if (wasCurrent) {
    state.sessionId = null;
    state.currentChat = null;
    state.activeAssistant = null;
    $("#messages").innerHTML = "";
  }
  await loadProjectsAndChatsAfterDelete(wasCurrent);
}

async function loadProjectsAndChatsAfterDelete(selectFirst = false) {
  const data = await api("/api/projects");
  state.projects = normalizeProjects(data.projects || data.recent || []);
  if (pathKey(data.current?.path) === pathKey(state.config?.workspace)) {
    state.chats = (state.projects.find((project) => pathKey(project.path) === pathKey(state.config?.workspace))?.chats) || [];
  }
  renderProjectList();
  if (selectFirst) await loadChats(true);
  syncCurrentStreamKey();
}

function projectNameForPath(projectPath) {
  const project = normalizeProjects(state.projects).find((item) => pathKey(item.path) === pathKey(projectPath));
  return project?.name || basename(projectPath) || "проект";
}

async function deleteProject(projectPath) {
  if (!projectPath) return;
  const name = projectNameForPath(projectPath);
  const ok = await confirmDialog({
    title: `Убрать проект "${name}" из списка?`,
    body: "Файлы и папки на диске останутся. Проект просто исчезнет из списка приложения.",
    confirmText: "Убрать проект",
  });
  if (!ok) return;

  const removingCurrent = pathKey(projectPath) === pathKey(state.config?.workspace);
  if (removingCurrent) {
    for (const stream of state.activeStreams.values()) {
      if (pathKey(stream.projectPath) === pathKey(projectPath)) {
        stream.cancelled = true;
        stream.controller.abort();
        state.activeStreams.delete(stream.key);
      }
    }
    state.currentStreamKey = null;
    state.activeAssistant = null;
  }

  const data = await api(`/api/projects?path=${encodeURIComponent(projectPath)}`, {
    method: "DELETE",
  });

  if (data.removed_current) {
    await applyProjectPayload(data, { selectFirst: false, startDraft: true });
    return;
  }

  state.projects = normalizeProjects(data.projects || data.recent || []);
  renderProjectList();
  renderRecentProjects();
}

function openProjectModal() {
  $("#projectModal").classList.remove("hidden");
  $("#projectPathInput").value = state.config?.workspace || "";
  renderRecentProjects();
  window.setTimeout(() => $("#projectPathInput").focus(), 50);
}

function closeProjectModal() {
  $("#projectModal").classList.add("hidden");
}

function renderRecentProjects() {
  const target = $("#recentProjects");
  const projects = normalizeProjects(state.projects);
  if (!projects.length) {
    target.innerHTML = '<div class="modal-empty">Недавних проектов пока нет</div>';
    return;
  }
  target.innerHTML = projects
    .map((project) => {
      const missing = project.exists === false ? " missing" : "";
      const deleteButton = project.no_project
        ? ""
        : `<button class="recent-project-delete" type="button" data-path="${escapeHtml(project.path)}" title="Убрать проект из списка" aria-label="Убрать проект из списка">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12" /></svg>
        </button>`;
      return `<div class="recent-project-row">
        <button class="recent-project${missing}" data-path="${escapeHtml(project.path)}">
          <span class="recent-name">${escapeHtml(project.name || basename(project.path))}</span>
          <span class="recent-path">${escapeHtml(shortPath(project.path, 72))}</span>
        </button>
        ${deleteButton}
      </div>`;
    })
    .join("");
}

async function applyProjectPayload(data, options = {}) {
  if (data.cancelled) return;
  const project = data.project || {};
  state.config.project_name = project.name || "Без проекта";
  state.config.workspace = project.path || "";
  state.config.project = project;
  state.config.chat_store = project.chat_store || "";
  state.projects = normalizeProjects(data.projects || data.recent || []);
  state.chats = data.chats || [];
  state.sessionId = null;
  state.currentChat = null;
  state.editingIndex = null;
  state.activeAssistant = null;
  state.currentStreamKey = null;
  updateProjectChrome();
  renderProjectList();
  renderRecentProjects();
  closeProjectModal();
  if (options.selectChatId) {
    await loadChat(options.selectChatId);
  } else if (options.selectFirst !== false) {
    await loadChats(true);
  } else if (options.startDraft) {
    startDraftChat();
  }
  updateGenerationControls();
}

async function openProject(path, options = {}) {
  const data = await api("/api/projects/open", {
    method: "POST",
    body: JSON.stringify({ path }),
  });
  await applyProjectPayload(data, options);
}

async function pickNativeProject() {
  const buttons = [$("#openProjectButton"), $("#topOpenProject"), $("#nativeFolderPick")].filter(Boolean);
  buttons.forEach((button) => (button.disabled = true));
  try {
    const data = await api("/api/projects/pick", {
      method: "POST",
      body: JSON.stringify({ initial_path: state.config?.workspace || "" }),
    });
    await applyProjectPayload(data, { selectFirst: true });
  } catch (error) {
    pushAssistant(`Ошибка выбора папки: ${error.message}`);
    openProjectModal();
  } finally {
    buttons.forEach((button) => (button.disabled = false));
  }
}

async function selectProjectChat(projectPath, chatId) {
  if (pathKey(projectPath) !== pathKey(state.config?.workspace)) {
    await openProject(projectPath, { selectChatId: chatId, selectFirst: false });
    return;
  }
  await loadChat(chatId);
}

function bindEvents() {
  ensureUpdateButton();
  renderComposerMeta();
  $("#newChat").addEventListener("click", startDraftChat);
  $("#openProjectButton").addEventListener("click", pickNativeProject);
  $("#topOpenProject").addEventListener("click", pickNativeProject);
  $("#closeProjectModal").addEventListener("click", closeProjectModal);
  $("#nativeFolderPick").addEventListener("click", pickNativeProject);
  $("#projectModal").addEventListener("click", (event) => {
    if (event.target.id === "projectModal") closeProjectModal();
  });
  $("#confirmModal").addEventListener("click", (event) => {
    if (event.target.id === "confirmModal") closeConfirmDialog(false);
  });
  $("#confirmCancel").addEventListener("click", () => closeConfirmDialog(false));
  $("#confirmAccept").addEventListener("click", () => closeConfirmDialog(true));
  $("#settingsButton").addEventListener("click", () => openSettingsModal().catch((error) => pushAssistant(`Ошибка настроек: ${error.message}`)));
  $("#closeSettingsModal").addEventListener("click", closeSettingsModal);
  $("#settingsCancel").addEventListener("click", closeSettingsModal);
  document.querySelectorAll(".settings-tab").forEach((button) => {
    button.addEventListener("click", () => selectSettingsSection(button.dataset.settingsSection));
  });
  $("#settingsForm").addEventListener("submit", (event) => saveSettings(event).catch((error) => pushAssistant(`Ошибка сохранения настроек: ${error.message}`)));
  $("#settingsModal").addEventListener("click", (event) => {
    if (event.target.id === "settingsModal") closeSettingsModal();
  });
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.confirmResolve) closeConfirmDialog(false);
  });
  $("#projectPathForm").addEventListener("submit", (event) => {
    event.preventDefault();
    openProject($("#projectPathInput").value.trim()).catch((error) => pushAssistant(`Ошибка: ${error.message}`));
  });
  $("#recentProjects").addEventListener("click", (event) => {
    const deleteButton = event.target.closest(".recent-project-delete");
    if (deleteButton) {
      deleteProject(deleteButton.dataset.path).catch((error) => pushAssistant(`Ошибка удаления проекта: ${error.message}`));
      return;
    }
    const row = event.target.closest(".recent-project");
    if (!row) return;
    openProject(row.dataset.path).catch((error) => pushAssistant(`Ошибка: ${error.message}`));
  });
  $("#projectList").addEventListener("click", (event) => {
    const projectDelete = event.target.closest(".project-delete");
    if (projectDelete) {
      deleteProject(projectDelete.dataset.projectPath).catch((error) => pushAssistant(`Ошибка удаления проекта: ${error.message}`));
      return;
    }
    const deleteButton = event.target.closest(".chat-delete");
    if (deleteButton) {
      const row = deleteButton.closest(".project-chat-row");
      if (!row) return;
      deleteChat(row.dataset.chatId, row.dataset.projectPath).catch((error) => pushAssistant(`Ошибка удаления: ${error.message}`));
      return;
    }
    const chat = event.target.closest(".project-chat-row");
    if (chat) {
      selectProjectChat(chat.dataset.projectPath, chat.dataset.chatId).catch((error) => pushAssistant(`Ошибка: ${error.message}`));
      return;
    }
    const project = event.target.closest(".project-group-header");
    if (project && pathKey(project.dataset.projectPath) !== pathKey(state.config?.workspace)) {
      openProject(project.dataset.projectPath).catch((error) => pushAssistant(`Ошибка: ${error.message}`));
    }
  });
  $("#chatSearch").addEventListener("input", renderProjectList);
  $("#focusSearch").addEventListener("click", () => $("#chatSearch").focus());
  $("#modelSelect").addEventListener("change", () => {
    state.selectedModel = $("#modelSelect").value;
  });
  $("#updateButton").addEventListener("click", () => {
    if (state.updateStatus?.update_available && (state.updateStatus?.download_url || state.updateStatus?.installer_url)) {
      installUpdate();
      return;
    }
    checkUpdate(false).catch((error) => pushAssistant(`Ошибка проверки обновления: ${error.message}`));
  });
  $("#composerMenuButton").addEventListener("click", (event) => {
    event.stopPropagation();
    setComposerMenu(!state.composerMenuOpen);
  });
  $("#attachFileButton").addEventListener("click", () => {
    setComposerMenu(false);
    attachFileFromFolder();
  });
  $("#taskTagsButton").addEventListener("click", (event) => {
    event.stopPropagation();
    setTaskTagPanel(!state.taskTagPanelOpen);
  });
  $("#taskTagList").addEventListener("click", (event) => {
    const button = event.target.closest("[data-task-tag]");
    if (!button) return;
    toggleTaskTag(button.dataset.taskTag);
  });
  $("#composerMeta").addEventListener("click", (event) => {
    const tagButton = event.target.closest("[data-remove-tag]");
    if (tagButton) {
      state.taskTags = state.taskTags.filter((id) => id !== tagButton.dataset.removeTag);
      renderComposerMeta();
      return;
    }
    const fileButton = event.target.closest("[data-remove-file]");
    if (fileButton) {
      state.attachments = state.attachments.filter((_, index) => index !== Number(fileButton.dataset.removeFile));
      renderComposerMeta();
    }
  });
  document.addEventListener("click", (event) => {
    if (!state.composerMenuOpen) return;
    if (event.target.closest("#composerMenu") || event.target.closest("#composerMenuButton")) return;
    setComposerMenu(false);
  });
  $("#fullAccessToggle").addEventListener("click", () => toggleFullAccess().catch((error) => pushAssistant(`Ошибка: ${error.message}`)));
  $("#cancelGeneration").addEventListener("click", cancelGeneration);
  $("#chatForm").addEventListener("submit", sendPrompt);
  $("#prompt").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendPrompt(event);
    }
  });
  $("#messages").addEventListener("click", (event) => {
    const edit = event.target.closest(".message-edit");
    if (edit) {
      startEditMessage(Number(edit.dataset.editIndex));
      return;
    }
    if (event.target.closest("[data-edit-cancel]")) {
      cancelEditMessage();
    }
  });
  $("#messages").addEventListener("submit", (event) => {
    const form = event.target.closest(".edit-form");
    if (!form) return;
    event.preventDefault();
    const textarea = form.querySelector(".edit-textarea");
    submitEditMessage(Number(form.dataset.editIndex), textarea?.value || "");
  });
  $("#messages").addEventListener("keydown", (event) => {
    if (!event.target.closest(".edit-textarea")) return;
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      const form = event.target.closest(".edit-form");
      submitEditMessage(Number(form.dataset.editIndex), event.target.value || "");
    }
  });
  document.querySelectorAll(".starter").forEach((button) => {
    button.addEventListener("click", () => {
      $("#prompt").value = button.dataset.prompt || "";
      $("#prompt").focus();
    });
  });
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeProjectModal();
    if (event.key === "Escape") closeSettingsModal();
    if (event.key === "Escape") setComposerMenu(false);
  });
}

async function boot() {
  bindEvents();
  await loadConfig();
  await loadChats(true);
}

boot().catch((error) => pushAssistant(`Ошибка запуска: ${error.message}`));
