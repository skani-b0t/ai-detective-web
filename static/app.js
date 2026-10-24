// AI 审讯室 前端逻辑：场景切换 + 提交查证 + 渲染报告卡
const state = { scene: "rumor", fromScreenshot: false, ocrText: "", ocrCleaned: false };
// 官方措辞对齐：结论用「真 / 假 / 说不准」，查不到就明说「没查到可靠来源」
const VMAP = { "✅": "真 · 有据可查", "⚠️": "说不准 · 存疑", "❌": "假 · 与信源冲突", "🔍": "没查到可靠来源" };
const VCLS = { "✅": "ok", "⚠️": "warn", "❌": "bad", "🔍": "noev" };
let currentItems = [];
const overrides = {};  // idx -> {verdict, note}

// 各场景对应的输入占位提示，让占位文案跟随 tab 变化
const PLACEHOLDERS = {
  ai: "例如：每天喝白酒可以杀死幽门螺杆菌，预防胃癌……",
  rumor: "例如：紧急通知！吃大蒜能预防新病毒，大家转发提醒家人……",
  latest: "例如：今天上海天气怎么样？周杰伦演唱会还有票吗？",
  fuzzy: "例如：那个讲法医的女主剧叫什么？只用记得一句台词也行",
};

document.querySelectorAll(".tab").forEach(t => {
  t.addEventListener("click", () => {
    // 点的是当前已选中的 tab：不做任何重置，避免误触把已经输入的内容清掉
    if (t.classList.contains("active")) return;
    document.querySelectorAll(".tab").forEach(x => x.classList.remove("active"));
    t.classList.add("active");
    state.scene = t.dataset.scene;
    // 占位提示跟随场景
    if (PLACEHOLDERS[state.scene]) $("input").setAttribute("placeholder", PLACEHOLDERS[state.scene]);
    // 换场景 = 换一条新线索：清空输入框，回到新场景的默认占位提示
    $("input").value = "";
    // 切换场景时清掉「来自截图」标记，避免误标
    state.fromScreenshot = false; state.ocrText = ""; state.ocrCleaned = false;
    $("ocrbox").classList.add("hidden");
    $("ocr-badge").classList.add("hidden");
    $("shotbanner").classList.add("hidden");
    // 场景都换了，上一份报告与进度面板也不该继续挂着（与上一步的体验一致）
    reportEl.classList.add("hidden");
    stopProgress();
    statusEl.textContent = "";
  });
});

const $ = id => document.getElementById(id);
const statusEl = $("status");
// 用户手动改动输入框时，清掉「来自截图」标记（说明文字已非截图原文）
$("input").addEventListener("input", () => {
  state.fromScreenshot = false; state.ocrText = ""; state.ocrCleaned = false;
  $("ocrbox").classList.add("hidden");
  $("ocr-badge").classList.add("hidden");
  $("shotbanner").classList.add("hidden");
});
const reportEl = $("report-card");  // 注意：按钮 id="report" 用于导出，卡片容器用 report-card 避免 id 冲突

// ——— 查证进行中：九阶段进度面板 ———
// 新查证开始时立即隐藏上一份报告卡，避免"旧结果挂在下面"的错位体验；
// 延迟 300ms 才显示进度面板：命中缓存（<1s 返回）时根本不会闪一下。
const PROG_STEPS = ["意图识别与对齐", "查询改写", "查询拆分", "多路信息源检索",
  "检索重规划", "多层过滤", "代码核验（数值型）", "跨源交叉验证", "内容融合与生成"];
let _prog = null;

