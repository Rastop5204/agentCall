const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const paths = {
  signal:
    '<rect x="3" y="8" width="18" height="13" rx="2"/><path d="m3 9 9 7 9-7M7 4h10M10 1h4"/>',
  mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 6 9 7 9-7"/>',
  inbox: '<path d="m4 4-2 12v4h20v-4L20 4H4Z"/><path d="M2 15h6l2 3h4l2-3h6"/>',
  sliders:
    '<path d="M4 7h7m4 0h5M4 17h3m4 0h9"/><circle cx="13" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
  "chevron-right": '<path d="m9 5 7 7-7 7"/>',
  "chevron-left": '<path d="m15 5-7 7 7 7"/>',
  "chevron-down": '<path d="m5 9 7 7 7-7"/>',
  spark:
    '<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3ZM20 2v4m-2-2h4"/>',
  lock: '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3m-4 5v2"/>',
  moon: '<path d="M20.5 14a8.5 8.5 0 0 1-10.5-10.5A9 9 0 1 0 20.5 14Z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>',
  shield:
    '<path d="m12 3 8 3v6c0 4-4 7-8 9-4-2-8-5-8-9V6l8-3Z"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
  cloud:
    '<path d="M7 18h10a4 4 0 0 0 .5-8 6 6 0 0 0-11.6-1.5A5 5 0 0 0 7 18Z"/>',
  message:
    '<path d="M21 11.5a8.4 8.4 0 0 1-.9 3.8A8.5 8.5 0 0 1 12.5 20a8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8v.5Z"/><path d="M8 11h8m-8 4h5"/>',
  server:
    '<rect x="3" y="3" width="18" height="7" rx="2"/><rect x="3" y="14" width="18" height="7" rx="2"/><path d="M7 6.5h.01M7 17.5h.01M11 6.5h6M11 17.5h6"/>',
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>',
  "eye-off":
    '<path d="m3 3 18 18M10.6 5.1 12 5c6.5 0 10 7 10 7a18.2 18.2 0 0 1-3.3 4.3M6.2 6.2A20.6 20.6 0 0 0 2 12s3.5 7 10 7a10 10 0 0 0 5.8-1.8M9.9 9.9a3 3 0 0 0 4.2 4.2"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5m0-9h.01"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  repeat:
    '<path d="m17 2 4 4-4 4M3 11V8a2 2 0 0 1 2-2h16M7 22l-4-4 4-4m14-1v3a2 2 0 0 1-2 2H3"/>',
  terminal:
    '<rect x="2" y="3" width="20" height="18" rx="3"/><path d="m6 8 4 4-4 4m7 0h5"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 15v5h16v-5"/>',
  "arrow-up-right": '<path d="M6 18 18 6M6 6h12v12"/>',
  activity: '<path d="M2 12h5l3-8 4 16 3-8h5"/>',
  plug: '<path d="M9 3v5m6-5v5M7 8h10v5a5 5 0 0 1-10 0V8Zm5 10v4M5 8h14"/>',
  send: '<path d="m22 2-7 20-4-9-9-4L22 2Zm0 0L11 13"/>',
  code: '<path d="m8 5-6 7 6 7m8-14 6 7-6 7m-3-17-2 20"/>',
  copy: '<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
  refresh:
    '<path d="M20 7a9 9 0 0 0-15-2L2 8m0-6v6h6m-4 9a9 9 0 0 0 15 2l3-3m0 6v-6h-6"/>',
  stack:
    '<path d="m12 2 10 5-10 5L2 7l10-5Zm-10 10 10 5 10-5M2 17l10 5 10-5"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  alert: '<path d="m12 3 10 18H2L12 3Zm0 6v5m0 3h.01"/>',
  search: '<circle cx="10.5" cy="10.5" r="7.5"/><path d="m16 16 6 6"/>',
  x: '<path d="m6 6 12 12M6 18 18 6"/>',
  key: '<circle cx="8" cy="8" r="5"/><path d="m11.5 11.5 9 9M16 16l3-3m0 6 3-3"/>',
  robot:
    '<rect x="4" y="7" width="16" height="14" rx="3"/><path d="M12 3v4m-2-4h4M8 12h.01M16 12h.01M9 17h6M1 12v5m22-5v5"/>',
  wechat:
    '<path d="M15.5 10.5c0-4-3-7-7-7s-7 2.7-7 6c0 2 1 3.6 3 4.8L4 17l3-1.5h1.5M22.5 15c0-3-3-5.5-6.5-5.5S9.5 12 9.5 15s3 5.5 6.5 5.5h1.5L21 22l-.5-3c1.3-1 2-2.4 2-4Z"/><path d="M6 8.5h.01M11 8.5h.01M13.5 14.5h.01M18.5 14.5h.01"/>',
  qr: '<path d="M3 3h6v6H3zm12 0h6v6h-6zM3 15h6v6H3zm12 0h3v3h3v3h-6zm6-3v3M12 3v3m0 3v3H9m-6 0h3m6 3v6m3-9h3"/>',
  logout: '<path d="M9 4H4v16h5m6-13 5 5-5 5m-7-5h12"/>',
};
const icon = (name) =>
  `<span data-icon="${name}"><svg viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.info}</svg></span>`;
