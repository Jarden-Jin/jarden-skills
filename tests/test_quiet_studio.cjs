const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const assets = path.join(root, "skills/jarden-ip-visual/assets");
const source = fs.readFileSync(path.join(assets, "gallery.js"), "utf8");
const catalog = JSON.parse(fs.readFileSync(path.join(assets, "asset-catalog.json"), "utf8"));
const walk = node => [node, ...node.children.flatMap(walk)];
const content = node => [node.textContent, ...node.children.map(content)].join(" ");
const settle = async () => { for (let turn = 0; turn < 40; turn++) await Promise.resolve(); };

function matches(node, selector) {
  const attribute = selector.match(/\[([^=\]]+)(?:="([^"]*)")?\]/);
  const tag = selector.match(/^[a-z]+/i)?.[0];
  const classNames = [...selector.matchAll(/\.([\w-]+)/g)].map(match => match[1]);
  const identifier = selector.match(/#([\w-]+)/)?.[1];
  const attributeMatches = !attribute || (attribute[2] === undefined ? node.attributes[attribute[1]] !== undefined : node.attributes[attribute[1]] === attribute[2]);
  return (!tag || node.tagName === tag.toUpperCase()) && classNames.every(className => String(node.className || "").split(/\s+/).includes(className)) && (!identifier || node.attributes.id === identifier) && attributeMatches;
}

function environment(input, options = {}) {
  let document;
  class Element {
    constructor(tag) {
      this.tagName = tag.toUpperCase();
      this.children = [];
      this.attributes = {};
      this.listeners = {};
      this.textContent = "";
      this.parentElement = null;
      this.disabled = false;
    }
    setAttribute(name, value) { this.attributes[name] = value; if (name === "disabled") this.disabled = true; }
    getAttribute(name) { return this.attributes[name]; }
    removeAttribute(name) { delete this.attributes[name]; }
    addEventListener(name, handler) { this.listeners[name] = handler; }
    appendChild(child) { child.parentElement = this; this.children.push(child); return child; }
    replaceChildren(...children) { this.children.forEach(child => { child.parentElement = null; }); this.children = []; children.forEach(child => this.appendChild(child)); }
    remove() { if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(child => child !== this); this.parentElement = null; }
    get isConnected() { return this === app || Boolean(this.parentElement?.isConnected); }
    focus() { document.activeElement = this; }
    scrollIntoView() {}
    querySelectorAll(selector) { return walk(this).filter(node => selector.split(",").some(part => matches(node, part.trim()))); }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    showModal() { this.setAttribute("open", ""); this.querySelector("button")?.focus(); }
    close() { delete this.attributes.open; this.listeners.close?.(); }
  }
  const data = structuredClone(input);
  const app = new Element("div");
  const stored = options.storage || new Map();
  const posts = [];
  const requests = [];
  const navigations = [];
  const reloads = [];
  const replacements = [];
  const timers = new Map();
  const documentListeners = new Map();
  let timerSequence = 0;
  let livePayload = options.livePayload || null;
  let holdSubmission = null;
  let receipt = null;
  document = {
    activeElement: null,
    hidden: false,
    createElement: tag => new Element(tag),
    addEventListener: (name, handler) => documentListeners.set(name, handler),
    getElementById: identifier => identifier === "gallery-data" ? { textContent: JSON.stringify(data) } : identifier === "app" ? app : walk(app).find(node => node.attributes.id === identifier),
    querySelector: selector => app.querySelector(selector),
    querySelectorAll: selector => app.querySelectorAll(selector),
  };
  const reply = (body, status = 200) => ({ ok: status < 400, status, json: async () => body });
  const location = {
    hash: options.interactive ? "#token=ui-fixture-token" : "",
    protocol: options.interactive ? "http:" : "file:",
    pathname: options.pathname || "/gallery.html",
    replace: target => { replacements.push(target); navigations.push(target); },
    reload: () => { const target = location.pathname + location.hash; reloads.push(target); navigations.push(target); }
  };
  const context = {
    document, CSS: { escape: value => value },
    location,
    localStorage: { getItem: key => stored.get(key) || null, setItem: (key, value) => stored.set(key, value) },
    URLSearchParams, Intl, Date, AbortController,
    setTimeout: (callback, delay) => { const identifier = ++timerSequence; timers.set(identifier, { callback, delay }); return identifier; },
    clearTimeout: identifier => timers.delete(identifier),
    requestAnimationFrame: callback => callback(),
    crypto: { randomUUID: () => `request-${posts.length}` },
    fetch: async (url, request) => {
      requests.push({ url, authorization: request?.headers?.Authorization });
      if (url === "/api/state") {
        if (options.syncFailure) throw new Error("状态不可读");
        return reply(livePayload || { project_id: data.project_id, revision: options.liveRevision ?? data.project_revision, manifest: data, receipts: {} });
      }
      if (url === "/api/decision") {
        const payload = JSON.parse(request.body);
        posts.push(payload);
        if (options.deferSubmission) await new Promise(resolve => { holdSubmission = resolve; });
        if (options.submitFailure) return reply({ error: "拒绝写入" }, 409);
        receipt = { committed: true, action: payload.action, request_id: payload.request_id, delivery_id: payload.delivery_id, new_revision: data.project_revision + 1, selected: options.assetReceipt ? [] : payload.items.map(item => item.id), asset_result: options.assetReceipt ? { instance_id: payload.instance_id } : undefined };
        return reply(receipt);
      }
      if (url.startsWith("/api/receipt/")) return receipt ? reply(receipt) : reply({}, 404);
      throw new Error(`Unexpected request: ${url}`);
    }
  };
  context.window = context;
  vm.runInNewContext(source, context, { timeout: 2000 });
  return {
    app, document, stored, posts, requests, timers, navigations, reloads, replacements, location,
    setLive: value => { livePayload = value; },
    releaseSubmission: () => holdSubmission?.(),
    poll: async () => {
      const timer = [...timers].find(([, value]) => value.delay === 2500);
      assert.ok(timer, "missing live sync timer");
      timers.delete(timer[0]);
      await timer[1].callback();
      await settle();
    },
    visibility: async hidden => { document.hidden = hidden; documentListeners.get("visibilitychange")?.(); await settle(); },
    nodes: selector => app.querySelectorAll(selector),
    text: () => content(app),
    button: label => walk(app).find(node => node.tagName === "BUTTON" && (content(node).trim() === label || node.children[0]?.textContent === label)),
    click: async button => { assert.ok(button, "missing button"); assert.equal(button.disabled, false); button.focus(); await button.listeners.click?.({ target: button }); await settle(); },
    change: async (selector, value, checked = true) => {
      const node = document.querySelector(selector);
      assert.ok(node, `missing ${selector}`);
      assert.equal(node.disabled, false, `${selector} is disabled`);
      node.focus();
      node.value = value;
      node.checked = checked;
      await node.listeners.change?.({ target: node });
      await settle();
    },
    input: (identifier, value) => {
      const node = document.getElementById(identifier);
      assert.ok(node, `missing ${identifier}`);
      node.focus();
      node.value = value;
      node.listeners.input({ target: node });
    }
  };
}

function baseData(overrides = {}) {
  return {
    title: "山间小兔", project_id: "quiet-studio-fixture", delivery_id: "D0042", project_revision: 8,
    stage: "material", snapshot_at: "2026-11-14T12:00:00Z", next: { action: "await-choice" },
    current: {
      kind: "alternatives", heading: "比较材质方向", round_id: "R002-material", hint: "只比较材质，造型继续保留。",
      items: [
        { id: "A", title: "细密短绒", idea: "轮廓更利落", path: "a.png", exists: true, selectable: true, image_sha256: "a".repeat(64) },
        { id: "B", title: "松软长绒", idea: "更轻盈蓬松", path: "b.png", exists: true, selectable: true, image_sha256: "b".repeat(64) }
      ]
    },
    ...overrides
  };
}

function workStatus(overrides = {}) {
  return {
    schema_version: 1, project_id: "quiet-studio-fixture", revision: 8,
    phase_id: "confirm", status: "waiting", title: "等待确认材质", detail: "比较本轮两个方向后确认。",
    actor: "user", phases: [
      { id: "intake", label: "理解需求", status: "done" },
      { id: "scope", label: "选择资产", status: "done" },
      { id: "create", label: "设计制作", status: "done" },
      { id: "review", label: "质量审查", status: "done" },
      { id: "confirm", label: "确认方案", status: "current" },
      { id: "deliver", label: "整理交付", status: "pending" }
    ], tasks: [], blockers: [], recent_events: [], counts: { selected: 2, approved: 0, pending: 2 },
    ...overrides
  };
}

async function run() {
  const staticPage = environment(baseData());
  await settle();
  assert.equal(staticPage.nodes(".candidate").length, 2);
  assert.match(staticPage.text(), /山间小兔/);
  assert.equal(staticPage.nodes("[data-submit-action]").length, 0);
  assert.equal(staticPage.requests.length, 0);
  assert.equal(staticPage.timers.size, 0);
  const livePreview = environment(baseData({ current: null, live_photo: { jobs: [{
    id: "live-fixture", status: "macos-verified", source: { path: "source.png" }, video: { path: "motion.mp4" },
    package: { photo: { path: "live.jpg" }, video: { path: "live.mov" }, verification: { passed_local_validation: true } },
    acceptance: { "macos-photos": { live_badge: true, playback_confirmed: true } }, quality: "draft", watermark: "preserved"
  }] } }));
  await settle();
  assert.equal(livePreview.nodes("video").length, 1);
  assert.equal(livePreview.nodes(".live-checkpoints")[0].children.filter(node => node.className === "verified").length, 2);
  assert.match(livePreview.text(), /Mac Photos 已验收/);
  assert.match(livePreview.text(), /网页播放器只预览视频/);
  assert.match(livePreview.text(), /iPhone Photos/);
  assert.match(livePreview.text(), /待验收/);
  assert.equal(livePreview.posts.length, 0);
  const corruptDescriptor = { path: null, display_path: "corrupt-resource.jpg", exists: false };
  const corruptLive = environment(baseData({ current: null, live_photo: { jobs: [{
    id: "live-corrupt", status: "macos-verified", source: corruptDescriptor, video: { ...corruptDescriptor, display_path: "corrupt-video.mp4" },
    package: { photo: corruptDescriptor, video: { ...corruptDescriptor, display_path: "corrupt-movie.mov" }, archive: { ...corruptDescriptor, display_path: "corrupt-live.zip" }, verification: { passed_local_validation: true } },
    acceptance: { "macos-photos": { live_badge: true, playback_confirmed: true } }
  }] } }));
  assert.equal(corruptLive.nodes("video").length, 0, "corrupt snapshots must not restore display_path into video URLs");
  assert.equal(corruptLive.nodes(".live-checkpoints")[0].children.filter(node => node.className === "verified").length, 0);
  assert.match(corruptLive.text(), /资源不可用，需重验/);
  assert.ok(!corruptLive.nodes("a").some(node => String(node.getAttribute("href")).includes("corrupt-")));
  assert.equal(staticPage.document.getElementById("technical-evidence").attributes.open, undefined);
  await staticPage.change('input[data-item-id="B"]', "B");
  staticPage.input("decision-note", "脸部再干净一些");
  staticPage.input("decision-keep", "圆眼睛、长耳朵");
  const opener = staticPage.button("导出回复草稿");
  await staticPage.click(opener);
  assert.equal(staticPage.nodes("dialog").length, 1);
  assert.match(staticPage.nodes("textarea").at(-1).value, /选择：B/);
  assert.match(staticPage.nodes("textarea").at(-1).value, /尚未提交/);
  staticPage.nodes("dialog")[0].close();
  assert.equal(staticPage.nodes("dialog").length, 0);
  assert.equal(staticPage.document.activeElement, opener);
  const restored = environment(baseData(), { storage: staticPage.stored });
  assert.equal(restored.nodes('input[data-item-id="B"]')[0].checked, true);
  assert.equal(restored.document.getElementById("decision-note").value, "脸部再干净一些");
  assert.equal(restored.nodes(".keep-tags")[0].children.length, 2);

  const interactive = environment(baseData(), { interactive: true });
  await settle();
  await interactive.change('input[data-item-id="B"]', "B");
  await interactive.click(interactive.button("按 B 继续"));
  assert.equal(interactive.posts.length, 1);
  assert.equal(interactive.posts[0].action, "select");
  assert.equal(interactive.posts[0].expected_revision, 8);
  assert.equal(interactive.posts[0].items[0].sha256, "b".repeat(64));
  assert.ok(interactive.requests.some(request => request.url.startsWith("/api/receipt/")));
  assert.ok(interactive.requests.every(request => request.authorization === "Bearer ui-fixture-token"));
  assert.match(interactive.text(), /回执已读回/);

  const failed = environment(baseData(), { interactive: true, submitFailure: true });
  await settle();
  await failed.change('input[data-item-id="A"]', "A");
  failed.input("decision-note", "保留这个草稿");
  await failed.click(failed.button("按 A 继续"));
  assert.match(failed.text(), /拒绝写入/);
  assert.equal(failed.document.getElementById("decision-note").value, "保留这个草稿");
  assert.equal(failed.nodes('input[data-item-id="A"]')[0].checked, true);

  for (const options of [{ liveRevision: 9 }, { syncFailure: true }]) {
    const locked = environment(baseData(), { interactive: true, ...options });
    await settle();
    assert.ok(locked.nodes("[data-submit-action]").every(button => button.disabled));
  }
  const readonly = environment(baseData({ readonly: true }), { interactive: true });
  await settle();
  assert.ok(readonly.nodes("input[data-item-id]").every(control => control.disabled));

  const workflow = { catalog, selected: [], instances: [], current: null, next: { action: "choose-assets" } };
  const assetsPage = environment(baseData({ current: null, next: null, asset_workflow: workflow }), { interactive: true });
  await settle();
  assert.equal(assetsPage.nodes(".catalog-option").length, 21);
  assert.equal(assetsPage.nodes(".catalog-group").length, 4);
  assert.match(assetsPage.text(), /设计过程/);
  assert.match(assetsPage.text(), /设定规范/);
  assert.equal(assetsPage.nodes('input[data-asset-id="06"]')[0].disabled, true);
  assert.equal(assetsPage.nodes('input[data-asset-id="09"]')[0].disabled, false);
  await assetsPage.change('input[data-asset-id="02"]', "02");
  await assetsPage.change('input[data-asset-id="09"]', "09");
  assert.equal(assetsPage.nodes("[data-submit-action]").length, 0);
  await assetsPage.click(assetsPage.button("导出资产选择草稿"));
  const exported = assetsPage.nodes("textarea").at(-1).value;
  assert.match(exported, /02 定稿精细线稿/);
  assert.match(exported, /09 标准三视图/);
  assert.equal(assetsPage.posts.length, 0);

  const wired = environment(baseData({ current: null, next: null, asset_workflow: { ...workflow, selected: ["02", "09"], submit_actions: ["choose-assets"] } }), { interactive: true });
  await settle();
  await wired.click(wired.button("资产清单"));
  await wired.click(wired.button("保存资产选择"));
  assert.deepEqual(wired.posts[0].asset_ids, ["02", "09"]);
  assert.deepEqual(wired.posts[0].items, []);
  assert.match(wired.posts[0].evidence, /点击保存资产选择/);
  assert.match(wired.text(), /资产计划已保存/);

  const assetCurrent = {
    kind: "set", heading: "确认线稿", round_id: "asset-round-02", asset_id: "02",
    items: { "line-1": { title: "线稿版本一", badge: "A", asset_id: "02", instance_id: "line-1", path: "a.png", exists: true, selectable: true, image_sha256: "a".repeat(64) } }
  };
  const approval = environment(baseData({ current: null, next: null, asset_workflow: { ...workflow, current: assetCurrent, selected: ["02"], submit_actions: ["approve-asset", "asset-intent"] } }), { interactive: true, assetReceipt: true });
  await settle();
  assert.equal(approval.nodes(".candidate").length, 1);
  await approval.change('input[data-item-id="line-1"]', "line-1");
  await approval.click(approval.button("按 A 继续"));
  assert.equal(approval.posts[0].action, "approve-asset");
  assert.equal(approval.posts[0].asset_id, "02");
  assert.equal(approval.posts[0].instance_id, "line-1");
  assert.match(approval.posts[0].evidence, /点击采用候选 line-1/);
  assert.equal(approval.nodes(".candidate.selected").length, 1);
  assert.match(approval.nodes(".current-pick")[0].children[1].textContent, /A · 线稿版本一/);

  const draftCurrent = structuredClone(assetCurrent);
  draftCurrent.items["line-1"].selectable = false;
  draftCurrent.items["line-1"].intent_selectable = true;
  draftCurrent.items["line-2"] = { ...draftCurrent.items["line-1"], id: "line-2", badge: "B", intent_selectable: false };
  const intent = environment(baseData({ current: null, next: null, asset_workflow: { ...workflow, current: draftCurrent, selected: ["02"], submit_actions: ["approve-asset", "asset-intent"] } }), { interactive: true });
  await settle();
  assert.equal(intent.nodes('input[data-item-id="line-1"]')[0].disabled, true);
  await intent.change("#decision-action", "request-variation");
  assert.equal(intent.nodes('input[data-item-id="line-1"]')[0].disabled, false);
  assert.equal(intent.nodes('input[data-item-id="line-2"]')[0].disabled, true);
  await intent.change('input[data-item-id="line-1"]', "line-1");
  intent.input("decision-note", "继续完善耳朵比例");
  await intent.click(intent.button("登记制作意向"));
  assert.equal(intent.posts[0].action, "asset-intent");
  assert.equal(intent.posts[0].intent, "request-variation");
  assert.match(intent.text(), /意向已登记（未生成图片）/);

  const documentCurrent = structuredClone(assetCurrent);
  documentCurrent.asset_id = "08";
  documentCurrent.items["line-1"] = { ...documentCurrent.items["line-1"], asset_kind: "document", asset_id: "08", path: "identity.md", title: "核心设定文档" };
  const documentPage = environment(baseData({ current: null, next: null, asset_workflow: { ...workflow, current: documentCurrent, selected: ["08"] } }));
  assert.equal(documentPage.nodes("img").length, 0);
  assert.equal(documentPage.nodes(".file-preview").length, 1);
  assert.match(documentPage.text(), /打开原文件/);

  const unknownCatalog = structuredClone(catalog);
  unknownCatalog.assets.find(entry => entry.id === "01").support = "unverified";
  unknownCatalog.assets.find(entry => entry.id === "02").support = null;
  unknownCatalog.groups = unknownCatalog.groups.map(({ name, ...group }) => ({ ...group, label: name }));
  const unknownSupport = environment(baseData({ current: null, next: null, asset_workflow: { ...workflow, catalog: unknownCatalog } }));
  assert.equal(unknownSupport.nodes('input[data-asset-id="01"]')[0].disabled, true);
  assert.equal(unknownSupport.nodes('input[data-asset-id="02"]')[0].disabled, true);
  assert.match(unknownSupport.text(), /设计过程/);

  const incomplete = environment(baseData({ current: { kind: "set", items: null }, asset_workflow: { catalog: null, instances: null, current: null, selected: null } }));
  assert.ok(incomplete.text());
  const malformedDraft = new Map([["jarden:draft:quiet-studio-fixture:D0042", JSON.stringify({ selected: "invalid", note: 5, keep: [], action: false })]]);
  assert.ok(environment(baseData(), { storage: malformedDraft }).text());

  const updatePayload = (revision = 9, manifestRevision = revision) => ({ project_id: "quiet-studio-fixture", revision, manifest: baseData({ delivery_id: "D0043", project_revision: manifestRevision }), receipts: {} });
  const statusPage = environment(baseData({ workflow_status: workStatus() }), { interactive: true });
  await settle();
  assert.match(statusPage.text(), /等待确认材质/);
  assert.equal(statusPage.nodes('[data-flow-node="confirm"]')[0].getAttribute("aria-current"), "step");
  statusPage.input("decision-note", "状态刷新时保留我输入的内容");
  const statusInput = statusPage.document.getElementById("decision-note");
  const statusWorkspace = statusPage.app.children[0];
  const runningStatus = workStatus({
    phase_id: "create", status: "working", actor: "agent", title: "正在制作平涂图", detail: "原生生图请求已发出。",
    phases: workStatus().phases.map(phase => ({ ...phase, status: phase.id === "create" ? "current" : phase.id === "confirm" || phase.id === "review" ? "pending" : phase.status })),
    tasks: [{ id: "task-a", label: "平涂方向 A", status: "requesting" }]
  });
  const statusPayload = workflowStatus => ({ project_id: "quiet-studio-fixture", revision: 8, manifest: baseData(), workflow_status: workflowStatus, receipts: {} });
  statusPage.setLive(statusPayload(runningStatus));
  await statusPage.poll();
  assert.equal(statusPage.app.children[0], statusWorkspace, "task state updates must not remount the workspace");
  assert.equal(statusPage.document.getElementById("decision-note"), statusInput);
  assert.equal(statusPage.document.activeElement, statusInput);
  assert.equal(statusInput.value, "状态刷新时保留我输入的内容");
  assert.match(statusPage.text(), /正在制作平涂图/);
  assert.equal(statusPage.nodes('[data-flow-node="confirm"]')[0].getAttribute("aria-current"), undefined);
  assert.equal(statusPage.nodes('[data-flow-node="create"]')[0].getAttribute("aria-current"), "step");
  assert.equal(statusPage.navigations.length, 0, "task updates at the same revision must not reload");
  await statusPage.click(statusPage.button("工作详情"));
  const statusDialog = statusPage.nodes("dialog")[0];
  statusPage.setLive(statusPayload(workStatus({
    phase_id: "create", status: "blocked", title: "生成结果待核对", detail: "保留请求，先核对真实输出。",
    tasks: [{ id: "task-a", label: "平涂方向 A", status: "unknown" }],
    blockers: [{ id: "task-a", label: "结果未决", detail: "不得重复发送生成请求。" }]
  })));
  await statusPage.poll();
  assert.equal(statusPage.nodes("dialog")[0], statusDialog, "live status must preserve an open workflow dialog");
  assert.match(content(statusDialog), /生成结果待核对/);
  assert.match(content(statusDialog), /不得重复发送/);
  assert.equal(statusPage.posts.length, 0, "inspecting the map must not write decisions");
  statusDialog.close();
  const staticStatus = environment(baseData({ workflow_status: workStatus() }));
  await settle();
  assert.match(staticStatus.text(), /快照状态/);
  assert.equal(staticStatus.requests.length, 0);

  const live = environment(baseData(), { interactive: true });
  await settle();
  const unchangedTree = live.app.children[0];
  await live.poll();
  assert.equal(live.app.children[0], unchangedTree, "unchanged polling must not rerender the workspace");
  live.setLive(updatePayload());
  await live.poll();
  assert.deepEqual(live.navigations, ["/gallery.html#token=ui-fixture-token"]);
  assert.deepEqual(live.reloads, ["/gallery.html#token=ui-fixture-token"], "same-URL updates must download the current HTML with reload");
  assert.equal(live.replacements.length, 0, "replacing the identical URL does not reload Chromium");
  assert.equal(live.location.hash, "#token=ui-fixture-token", "reload must preserve the authentication fragment");
  assert.equal(live.posts.length, 0);
  assert.ok(live.requests.every(request => request.authorization === "Bearer ui-fixture-token"));
  assert.equal([...live.timers.values()].some(timer => timer.delay === 2500), false);

  const rootAlias = environment(baseData(), { interactive: true, pathname: "/" });
  await settle();
  rootAlias.setLive(updatePayload());
  await rootAlias.poll();
  assert.deepEqual(rootAlias.reloads, ["/#token=ui-fixture-token"]);
  assert.equal(rootAlias.replacements.length, 0);

  const preparing = environment(baseData(), { interactive: true });
  await settle();
  preparing.setLive(updatePayload(10, 9));
  await preparing.poll();
  await preparing.poll();
  assert.equal(preparing.navigations.length, 0, "a manifest behind the live revision is not ready to navigate");
  assert.match(preparing.text(), /最新工作台正在准备/);
  assert.ok(preparing.nodes("[data-submit-action]").every(button => button.disabled));
  preparing.setLive(updatePayload(10));
  await preparing.poll();
  assert.equal(preparing.navigations.length, 1);

  const drafting = environment(baseData(), { interactive: true });
  await settle();
  drafting.input("decision-note", "正在编辑的草稿不能丢");
  const draftInput = drafting.document.getElementById("decision-note");
  drafting.setLive(updatePayload());
  await drafting.poll();
  assert.equal(drafting.navigations.length, 0);
  assert.equal(drafting.document.getElementById("decision-note"), draftInput, "focused text controls must remain mounted");
  assert.equal(draftInput.value, "正在编辑的草稿不能丢");
  assert.match(drafting.text(), /工作台有新版本/);
  drafting.document.activeElement = null;
  await drafting.poll();
  assert.equal(drafting.navigations.length, 0, "unsent drafts prevent automatic navigation after blur");
  await drafting.click(drafting.button("查看最新工作台"));
  assert.equal(drafting.navigations.length, 1);
  const preservedDraft = JSON.parse(drafting.stored.get("jarden:draft:quiet-studio-fixture:D0042"));
  assert.equal(preservedDraft.note, "正在编辑的草稿不能丢");
  assert.equal(preservedDraft.draftDirty, true);

  const focused = environment(baseData(), { interactive: true });
  await settle();
  const focusInput = focused.document.getElementById("decision-note");
  focusInput.focus();
  focused.setLive(updatePayload());
  await focused.poll();
  assert.equal(focused.navigations.length, 0);
  assert.equal(focused.document.getElementById("decision-note"), focusInput);
  focused.document.activeElement = null;
  await focused.poll();
  assert.equal(focused.navigations.length, 1);

  const dialogPage = environment(baseData(), { interactive: true });
  await settle();
  await dialogPage.click(dialogPage.button("导出回复草稿"));
  const originalDialog = dialogPage.nodes("dialog")[0];
  dialogPage.setLive(updatePayload());
  await dialogPage.poll();
  assert.equal(dialogPage.navigations.length, 0);
  assert.equal(dialogPage.nodes("dialog")[0], originalDialog, "polling must not dismiss open dialogs");
  originalDialog.close();
  await dialogPage.poll();
  assert.equal(dialogPage.navigations.length, 1);

  const hidden = environment(baseData(), { interactive: true });
  await settle();
  const requestCount = hidden.requests.length;
  await hidden.visibility(true);
  assert.equal([...hidden.timers.values()].some(timer => timer.delay === 2500), false);
  assert.equal(hidden.requests.length, requestCount);
  hidden.setLive(updatePayload());
  await hidden.visibility(false);
  assert.equal(hidden.requests.length, requestCount + 1);
  assert.equal(hidden.navigations.length, 1);

  const archived = environment(baseData(), { interactive: true, pathname: "/deliveries/D0042/index.html", livePayload: updatePayload() });
  await settle();
  assert.equal(archived.navigations.length, 0);
  assert.equal([...archived.timers.values()].some(timer => timer.delay === 2500), false);
  await archived.click(archived.button("查看最新工作台"));
  assert.deepEqual(archived.replacements, ["/gallery.html#token=ui-fixture-token"], "explicit history navigation must switch to the current entry");
  assert.equal(archived.reloads.length, 0, "reloading history would leave the user on the historical delivery");

  const historyView = environment(baseData(), { interactive: true });
  await settle();
  await historyView.click(historyView.button("创作记录"));
  historyView.setLive(updatePayload());
  await historyView.poll();
  assert.equal(historyView.navigations.length, 0, "viewing history must not jump to the latest workspace");
  assert.match(historyView.text(), /工作台有新版本/);

  const selectedDraft = environment(baseData(), { interactive: true });
  await settle();
  await selectedDraft.change('input[data-item-id="B"]', "B");
  selectedDraft.document.activeElement = null;
  selectedDraft.setLive(updatePayload());
  await selectedDraft.poll();
  assert.equal(selectedDraft.navigations.length, 0, "an unsubmitted selection is also a draft");
  const resumedDraft = environment(baseData(), { interactive: true, storage: selectedDraft.stored });
  await settle();
  resumedDraft.setLive(updatePayload());
  await resumedDraft.poll();
  assert.equal(resumedDraft.navigations.length, 0, "restored drafts remain protected");

  const foreign = environment(baseData(), { interactive: true });
  await settle();
  foreign.setLive({ ...updatePayload(), manifest: baseData({ project_id: "different-project", delivery_id: "D0043", project_revision: 9 }) });
  await foreign.poll();
  assert.equal(foreign.navigations.length, 0);
  assert.match(foreign.text(), /交付快照不属于本页项目/);

  const sending = environment(baseData(), { interactive: true, deferSubmission: true });
  await settle();
  await sending.change('input[data-item-id="A"]', "A");
  const submission = sending.click(sending.button("按 A 继续"));
  await settle();
  assert.equal(sending.posts.length, 1);
  sending.setLive(updatePayload());
  await sending.poll();
  assert.equal(sending.navigations.length, 0, "an inflight submission cannot be interrupted");
  assert.equal(sending.button("查看最新工作台").disabled, true);
  sending.releaseSubmission();
  await submission;

  console.log("quiet-studio runtime: static drafts, receipts, locking, 21 assets, safe polling, live refresh, and draft/dialog/submission protection passed");
}

function serve() {
  const http = require("node:http");
  const imageRoot = process.env.JARDEN_UI_IMAGE_ROOT;
  const receipts = new Map();
  const dataFor = pathname => {
    const data = baseData();
    data.asset_workflow = {
      catalog, selected: ["02", "04", "07", "09"], plan: {}, instances: [], current: data.current,
      next: data.next, completion: { complete: false }, submit_actions: ["choose-assets", "approve-asset", "asset-intent"]
    };
    data.current.asset_id = "07";
    data.current.keep = ["奶油白色", "圆眼睛", "长耳朵"];
    data.current.items.forEach(item => { item.asset_id = "07"; item.badge = item.id; item.id = `assets-long-instance-identifier-${item.id}`; item.instance_id = item.id; });
    if (pathname === "/catalog.html") {
      data.current = null;
      data.next = { action: "choose-assets" };
      data.asset_workflow.current = null;
      data.asset_workflow.selected = [];
      data.asset_workflow.next = data.next;
    }
    if (pathname === "/legacy.html") delete data.asset_workflow;
    if (pathname === "/document.html") {
      const documentItem = { ...data.current.items[0], asset_kind: "document", asset_id: "08", path: "identity.md", title: "IP 核心设定文档" };
      data.current = { ...data.current, kind: "set", heading: "确认核心设定文档", asset_id: "08", items: [documentItem] };
      data.asset_workflow.current = data.current;
      data.asset_workflow.selected = ["08"];
    }
    if (pathname === "/readonly.html") data.readonly = true;
    if (pathname === "/stale.html") data.stale = true;
    return data;
  };
  const server = http.createServer(async (request, response) => {
    const pathname = new URL(request.url, "http://localhost").pathname;
    const send = (body, type = "application/json", status = 200) => {
      response.writeHead(status, { "Content-Type": type, "Cache-Control": "no-store" });
      response.end(typeof body === "string" || Buffer.isBuffer(body) ? body : JSON.stringify(body));
    };
    if (pathname === "/api/state") {
      const data = dataFor("/gallery.html");
      send({ project_id: data.project_id, revision: data.project_revision, manifest: data, receipts: {} });
      return;
    }
    if (pathname === "/api/decision" && request.method === "POST") {
      let body = "";
      for await (const chunk of request) body += chunk;
      const payload = JSON.parse(body);
      const receipt = { committed: true, action: payload.action, request_id: payload.request_id, delivery_id: payload.delivery_id, new_revision: payload.expected_revision + 1, selected: payload.items.map(item => item.id) };
      receipts.set(payload.request_id, receipt);
      send(receipt);
      return;
    }
    if (pathname.startsWith("/api/receipt/")) {
      const receipt = receipts.get(decodeURIComponent(pathname.split("/").pop()));
      send(receipt || {}, "application/json", receipt ? 200 : 404);
      return;
    }
    if (["/a.png", "/b.png"].includes(pathname) && imageRoot) {
      const filename = path.join(imageRoot, pathname === "/a.png" ? "material-a.png" : "material-b.png");
      send(fs.readFileSync(filename), "image/png");
      return;
    }
    if (pathname === "/favicon.ico") { response.writeHead(204); response.end(); return; }
    if (pathname === "/identity.md") { send("# 核心设定文档\n\n这是本地协议夹具，不是真实项目资产。\n", "text/plain; charset=utf-8"); return; }
    const data = dataFor(pathname);
    const template = fs.readFileSync(path.join(assets, "gallery.html"), "utf8");
    const html = template.replace("__GALLERY_CSS__", () => fs.readFileSync(path.join(assets, "gallery.css"), "utf8")).replace("__GALLERY_DATA__", () => JSON.stringify(data).replace(/</g, "\\u003c")).replace("__GALLERY_JS__", () => fs.readFileSync(path.join(assets, "gallery.js"), "utf8"));
    send(html, "text/html; charset=utf-8");
  });
  server.listen(0, "127.0.0.1", () => console.log(JSON.stringify({ origin: `http://127.0.0.1:${server.address().port}`, mode: "isolated-ui-fixture", production_assets: assets })));
}

if (process.argv.includes("--serve")) serve();
else run().catch(error => { console.error(error); process.exitCode = 1; });