function startProgress() {
  $("prog-steps").innerHTML = PROG_STEPS.map((s, i) =>
    `<div class="prog-step"><span class="ps-n">${i + 1}</span><span>${s}</span></div>`).join("");
  reportEl.classList.add("hidden");           // 旧报告立刻消失
  $("shotbanner").classList.add("hidden");
  $("progress-card").classList.remove("hidden");
  $("prog-foot").textContent = "每一步都会在完成后点亮，全程可见、不做黑盒。";
  const steps = [...document.querySelectorAll(".prog-step")];
  let idx = 0;
  const tick = () => {
    steps.forEach((el, i) => {
      el.classList.toggle("done", i < idx);
      el.classList.toggle("active", i === idx);
    });
    $("prog-bar-inner").style.width = Math.round(idx / PROG_STEPS.length * 100) + "%";
    if (idx < PROG_STEPS.length - 1) {
      $("prog-hint").textContent = "正在 " + PROG_STEPS[idx] + "…";
      idx++;
      _prog = setTimeout(tick, 1100);
    } else {
      $("prog-hint").textContent = "最后一步：混元正在生成总评…";
      $("prog-bar-inner").style.width = "92%";
      $("prog-foot").textContent = "这一步是推理模型写总评，通常需要 20–60 秒，请稍候。";
    }
  };
  tick();
}

function stopProgress() {
  if (_prog) { clearTimeout(_prog); _prog = null; }
  $("progress-card").classList.add("hidden");
}

function armProgress() {
  stopProgress();
  // 旧报告立即收起（不等 300ms）——新查证一开始，上一份结论就不该还挂在页面上
  reportEl.classList.add("hidden");
  $("shotbanner").classList.add("hidden");
  return setTimeout(startProgress, 300);   // 返回句柄，快速返回时 clear 掉就不显示
}

async function submitVerify() {
  const text = $("input").value.trim();
  if (!text) { statusEl.textContent = "请先粘贴要查证的内容。"; return; }
  setLoading(true);
  const progTimer = armProgress();
  statusEl.textContent = "正在调用元宝搜索 API 逐条查证…";
  try {
    const res = await fetch("/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text, scene: state.scene, use_llm: true,
        from_screenshot: state.fromScreenshot, ocr_text: state.ocrText,
        ocr_cleaned: state.ocrCleaned,
      }),
    });
    const data = await res.json();
    if (data.error) { statusEl.textContent = data.error; return; }
    renderReport(data);
    statusEl.textContent = "查证完成。";
  } catch (e) {
    statusEl.textContent = "请求失败：" + e.message;
  } finally {
    clearTimeout(progTimer); stopProgress(); setLoading(false);
  }
}

$("run").addEventListener("click", submitVerify);

$("demo").addEventListener("click", async () => {
  setLoading(true);
  const progTimer = armProgress();
  statusEl.textContent = "加载示例…";
  try {
    const res = await fetch("/demo/" + state.scene);
    renderReport(await res.json());
    statusEl.textContent = "已载入示例，可点「开始查证」重新跑。";
  } catch (e) {
    statusEl.textContent = "示例加载失败：" + e.message;
  } finally {
    clearTimeout(progTimer); stopProgress(); setLoading(false);
  }
});

// —— 截图查证：上传截图 → 腾讯云 OCR 识别 → 自动填入并查证 ——
$("shotcheck").addEventListener("click", () => $("shotfile").click());
$("shotfile").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = "";  // 允许重复选同一张图
  if (!file) return;
  setLoading(true);
  statusEl.textContent = "正在用腾讯云 OCR 识别截图文字…";
  try {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch("/ocr", { method: "POST", body: fd });
    const r = await res.json();
    if (!res.ok || r.error) {
      statusEl.textContent = (r.error || "识别失败") + (r.hint ? "（" + r.hint + "）" : "（可手动粘贴截图文字）");
      $("input").focus();
      return;
    }
    // 识别成功：hy3 已提炼核心 → 填预览 + 填输入框 + 切到「审AI」+ 自动查证
    state.fromScreenshot = true;
    state.ocrText = r.text;
    state.ocrCleaned = r.clean_method === "hunyuan";
    $("ocrbox").classList.remove("hidden");
    $("ocr-badge").classList.toggle("hidden", !state.ocrCleaned);
    $("ocr-preview").textContent = r.text.length > 120 ? r.text.slice(0, 120) + "…" : r.text;
    $("input").value = r.text;
    document.querySelectorAll(".tab").forEach(x => x.classList.remove("active"));
    const rumorTab = document.querySelector('.tab[data-scene="rumor"]');
    if (rumorTab) { rumorTab.classList.add("active"); state.scene = "rumor"; }
    statusEl.textContent = "识别完成，正在查证…";
    await submitVerify();
  } catch (err) {
    statusEl.textContent = "识别请求失败：" + err.message + "（可手动粘贴截图文字）";
  } finally {
    setLoading(false);
  }
});