function hydrateIcons(root = document) {
  $$("[data-icon]", root).forEach((element) => {
    if (!element.querySelector("svg"))
      element.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[element.dataset.icon] || paths.info}</svg>`;
  });
}
const escapeHTML = (value = "") =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const storage = {
  get(key) {
    try {
      return localStorage.getItem(`agentcall.${key}`) ?? localStorage.getItem(`emailcall.${key}`);
    } catch {
      return null;
    }
  },
  set(key, value) {
    try {
      localStorage.setItem(`agentcall.${key}`, value);
    } catch {
      /* Private browsing can disable storage. */
    }
  },
};
const defaults = {
  icloud: {
    name: "iCloud",
    smtp_host: "smtp.mail.me.com",
    smtp_port: 587,
    smtp_security: "starttls",
    imap_host: "imap.mail.me.com",
    imap_port: 993,
    imap_security: "ssl",
    hint: "在 Apple 账户的「登录和安全」中创建 App 专用密码。",
  },
  gmail: {
    name: "Gmail",
    smtp_host: "smtp.gmail.com",
    smtp_port: 465,
    smtp_security: "ssl",
    imap_host: "imap.gmail.com",
    imap_port: 993,
    imap_security: "ssl",
    hint: "开启 Google 账号两步验证后，创建应用专用密码。部分组织账户可能禁用此功能。",
  },
  163: {
    name: "网易邮箱",
    smtp_host: "smtp.163.com",
    smtp_port: 465,
    smtp_security: "ssl",
    imap_host: "imap.163.com",
    imap_port: 993,
    imap_security: "ssl",
    hint: "在网易邮箱设置中开启 IMAP / SMTP 服务，使用生成的客户端授权码。",
  },
  qq: {
    name: "QQ 邮箱",
    smtp_host: "smtp.qq.com",
    smtp_port: 465,
    smtp_security: "ssl",
    imap_host: "imap.qq.com",
    imap_port: 993,
    imap_security: "ssl",
    hint: "在 QQ 邮箱「设置 → 账户」中开启 IMAP / SMTP 服务，使用生成的授权码。",
  },
  custom: {
    name: "自定义",
    smtp_host: "",
    smtp_port: 465,
    smtp_security: "ssl",
    imap_host: "",
    imap_port: 993,
    imap_security: "ssl",
    hint: "使用邮箱服务提供的应用密码或客户端授权码，连接需支持 TLS 加密。",
  },
};
function readPendingTest() {
  try {
    const pending = JSON.parse(storage.get("pending-test"));
    if (
      pending &&
      typeof pending.key === "string" &&
      typeof pending.config === "string" &&
      pending.body?.subject &&
      pending.body?.body
    )
      return pending;
  } catch {
    /* An incomplete browser storage write must not stop the application. */
  }
  return null;
}
const state = {
  csrf: null,
  config: null,
  providers: defaults,
  provider: "icloud",
  dirty: false,
  editVersion: 0,
  online: false,
  page: "config",
  records: [],
  total: 0,
  offset: 0,
  limit: 20,
  recordSignature: "",
  detailId: null,
  detailSignature: "",
  liveTestId: storage.get("live-test"),
  liveSignature: "",
  pendingTest: readPendingTest(),
  lastHealth: 0,
  refreshing: false,
  sessionPromise: null,
  configChannel: storage.get("config-channel") || "wechat",
  wechat: null,
  wechatStatus: null,
  wechatDirty: false,
  wechatEditVersion: 0,
  contacts: [],
  contactsLoaded: false,
  selectedContact: null,
  wechatContactDirty: false,
  contactSearchVersion: 0,
  wechatRefreshing: false,
  wechatSignature: "",
  wechatConfigSignature: "",
};
const statusNames = {
  queued: "等待发送",
  sending: "正在发送",
  sent: "已完成",
  waiting: "等待回复",
  replied: "已回复",
  timed_out: "等待超时",
  failed: "失败",
};
const statusIcons = {
  queued: "clock",
  sending: "send",
  sent: "check",
  waiting: "clock",
  replied: "message",
  timed_out: "clock",
  failed: "alert",
};
const kindNames = {
  notify: "仅提醒",
  ask: "提醒并等待回复",
  diagnostic: "连接检查",
};

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $("#theme-label").textContent =
    theme === "dark" ? "切换浅色外观" : "切换深色外观";
  $("#theme-toggle").setAttribute(
    "aria-label",
    theme === "dark" ? "切换浅色外观" : "切换深色外观",
  );
  $("#theme-icon").innerHTML =
    `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[theme === "dark" ? "sun" : "moon"]}</svg>`;
  $('meta[name="theme-color"]').content =
    theme === "dark" ? "#141d19" : "#f5f7f5";
}
setTheme(
  storage.get("theme") ||
    (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"),
);
hydrateIcons();
$("#theme-toggle").addEventListener("click", () => {
  const theme =
    document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  setTheme(theme);
  storage.set("theme", theme);
});

function toast(message, isError = false) {
  const element = document.createElement("div");
  element.className = `toast${isError ? " error" : ""}`;
  element.setAttribute("role", isError ? "alert" : "status");
  element.innerHTML = `${icon(isError ? "alert" : "check")}<span>${escapeHTML(message)}</span>`;
  $("#toast-region").append(element);
  setTimeout(
    () => {
      element.classList.add("fade-out");
      setTimeout(() => element.remove(), 220);
    },
    isError ? 8500 : 5000,
  );
}

async function getSession() {
  if (!state.sessionPromise)
    state.sessionPromise = fetch("/api/session", {
      credentials: "same-origin",
      cache: "no-store",
    })
      .then(async (response) => {
        if (!response.ok) throw new Error("无法建立本地会话，请刷新页面重试。");
        const data = await response.json();
        state.csrf = data.csrf_token;
      })
      .finally(() => {
        state.sessionPromise = null;
      });
  return state.sessionPromise;
}
async function api(path, options = {}, recoveredSession = false) {
  if (!state.csrf) await getSession();
  const { body, headers = {}, ...rest } = options;
  let response;
  try {
    response = await fetch(path, {
      credentials: "same-origin",
      cache: "no-store",
      ...rest,
      headers: {
        "X-CSRF-Token": state.csrf,
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...headers,
      },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
  } catch {
    setConnection(false);
    throw new Error(
      "无法连接本地服务。请确认 agentCall 容器已启动，然后重试。",
    );
  }
  if (response.status === 401 && !recoveredSession) {
    await getSession();
    return api(path, options, true);
  }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const detail = data.error || {};
    const error = new Error(
      detail.message || `请求失败（HTTP ${response.status}）`,
    );
    error.code = detail.code;
    error.hint = detail.hint;
    error.status = response.status;
    error.requestId = data.request_id;
    throw error;
  }
  state.online = true;
  return response.headers.get("content-type")?.includes("application/zip")
    ? response.blob()
    : response.json();
}
function errorMessage(error) {
  return [error.message, error.hint].filter(Boolean).join(" ");
}
async function withBusy(button, label, action) {
  if (button.disabled) return;
  const previous = button.innerHTML;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  button.classList.add("is-busy");
  button.innerHTML = `${icon("refresh")}${escapeHTML(label)}`;
  try {
    return await action();
  } catch (error) {
    toast(errorMessage(error), true);
  } finally {
    button.disabled = false;
    button.removeAttribute("aria-busy");
    button.classList.remove("is-busy");
    button.innerHTML = previous;
  }
}
function setConnection(online, service = {}) {
  state.online = online;
  $("#service-dot").className = `status-dot ${online ? "online" : "error"}`;
  $("#service-label").textContent = online
    ? "本地服务运行中"
    : "本地服务未连接";
  const pollError = service.poll_error;
  $("#connection-banner").hidden = online && !pollError;
  if (!online)
    $("#connection-message").textContent =
      "暂时无法连接服务，请确认 agentCall 容器正在运行。";
  else if (pollError)
    $("#connection-message").textContent =
      `收件检查遇到问题：${pollError.message}${pollError.hint ? ` ${pollError.hint}` : ""}`;
}

function applyProvider(provider, updateFields) {
  state.provider = state.providers[provider] ? provider : "custom";
  const preset = state.providers[state.provider] || defaults.custom;
  $$(".provider-button").forEach((button) => {
    const selected = button.dataset.provider === state.provider;
    button.classList.toggle("selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
  $("#provider-help").textContent =
    preset.hint || defaults[state.provider]?.hint || defaults.custom.hint;
  $("#server-summary").textContent =
    state.provider === "custom"
      ? "请填写邮箱服务器信息"
      : `已使用 ${preset.name} 推荐设置`;
  $("#email").placeholder = {
    icloud: "agent@icloud.com",
    gmail: "agent@gmail.com",
    163: "agent@163.com",
    qq: "agent@qq.com",
    custom: "agent@example.com",
  }[state.provider];
  if (updateFields) {
    [
      "smtp_host",
      "smtp_port",
      "smtp_security",
      "imap_host",
      "imap_port",
      "imap_security",
    ].forEach((name) => {
      $(`#${name}`).value = preset[name] ?? defaults.custom[name];
    });
    if (state.provider === "custom") $("#advanced-settings").open = true;
    markDirty();
  }
}
function applyConfig(data) {
  if (Array.isArray(data.providers)) {
    data.providers.forEach((provider) => {
      state.providers[provider.id] = {
        ...(defaults[provider.id] || {}),
        ...provider,
      };
    });
  }
  state.config = data.config;
  if (data.wechat) applyWechat(data.wechat);
  const config = state.config || {};
  applyProvider(config.provider || "icloud", false);
  [
    "email",
    "username",
    "target_email",
    "smtp_host",
    "smtp_port",
    "smtp_security",
    "imap_host",
    "imap_port",
    "imap_security",
    "imap_folder",
    "poll_interval",
  ].forEach((name) => {
    if (config[name] !== undefined && config[name] !== null)
      $(`#${name}`).value = config[name];
  });
  $("#password").value = "";
  $("#password").placeholder = config.password_set
    ? "已保存 · 留空保留现有密码"
    : "输入邮箱服务提供的应用专用密码";
  $("#password-state").textContent = config.password_set ? "已保存" : "";
  state.dirty = false;
  $("#save-status").className = "";
  $("#save-status").innerHTML = `${icon("lock")}配置与记录保存在本机`;
  setConnection(true, data.service);
  updateProgress();
  updateChannelLabels();
}
function markDirty() {
  state.dirty = true;
  state.editVersion += 1;
  $("#save-status").className = "dirty";
  $("#save-status").innerHTML = `${icon("info")}有尚未保存的更改`;
}
function emailConfigured(config = state.config) {
  return Boolean(config?.email && config?.target_email && config?.password_set);
}
function wechatConfigured(config = state.wechat) {
  return Boolean(config?.enabled && config?.target_contact_id);
}
function testChannel() {
  return $("#test-channel").value || "auto";
}
function hasSavedConfig(channel = testChannel(), connectionOnly = false) {
  if ((channel !== "wechat" && state.dirty) || (channel !== "email" && state.wechatDirty)) {
    toast("请先保存通信配置中的更改。", true);
    $(state.wechatDirty ? "#save-wechat" : "#save-config").focus();
    return false;
  }
  // Diagnostics must explain and persist missing login/recipient errors too.
  if (connectionOnly && channel === "wechat") return true;
  const ready = channel === "wechat" ? wechatConfigured() : channel === "email" ? emailConfigured() : wechatConfigured() || emailConfigured();
  if (!ready) {
    toast(channel === "wechat" ? "请先启用微信、选择联系人并保存配置。" : channel === "email" ? "请先填写并保存发件邮箱、应用专用密码和目标邮箱。" : "请先保存微信联系人或完整的邮箱配置。", true);
    return false;
  }
  return true;
}
function updateProgress() {
  const completed = [
    emailConfigured() || wechatConfigured(),
    storage.get("exported") === "yes",
    storage.get("tested") === "yes",
  ];
  ["mail", "agent", "test"].forEach((step, index) => {
    const element = $(`#step-${step}`);
    element.classList.toggle("complete", completed[index]);
    $(".journey-marker", element).innerHTML = completed[index]
      ? icon("check")
      : String(index + 1);
  });
  $("#setup-progress").textContent = `${completed.filter(Boolean).length} / 3`;
}
$("#config-form").addEventListener("input", markDirty);
$("#config-form").addEventListener("change", markDirty);
$("#config-form").addEventListener(
  "invalid",
  (event) => {
    if (event.target.closest(".advanced-settings"))
      $("#advanced-settings").open = true;
  },
  true,
);
$("#provider-options").addEventListener("click", (event) => {
  const button = event.target.closest("[data-provider]");
  if (button) applyProvider(button.dataset.provider, true);
});
$("#toggle-password").addEventListener("click", () => {
  const show = $("#password").type === "password";
  $("#password").type = show ? "text" : "password";
  $("#toggle-password").setAttribute(
    "aria-label",
    show ? "隐藏密码" : "显示密码",
  );
  $("#toggle-password").setAttribute("aria-pressed", String(show));
  $("#toggle-password").innerHTML = icon(show ? "eye-off" : "eye");
});
$("#config-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(event.currentTarget));
  data.provider = state.provider;
  [
    "email",
    "username",
    "target_email",
    "smtp_host",
    "imap_host",
    "imap_folder",
  ].forEach((name) => {
    data[name] = data[name].trim();
  });
  ["smtp_port", "imap_port", "poll_interval"].forEach((name) => {
    data[name] = Number(data[name]);
  });
  if (!state.config?.password_set && !data.password) {
    toast("请输入邮箱的应用专用密码或授权码。", true);
    $("#password").focus();
    return;
  }
  if (state.pendingTest) {
    const candidate = {
      ...state.config,
      ...data,
      username: data.username || data.email,
      password_set: Boolean(data.password || state.config?.password_set),
    };
    delete candidate.password;
    if (testFingerprint(candidate, state.wechat, state.pendingTest.body.channel || "auto") !== state.pendingTest.config) {
      toast(
        "上次测试的创建结果尚未确认。请先点击「重试这次测试」，确认后再保存新配置；当前修改会保留。",
        true,
      );
      $("#send-test").focus();
      return;
    }
  }
  const submittedVersion = state.editVersion;
  withBusy($("#save-config"), "正在保存", async () => {
    const result = await api("/api/config", { method: "PUT", body: data });
    storage.set("tested", "no");
    state.liveTestId = null;
    state.liveSignature = "";
    storage.set("live-test", "");
    $("#live-test").hidden = true;
    if (state.editVersion === submittedVersion) {
      applyConfig(result);
      toast("配置已保存，现在可以检查连接并导出 Skill。");
    } else {
      state.config = result.config;
      setConnection(true, result.service);
      updateProgress();
      toast("配置已保存。你刚刚做出的新更改仍保留在表单中，请再次保存。");
    }
  });
});

