(function () {
  "use strict";
  const data = JSON.parse(document.getElementById("gallery-data").textContent);
  const app = document.getElementById("app");
  const fragment = new URLSearchParams(location.hash.replace(/^#/, ""));
  const token = fragment.get("token");
  const interactive = Boolean(token) && location.protocol.startsWith("http");
  const current = data.current || null;
  const items = current ? current.items || [] : [];
  const storageKey = `jarden:draft:${data.project_id || data.title}:${data.delivery_id || data.snapshot_at}`;
  const persisted = loadDraft() || {};
  const state = {
    action: persisted.action || (data.next && data.next.action === "ask" ? "answer-field" : (current && current.kind === "set" ? "approve-set" : "select")),
    selected: new Set((persisted.selected || []).filter(id => items.some(item => item.id === id && (item.selectable || (persisted.action || "").startsWith("request-"))))),
    note: persisted.note || "",
    keep: persisted.keep || "",
    fieldValue: persisted.fieldValue ?? "",
    submitting: false,
    result: null,
    adopted: false,
    syncStatus: interactive ? "checking" : "static",
    syncError: "",
    error: "",
    requestId: persisted.requestId || null,
    requestPayload: persisted.requestPayload || "",
    zoomItem: null,
    zoomBackground: "checker",
    lastFocused: null
  };
  if (state.action === "approve-set") {
    const eligible = items.filter(item => item.selectable).map(item => item.id);
    state.selected = new Set((persisted.selected || eligible).filter(id => eligible.includes(id)));
  }
  if (data.receipts && data.receipts.length) {
    state.result = data.receipts[data.receipts.length - 1];
    state.adopted = state.result && state.result.committed === true;
  }

  const el = (tag, attrs, children) => {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([key, value]) => {
      if (value === undefined || value === null || value === false) return;
      if (key === "text") node.textContent = value;
      else if (key === "class") node.className = value;
      else if (key === "checked") node.checked = Boolean(value);
      else if (key === "value") node.value = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, String(value));
    });
    (children || []).forEach(child => child && node.appendChild(child));
    return node;
  };
  const imageSource = path => {
    try { return path.split("/").map(encodeURIComponent).join("/"); } catch (_) { return path; }
  };
  const stageLabel = { concept: "概念图", material: "材质方向", cutout: "透明图", scene: "场景图", package: "发帖素材", done: "已完成" };
  const viewLabel = value => ({ front: "正面", "three-quarter": "四分之三视角", left: "左侧", right: "右侧", back: "背面", detail: "局部细节" })[value] || "视面待补";
  const originLabel = value => ({ "user-confirmed": "用户说明", "agent-inferred": "设计推定", "reference-derived": "参考图提取" })[value] || "来源待补";
  const confirmationLabel = value => ({ confirmed: "已确认", proposed: "待确认", unknown: "未知，待补", stale: "已失效，待核对" })[value] || "确认状态待补";
  const reviewLabel = value => ({ needs_review: "缺审", stale: "已失效", invalid: "已失效", uncertain: "不确定", needs_revision: "需修改", pass: "通过", pending: "待补" })[value] || "待补";
  const checkLabel = { identity: "身份与结构", scale: "设计尺度", view: "实际视面、姿态与取景", coverage: "整套覆盖", preservation: "原图保持", order: "实际排列" };
  const nextStatus = action => ({ review: "待审查", package: "待整理", ask: "等待回答", generate: "待制作", round: "待制作", backend: "待配置", "await-choice": "等待选择", done: "已完成" })[action] || "状态待核对";
  const statusLabel = draft => {
    if (state.adopted) return state.result.action === "answer-field" ? "回答已保存" : (state.result.action || "").startsWith("request-") ? "意向已登记" : "已采用";
    if (data.stale) return "交付已过期";
    if (offer && (offer.action === "review" || offer.action === "package")) return nextStatus(offer.action);
    if (data.stage === "done") return nextStatus(offer && offer.action);
    if (!current) return data.kind === "post" ? "发帖交付" : (offer && offer.action === "ask" ? "等待回答" : "状态交付");
    if (data.readonly) return "旧项目只读";
    if (current.missing || data.missing) return "部分图片缺失";
    if (items.some(item => item.disabled_reason && !item.selectable)) return "有待审或不可用项";
    if (offer && offer.action === "ask") return "等待回答";
    if (draft) return "等待提交";
    return "等待选择";
  };
  const revLabel = () => `业务版本 ${data.project_revision ?? "—"}${data.stale ? ` · 当前 ${data.live_revision}` : ""}`;
  const compactTime = value => {
    if (!value) return "时间未记录";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date);
  };
  const text = value => value == null ? "" : String(value);
  const safeNote = value => value && typeof value === "object" ? JSON.stringify(value) : text(value);
  const offer = data.next || null;
  const questionFields = {
    "answers.product_form": { label: "产品形态", intro: "这次想把 IP 做成哪种周边？选一个最接近的类型，具体款式可以在聊天里补充。" },
    "answers.subject_scope": {
      label: "主体范围", intro: "这次想做参考图里的哪些角色？这里先选择范围；保存后可以回到聊天里，用名字或外观说明具体角色。",
      options: [
        { value: "single", label: "只做一个角色" },
        { value: "all", label: "全部角色都做" },
        { value: "selected", label: "挑选其中几个角色" }
      ]
    },
    "answers.subjects": { label: "图中主体", intro: "这次想做哪些角色？可以用名字或外观说明，也可以回到聊天里补充。", placeholder: "例如：戴红帽的兔子、左边的小熊" },
    "answers.note_type": {
      label: "笔记类型", intro: "这次想怎样展示作品？建议仅供参考，请按你想展示的内容选择。",
      intros: {
        "series-collection": "围绕一组角色展示单只、合照和场景，保持系列风格统一。",
        "design-manuscript": "展示三视图、配色、手稿和设计延展，侧重设计过程与设定。",
        "single-multiview": "围绕一个角色或产品展示正侧背、细节、尺度和使用场景。",
        "multi-scene": "让多个角色或产品在不同场景中组合展示。"
      }
    },
    "answers.concept_count": { label: "概念数量", intro: "这次一共想做几个角色或设计？每个都会分开确认；如果做系列，会先确认第一个的风格。", placeholder: "填写想做的角色或设计数量" },
    "answers.delivery": {
      label: "交付形式",
      intro: offer?.issue === "grid-capacity"
        ? "当前角色与必要场景超过九宫格容量。可以选择整套发帖；若仍想做九宫格，请回到聊天里确认分组或调整角色范围。"
        : "最后希望拿到哪种图片组合？两种形式都附标题、正文和标签，请按你的发布计划选择。"
    }
  };
  const questionField = questionFields[offer?.field] || { label: offer?.label || "本轮需要确认", intro: offer?.intro || "这次希望怎样安排？请填写你的想法，也可以回到聊天里补充说明。" };
  const questionChoices = Array.isArray(offer?.options) && offer.options.length ? offer.options : questionField.options || [];

  function loadDraft() {
    try {
      const stored = JSON.parse(localStorage.getItem(storageKey) || "null");
      return stored && typeof stored === "object" ? stored : null;
    } catch (_) { return null; }
  }

  function saveDraft() {
    try { localStorage.setItem(storageKey, JSON.stringify({ action: state.action, selected: [...state.selected], note: state.note, keep: state.keep, fieldValue: state.fieldValue, requestId: state.requestId, requestPayload: state.requestPayload })); } catch (_) {}
  }

  function setResult(message, isError) {
    state.error = isError ? message : "";
    if (isError) state.result = null;
    render();
    requestAnimationFrame(() => document.getElementById("result")?.scrollIntoView({ block: "nearest" }));
  }

  function parseKeep() {
    return state.keep.split(/[，,、\n]/).map(value => value.trim()).filter(Boolean);
  }

  function setSelection(id, checked) {
    if (state.action === "select" || state.action === "request-variation") {
      state.selected.clear();
      if (checked) state.selected.add(id);
    } else if (state.action === "approve-set") {
      if (checked) state.selected.add(id); else state.selected.delete(id);
    } else {
      if (checked) state.selected.add(id); else state.selected.delete(id);
    }
    state.result = state.result && !state.result.committed ? state.result : null;
    state.requestId = null;
    state.requestPayload = "";
    saveDraft();
    render();
    requestAnimationFrame(() => {
      const control = document.querySelector(`input[data-item-id="${CSS.escape(id)}"]`);
      if (control && !control.disabled) control.focus({ preventScroll: true });
    });
  }

  function setAction(action) {
    state.action = action;
    state.result = null;
    state.error = "";
    state.requestId = null;
    state.requestPayload = "";
    if (action === "select" || action === "approve-set") {
      state.selected = new Set([...state.selected].filter(id => items.find(item => item.id === id && item.selectable)));
    }
    if (action === "select" || action === "request-variation") {
      state.selected = new Set([...state.selected].slice(0, 1));
    }
    if (action === "request-redo") state.selected.clear();
    saveDraft();
    render();
    document.getElementById("decision-action")?.focus({ preventScroll: true });
  }

  function selectedPayloadItems() {
    return [...state.selected].map(id => {
      const item = items.find(entry => entry.id === id);
      return { id, sha256: item && item.image_sha256 };
    });
  }

  function actionPayload(kind) {
    const freshId = makeRequestId();
    const payload = {
      schema_version: 1,
      request_id: state.requestId || freshId,
      project_id: data.project_id,
      delivery_id: data.delivery_id,
      expected_revision: data.project_revision,
      round_id: current && current.round_id,
      action: kind,
      items: selectedPayloadItems(),
      note: state.note.trim(),
      keep: parseKeep()
    };
    if (kind === "answer-field") {
      payload.field = offer && offer.field;
      payload.value = offer && offer.field === "answers.concept_count" && state.fieldValue !== "" ? Number(state.fieldValue) : state.fieldValue;
    }
    const comparable = JSON.stringify({ ...payload, request_id: "" });
    if (state.requestId && comparable !== state.requestPayload) {
      payload.request_id = freshId;
      state.requestId = freshId;
    }
    state.requestPayload = comparable;
    return payload;
  }

  function makeRequestId() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return `req-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 14)}`;
  }

  function validate(action) {
    if (!interactive) return "这是静态查看页面，不会写入项目。请在聊天里回复本次草稿与继续指令。";
    if (data.readonly) return "旧项目当前只读；先明确迁移后再提交。";
    if (data.stale) return "页面已经过期；刷新后再核对交付。";
    if (state.adopted) return "本交付已提交；请刷新交付，再核对新的状态。";
    if (action === "answer-field") {
      if (!offer || offer.action !== "ask" || !offer.field) return "当前没有待回答的问题。";
      if (state.fieldValue === "") return "先选择或填写一个答案。";
      if (offer.field === "answers.concept_count" && !Number.isInteger(Number(state.fieldValue))) return "概念数量必须是整数。";
      return "";
    }
    if (!current || !data.delivery_id) return "当前没有可提交的轮次。";
    if (action === "request-redo") return "";
    if (state.selected.size === 0) return action === "approve-set" ? "至少保留一张可用的图。" : "先选择一个方向或图位。";
    if (action.startsWith("request-")) return "";
    const bad = [...state.selected].find(id => {
      const item = items.find(entry => entry.id === id);
      return !item || !item.selectable || !item.image_sha256;
    });
    return bad ? `${bad} 当前不可采用；只保留可选图位。` : "";
  }

  async function submit(action) {
    const problem = validate(action);
    if (problem) { setResult(problem, true); return; }
    if (state.submitting) return;
    const payload = actionPayload(action);
    state.requestId = payload.request_id;
    state.submitting = true;
    state.error = "";
    saveDraft();
    render();
    try {
      const response = await apiPost("/api/decision", payload);
      if (!response.ok || !response.body || response.body.committed !== true) {
        throw new Error(response.body?.error || `提交未完成（HTTP ${response.status}）`);
      }
      const receipt = await confirmReceipt(payload.request_id, response.body);
      acceptReceipt(receipt);
    } catch (error) {
      const recovered = await recoverReceipt(payload.request_id);
      if (recovered) acceptReceipt(recovered);
      else setResult(`${error.message}。草稿已保留；可重试。`, true);
    } finally {
      state.submitting = false;
      render();
    }
  }

  function acceptReceipt(receipt) {
    state.result = receipt;
    state.adopted = receipt.committed === true;
    state.action = receipt.action || state.action;
    state.selected.clear();
    state.error = "";
    saveDraft();
  }

  async function api(url, options) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10000);
    try {
      return await fetch(url, { ...options, signal: controller.signal, headers: { Authorization: `Bearer ${token}`, ...(options && options.headers) } });
    } finally { clearTimeout(timer); }
  }

  async function apiPost(url, payload) {
    const response = await api(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    let body = null;
    try { body = await response.json(); } catch (_) {}
    return { ok: response.ok, status: response.status, body };
  }

  async function confirmReceipt(requestId, initial) {
    const deadline = Date.now() + 4500;
    while (Date.now() < deadline) {
      const response = await api(`/api/receipt/${encodeURIComponent(requestId)}`);
      if (response.ok) {
        const receipt = await response.json();
        if (receipt && receipt.committed === true && receipt.request_id === requestId && receipt.delivery_id === data.delivery_id) return receipt;
      }
      await new Promise(resolve => setTimeout(resolve, initial ? 350 : 700));
    }
    throw new Error("写入已返回，但回执读回超时；请保持页面并稍后重试相同请求");
  }

  async function recoverReceipt(requestId) {
    try {
      const response = await api(`/api/receipt/${encodeURIComponent(requestId)}`);
      if (!response.ok) return null;
      const receipt = await response.json();
      return receipt && receipt.committed === true && receipt.request_id === requestId && receipt.delivery_id === data.delivery_id ? receipt : null;
    } catch (_) { return null; }
  }

  async function syncState() {
    if (!interactive) return;
    try {
      const response = await api("/api/state", { method: "GET", headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(`状态接口返回 HTTP ${response.status}`);
      const payload = await response.json();
      const manifest = payload.manifest;
      if (!manifest) throw new Error("状态接口没有返回当前交付 manifest");
      if (payload.project_id !== data.project_id) throw new Error("实时状态不属于本页项目；没有提交任何选择");
      data.live_revision = payload.revision ?? payload.project?.revision ?? manifest.project_revision;
      data.stale = manifest.delivery_id !== data.delivery_id || data.live_revision !== data.project_revision;
      const saved = Object.values(payload.receipts || {}).filter(receipt => receipt.delivery_id === data.delivery_id && receipt.committed === true).pop();
      const requestId = state.requestId || saved?.request_id;
      if (requestId && !state.adopted) {
        const recovered = await recoverReceipt(requestId);
        if (recovered) acceptReceipt(recovered);
      }
      state.syncStatus = "synced";
      state.syncError = "";
    } catch (error) {
      state.syncStatus = "error";
      state.syncError = error.name === "AbortError" ? "读取实时状态超时。" : error.message;
    }
    if (document.activeElement && /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) {
      document.querySelector(".mode").textContent = state.syncStatus === "synced" ? "本机交互 · 已核对项目状态" : "本机交互 · 状态核对失败";
      if (data.stale || state.syncError) document.querySelectorAll(".actions button").forEach(button => { button.disabled = true; });
    } else if (!document.querySelector("dialog[open]") && !state.submitting) render();
  }

  function frame(item, index, label) {
    const box = el("div", { class: "image-frame" });
    if (item.exists && item.path) {
      box.appendChild(el("button", {
        class: "image-open", type: "button", "aria-label": `放大 ${item.title || item.id || "图片"}`,
        onclick: () => openZoomItem(item)
      }, [
        el("img", {
          src: imageSource(item.path), alt: item.title || `${stageLabel[item.stage] || "本轮"} ${item.id}`,
          loading: index >= 0 && index < 3 ? "eager" : "lazy", decoding: "async",
          onerror: () => {
            item.exists = false;
            item.selectable = false;
            item.disabled_reason = "图片解码失败；保留原交付记录，不能采用";
            if (items.includes(item)) state.selected.delete(item.id);
            render();
          }
        })
      ]));
      box.appendChild(el("span", { class: "enlarge", text: "点击放大", "aria-hidden": "true" }));
    } else {
      box.appendChild(el("div", { class: "placeholder" }, [
        el("strong", { text: label || "图片暂不可用" }),
        el("span", { text: item.disabled_reason || "保留方向与提示词；当前没有可采用的图片" })
      ]));
    }
    if (item.id) box.appendChild(el("span", { class: "image-marker", text: item.id }));
    return box;
  }

  function candidate(item, index, historical) {
    const chosen = (state.result?.selected || []).includes(item.id);
    const active = historical ? Boolean(item.picked) : state.adopted ? chosen : state.selected.has(item.id);
    const isSet = state.action === "approve-set";
    const intent = state.action.startsWith("request-");
    const control = el("input", {
      type: isSet || state.action === "request-slots" ? "checkbox" : "radio",
      name: !historical && !isSet && state.action !== "request-slots" ? "selection" : undefined,
      checked: state.action === "select" ? active : active,
      "data-item-id": historical ? undefined : item.id,
      disabled: historical || (!item.selectable && !intent) || state.submitting || data.stale || data.readonly || state.adopted || state.action === "request-redo",
      "aria-label": `${isSet ? "保留" : "选择"} ${item.id}${item.title ? " " + item.title : ""}`,
      onchange: event => setSelection(item.id, event.target.checked)
    });
    return el("article", { class: "candidate" + (active ? " selected" : "") }, [
      el("div", { class: "candidate-head" }, [
        el("label", { class: "choice" }, [
          control,
          el("div", {}, [
            el("h3", { text: `${item.id}${item.title ? " · " + item.title : ""}` }),
            historical ? el("div", { class: "item-status", text: item.picked ? "历史已选 · 只读" : "历史记录 · 只读" }) : state.adopted ? el("div", { class: "item-status", text: chosen ? ((state.result.action || "").startsWith("request-") ? "意向已登记" : "已采用 · 回执已读回") : "本次未采用" }) : intent ? el("div", { class: "item-status", text: active ? "已标记意向，尚未提交" : "仅登记制作意向" }) : item.selectable ? el("div", { class: "item-status", text: active ? "已暂选，尚未提交" : (isSet ? "可保留" : "可选择") }) : el("div", { class: "item-status blocked", text: "不可采用" })
          ])
        ])
      ]),
      frame(item, historical ? index + 3 : index, item.exists ? "" : "图片未生成"),
      el("div", { class: "candidate-body" }, [
        item.idea ? el("p", { text: item.idea }) : null,
        item.method ? el("div", { class: "meta", text: `制作方式：${item.method}` }) : null,
        item.disabled_reason ? el("div", { class: "item-status blocked", text: `原因：${item.disabled_reason}` }) : null,
        item.physics ? el("div", { class: "meta", text: `独立物理审查：${item.physics.verdict === "pass" ? "通过" : "未通过"} · ${item.physics.reason || "见审查记录"}` }) : null,
        item.prompt ? el("details", {}, [el("summary", { text: item.method_code && item.method_code !== "generate" ? "制作说明" : "生图提示词" }), el("pre", { text: item.prompt })]) : null,
        item.sources && item.sources.length ? el("details", {}, [el("summary", { text: "来源图片" }), el("pre", { text: item.sources.join("\n") })]) : null,
        item.image_sha256 ? el("details", {}, [el("summary", { text: "文件与摘要" }), el("pre", { text: `原始路径：${item.original_path || "未记录"}\n显示快照：${item.display_path || item.path || "缺失"}\nSHA-256：${item.image_sha256}` })]) : null
      ])
    ]);
  }

  function imageList(summary, limit) {
    const valid = (summary && summary.items || []).filter(item => item.exists && item.path).slice(0, limit || 4);
    if (!valid.length) return el("div", { class: "empty", text: summary && summary.error ? "快照不可读；没有用当前图片替代。" : "没有可显示的图片快照。" });
    return el("div", { class: "thumbs" }, valid.map((item, index) => frame(item, index, "历史图片缺失")));
  }

  function receiptLine(receipt) {
    if (!receipt) return null;
    if (receipt.committed === true) {
      const label = receipt.action === "answer-field" ? "回答已保存" : (receipt.action || "").startsWith("request-") ? "意向已登记（未生成图片）" : "已采用";
      return el("div", { class: "item-status", text: `${label} · 请求 ${receipt.request_id} · 读回版本 ${receipt.new_revision ?? "—"} · 回聊天回复「继续」` });
    }
    if (receipt.error) return el("div", { class: "item-status blocked", text: receipt.error });
    return null;
  }

  function header() {
    const draft = [...state.selected].length > 0 || state.note.trim() || parseKeep().length;
    const modeText = interactive
      ? (state.syncStatus === "synced" ? "本机交互 · 已核对项目状态" : state.syncStatus === "error" ? "本机交互 · 状态核对失败" : "本机交互 · 正在核对状态")
      : `静态快照 · ${data.snapshot_at ? compactTime(data.snapshot_at) : "时间未记录"}`;
    return [
      el("div", { class: "brandbar" }, [
        el("div", { class: "brand" }, [el("span", { class: "brandmark", text: "J", "aria-hidden": "true" }), el("span", { text: "Jarden" })]),
        el("div", { class: "mode", text: modeText })
      ]),
      el("div", { class: "taskhead" }, [
        el("div", {}, [
          el("div", { class: "eyebrow", text: `${data.answers?.product_form ? "IP 创作" : "创意项目"} / ${data.stage === "done" ? nextStatus(offer && offer.action) : stageLabel[data.stage] || "状态"}` }),
          el("h1", { text: current ? current.heading : (data.title || "Jarden 创作工作台").replace(" · 视觉对比页", "") }),
          el("div", { class: "meta", text: `${data.delivery_id ? data.delivery_id + " · " : ""}${revLabel()}${data.snapshot_at ? " · " + compactTime(data.snapshot_at) : ""}` })
        ]),
        el("span", { class: "state" + (data.stale || data.missing || data.readonly ? " warning" : ""), text: statusLabel(draft), role: "status", "aria-live": "polite" })
      ])
    ];
  }

  function agreements() {
    const decisions = data.decisions || [];
    const bible = data.bible || {};
    const known = Object.entries(bible).filter(([, value]) => value && typeof value === "string").slice(0, 4);
    const answerList = Object.entries(data.answers || {}).filter(([, value]) => value && typeof value !== "object").slice(0, 3);
    if (!decisions.length && !known.length && !answerList.length) return el("div");
    return el("details", { class: "agreements" }, [
      el("summary", {}, [el("span", { class: "eyebrow", text: "已确认约定" }), el("div", { class: "rules" }, [
        ...decisions.slice(0, 2).map(value => el("span", { text: value.rule || safeNote(value) })),
        ...known.slice(0, 2).map(([, value]) => el("span", { text: safeNote(value) }))
      ])]),
      el("div", { class: "rules", style: "margin-top:12px" }, [
        ...decisions.map(value => el("div", {}, [
          el("span", { text: `· ${value.rule || safeNote(value)}` }),
          el("small", { text: ` · ${value.origin || "未记录来源"}${value.evidence?.delivery_id ? ` · ${value.evidence.delivery_id} ${value.evidence.item_id || ""}` : ""}` })
        ])),
        ...known.map(([key, value]) => el("div", { text: `· ${key}: ${value}` })),
        ...answerList.map(([key, value]) => el("div", { text: `· ${key}: ${safeNote(value)}` }))
      ])
    ]);
  }

  function productSection() {
    const contract = data.product_contract;
    return el("details", { class: "agreements contract", open: true }, [
      el("summary", {}, [el("h2", { text: "产品约定" }), el("span", { class: "meta", text: "用途、设计尺度与来源" })]),
      !contract || contract.reason ? el("p", { class: "meta", text: contract?.reason || "本交付未记录产品约定；未知内容待补。" }) : null,
      ...(contract?.subjects || []).map(subject => el("article", { class: "contract-subject" }, [
        el("h3", { text: subject.name }),
        !subject.specs?.length ? el("p", { class: "meta", text: "用途与尺度记录待补" }) : null,
        ...(subject.specs || []).map(spec => el("div", { class: "contract-spec" }, [
          spec.name ? el("strong", { text: spec.name }) : null,
          el("p", { text: `用途：${spec.usage?.text || "未知，待补"}` }),
          el("p", { class: "meta", text: `${originLabel(spec.usage?.origin)} · ${confirmationLabel(spec.usage?.status)}` }),
          el("p", { text: `尺度：${spec.size?.description || "未知，待补"}` }),
          el("p", { class: "meta", text: `量取口径：${spec.size?.measurement_basis || "未知，待补"}` }),
          el("p", { class: "meta", text: `${({ design_target: "设计目标", user_specification: "用户提供规格", measured_sample: "样品测量（依据另行核对）" })[spec.size?.evidence_kind] || "尺度依据待补"} · ${originLabel(spec.size?.origin)} · ${confirmationLabel(spec.size?.status)}` })
        ])),
        ...(subject.views || []).map(asset => el("div", { class: "plan-row" }, [
          asset.image ? frame({ ...asset.image, title: `${subject.name} · ${viewLabel(asset.view)}` }, 3, "参考图待补") : null,
          el("div", {}, [
            el("p", { text: `${viewLabel(asset.view)}设计资料 · ${confirmationLabel(asset.status)}` }),
            el("p", { class: "meta", text: `原始来源：${originLabel(asset.origin)}${asset.confirmation_origin ? ` · 确认来源：${originLabel(asset.confirmation_origin)}` : ""}` }),
            asset.evidence ? el("p", { class: "meta", text: asset.evidence }) : null
          ])
        ]))
      ]))
    ]);
  }

  function planSection() {
    const plan = data.scene_plan;
    return el("details", { class: "agreements contract", open: true }, [
      el("summary", {}, [el("h2", { text: "视角计划" }), el("span", { class: "meta", text: "G0 · 计划依据" })]),
      el("p", { class: "meta", text: plan?.reason || "本交付未记录视角计划；G0 计划不是已实证图像。" }),
      plan?.required_views?.length ? el("p", { class: "meta", text: `计划要求：${plan.required_views.map(viewLabel).join("、")}` }) : null,
      plan?.missing_views?.length ? el("p", { class: "item-status blocked", text: `计划尚缺：${plan.missing_views.map(viewLabel).join("、")}` }) : null,
      ...(plan?.slots || []).map(slot => el("article", { class: "plan-row", "data-plan-slot": slot.id }, [
        slot.image ? frame({ ...slot.image, title: `${slot.id} · 已保留原图` }, 3, "原图待补") : null,
        el("div", {}, [
          el("h3", { text: `${slot.id} · ${viewLabel(slot.view)}${slot.subject ? ` · ${slot.subject}` : slot.subjects?.length ? ` · ${slot.subjects.join("、")}` : ""}` }),
          el("p", { text: `姿态：${slot.pose || "待补"}` }),
          el("p", { text: `取景：${slot.framing || "待补"}` }),
          ...Object.entries(slot.constraint_policy || {}).map(([field, rule]) => el("p", {
            class: "meta",
            text: `${({ framing: "取景", yaw: "方位", elevation: "相机高低", pose: "姿态", scale_anchor: "尺度表达", description: "图位说明" })[field] || field}：${rule.level === "advisory" ? "Agent 建议，非额外硬要求" : "必需约束"} · ${rule.origin === "agent-inferred" ? "设计推定" : rule.origin === "user-confirmed" ? "用户提供" : "参考来源"} · ${rule.evidence || "依据待补"}`
          })),
          el("p", { class: "meta", text: `保持状态：${slot.preserve ? "要求保留原图；" : ""}${slot.image_state === "pending_change" ? "原图待修改" : slot.image_state === "retained" && slot.image?.exists ? "原图已保留，验收见 G1" : "图片待补"}${slot.keep?.length ? ` · 保持项：${slot.keep.join("、")}` : ""}` }),
          slot.image?.original_path ? el("small", { class: "asset-path", text: `原图：${slot.image.original_path}` }) : null
        ])
      ]))
    ]);
  }

  function reviewDetail(result, title) {
    return el("div", { class: "review-detail" }, [
      el("strong", { class: result.status === "pass" ? "item-status" : "item-status blocked", text: `${title} · ${reviewLabel(result.status)}` }),
      el("p", { class: "meta", text: result.reason || "审查依据待补" }),
      ...(result.checks || []).map(check => el("p", { class: "meta", text: `${checkLabel[check.name] || "检查项"}：${reviewLabel(check.verdict)} · ${check.reason || "依据待补"}` }))
    ]);
  }

  function g1Section() {
    const groups = data.g1 || [];
    return el("details", { class: "agreements contract", open: true }, [
      el("summary", {}, [el("h2", { text: "G1 实际图验收" }), el("span", { class: "meta", text: "逐图检查与整套范围" })]),
      el("p", { class: "meta", text: "以下为已绑定原图和适用约定的报告状态。程序核对范围与记录，不能证明实际独立看图或精确测量。" }),
      !groups.length ? el("p", { class: "meta", text: "本交付未记录 G1 实际图验收，待补；不由计划推定通过。" }) : null,
      ...groups.map(group => el("details", { class: "review-group", open: true }, [
        el("summary", { text: `${group.label} · ${group.stage === "package" || group.stage === "done" ? "完整实际发布集合" : "本批实际图片"} · ${reviewLabel(group.sequence?.status)}` }),
        ...(group.items || []).map(item => el("div", {}, [
          reviewDetail(item, item.slot_id || "图位待绑定"),
          el("small", { class: "asset-path", text: `原图：${item.original_path || "待补"}${item.source_path && item.source_path !== item.original_path ? ` · 制作源图：${item.source_path}` : ""}` })
        ])),
        reviewDetail(group.sequence || {}, "整套覆盖、保持与排列")
      ]))
    ]);
  }

  function notices() {
    const parts = [];
    if (!interactive) parts.push(el("div", { class: "notice" }, [
      el("strong", { text: "只读快照。" }),
      el("span", { text: " 选择与备注只保存在本机草稿；不会写入项目。请在聊天里回复编号或明确指令继续。" })
    ]));
    if (data.readonly) parts.push(el("div", { class: "notice" }, [el("strong", { text: "旧项目只读。" }), el("span", { text: " 可用 build_gallery 查看；明确迁移后才能提交。" })]));
    if (data.stale) parts.push(el("div", { class: "notice" }, [
      el("strong", { text: state.adopted ? "这份交付已有真实回执。" : "这份交付已过期。" }), el("span", { text: ` 当前业务版本 ${data.live_revision}，${state.adopted ? "回聊天回复「继续」；本页保留已提交记录。" : "请从最近交付重新打开。"}` }),
      interactive && !state.adopted ? el("button", { type: "button", style: "margin-left:12px", text: "打开当前入口", onclick: () => { location.href = `/gallery.html#token=${encodeURIComponent(token)}`; } }) : null
    ]));
    if (state.syncError) parts.push(el("div", { class: "notice error" }, [el("strong", { text: "实时状态未确认。" }), el("span", { text: ` ${state.syncError} 提交已暂时停用，草稿仍会保留。` })]));
    if (data.missing) parts.push(el("div", { class: "notice" }, [el("strong", { text: `${data.missing} 张图片缺失。` }), el("span", { text: " 可以查看方向和提示词，缺图项不能采用。" })]));
    return parts;
  }

  function question() {
    if (!offer || offer.action !== "ask") return null;
    if (state.fieldValue === "" && questionChoices.length && offer.field !== "answers.subject_scope") state.fieldValue = offer.recommended !== undefined ? offer.recommended : questionChoices[0].value;
    const rows = questionChoices.length ? questionChoices.map(choice => el("label", {}, [
      el("input", {
        type: "radio", name: "answer", value: JSON.stringify(choice.value),
        checked: JSON.stringify(state.fieldValue) === JSON.stringify(choice.value),
        disabled: state.submitting || state.adopted,
        onchange: () => { state.fieldValue = choice.value; state.requestId = null; state.requestPayload = ""; saveDraft(); }
      }),
      el("span", {}, [el("strong", { text: choice.label }), choice.value === offer.recommended ? el("strong", { text: "（建议，可改选）" }) : null, (questionField.intros?.[choice.value] || choice.intro) ? el("small", { style: "display:block", text: questionField.intros?.[choice.value] || choice.intro }) : null])
    ])) : [el("label", { class: "field", text: "填写这个答案" }, [
      el("input", {
        type: offer.field === "answers.concept_count" ? "number" : "text",
        value: state.fieldValue, placeholder: questionField.placeholder || "写下你的想法，也可以在聊天里补充说明",
        disabled: state.submitting || state.adopted,
        oninput: event => { state.fieldValue = event.target.value; state.requestId = null; state.requestPayload = ""; saveDraft(); }
      })
    ])];
    const control = el("div", { class: "question" }, [
      el("div", { class: "eyebrow", text: "还差一个确认" }),
      el("h2", { text: questionField.label }),
      el("p", { class: "meta", text: questionField.intro }),
      ...rows
    ]);
    return control;
  }

  function sidebar() {
    const selectedCount = [...state.selected].filter(id => items.find(item => item.id === id && item.selectable)).length;
    const canChoose = current && !data.readonly && !data.stale && !state.adopted && (!interactive || state.syncStatus === "synced");
    const canWrite = interactive && data.delivery_id && !data.readonly && !data.stale && !state.adopted && state.syncStatus === "synced";
    const choiceAvailable = items.some(item => item.selectable);
    const intent = state.action.startsWith("request-");
    const actionLabels = {
      select: "采用一个方向", "approve-set": "保留勾选图位",
      "request-variation": "沿所选方向补变化", "request-slots": "指定图位补图", "request-redo": "整轮重新制作"
    };
    return el("aside", { class: "sidebar", "aria-label": "本轮选择" }, [
      el("div", { class: "eyebrow", text: current && current.kind === "set" ? "逐图保留" : "本轮选择" }),
      el("h2", { text: canChoose ? (current.kind === "set" ? "保留这一组" : "选一个方向") : (current ? "查看本轮状态" : "确认项目需求") }),
      el("div", { class: "draft-status", text: state.adopted ? "已写入并读回真实回执" : selectedCount ? `${selectedCount} 项暂选 · 尚未提交` : "尚未选择" }),
      current && !state.adopted ? el("label", { class: "field", text: "这次想做什么" }, [
        el("select", { id: "decision-action", "aria-label": "这次想做什么", disabled: state.submitting || data.stale || data.readonly, onchange: event => setAction(event.target.value) },
          [current.kind === "set" ? "approve-set" : "select", "request-variation", "request-slots", "request-redo"].map(action => el("option", { value: action, selected: state.action === action, text: actionLabels[action] })))
      ]) : null,
      el("div", { class: "submit-summary", id: "submit-summary", text: selectionSummary(selectedCount) }),
      el("div", { class: "actions" }, [
        canWrite && current && !intent && current.kind === "set" ? el("button", { class: "primary", type: "button", disabled: state.submitting || !choiceAvailable || !state.selected.size, onclick: () => submit("approve-set"), text: !choiceAvailable ? "暂无可保留图位" : !state.selected.size ? "先勾选要保留的图" : state.submitting ? "正在确认…" : "确认保留未选者标为跳过" }) : null,
        canWrite && current && !intent && current.kind !== "set" ? el("button", { class: "primary", type: "button", disabled: state.submitting || !choiceAvailable || !state.selected.size, onclick: () => submit("select"), text: !choiceAvailable ? "暂无可采用方向" : !state.selected.size ? "先选择一个方向" : state.submitting ? "正在提交…" : "确认采用所选方向" }) : null,
        canWrite && current && intent ? el("button", { class: "primary", type: "button", disabled: state.submitting || (state.action !== "request-redo" && !state.selected.size), onclick: () => submit(state.action), text: state.submitting ? "正在登记…" : "确认登记意向（不会生图）" }) : null,
        canWrite && offer && offer.action === "ask" ? el("button", { class: "primary", type: "button", disabled: state.submitting, onclick: () => submit("answer-field"), text: state.submitting ? "正在保存…" : "保存这个答案" }) : null
      ]),
      el("label", { class: "field", text: "给下一轮的备注" }, [el("textarea", {
        value: state.note, placeholder: current && current.kind === "set" ? "例如：第 3 张换室外光，其余保持" : "例如：采用 B，保留眼睛和配色",
        maxlength: 5000,
        disabled: state.submitting, oninput: event => { state.note = event.target.value; state.requestId = null; state.requestPayload = ""; saveDraft(); updateSummary(); }
      })]),
      el("label", { class: "field", text: "保持项（逗号分隔）" }, [el("input", {
        type: "text", value: state.keep, placeholder: "例如：左耳缺口、圆眼睛",
        maxlength: 2000,
        disabled: state.submitting, oninput: event => { state.keep = event.target.value; state.requestId = null; state.requestPayload = ""; saveDraft(); updateSummary(); }
      })]),
      el("div", { class: "hint", text: "提交后必须读回真实回执才显示「已采用」。页面不会调用生图或下一阶段。" }),
      state.result || state.error ? el("div", { id: "result", class: "result" + (state.error ? " notice error" : ""), role: "status", "aria-live": "polite" }, [
        state.error ? el("span", { text: state.error }) : null,
        receiptLine(state.result)
      ]) : null,
      el("div", { class: "hint", id: "save-state", text: "草稿自动保存在此浏览器；提交失败不会清空。" }),
      el("div", { class: "secondary-actions", style: "margin-top:12px" }, [
        el("button", { type: "button", onclick: exportIntent, text: "导出回复草稿" })
      ])
    ]);
  }

  function selectionSummary(count) {
    if (state.adopted) return "本交付已提交；回聊天回复「继续」进入下一步。";
    if (offer && offer.action === "ask" && !current) return "只回答当前一个缺失问题；不会伪造图片或进入生成。";
    if (!current) return "当前交付没有需要提交的轮次。";
    if (state.action === "request-redo") return "登记整轮重做意向，不撤销原图，不调用生图。回聊天继续执行。";
    if (state.action === "request-slots") return state.selected.size ? `将请求补齐或修改 ${[...state.selected].join("、")}；其余图位保留。` : "勾选需要补齐或修改的图位；只登记意向。";
    if (state.action === "request-variation") return state.selected.size ? `将沿 ${[...state.selected].join("、")} 登记补变化意向；尚未生成新图。` : "先选一个方向作为后续制作意向；待审图仍不能采用。";
    if (!items.some(item => item.selectable)) return "本轮没有可采用项；请先查看待审、缺图或过期原因。";
    if (current.kind === "set") return count ? `${count} 张保留；未勾选图位提交后标为跳过。` : "勾选要保留的图位；未选者提交后标为跳过。";
    if (offer && offer.action === "ask") return "保存当前唯一缺失问题；不会重复提问。";
    return count ? `将提交 ${[...state.selected].join("、")}，其他方向不采用。` : "先选一个方向，再确认采用。";
  }

  function updateSummary() {
    const target = document.getElementById("submit-summary");
    if (target) target.textContent = selectionSummary([...state.selected].length);
  }

  function previousSection() {
    const previous = data.previous_delivery;
    if (!previous) return el("section", { class: "previous" }, [el("h2", { text: "上次交付" }), el("div", { class: "empty", text: "这是本项目第一次保存可见交付。" })]);
    const receipts = previous.receipts || [];
    const lastReceipt = receipts.filter(receipt => receipt.committed === true).pop();
    return el("section", { class: "previous", "aria-labelledby": "previous-title" }, [
      el("div", { class: "sectionhead" }, [
        el("div", {}, [el("h2", { id: "previous-title", text: "上次交付" }), el("span", { class: "meta", text: `${previous.delivery_id} · ${stageLabel[previous.stage] || previous.stage || "历史"} · ${compactTime(previous.created_at)}` })]),
        receipts.length ? el("span", { class: "state", text: "已有选择回执" }) : null
      ]),
      el("div", { class: "previous-content" }, [
        imageList(previous, 3),
        el("div", { class: "previous-copy" }, [
          el("strong", { text: previous.receipts?.length ? "上次选择已读回" : "上次状态" }),
          lastReceipt ? receiptLine(lastReceipt) : el("p", { text: "这是最近一次真实呈现给用户的交付；不是 rounds 数组里的猜测。" }),
          previous.feedback?.length ? el("p", { text: `上次意见：${previous.feedback.map(value => value.raw).filter(Boolean).join("；")}` }) : null
        ])
      ])
    ]);
  }

  function comparisonSection() {
    const baseline = data.comparison;
    if (!baseline) return el("section", { class: "comparison" }, [
      el("h2", { text: "与比较基线对照" }),
      el("div", { class: "empty", text: "本轮首次制作；没有同主体、同阶段、同图位的有效比较基线。" })
    ]);
    const basis = baseline.basis || {};
    const baselineItems = baseline.items || [];
    const pairs = items.map(item => {
      const match = (basis.matches || []).find(value => value.item_id === item.id);
      return { item, before: match ? baselineItems.find(value => match.baseline_snapshot_source ? value.snapshot_source === match.baseline_snapshot_source : value.id === match.baseline_item_id) : null };
    }).filter(pair => pair.before);
    if (!pairs.length) return el("section", { class: "comparison" }, [
      el("h2", { text: "与比较基线对照" }),
      el("div", { class: "empty", text: `找到 ${baseline.delivery_id}，但没有同图位的可比值；不强行并排。` })
    ]);
    return el("section", { class: "comparison" }, [
      el("div", { class: "sectionhead" }, [
        el("div", {}, [el("h2", { text: "与比较基线对照" }), el("span", { class: "meta", text: `${baseline.delivery_id} · ${basis.reason || "同主体、同阶段、同图位"}` })]),
        el("span", { class: "state", text: "同一视角比较" })
      ]),
      ...pairs.map(pair => el("div", { class: "comparison-grid" }, [
        el("figure", {}, [el("figcaption", { text: `基线 · ${pair.before ? pair.before.id + " " + (pair.before.title || "") : "无可比图位"}` }), pair.before ? frame(pair.before, 0, "基线缺失") : el("div", { class: "placeholder" }, [el("strong", { text: "基线无同图位" })])]),
        el("figure", {}, [el("figcaption", { text: `本轮 · ${pair.item.id} ${pair.item.title || ""}` }), frame(pair.item, 0, "本轮缺图")])
      ]))
    ]);
  }

  function feedbackSection() {
    const currentFeedback = (current && current.feedback) || data.feedback || [];
    const keep = (current && current.keep) || parseKeep();
    if (!currentFeedback.length && !keep.length && !(current && current.note)) return null;
    return el("section", { class: "feedback" }, [
      el("h2", { text: "本轮修改与保持" }),
      current && current.note ? el("p", { text: `原始意见：${current.note}` }) : null,
      el("p", { class: "muted", text: "以下只记录已保存的反馈；具体执行仍由 Agent 依据当前授权继续。" }),
      currentFeedback.length ? el("ul", {}, currentFeedback.map(value => el("li", { text: `${value.raw || safeNote(value)}${value.keep?.length ? ` · 保持：${value.keep.join("、")}` : ""}` }))) : null,
      keep.length ? el("p", { text: `保持项：${keep.join("、")}` }) : null
    ]);
  }

  function archiveSection() {
    const history = data.delivery_history || [];
    const early = data.history || [];
    return el("details", { class: "archive" }, [
      el("summary", { text: `历史与证据（${history.length} 个交付，${early.length} 个旧轮次）` }),
      history.length ? el("ul", { class: "archive-list" }, history.slice(0, 20).map(value => el("li", {}, [el("a", { href: imageSource(value.path || "#"), text: `${value.delivery_id} · ${value.round_id || value.stage || "交付"} · ${compactTime(value.created_at)}` })]))) : null,
      early.length ? el("div", { style: "margin-top:18px" }, early.map(value => el("div", {}, [el("h3", { text: value.heading }), el("div", { class: "candidates" }, (value.items || []).map((item, index) => candidate(item, index, true)))]))) : null,
      data.post ? postSection() : null
    ]);
  }

  function postSection() {
    return el("section", { class: "post" }, [
      el("h2", { text: "发帖交付" }),
      el("div", { class: "post-grid" }, (data.post.images || []).map((image, index) => {
        image.exists = image.exists !== false && Boolean(image.path);
        image.id = String(index + 1).padStart(2, "0");
        return el("figure", {}, [frame(image, index, "发布图缺失"), el("figcaption", { text: image.role || "" })]);
      })),
      el("div", { class: "post-copy" }, [el("h3", { text: data.post.title || "" }), el("p", { text: data.post.body || "" }), el("p", { text: (data.post.tags || []).join(" ") })])
    ]);
  }

  function picksSection() {
    const picks = data.picks || [];
    if (!picks.length) return null;
    return el("details", { class: "archive" }, [
      el("summary", { text: `已选资产（${picks.length}）` }),
      el("div", { class: "candidates" }, picks.map((item, index) => candidate(item, index, true)))
    ]);
  }

  function workarea() {
    if (!current && !data.post) {
      return el("section", { class: "workarea", id: "current" }, [
        question() || el("div", { class: "empty" }, [el("h2", { text: "当前没有待查看的视觉轮次" }), el("p", { text: data.next?.say || "完成下一项确认后，新的可查看交付会出现在这里。" })]),
        previousSection(), picksSection(), archiveSection()
      ]);
    }
    if (!current) return el("section", { class: "workarea", id: "current" }, [data.post ? postSection() : el("div", { class: "empty", text: "当前交付只有状态。" }), previousSection(), picksSection(), archiveSection()]);
    return el("section", { class: "workarea", id: "current", "aria-labelledby": "current-title" }, [
      question(),
      el("div", { class: "sectionhead", style: "margin-top:18px" }, [
        el("div", {}, [el("h2", { id: "current-title", text: current.kind === "set" ? "本轮交付图位" : "本轮方向" }), el("span", { class: "meta", text: current.hint || "先看图，再决定；技术证据按需展开。" })]),
        el("button", { type: "button", onclick: exportIntent, text: "导出回复草稿" })
      ]),
      el("div", { class: "candidates" }, items.map((item, index) => candidate(item, index))),
      previousSection(),
      comparisonSection(),
      feedbackSection(),
      picksSection(),
      archiveSection()
    ]);
  }

  function exportIntent() {
    const selected = [...state.selected];
    const lines = [
      `项目：${data.title || data.project_id || "Jarden"}`,
      `交付：${data.delivery_id || "静态页面"}`,
      `动作：${offer && offer.action === "ask" ? "回答当前问题" : state.action}`,
      offer && offer.action === "ask" ? `回答（${questionField.label}）：${questionChoices.find(choice => JSON.stringify(choice.value) === JSON.stringify(state.fieldValue))?.label || safeNote(state.fieldValue) || "尚未选择"}` : null,
      `选择：${selected.length ? selected.join("、") : "尚未选择"}`,
      `备注：${state.note || "—"}`,
      `保持：${parseKeep().length ? parseKeep().join("、") : "—"}`,
      "请按上面的选择继续。这只是回复草稿，尚未提交。"
    ].filter(Boolean);
    const modal = el("dialog", { id: "export-dialog" }, [
      el("div", { class: "dialog-toolbar" }, [el("strong", { text: "回复草稿（未提交）" }), el("button", { type: "button", onclick: () => modal.close(), text: "关闭" })]),
      el("div", { class: "export-content" }, [
        el("p", { class: "meta", text: "复制后回到聊天发送。静态页面没有服务，不会写入项目。" }),
        el("textarea", { readonly: true, value: lines.join("\n") })
      ])
    ]);
    app.appendChild(modal);
    modal.showModal();
  }

  function maintenanceRequest(plan) {
    const maintenance = data.maintenance || {};
    const message = plan
      ? `请为 jarden-ip-visual ${maintenance.latest} 生成更新计划，核对变化与备份。此请求不授权安装。`
      : "请检查 jarden-ip-visual 是否有更新，只检查，不安装。";
    const modal = el("dialog", { id: "maintenance-dialog", "aria-label": "维护请求（尚未执行）" }, [
      el("div", { class: "dialog-toolbar" }, [
        el("strong", { text: "维护请求（尚未执行）" }),
        el("button", { type: "button", text: "关闭", onclick: () => modal.close() })
      ]),
      el("div", { class: "export-content" }, [
        el("p", { text: "复制后回到对话发送。页面不会下载或安装更新，也不会改变当前创作。" }),
        el("textarea", { readonly: true, value: message })
      ])
    ]);
    modal.addEventListener("close", () => modal.remove());
    app.appendChild(modal);
    modal.showModal();
  }

  function maintenanceSection() {
    const maintenance = data.maintenance || {};
    const descriptions = {
      unchecked: "本轮快照没有版本检查结果；离线或关闭检查时可继续创作。",
      unavailable: "暂无法读取版本信息；当前创作不受影响。",
      failed: "暂无法检查更新，不能确认是否为最新版本。",
      stale: "版本检查结果已过期，可重新检查。",
      available: `有新版本 ${maintenance.latest}，可先查看变化与更新计划。`,
      current: "最近一次检查未发现更高的正式版本。"
    };
    return el("details", { class: "archive maintenance", id: "maintenance" }, [
      el("summary", { text: `版本与更新 · ${maintenance.current || "版本待核对"}` }),
      el("p", { text: descriptions[maintenance.status] || descriptions.unchecked }),
      el("p", { class: "meta", text: `结果随本页快照保存${maintenance.checked_at ? " · 检查于 " + compactTime(maintenance.checked_at * 1000) : ""}。检查与计划均不代表安装授权。` }),
      el("div", { class: "dialog-tools" }, [
        el("button", { type: "button", text: "检查更新", onclick: () => maintenanceRequest(false) }),
        el("button", { type: "button", text: "查看更新计划", disabled: maintenance.status !== "available", onclick: () => maintenanceRequest(true) })
      ])
    ]);
  }

  function openZoomItem(item) {
    state.zoomItem = item;
    state.lastFocused = document.activeElement;
    if (!item) return;
    const modal = el("dialog", { id: "zoom-dialog", "aria-label": `${item.id} 图片放大` }, [
      el("div", { class: "dialog-toolbar" }, [
      el("div", {}, [el("strong", { text: `${item.id} · ${item.title || "本轮图片"}` }), el("div", { class: "meta", text: "使用右侧按钮切换浅底、深底与棋盘" })]),
        el("div", { class: "dialog-tools" }, [
          el("button", { type: "button", onclick: () => changeBg(modal), text: "浅底 / 深底 / 棋盘" }),
          el("button", { type: "button", onclick: () => modal.close(), text: "关闭 ✕" })
        ])
      ]),
      el("div", { class: "zoom-stage", "data-background": state.zoomBackground }, [el("img", { src: imageSource(item.path), alt: item.title || item.id })]),
      el("div", { class: "dialog-caption", text: `${item.method || ""}${item.image_sha256 ? " · 图片摘要 " + item.image_sha256.slice(0, 12) : ""}` })
    ]);
    app.appendChild(modal);
    modal.addEventListener("click", event => { if (event.target === modal) modal.close(); });
    modal.addEventListener("close", () => { modal.remove(); state.zoomItem = null; if (state.lastFocused) state.lastFocused.focus(); });
    modal.showModal();
  }

  function changeBg(modal) {
    const backgrounds = ["light", "dark", "checker"];
    state.zoomBackground = backgrounds[(backgrounds.indexOf(state.zoomBackground) + 1) % backgrounds.length];
    const stage = modal.querySelector(".zoom-stage");
    if (stage) stage.dataset.background = state.zoomBackground;
  }

  function render() {
    const focused = document.activeElement;
    const savedSelection = focused && focused.getAttribute && focused.getAttribute("data-item-id");
    app.replaceChildren(
      ...header(),
      ...notices(),
      agreements(),
      productSection(),
      planSection(),
      g1Section(),
      el("div", { class: "workspace" }, [workarea(), sidebar()]),
      maintenanceSection()
    );
    if (savedSelection) {
      document.querySelector(`input[data-item-id="${CSS.escape(savedSelection)}"]`)?.focus({ preventScroll: true });
    }
  }

  document.addEventListener("keydown", event => {
    if (event.key === "Escape") {
      const dialogs = [...document.querySelectorAll("dialog[open]")];
      if (dialogs.length) dialogs[dialogs.length - 1].close();
    }
  });
  render();
  syncState();
})();