// 导出带证据链的调查报告（深度调查进阶挑战交付物）
$("report").addEventListener("click", () => {
  const d = state.report;
  if (!d) { alert("请先完成一次查证，再导出报告。"); return; }
  const md = buildReportMarkdown(d);
  const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
  const a = document.createElement("a");
  const ts = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
  a.download = `AI侦探社-调查报告-${ts}.md`;
  a.href = URL.createObjectURL(blob);
  a.click();
  URL.revokeObjectURL(a.href);
});

function buildReportMarkdown(d) {
  const L = [];
  L.push("# AI 侦探社 · 查证调查报告");
  L.push("");
  L.push(`> 由元宝搜索 API 驱动，九阶段查证流水线生成。本作品核心即搜索：去掉元宝搜索，查证链路即停转。`);
  L.push("");
  L.push(`- 生成时间：${new Date().toLocaleString("zh-CN")}`);
  if (d.mode === "fuzzy") {
    L.push(`- 模式：查模糊（线索检索，只摆证据不下四态结论）`);
  } else {
    L.push(`- 模式：${({ ai: "审 AI（查幻觉）", rumor: "审谣言（查真假）", latest: "查最新（时效核对）" })[d.scene] || d.scene}`);
  }
  if (d.ocr_text) L.push(`- 来源：截图识别（共 ${d.ocr_text.length} 字）`);
  L.push("");
  L.push("## 一、结论总评");
  L.push("");
  L.push(d.headline || "（无）");
  L.push("");

  if (d.mode === "fuzzy") {
    L.push("## 二、线索检索候选");
    L.push("");
    (d.candidates || []).forEach((c, i) => {
      L.push(`### 候选 ${i + 1}：${c.title || "（无标题）"}`);
      L.push("");
      if (c.summary) L.push(c.summary);
      L.push("");
      L.push(`- 来源：${c.url || "（无链接）"}`);
      L.push("");
    });
    L.push("> 查模糊模式不强行下结论：以上为检索到的候选答案与可点来源，供人判断。");
    return L.join("\n");
  }

  L.push("## 二、九阶段查证流水线");
  L.push("");
  (d.pipeline || []).forEach((p, i) => {
    L.push(`${i + 1}. **${p.stage}** — ${p.detail || ""}`);
  });
  L.push("");

  L.push("## 三、逐条断言与证据链");
  L.push("");
  (d.items || []).forEach((it, i) => {
    L.push(`### 断言 ${i + 1}：${it.claim || "（空）"}`);
    L.push("");
    L.push(`- 判定：**${it.verdict} ${it.label || ""}**${it.overridden ? "（用户复核改判）" : ""}`);
    if (it.query) L.push(`- 检索查询：${it.query}`);
    if (it.code && it.code.computed) {
      L.push(`- 代码核验：声明 ${it.code.claimed} ｜ 实测 ${it.code.computed} ｜ ${it.code.ok ? "吻合" : "不符"}`);
    }
    if (it.sources && it.sources.length) {
      L.push("- 来源（点得开）：");
      it.sources.forEach(s => {
        const meta = [s.site || s.domain, s.publish_time ? s.publish_time.slice(0, 10) : "", s.authority_level || ""].filter(Boolean).join(" · ");
        L.push(`  - [${s.title || s.url}](${s.url})${meta ? " — " + meta : ""}`);
      });
    } else {
      L.push("- 来源：未检索到可靠来源（查无）。");
    }
    L.push("");
  });

  if (d.authority_matrix && d.authority_matrix.length) {
    L.push("## 四、可信源矩阵");
    L.push("");
    d.authority_matrix.forEach(m => {
      L.push(`- ${m.tier || ""}：${m.label || ""}（${m.count ?? 0} 个来源）`);
    });
    L.push("");
  }

  if (d.narrative) {
    L.push("## 五、融合总评");
    L.push("");
    L.push(d.narrative);
    L.push("");
  }

  L.push("---");
  L.push("");
  L.push("AI 侦探社 · 把结论的来路查清楚（元宝搜索 × 腾讯混元 hy3）");
  return L.join("\n");
}