function selectConfigChannel(channel, focus = false) {
  state.configChannel = channel === "email" ? "email" : "wechat";
  storage.set("config-channel", state.configChannel);
  $("#wechat-config-panel").hidden = state.configChannel !== "wechat";
  $("#config-form").hidden = state.configChannel !== "email";
  $$("[data-channel-tab]").forEach((button) => {
    const selected = button.dataset.channelTab === state.configChannel;
    button.classList.toggle("selected", selected);
    button.setAttribute("aria-selected", String(selected));
    button.setAttribute("tabindex", selected ? "0" : "-1");
    if (selected && focus) button.focus();
  });
}
$$("[data-channel-tab]").forEach((button) => {
  button.addEventListener("click", () => selectConfigChannel(button.dataset.channelTab));
  button.addEventListener("keydown", (event) => {
    if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      selectConfigChannel(event.key === "Home" ? "wechat" : event.key === "End" ? "email" : state.configChannel === "wechat" ? "email" : "wechat", true);
    }
  });
});
function updateChannelLabels() {
  $("#email-tab-status").textContent = emailConfigured() ? "已配置" : "未配置";
  $("#wechat-tab-status").textContent = state.wechatStatus?.available ? "已就绪" : state.wechatStatus?.logged_in ? "已登录" : state.wechat?.enabled ? "待登录" : "未启用";
}
function markWechatDirty() {
  state.wechatDirty = true;
  state.wechatEditVersion += 1;
  $("#wechat-save-status").className = "dirty";
  $("#wechat-save-status").innerHTML = `${icon("info")}有尚未保存的更改`;
}
function updateWechatMode() {
  const external = $("#wechat-mode").value === "external";
  $("#wechat-external-settings").hidden = !external;
  $("#wechat-endpoint").required = external;
}
function applyWechat(data, preserveEdits = true) {
  if (data.config) state.wechat = data.config;
  const configSignature = JSON.stringify(state.wechat || {});
  if (!preserveEdits || (!state.wechatDirty && configSignature !== state.wechatConfigSignature)) {
    const config = state.wechat || {};
    state.wechatConfigSignature = configSignature;
    $("#wechat-enabled").checked = Boolean(config.enabled);
    $("#wechat-mode").value = config.mode || "local";
    $("#wechat-endpoint").value = config.service_endpoint || "";
    $("#wechat-token").value = "";
    $("#wechat-token").placeholder = config.service_token_set ? "已保存 · 留空保留现有令牌" : "输入外部服务的访问令牌";
    $("#wechat-token-state").textContent = config.service_token_set ? "已保存" : "";
    state.selectedContact = config.target_contact_id ? { id: config.target_contact_id, name: config.target_contact_name || config.target_contact_id } : null;
    state.wechatContactDirty = false;
    state.wechatDirty = false;
    $("#wechat-save-status").className = "";
    $("#wechat-save-status").innerHTML = `${icon("lock")}配置与登录会话持久保存`;
    updateWechatMode();
    renderContacts();
  }
  if (data.status) renderWechatStatus(data.status);
  updateChannelLabels();
  updateProgress();
}
function renderWechatStatus(status) {
  state.wechatStatus = status;
  const signature = JSON.stringify([status.state, status.logged_in, status.available, status.account, status.qr_image, status.qr_status, status.error]);
  if (signature === state.wechatSignature) return;
  state.wechatSignature = signature;
  const labels = { disabled: "微信未启用", stopped: "微信已停止", starting: "正在启动微信连接", connecting: "正在连接微信服务", scanning: "等待扫码登录", scan: "等待扫码登录", waiting_scan: "等待扫码登录", awaiting_scan: "等待扫码登录", waiting_login: "等待扫码登录", logged_out: "微信当前未登录", error: "微信连接遇到问题", unavailable: "微信暂时不可用" };
  const loggedIn = Boolean(status.logged_in);
  $("#wechat-status-title").textContent = loggedIn ? `已登录${status.account?.name ? ` · ${status.account.name}` : ""}` : labels[status.state] || "微信尚未登录";
  $("#wechat-status-description").textContent = status.available ? "微信当前可用，将优先用于下一次 Agent 消息。" : loggedIn ? "选择并保存一位目标联系人后，即可发送消息。" : "微信不可用时，Agent 会自动使用已配置的邮箱。";
  $(".status-dot", $("#wechat-status")).className = `status-dot ${loggedIn ? "online" : status.error ? "error" : ""}`;
  $("#wechat-error").hidden = !status.error;
  $("#wechat-error").innerHTML = status.error ? `<strong>${escapeHTML(status.error.message || "微信连接失败")}</strong>${status.error.hint ? `<p>${escapeHTML(status.error.hint)}</p>` : ""}${status.error.code ? `<code>${escapeHTML(status.error.code)}</code>` : ""}` : "";
  const qr = status.qr_image;
  const safeImage = typeof qr === "string" && /^data:image\/(?:svg\+xml|png);base64,[A-Za-z0-9+/=\s]+$/.test(qr);
  $("#wechat-qr").innerHTML = loggedIn ? `<div class="qr-placeholder connected">${icon("check")}<span>微信已登录</span></div>` : safeImage ? `<img src="${escapeHTML(qr)}" alt="使用微信扫描此二维码并在手机上确认登录"/>` : `<div class="qr-placeholder">${icon("qr")}<span>${status.state === "starting" ? "正在获取登录二维码…" : "登录二维码会显示在这里"}</span></div>`;
  $("#wechat-login-title").textContent = loggedIn ? "连接已经建立" : safeImage ? "用微信扫一扫" : "从一次扫码开始";
  $("#wechat-login-help").textContent = loggedIn ? "登录会话保存在本机。断开并停用后，重新启用可恢复仍有效的会话；更换微信账号后需重新选择联系人。" : safeImage ? "请使用微信扫描二维码并在手机确认。二维码过期时，点击刷新二维码。" : "点击「扫码登录」连接微信；重新启用时会尝试恢复有效会话。更换微信账号后，请重新选择目标联系人。";
  $("#wechat-login").hidden = loggedIn;
  if (!$("#wechat-login").disabled) $("#wechat-login").innerHTML = `${icon("qr")}${safeImage ? "刷新二维码" : "扫码登录"}`;
  $("#wechat-logout").hidden = !loggedIn;
  $("#wechat-load-contacts").disabled = !loggedIn;
  updateChannelLabels();
}
async function refreshWechat(probe = false) {
  if (state.wechatRefreshing) return;
  state.wechatRefreshing = true;
  try { applyWechat(await api(`/api/wechat${probe ? "?probe=1" : ""}`)); }
  finally { state.wechatRefreshing = false; }
}
function wechatFormData() {
  return {
    enabled: $("#wechat-enabled").checked,
    mode: $("#wechat-mode").value || "local",
    service_endpoint: $("#wechat-endpoint").value.trim(),
    service_token: $("#wechat-token").value,
    ...(state.wechatContactDirty ? {
      target_contact_id: state.selectedContact?.id || "",
      target_contact_name: state.selectedContact?.alias || state.selectedContact?.name || "",
    } : {}),
  };
}
async function saveWechatConfig() {
  if (state.pendingTest && (state.pendingTest.body.channel || "auto") !== "email")
    throw new Error("上次测试的创建结果尚未确认。请先点击「重试这次测试」，再保存微信配置。");
  const submittedVersion = state.wechatEditVersion;
  const result = await api("/api/wechat", { method: "PUT", body: wechatFormData() });
  applyWechat(result, state.wechatEditVersion !== submittedVersion);
  storage.set("tested", "no");
  updateProgress();
  return result;
}
$("#wechat-config-panel").addEventListener("input", (event) => {
  if (event.target.id !== "wechat-contact-search") markWechatDirty();
});
$("#wechat-config-panel").addEventListener("change", (event) => {
  if (event.target.id !== "wechat-contact-search") markWechatDirty();
});
$("#wechat-mode").addEventListener("change", updateWechatMode);
$("#wechat-config-panel").addEventListener("submit", (event) => {
  event.preventDefault();
  withBusy($("#save-wechat"), "正在保存", async () => {
    await saveWechatConfig();
    toast("微信配置已保存。");
  });
});
$("#wechat-login").addEventListener("click", async () => {
  await withBusy($("#wechat-login"), "正在获取二维码", async () => {
    if (!$("#wechat-enabled").checked) { $("#wechat-enabled").checked = true; markWechatDirty(); }
    if (state.wechatDirty || !state.wechat?.enabled) await saveWechatConfig();
    applyWechat(await api("/api/wechat/login", { method: "POST", body: {} }));
    await refreshWechat();
  });
  state.wechatSignature = "";
  if (state.wechatStatus) renderWechatStatus(state.wechatStatus);
});
$("#wechat-refresh").addEventListener("click", () => withBusy($("#wechat-refresh"), "检查中", () => refreshWechat(true)));
$("#wechat-logout").addEventListener("click", () => withBusy($("#wechat-logout"), "正在断开并停用", async () => {
  applyWechat(await api("/api/wechat/logout", { method: "POST", body: {} }));
  state.contacts = [];
  state.contactsLoaded = false;
  renderContacts();
  await refreshWechat();
  toast("已断开并停用微信，后续自动发送将使用备用邮箱。有效登录会话仍保留，重新启用时可恢复。");
}));
function renderContacts() {
  const selected = state.selectedContact;
  $("#wechat-selected-contact").hidden = !selected;
  $("#wechat-selected-contact").innerHTML = selected ? `<span class="contact-avatar">${escapeHTML((selected.alias || selected.name || "微").slice(0, 1))}</span><span class="contact-name">${escapeHTML(selected.alias || selected.name || selected.id)}<small>当前选择的目标联系人</small></span><button type="button" class="text-button clear-contact" aria-label="清除目标联系人" data-clear-contact>${icon("x")}清除</button>` : "";
  $("#wechat-contacts").innerHTML = state.contacts.length ? state.contacts.map((contact) => `<button class="contact-option${selected?.id === contact.id ? " selected" : ""}" type="button" data-contact-id="${escapeHTML(contact.id)}" aria-pressed="${selected?.id === contact.id}"><span class="contact-avatar">${escapeHTML((contact.alias || contact.name || "微").slice(0, 1))}</span><span class="contact-name">${escapeHTML(contact.alias || contact.name || contact.id)}${contact.alias && contact.name ? `<small>${escapeHTML(contact.name)}</small>` : ""}<small>${escapeHTML(contact.id)}</small></span>${selected?.id === contact.id ? icon("check") : ""}</button>`).join("") : `<p class="contact-empty">${state.contactsLoaded ? "没有找到联系人。试试其他姓名，或确认已在微信添加对方。" : "登录微信后，获取并选择联系人。"}</p>`;
}
async function loadContacts() {
  const version = ++state.contactSearchVersion;
  const query = $("#wechat-contact-search").value.trim();
  $("#wechat-contacts").setAttribute("aria-busy", "true");
  try {
    const result = await api(`/api/wechat/contacts?q=${encodeURIComponent(query)}`);
    if (version !== state.contactSearchVersion) return;
    state.contacts = result.items || [];
    state.contactsLoaded = true;
    renderContacts();
  } catch (error) {
    if (version === state.contactSearchVersion) $("#wechat-contacts").innerHTML = `<p class="contact-empty error-text">${escapeHTML(errorMessage(error))}</p>`;
    throw error;
  } finally { if (version === state.contactSearchVersion) $("#wechat-contacts").removeAttribute("aria-busy"); }
}
$("#wechat-load-contacts").addEventListener("click", () => withBusy($("#wechat-load-contacts"), "获取中", loadContacts));
let contactSearchTimer;
$("#wechat-contact-search").addEventListener("input", () => {
  clearTimeout(contactSearchTimer);
  if (state.wechatStatus?.logged_in) contactSearchTimer = setTimeout(() => loadContacts().catch(() => {}), 300);
});
$("#wechat-contacts").addEventListener("click", (event) => {
  const button = event.target.closest("[data-contact-id]");
  const contact = button && state.contacts.find((item) => item.id === button.dataset.contactId);
  if (contact) { state.selectedContact = contact; state.wechatContactDirty = true; markWechatDirty(); renderContacts(); }
});
$("#wechat-selected-contact").addEventListener("click", (event) => {
  if (event.target.closest("[data-clear-contact]")) {
    state.selectedContact = null;
    state.wechatContactDirty = true;
    markWechatDirty();
    renderContacts();
  }
});