$("shot").addEventListener("click", () => {
  if (typeof html2canvas === "undefined") {
    alert("已为你准备好报告卡，直接用浏览器截图即可生成分享海报～");
    return;
  }
  html2canvas(reportEl, { backgroundColor: "#1d2336" }).then(canvas => {
    const a = document.createElement("a");
    a.download = "查证报告卡.png";
    a.href = canvas.toDataURL("image/png");
    a.click();
  });
});

function setLoading(on) {
  $("run").disabled = on; $("demo").disabled = on;
}

function renderReport(d) {
  stopProgress();   // 报告出来了，进度面板必须收起（双保险）
  // 存住最近一次报告，供「导出调查报告」使用
  state.report = d;
  // 「查模糊」走独立的候选呈现分支
  if (d.mode === "fuzzy") { renderFuzzy(d); return; }

  // 常规查证：恢复被模糊模式隐藏的区块
  $("matrix-sec").style.display = ""; $("metrics-sec").style.display = "";
  $("pipeline-sec").style.display = "";

  reportEl.classList.remove("hidden");
  // 「来自截图识别」横幅：把截图取证链路显性展示给评委
  if (d.ocr_text) {
    $("shotbanner").classList.remove("hidden");
    const sub = d.ocr_cleaned
      ? `hy3 已提炼核心 · ${d.ocr_text.length} 字`
      : `共识别 ${d.ocr_text.length} 字`;
    $("shotbanner").innerHTML =
      `<svg class="sb-ic" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"></path><circle cx="12" cy="13" r="4"></circle></svg>` +
      `<div><b>来自截图识别</b> · ${sub}<div class="sb-src">${escapeHtml(d.ocr_text)}</div></div>`;
  } else {
    $("shotbanner").classList.add("hidden");
  }
  $("headline").textContent = d.headline;
  $("m-consistency").textContent = d.consistency;
  $("m-verified").textContent = d.stats.verified;
  $("m-conflict").textContent = d.stats.conflict;
  $("m-indep").textContent = d.independent_sources;

  // —— 可信源矩阵摘要 ——
  const m = d.authority_matrix || {};
  const tiers = [
    { k: 3, label: "权威信源", cls: "t3" },
    { k: 2, label: "高可信", cls: "t2" },
    { k: 1, label: "一般", cls: "t1" },
    { k: 0, label: "低质/疑似", cls: "t0" },
  ];
  $("matrix").innerHTML = tiers.map(t =>
    `<span class="chip ${t.cls}">${t.label} <b>${m[t.k] || 0}</b></span>`).join("");

  // —— 流水线追踪 ——
  const engTag = { hunyuan: "🤖混元", heuristic: "📐规则", live: "🔌实时", offline: "💾离线", "原句(无Key)": "📐", "跳过": "⏭跳过" };
  $("pipeline").innerHTML = (d.pipeline || []).map(p =>
    `<div class="step">
       <span class="step-name">${p.stage}</span>
       <span class="step-detail">${escapeHtml(p.detail)}</span>
       <span class="step-engine">${engTag[p.engine] || p.engine}</span>
     </div>`).join("");

  // —— 断言卡片（含 ②人在回路 / ①代码核验 / ③重规划）——
  const box = $("claims");
  box.innerHTML = "";
  currentItems = d.items || [];
  for (let idx = 0; idx < currentItems.length; idx++) {
    box.appendChild(renderClaim(currentItems[idx], idx));
  }

  // —— 融合总评 ——
  $("narrative").innerHTML = d.narrative
    ? `<div class="narr-title">🧩 融合总评（混元生成）</div><div class="narr-body">${escapeHtml(d.narrative)}</div>`
    : "";

  // —— 技术栈状态条（全链路）——
  const tech = [];
  if (d.extract_method === "hunyuan") {
    tech.push("🤖 混元抽取 " + d.stats.total + " 条断言");
  } else {
    tech.push("📐 规则抽取 " + d.stats.total + " 条（混元不可用，已回退）");
  }
  tech.push(d.engine_mode === "live" ? "🔌 元宝搜索·多路实时检索" : "💾 离线样例检索");
  if (d.narrative) tech.push("🧩 混元融合总评");
  tech.push("🧮 代码核验已接入");
  $("techline").textContent = tech.join("  ·  ");
  $("humanloop").textContent = "";
}

function renderClaim(it, idx) {
  const div = document.createElement("div");
  const ov = overrides[idx];
  const dispV = ov ? ov.verdict : it.verdict;
  const dispL = ov ? VMAP[ov.verdict] : (VMAP[it.verdict] || it.label);
  div.className = "claim " + (VCLS[dispV] || "noev");
  div.id = "card-" + idx;

  const sources = (it.sources || []).map(s => {
    const a = s.authority || {};
    const authTag = a.tier >= 3
      ? `<span class="auth">${a.label}</span>`
      : (a.tier === 0 ? `<span class="auth bad">${a.label}</span>` : `<span class="auth low">${a.label}</span>`);
    return `<a class="source" href="${s.url}" target="_blank" rel="noopener">
       <span>🔗 ${s.title || s.source}</span>
       <span class="score">相关${Math.round((s.score || 0) * 100)}%</span>
       ${authTag}
       <span class="time">${s.publish_time || ""}</span>
     </a>`;
  }).join("");

  const sem = it.semantic ? `<span class="sem">语义核验：${it.semantic.verdict}·${Math.round((it.semantic.confidence || 0) * 100)}%</span>` : "";
  const q = it.query && it.query !== it.claim
    ? `<div class="claim-query">🔎 改写查询：${escapeHtml(it.query)}</div>` : "";

  // ① 代码核验
  const codeLine = it.code
    ? `<div class="code-verify ${it.code.ok ? "ok" : "bad"}">🧮 代码核验：声明 <b>${escapeHtml(String(it.code.claimed))}</b> ｜ 实测 <b>${escapeHtml(String(it.code.computed))}</b> ｜ ${it.code.ok ? "吻合 ✅" : "不符 ❌"} <span class="cv-detail">${escapeHtml(it.code.detail || "")}</span></div>`
    : "";

  // ③ 检索重规划
  const rp = it.replan;
  const replanLine = rp
    ? `<div class="replan">🔄 检索重规划：首轮「${escapeHtml(rp.from)}」仅 ${rp.before} 条 → 改写「${escapeHtml(rp.to)}」得 ${rp.after} 条</div>`
    : "";

  // ② 人在回路：四态可点选
  const chips = ["✅", "⚠️", "❌", "🔍"].map(v =>
    `<button class="hl-chip ${ov && ov.verdict === v ? "active" : ""}" data-idx="${idx}" data-v="${v}">${v}</button>`).join("");
  const ovTag = ov ? `<span class="ov-tag">✋ 用户复核</span>` : "";

  div.innerHTML = `
    <div class="claim-top">
      <span class="verdict" id="v-${idx}">${dispV}</span>
      <span class="vlabel" id="vl-${idx}">${dispL}</span>
      ${sem} ${ovTag}
    </div>
    <div class="claim-text">${escapeHtml(it.claim)}</div>
    ${q}
    <div class="claim-note">${it.note}</div>
    ${codeLine}
    ${replanLine}
    <div class="sources">${sources || '<span class="source">（无可用信源）</span>'}</div>
    <div class="human-loop">
      <span class="hl-label">✋ 人工复核（点选你的判定）：</span>
      <span class="hl-chips">${chips}</span>
      <input class="hl-note" id="hn-${idx}" placeholder="复核备注（可选）" value="${ov && ov.note ? escapeHtml(ov.note) : ""}" />
      <button class="hl-submit" data-idx="${idx}">提交复核</button>
    </div>`;

  // 点选判定
  div.querySelectorAll(".hl-chip").forEach(btn => {
    btn.addEventListener("click", () => applyOverride(idx, btn.dataset.v));
  });
  // 提交复核到后端（人在回路留痕）
  div.querySelector(".hl-submit").addEventListener("click", async () => {
    const v = overrides[idx] ? overrides[idx].verdict : it.verdict;
    const note = $("hn-" + idx).value.trim();
    overrides[idx] = { verdict: v, note };
    try {
      const res = await fetch("/override", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ claim: it.claim, verdict: v, note }),
      });
      const r = await res.json();
      if (r.count != null) {
        $("humanloop").textContent = `✋ 已记录 ${r.count} 条人工复核（人在回路 · 你的判定已回传服务端）`;
      }
    } catch (e) {
      $("humanloop").textContent = "✋ 复核已在本地保存（服务端记录暂不可用）。";
    }
  });

  return div;
}