function showPage(page) {
  state.page = page === "records" ? "records" : "config";
  $("#config-page").hidden = state.page !== "config";
  $("#records-page").hidden = state.page !== "records";
  $$(".nav-item").forEach((item) => {
    const active = item.dataset.page === state.page;
    item.classList.toggle("active", active);
    if (active) item.setAttribute("aria-current", "page");
    else item.removeAttribute("aria-current");
  });
  document.title = `${state.page === "config" ? "配置" : "记录"} · agentCall`;
  if (state.page === "records" && state.csrf)
    loadRecords().catch((error) => showRecordsError(error));
}
addEventListener("hashchange", () => showPage(location.hash.slice(1)));
showPage(location.hash.slice(1));
addEventListener("beforeunload", (event) => {
  if (state.dirty || state.wechatDirty) {
    event.preventDefault();
    event.returnValue = "";
  }
});

$("#test-connection").addEventListener("click", () => {
  const channel = testChannel();
  if (!hasSavedConfig(channel, true)) return;
  return withBusy($("#test-connection"), "正在检查连接", async () => {
    $("#test-results").hidden = false;
    $("#test-results").textContent =
      channel === "wechat" ? "正在检查微信服务、登录状态和目标联系人…" : channel === "email" ? "正在分别验证 SMTP 发信与 IMAP 收信，请稍候…" : "正在检查微信优先通道与可用的邮箱连接…";
    try {
      const result = await api(channel === "wechat" ? "/api/wechat/test" : "/api/config/test", {
        method: "POST",
        body: channel === "wechat" ? {} : { channel },
      });
      $("#test-results").innerHTML = (result.checks || [])
        .map(
          (check) =>
            `<div class="test-check${check.ok ? "" : " failed"}"><div>${icon(check.ok ? "check" : "alert")}${escapeHTML(String(check.name).toUpperCase())} ${check.ok ? "连接成功" : "连接失败"}</div>${check.error ? `<p>${escapeHTML(check.error.message)}${check.error.hint ? `<br>${escapeHTML(check.error.hint)}` : ""}</p>` : ""}</div>`,
        )
        .join("");
      toast(
        result.ok
          ? "连接检查通过，可以开始对话测试。"
          : "连接检查未通过，请查看检查结果。",
        !result.ok,
      );
    } catch (error) {
      $("#test-results").innerHTML =
        `<div class="test-check failed"><div>${icon("alert")}检查未完成</div><p>${escapeHTML(errorMessage(error))}</p></div>`;
      throw error;
    }
  });
});
function configFingerprint(config) {
  return JSON.stringify(
    Object.keys(config || {})
      .sort()
      .map((name) => [name, config[name]]),
  );
}
function testFingerprint(config, wechat, channel = "auto") {
  if (channel === "email" || !wechat) return configFingerprint(config);
  return configFingerprint({ ...(channel === "auto" ? { email: config } : {}), wechat });
}
function savePendingTest(pending) {
  state.pendingTest = pending;
  storage.set("pending-test", pending ? JSON.stringify(pending) : "");
}
function updateTestButton() {
  $("#send-test").innerHTML = state.pendingTest
    ? `${icon("refresh")}重试这次测试<span class="button-meta">确认原请求</span>`
    : `${icon("send")}发送对话测试<span class="button-meta">等待 5 分钟</span>`;
}
function renderPendingTest(message) {
  state.liveSignature = "";
  $("#live-test").hidden = false;
  $("#live-test").innerHTML =
    `<span class="status-pill waiting">${icon("info")}请求结果待确认</span><p>${escapeHTML(message || "上次请求的响应未能确认。重试会继续确认同一次测试，不会创建新的测试请求。")}</p>`;
}
function rememberLiveTest(record) {
  state.liveTestId = record.id;
  storage.set("live-test", record.id);
  renderLiveTest(record);
}
$("#send-test").addEventListener("click", async () => {
  if (!state.pendingTest && !hasSavedConfig()) return;
  const retrying = Boolean(state.pendingTest);
  await withBusy(
    $("#send-test"),
    retrying ? "正在确认原请求" : "正在创建测试",
    async () => {
      if (!state.pendingTest) {
        savePendingTest({
          key: `web-test-${crypto.randomUUID()}`,
          config: testFingerprint(state.config, state.wechat, testChannel()),
          body: {
            subject: "agentCall 连通性测试 · 请回复这条消息",
            body: "你好！这是来自 agentCall 的对话测试。\n\n请在 5 分钟内回复，可以写下「已收到，连接成功」，或任何你想说的话。收到回复后，agentCall 会在网页中显示回复原文。\n\n使用微信时，请按消息末尾的说明回复；使用邮箱时，请直接回复原邮件。安装 Skill 后，你也可以让 Agent 发起测试并复述你的回复。",
            agent_name: "agentCall 连通性测试",
            timeout_seconds: 300,
            channel: testChannel(),
          },
        });
      }
      const pending = state.pendingTest;
      try {
        if (retrying) {
          const current = await api("/api/config");
          if (testFingerprint(current.config, current.wechat?.config, pending.body.channel || "auto") !== pending.config) {
            const error = new Error(
              "已保存的通信配置已发生变化。请恢复原配置后重试；原请求仍保留，已暂停向新收件人重发。",
            );
            error.configChanged = true;
            throw error;
          }
        }
        const record = await api("/api/ask", {
          method: "POST",
          headers: { "Idempotency-Key": pending.key },
          body: pending.body,
        });
        if (!record?.id)
          throw new Error("服务未返回有效的请求编号，请重试确认这次测试。");
        savePendingTest(null);
        rememberLiveTest(record);
        toast(
          retrying
            ? "已确认原测试请求，当前状态已同步。"
            : "测试请求已创建，发出后请在 5 分钟内回复消息。",
        );
      } catch (error) {
        if ([400, 409, 413, 415, 422].includes(error.status)) {
          savePendingTest(null);
          if (error.requestId) {
            state.liveTestId = error.requestId;
            storage.set("live-test", error.requestId);
            try {
              renderLiveTest(
                await api(
                  `/api/records/${encodeURIComponent(error.requestId)}`,
                ),
              );
            } catch {
              /* The durable record remains available in Records. */
            }
          } else $("#live-test").hidden = true;
        } else
          renderPendingTest(error.configChanged ? error.message : undefined);
        throw error;
      }
    },
  );
  updateTestButton();
});
function latestIgnoredReply(record) {
  return record.ignored_replies?.at(-1);
}
function statusBadge(record) {
  const status = Object.hasOwn(statusNames, record.status)
    ? record.status
    : "queued";
  const waiting = status === "waiting" && record.deadline_at;
  const reconciling = waiting && Date.parse(record.deadline_at) <= Date.now();
  const badge = `<span class="status-pill ${status}">${icon(statusIcons[status])}<span${waiting ? ` data-wait-status="${escapeHTML(record.deadline_at)}"` : ""}>${reconciling ? "核对最后回复" : statusNames[status]}</span></span>`;
  return latestIgnoredReply(record)
    ? `<span class="status-group">${badge}<span class="status-pill ignored-reply">${icon("alert")}<span>回复未采纳</span></span></span>`
    : badge;
}
function waitingMessage(deadline, channel = "email") {
  const instruction = channel === "wechat" ? "请在微信中按测试消息的说明回复。" : "请直接回复收到的邮件。";
  const remaining = Math.ceil((Date.parse(deadline) - Date.now()) / 1000);
  if (!Number.isFinite(remaining)) return instruction;
  if (remaining < -30)
    return "回复截止时间已过，正在等待服务同步最终结果。若长时间未更新，请检查本地服务连接。";
  if (remaining <= 0)
    return "回复截止时间已到，正在核对最后收到的消息（最多 30 秒），随后同步最终结果。";
  return `${instruction}剩余 ${Math.floor(remaining / 60)} 分 ${remaining % 60} 秒，截止 ${formatDate(deadline)}。`;
}
function updateCountdowns() {
  $$("[data-wait-status]").forEach((element) => {
    const label =
      Date.parse(element.dataset.waitStatus) <= Date.now()
        ? "核对最后回复"
        : "等待回复";
    if (element.textContent !== label) element.textContent = label;
  });
  $$("[data-wait-message]").forEach((element) => {
    const message = `${element.dataset.waitMessagePrefix || ""}${waitingMessage(element.dataset.waitMessage, element.dataset.waitChannel)}`;
    if (element.textContent !== message) element.textContent = message;
  });
}
function renderLiveTest(record) {
  if (state.pendingTest) return;
  const signature = JSON.stringify(record);
  if (signature === state.liveSignature) return;
  state.liveSignature = signature;
  const container = $("#live-test");
  container.hidden = false;
  const ignoredReply = latestIgnoredReply(record);
  const ignoredMessage = ignoredReply
    ? `已收到回复，但未采纳：${ignoredReply.ignored_reason?.message || "该消息未通过回复校验。"}\n${ignoredReply.ignored_reason?.hint || (record.channel === "wechat" ? "请使用所选目标联系人的微信账号回复。" : `请使用配置的目标邮箱 ${record.target_email || ""} 直接回复测试邮件。`)}\n`
    : "";
  let message = "测试消息正在发送，请稍候。";
  if (record.status === "waiting")
    message = `${ignoredMessage}${waitingMessage(record.deadline_at, record.channel)}`;
  if (record.status === "replied") {
    message = `已收到你的回复：\n${record.reply?.body || record.replies?.find((reply) => !reply.late)?.body || "（空回复）"}`;
    storage.set("tested", "yes");
    updateProgress();
  }
  if (record.status === "failed")
    message = record.error
      ? `${record.error.message} ${record.error.hint || ""}`
      : "测试发送失败，请在记录中查看原因。";
  if (record.status === "timed_out")
    message = `${ignoredMessage}等待有效回复已超时。仍会保存晚到的回复，你可以重新发起测试。`;
  if (!$("[data-live-status]", container)) {
    container.innerHTML = `<div data-live-status></div><p data-live-message></p><button class="text-button" type="button" data-record-id="">查看完整记录 ${icon("arrow-up-right")}</button>`;
  }
  $("[data-live-status]", container).innerHTML = statusBadge(record);
  const paragraph = $("[data-live-message]", container);
  paragraph.textContent = message;
  paragraph.classList.toggle(
    "warning-text",
    Boolean(ignoredReply) && ["waiting", "timed_out"].includes(record.status),
  );
  if (record.status === "waiting" && record.deadline_at) {
    paragraph.dataset.waitMessage = record.deadline_at;
    paragraph.dataset.waitMessagePrefix = ignoredMessage;
    paragraph.dataset.waitChannel = record.channel || "email";
    paragraph.setAttribute("role", "timer");
    paragraph.setAttribute("aria-live", "off");
  } else {
    paragraph.removeAttribute("data-wait-message");
    paragraph.removeAttribute("data-wait-message-prefix");
    paragraph.removeAttribute("data-wait-channel");
    paragraph.removeAttribute("role");
    paragraph.removeAttribute("aria-live");
  }
  $("[data-record-id]", container).dataset.recordId = record.id;
}
$("#live-test").addEventListener("click", (event) => {
  const button = event.target.closest("[data-record-id]");
  if (button) openRecord(button.dataset.recordId);
});