function applyOverride(idx, v) {
  overrides[idx] = overrides[idx] || {};
  overrides[idx].verdict = v;
  const card = $("card-" + idx);
  if (!card) return;
  $("v-" + idx).textContent = v;
  $("vl-" + idx).textContent = VMAP[v];
  card.className = "claim " + (VCLS[v] || "noev");
  // 高亮所选 chip
  card.querySelectorAll(".hl-chip").forEach(b => {
    b.classList.toggle("active", b.dataset.v === v);
  });
  // 标注用户复核
  let tag = card.querySelector(".ov-tag");
  if (!tag) {
    tag = document.createElement("span");
    tag.className = "ov-tag";
    tag.textContent = "✋ 用户复核";
    card.querySelector(".claim-top").appendChild(tag);
  }
}

function escapeHtml(s) {
  return (s || "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

// —— 查模糊：线索检索模式，只摆证据不下结论 ——
function renderFuzzy(d) {
  state.report = d;  // 供「导出调查报告」使用
  reportEl.classList.remove("hidden");
  $("shotbanner").classList.add("hidden");  // 模糊模式不走截图取证链路
  $("matrix-sec").style.display = "none";
  $("metrics-sec").style.display = "none";
  $("pipeline-sec").style.display = "";
  $("headline").textContent = d.headline;

  const engTag = { hunyuan: "🤖混元", heuristic: "📐规则", live: "🔌实时", offline: "💾离线", "原句(无Key)": "📐", "跳过": "⏭跳过", "规则": "📐规则" };
  $("pipeline").innerHTML = (d.pipeline || []).map(p =>
    `<div class="step">
       <span class="step-name">${p.stage}</span>
       <span class="step-detail">${escapeHtml(p.detail)}</span>
       <span class="step-engine">${engTag[p.engine] || p.engine}</span>
     </div>`).join("");

  const qline = (d.queries || []).map(q => `<span class="chip t2">${escapeHtml(q)}</span>`).join(" ");
  const cands = (d.candidates || []).map(c => {
    const a = c.authority || {};
    const authTag = a.tier >= 3 ? `<span class="auth">${a.label}</span>`
      : (a.tier === 0 ? `<span class="auth bad">${a.label}</span>` : `<span class="auth low">${a.label}</span>`);
    return `<div class="claim noev cand">
      <div class="cand-title"><a href="${c.url}" target="_blank" rel="noopener">🔗 ${escapeHtml(c.title) || escapeHtml(c.source)}</a> ${authTag}</div>
      <div class="cand-sum">${escapeHtml(c.summary)}</div>
      <div class="cand-meta"><span>${escapeHtml(c.source)}</span><span>相关 ${Math.round((c.score || 0) * 100)}%</span><span class="time">${c.publish_time || ""}</span></div>
    </div>`;
  }).join("");

  $("claims").innerHTML =
    (qline ? `<div class="fuzzy-queries">🔎 改写检索词：${qline}</div>` : "") +
    (cands || '<div class="claim noev cand"><div class="cand-sum">没查到可靠来源——我不猜、不编。换个说法再试一次？</div></div>');
  $("narrative").innerHTML = "";
  $("techline").textContent = "🧭 线索检索模式 · 候选来源均点得开 · 查不到就明说不知道";
  $("humanloop").textContent = "";
}

// 初始化：默认场景=审谣言（rumor），同步占位提示与高亮 tab
(function init() {
  state.scene = "rumor";
  document.querySelectorAll(".tab").forEach(x => {
    const on = x.dataset.scene === "rumor";
    x.classList.toggle("active", on);
    x.setAttribute("aria-selected", on ? "true" : "false");
  });
  if (PLACEHOLDERS[state.scene]) $("input").setAttribute("placeholder", PLACEHOLDERS[state.scene]);
})();