$("#export-skill").addEventListener("click", () => {
  if (!hasSavedConfig("auto")) return;
  withBusy($("#export-skill"), "正在打包 Skill", async () => {
    const blob = await api("/api/skill/export");
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "agentcall-skill.zip";
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    storage.set("exported", "yes");
    updateProgress();
    if (!$(".install-guide")) {
      const guide = document.createElement("div");
      guide.className = "install-guide";
      guide.innerHTML =
        "<strong>接下来，把 Skill 安装到 Agent</strong><p>解压 ZIP 后，在解压所在目录运行对应命令：</p><code>python3 agentcall/install.py codex\npython3 agentcall/install.py claude</code><p>同时安装到两个 Agent，可运行 <code>python3 agentcall/install.py both</code>安装器会同步配置任务完成与决策时的调用规则。</p><p>随后开启新的 Agent 会话，说：「使用 agentCall 做一次连通性测试，等待并复述我的回复。」</p>";
      $(".skill-panel").after(guide);
    }
    toast("Skill 已导出，安装方式见下载包中的 README。");
  });
});
$("#copy-token").addEventListener("click", () =>
  withBusy($("#copy-token"), "读取中", async () => {
    const { token } = await api("/api/token");
    if (!navigator.clipboard)
      throw new Error("当前浏览器不支持复制，请通过导出的 Skill 使用密钥。");
    await navigator.clipboard.writeText(token);
    toast("访问密钥已复制，请勿分享给他人。");
  }),
);
$("#rotate-token").addEventListener("click", () =>
  $("#confirm-dialog").showModal(),
);
$("#confirm-rotate").addEventListener("click", () =>
  withBusy($("#confirm-rotate"), "正在更换", async () => {
    await api("/api/token/rotate", { method: "POST", body: {} });
    $("#confirm-dialog").close();
    storage.set("exported", "no");
    updateProgress();
    toast("密钥已更换，请重新导出并安装 Skill。");
  }),
);
$$("[data-close-dialog]").forEach((button) =>
  button.addEventListener("click", () =>
    $(`#${button.dataset.closeDialog}`).close(),
  ),
);
$$("dialog").forEach((dialog) =>
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) {
      const bounds = dialog.getBoundingClientRect();
      if (
        event.clientX < bounds.left ||
        event.clientX > bounds.right ||
        event.clientY < bounds.top ||
        event.clientY > bounds.bottom
      )
        dialog.close();
    }
  }),
);

function formatDate(value, options = {}) {
  const date = new Date(value);
  if (!value || Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    ...options,
  }).format(date);
}
function renderStats(stats = {}) {
  ["total", "waiting", "replied", "failed"].forEach((name) => {
    $(`#stat-${name}`).textContent = String(stats[name] ?? 0);
  });
  $("#waiting-badge").hidden = !(stats.waiting > 0);
  $("#waiting-badge").textContent = String(stats.waiting || 0);
}
async function loadRecords() {
  const query = new URLSearchParams({
    q: $("#record-search").value.trim(),
    kind: $("#kind-filter").value,
    status: $("#status-filter").value,
    limit: String(state.limit),
    offset: String(state.offset),
  });
  const snapshot = query.toString();
  const result = await api(`/api/records?${query}`);
  const current = new URLSearchParams({
    q: $("#record-search").value.trim(),
    kind: $("#kind-filter").value,
    status: $("#status-filter").value,
    limit: String(state.limit),
    offset: String(state.offset),
  }).toString();
  if (current !== snapshot) return;
  state.records = result.items || [];
  state.total = result.total || 0;
  renderStats(result.stats);
  renderRecords();
}
function renderRecords() {
  const signature = JSON.stringify([
    state.records,
    state.total,
    $("#record-search").value,
    $("#kind-filter").value,
    $("#status-filter").value,
    state.offset,
  ]);
  if (signature === state.recordSignature) return;
  state.recordSignature = signature;
  const filtered = Boolean(
    $("#record-search").value ||
      $("#kind-filter").value ||
      $("#status-filter").value,
  );
  if (!state.records.length)
    $("#records-list").innerHTML =
      `<div class="empty-state"><div class="empty-art">${icon(filtered ? "search" : "inbox")}</div><h3>${filtered ? "没有找到匹配的记录" : "让第一条消息，开启连接"}</h3><p>${filtered ? "换一个关键词，或清除筛选条件查看全部消息。" : "Agent 的通知、你的回复和连接检查都会出现在这里。先连接微信或邮箱，再发起一次对话吧。"}</p>${filtered ? '<button class="button secondary" id="clear-filters" type="button">清除筛选</button>' : '<a class="button secondary" href="#config">配置通信方式与测试连接</a>'}</div>`;
  else
    $("#records-list").innerHTML = state.records
      .map((record) => {
        const ignoredReply = latestIgnoredReply(record);
        const preview =
          record.error?.message ||
          record.reply?.body ||
          record.replies?.find((reply) => !reply.late)?.body ||
          ignoredReply?.ignored_reason?.message ||
          record.body ||
          "";
        const previewClass = record.error
          ? " error-text"
          : ignoredReply && !record.reply && record.status !== "replied"
            ? " warning-text"
            : "";
        return `<button class="record-row" type="button" data-record-id="${escapeHTML(record.id)}" aria-label="查看记录：${escapeHTML(record.subject || "无主题")}，${escapeHTML(statusNames[record.status] || record.status)}${ignoredReply ? "，回复未采纳" : ""}"><span class="record-main"><span class="record-subject">${escapeHTML(record.subject || "无主题")}</span><span class="record-byline">${icon(record.kind === "diagnostic" ? "activity" : "robot")}${escapeHTML(record.agent_name || "Agent")}${channelBadge(record)}${record.fallback_reason ? '<span class="late-badge">已切换邮箱</span>' : ""}${record.replies?.some((reply) => reply.late) ? '<span class="late-badge">有晚到回复</span>' : ""}</span><span class="record-preview${previewClass}">${escapeHTML(preview)}</span></span><span class="record-kind">${escapeHTML(kindNames[record.kind] || record.kind)}</span>${statusBadge(record)}<span class="record-time">${escapeHTML(formatDate(record.created_at))}<span>${escapeHTML(new Date(record.created_at).getFullYear())}</span></span><span class="row-chevron">${icon("chevron-right")}</span></button>`;
      })
      .join("");
  $("#record-count").textContent = state.total
    ? `共 ${state.total} 条记录 · 显示 ${state.offset + 1}–${Math.min(state.offset + state.limit, state.total)} 条`
    : "共 0 条记录";
  $("#current-page").textContent = String(
    Math.floor(state.offset / state.limit) + 1,
  );
  $("#previous-page").disabled = state.offset === 0;
  $("#next-page").disabled = state.offset + state.limit >= state.total;
}
function showRecordsError(error) {
  state.recordSignature = "";
  $("#records-list").innerHTML =
    `<div class="empty-state"><div class="empty-art">${icon("alert")}</div><h3>暂时无法加载记录</h3><p>${escapeHTML(errorMessage(error))}</p><button class="button secondary" type="button" id="retry-records">重新加载</button></div>`;
  $("#record-count").textContent = "记录加载失败";
}
let searchTimer;
$("#record-search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.offset = 0;
    loadRecords().catch(showRecordsError);
  }, 280);
});
["kind-filter", "status-filter"].forEach((id) =>
  $(`#${id}`).addEventListener("change", () => {
    state.offset = 0;
    loadRecords().catch(showRecordsError);
  }),
);
$("#previous-page").addEventListener("click", () => {
  state.offset = Math.max(0, state.offset - state.limit);
  loadRecords().catch(showRecordsError);
});
$("#next-page").addEventListener("click", () => {
  state.offset += state.limit;
  loadRecords().catch(showRecordsError);
});
$("#refresh-records").addEventListener("click", () =>
  withBusy($("#refresh-records"), "正在刷新", async () => {
    await loadRecords();
    toast("记录已更新");
  }),
);
$("#records-list").addEventListener("click", (event) => {
  const row = event.target.closest("[data-record-id]");
  if (row) openRecord(row.dataset.recordId);
  if (event.target.closest("#clear-filters")) {
    $("#record-search").value = "";
    $("#kind-filter").value = "";
    $("#status-filter").value = "";
    state.offset = 0;
    loadRecords().catch(showRecordsError);
  }
  if (event.target.closest("#retry-records"))
    loadRecords().catch(showRecordsError);
});

async function openRecord(id) {
  state.detailId = id;
  state.detailSignature = "";
  $("#record-dialog-title").textContent = "消息详情";
  $("#record-detail").innerHTML =
    '<div class="loading-state" aria-label="正在加载详情"><span class="loading-line"></span><span class="loading-line"></span></div>';
  if (!$("#record-dialog").open) $("#record-dialog").showModal();
  try {
    const record = await api(`/api/records/${encodeURIComponent(id)}`);
    if (state.detailId === id) renderDetail(record);
  } catch (error) {
    $("#record-detail").innerHTML =
      `<div class="error-box">${escapeHTML(errorMessage(error))}</div>`;
  }
}
$("#record-dialog").addEventListener("close", () => {
  state.detailId = null;
});
function channelBadge(record) {
  const channel = record.channel === "wechat" ? "wechat" : "email";
  return `<span class="channel-badge ${channel}">${channel === "wechat" ? "微信" : "邮箱"}</span>`;
}
function renderFallback(record) {
  if (!record.fallback_reason) return "";
  const reason = record.fallback_reason;
  return `<div class="fallback-box"><strong>微信暂不可用，已改用邮箱发送</strong><p>${escapeHTML(typeof reason === "string" ? reason : reason.message || "微信连接暂不可用。")}</p>${reason.hint ? `<p>${escapeHTML(reason.hint)}</p>` : ""}</div>`;
}
function renderIgnoredReplies(record) {
  if (!record.ignored_replies?.length) return "";
  return `<section class="detail-section ignored-replies-section">
    <h3>${icon("alert")}未采纳的回复 · ${record.ignored_replies.length}</h3>
    <p class="ignored-reply-note">以下消息已保存，但未计入有效回复。${record.channel === "wechat" ? "请使用所选目标联系人的微信账号回复。" : `请使用配置的目标邮箱 ${escapeHTML(record.target_email || "")} 直接回复原邮件。`}</p>
    ${record.ignored_replies.map((reply) => `<div class="reply-block ignored-reply-block">
      <div class="reply-meta"><span>${escapeHTML(reply.from_name || reply.from_email || reply.from_contact_id || "未知发件人")}</span><time datetime="${escapeHTML(reply.received_at)}">${escapeHTML(formatDate(reply.received_at, { year: "numeric", second: "2-digit" }))}</time></div>
      <div class="ignored-reply-reason"><strong>${escapeHTML(reply.ignored_reason?.message || "该消息未通过回复校验。")}</strong>${reply.ignored_reason?.hint ? `<p>${escapeHTML(reply.ignored_reason.hint)}</p>` : ""}${reply.ignored_reason?.code ? `<code>原因码：${escapeHTML(reply.ignored_reason.code)}</code>` : ""}</div>
      <pre class="message-body">${escapeHTML(reply.body || "（空回复）")}</pre>
    </div>`).join("")}
  </section>`;
}
function renderDetail(record) {
  if (!$("#record-dialog").open || record.id !== state.detailId) return;
  const signature = JSON.stringify(record);
  if (signature === state.detailSignature) return;
  state.detailSignature = signature;
  $("#record-dialog-title").textContent = record.subject || "无主题";
  const replies = record.replies?.length
    ? record.replies
    : record.reply
      ? [record.reply]
      : [];
  const metadata = [
    ["发起 Agent", escapeHTML(record.agent_name || "Agent")],
    ["发送通道", channelBadge(record)],
    [record.channel === "wechat" ? "目标联系人" : "目标邮箱", escapeHTML(record.recipient_label || record.target_email || record.target_contact_id || "尚未配置")],
    [
      "创建时间",
      escapeHTML(
        formatDate(record.created_at, { year: "numeric", second: "2-digit" }),
      ),
    ],
    ...(record.sent_at
      ? [
          [
            "发送时间",
            escapeHTML(
              formatDate(record.sent_at, {
                year: "numeric",
                second: "2-digit",
              }),
            ),
          ],
        ]
      : []),
    ...(record.deadline_at
      ? [
          [
            "回复截止",
            escapeHTML(
              formatDate(record.deadline_at, {
                year: "numeric",
                second: "2-digit",
              }),
            ),
          ],
        ]
      : []),
    ["请求编号", `<code>${escapeHTML(record.id)}</code>`],
  ];
  $("#record-detail").innerHTML =
    `<div class="detail-overview">${statusBadge(record)}<span>${escapeHTML(kindNames[record.kind] || record.kind)}</span></div><dl class="detail-metadata">${metadata.map(([key, value]) => `<dt>${key}</dt><dd>${value}</dd>`).join("")}</dl>${record.error ? `<section class="detail-section"><h3>${icon("alert")}发生的问题</h3><div class="error-box"><strong>${escapeHTML(record.error.message)}</strong>${record.error.hint ? `<p>${escapeHTML(record.error.hint)}</p>` : ""}<code>错误码：${escapeHTML(record.error.code)}</code></div></section>` : ""}${renderFallback(record)}${renderIgnoredReplies(record)}<section class="detail-section"><h3>${icon(record.channel === "wechat" ? "wechat" : "mail")}发送的消息</h3><pre class="message-body">${escapeHTML(record.body || "（此请求没有正文）")}</pre></section>${replies.length ? `<section class="detail-section"><h3>${icon("message")}收到的回复 · ${replies.length}</h3>${replies.some((reply) => reply.late) ? '<p class="late-note">晚到的回复已保存，不会改变原请求的超时状态。</p>' : ""}${replies.map((reply) => `<div class="reply-block"><div class="reply-meta"><span>${escapeHTML(reply.from_name || reply.from_email || reply.from_contact_id || record.recipient_label || record.target_email)}</span><span>${escapeHTML(formatDate(reply.received_at, { second: "2-digit" }))}</span>${reply.late ? '<span class="late-badge">超时后到达</span>' : ""}</div><pre class="message-body">${escapeHTML(reply.body || "（空回复）")}</pre></div>`).join("")}</section>` : ""}${record.events?.length ? `<section class="detail-section"><h3>${icon("activity")}消息时间线</h3><ol class="timeline">${record.events.map((event) => `<li>${escapeHTML(event.message || event.type)}<time datetime="${escapeHTML(event.at)}">${escapeHTML(formatDate(event.at, { year: "numeric", second: "2-digit" }))}</time></li>`).join("")}</ol></section>` : ""}${["queued", "sending", "waiting"].includes(record.status) ? `<div class="detail-refresh">${icon("refresh")}状态每 5 秒自动更新</div>` : ""}`;
}

async function connect() {
  try {
    await getSession();
    const result = await api("/api/config");
    if (!state.dirty) applyConfig(result);
    else {
      state.config = result.config;
      setConnection(true, result.service);
    }
    const records = await api("/api/records?limit=1&offset=0");
    renderStats(records.stats);
    if (state.page === "records") await loadRecords();
    if (state.liveTestId) {
      try {
        renderLiveTest(
          await api(`/api/records/${encodeURIComponent(state.liveTestId)}`),
        );
      } catch (error) {
        if (error.status === 404) {
          state.liveTestId = null;
          storage.set("live-test", "");
        }
      }
    }
  } catch (error) {
    setConnection(false);
    if (state.page === "records") showRecordsError(error);
  }
}
$("#retry-connection").addEventListener("click", () =>
  withBusy($("#retry-connection"), "连接中", connect),
);
async function refresh() {
  if (document.hidden || state.refreshing || !state.csrf) return;
  state.refreshing = true;
  try {
    if (Date.now() - state.lastHealth > 15000 || !state.online) {
      const config = await api("/api/config");
      setConnection(true, config.service);
      state.lastHealth = Date.now();
    }
    if (state.page === "records") await loadRecords();
    if (state.detailId) {
      const id = state.detailId;
      const record = await api(`/api/records/${encodeURIComponent(id)}`);
      if (state.detailId === id) renderDetail(record);
    }
    if (state.liveTestId) {
      try {
        renderLiveTest(
          await api(`/api/records/${encodeURIComponent(state.liveTestId)}`),
        );
      } catch (error) {
        if (error.status === 404) {
          state.liveTestId = null;
          storage.set("live-test", "");
          $("#live-test").hidden = true;
        }
      }
    }
  } catch {
    /* Connection and record error UI already describe failures; polling stays quiet. */
  } finally {
    state.refreshing = false;
  }
}
selectConfigChannel(state.configChannel);
applyProvider("icloud", true);
state.dirty = false;
$("#save-status").className = "";
$("#save-status").innerHTML = `${icon("lock")}配置与记录保存在本机`;
updateTestButton();
if (state.pendingTest) renderPendingTest();
connect();
setInterval(refresh, 5000);
setInterval(() => { if (!document.hidden && state.page === "config" && state.csrf) refreshWechat().catch(() => {}); }, 3000);
setInterval(updateCountdowns, 1000);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refresh();
});
