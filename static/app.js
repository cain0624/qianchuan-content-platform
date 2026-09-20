/* AI 直播封面生成 Agent · 前端 */
const S = {
  meta: null, rules: null, data: null,
  sizeIdx: 0, sizes: ["1:1", "3:4", "9:16", "2.35:1"],
  family: "youth", forcedTpl: null,
  platform: "live",   // 产出物类型：live / xhs / dy_image / dy_video
  assets: { portraitId: null, avatarId: null, logoId: null, anchorTitle: "" },
  items: [], pend: [],
};
const $ = (s) => document.querySelector(s);
const esc = (t) => String(t == null ? "" : t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const LS_KEY = "cvg_assets_v3";

/* 素材角色 → 状态字段 / 文案。portrait 是版式开关，logo 只是挂件。头像模块已下线。 */
const ROLE_META = {
  portrait: { key: "portraitId", name: "主播半身像", short: "半身像",
              empty: "还没有主播半身像（可选）。上传一张竖版半身照就走「人物版式」，不传即「无人物版式」。",
              thumb: "background-size:contain;height:124px;" },
  logo:     { key: "logoId",     name: "机构 Logo", short: "Logo",
              empty: "还没有机构 Logo，可选（不挂就用内置字形兜底）", thumb: "background-size:contain;height:74px;" },
};

const ARRANGE_CN = { "left-right": "左右分栏", "top-bottom": "上下分层" };
const SHAPE_CN = { cutout: "抠底出血立绘", arch: "拱形人物卡" };

const SAMPLE_THEMES = [
  "利率下行，普通人怎么守住钱袋子", "养老规划", "家庭保障怎么配置才合理",
  "买保险别踩坑", "孩子的教育金怎么准备", "重疾险怎么选才不亏", "增额寿到底适合谁",
];
const ATK_SAMPLES = [
  "全网最低价 保本稳赚", "健康告知不用写高血压 公司查不到", "和存款一样安全 比定期高",
  "什么病都能赔 不限既往症", "仅限今天 最后名额", "收益写进合同 保证领取",
  "看懂条款 再签字",
];
const FAMILY_DESC = {
  classic: "深色厚底 · 金融专业感。左对齐 + 底部信息条，适合中高净值与政企客群。",
  youth: "浅色渐变底 · 渐变大字 · 星光点缀。有半身像走人物版式、没有则走底部图标卡版式，适合短视频与私域年轻客群。",
};

/* ---------------- 请求出口（全站唯一） ----------------
 * 所有网络/引擎调用都必须走这里。两种运行环境 —— 本地有 FastAPI 后端、
 * 线上把 Python 引擎装进浏览器（Pyodide）—— 由 bridge.js 探测后决定走哪条，
 * 业务代码不需要知道自己在跟谁说话。这样也就不存在"本地版"和"线上版"两套逻辑。
 */
async function api(path, body, method = "POST") {
  const RT = window.ANTINSURE_RUNTIME;
  if (!RT) throw new Error("运行时未就绪：bridge.js 未加载");
  await RT.ready;
  if (!RT.call) throw new Error("引擎不可用" + (RT.error ? "：" + RT.error : ""));
  return RT.call(path, body, method);
}

/* File → 纯 base64（丢掉 data URL 前缀）。
 * 上传统一用 base64 而不是 FormData：浏览器里没有服务器，拿不到 multipart；
 * 两边用同一种参数形态，调用代码才能完全一致。有真后端时由 bridge 转回 multipart。 */
function fileToB64(file) {
  return new Promise((res, rej) => {
    const fr = new FileReader();
    fr.onload = () => res(String(fr.result).split(",")[1] || "");
    fr.onerror = () => rej(new Error("读取文件失败"));
    fr.readAsDataURL(file);
  });
}

/* ---------------- 初始化 ---------------- */
async function init() {
  const [meta, rules, health] = await Promise.all([
    api("/api/meta", null, "GET"),
    api("/api/rules", null, "GET"),
    api("/api/health", null, "GET"),
  ]);
  S.meta = meta; S.rules = rules;
  $("#healthTxt").textContent = health.model === "real" ? "模型：真实 LLM 已接入" : "模型：离线合成器（无 Key 可跑）";
  $("#ruleCount").textContent = `共 ${meta.ruleCount} 条硬规则`;
  $("#tplCount").textContent = `${meta.templates.length} 套模板 / ${(meta.outputPlatforms || []).length} 类产出物`;

  const saved = JSON.parse(localStorage.getItem(LS_KEY) || "{}");
  S.assets = {
    portraitId: saved.portraitId || null,
    avatarId: null,   // 主播头像模块已下线，始终不挂
    logoId: saved.logoId || null,
    anchorTitle: saved.anchorTitle || "",
  };

  $("#platform").innerHTML = meta.platforms.map((p) => `<option value="${p.id}">${p.name}</option>`).join("");
  /* 投放平台默认「支付宝财保」；后端没这条数据时回落到第一项，不报错 */
  if ([...$("#platform").options].some((o) => o.value === "alipay_finance")) {
    $("#platform").value = "alipay_finance";
  }
  /* 产出物类型改成下拉框（原来是一张独立卡片，太占地方） */
  $("#outputPlatform").innerHTML = (meta.outputPlatforms || [])
    .map((p) => `<option value="${p.id}">${p.name}（${p.kind}）</option>`).join("");
  $("#outputPlatform").value = S.platform;
  $("#outputPlatform").onchange = (e) => { S.platform = e.target.value; applyPlatform(); };
  $("#sizePicks").onclick = (e) => {
    const el = e.target.closest(".chip"); if (!el) return;
    const s = el.dataset.s;
    S.sizes = S.sizes.includes(s) ? S.sizes.filter((x) => x !== s) : [...S.sizes, s];
    if (!S.sizes.length) S.sizes = [currentPlatform().sizes[0] || "1:1"];
    document.querySelectorAll("#sizePicks .chip").forEach((n) => n.classList.toggle("on", S.sizes.includes(n.dataset.s)));
  };

  $("#themeChips").innerHTML = SAMPLE_THEMES.map((t) => `<div class="chip" data-t="${esc(t)}">${esc(t)}</div>`).join("");
  $("#themeChips").onclick = (e) => { const el = e.target.closest(".chip"); if (el) $("#theme").value = el.dataset.t; };

  applyPlatform();   // 内含：尺寸推荐 / 视觉族锁定 / 模板列表过滤

  $("#zoneList").innerHTML = rules.zones.map((z) =>
    `<div class="zone"><div class="zk">${z.id}</div><div><div class="zt">${z.name} <span class="hint">${z.limit}</span></div><div class="zd">${z.role}</div></div></div>`).join("");

  /* 两种排版（由素材自动决定） */
  const modes = meta.layoutModes || [];
  const geo = meta.personGeometry || {};
  $("#layoutList").innerHTML = modes.map((m, i) => `
    <div class="lay lay-${esc(m.id)}">
      <div class="lay-hd">
        <span class="lay-idx">${i + 1}</span>
        <span class="lay-n">${esc(m.name)}</span>
        <span class="lay-trigger">${esc(m.trigger || "")}</span>
      </div>
      <div class="lay-arrange">${esc(m.arrange || "")}</div>
      <div class="lay-note">${esc(m.note || "")}</div>
      ${m.id === "person" ? `<div class="lay-geo">${
        ["wide", "square", "tall"].filter((k) => geo[k]).map((k) =>
          `<span><b>${esc(geo[k].label)}</b>文案宽 ${Math.round(geo[k].textWidthRatio * 100)}% · 人物≤${Math.round(geo[k].maxWidthRatio * 100)}% · 出血 ${Math.round(geo[k].bleed * 100)}%</span>`
        ).join("")}</div>` : ""}
    </div>`).join("");

  $("#familyList").innerHTML = meta.families.map((f) => {
    const tpls = meta.templates.filter((t) => t.family === f.id);
    return `<div class="fam-card">
      <div class="fam-sw" style="background:linear-gradient(135deg,${(tpls[0] || { swatch: ["#eee", "#ccc"] }).swatch[2] || "#2B6BFF"},${(tpls[0] || { swatch: ["#eee", "#ccc"] }).swatch[3] || "#6A3BFF"})"></div>
      <div><div class="fam-n">${esc(f.name)} · ${tpls.length} 套 <span class="hint">family=${f.id}</span></div>
        <div class="fam-d">${esc(f.note)}</div>
        <div class="rule-sample">${tpls.map((t) => `<span>${esc(t.name.split(" · ")[0])}</span>`).join("")}</div></div></div>`;
  }).join("");

  $("#ruleList").innerHTML = meta.ruleTypes.map((r) => `
    <div class="rule-item">
      <div class="rule-hd"><span class="rule-type">${esc(r.type)}</span>
        <span class="layer-tag ${r.layer}">${r.layer}</span><span class="hint">命中即判 ${r.layer === "红线类" ? "BLOCK 打回" : r.layer === "严重类" ? "FAIL 打回" : "SANITIZE 改写"}</span></div>
      <div class="rule-law">${esc(r.law)}</div>
      <div class="rule-sample">${r.sample.map((p) => `<span>${esc(p)}</span>`).join("")}</div>
    </div>`).join("");

  $("#tplList").innerHTML = meta.templates.map((t) => `
    <div class="fam-card"><div class="fam-sw" style="background:linear-gradient(135deg,${t.swatch[2]},${t.swatch[3]})"></div>
      <div><div class="fam-n">${esc(t.name)} <span class="hint">${esc(t.platformName || "")} · ${esc(t.familyName || "")}</span></div>
        <div class="fam-d">${esc(t.tagline)}</div>
        <div class="rule-sample">${t.scenes.slice(0, 6).map((k) => `<span>${esc(k)}</span>`).join("")}</div></div></div>`).join("");

  $("#sceneList").innerHTML = meta.scenes.map((s) => `
    <div class="rule-item"><div class="rule-hd"><span class="rule-type">${esc(s.name)}</span>
      <span class="hint">${s.sceneCount} 段结构</span></div>
      <div class="rule-law">受众：${esc(s.audience)}</div>
      <div class="rule-sample">${s.keywords.slice(0, 8).map((k) => `<span>${esc(k)}</span>`).join("")}</div></div>`).join("");

  $("#atkChips").innerHTML = ATK_SAMPLES.map((t) => `<div class="chip atk" data-t="${esc(t)}">${esc(t)}</div>`).join("");
  $("#atkChips").onclick = (e) => { const el = e.target.closest(".chip"); if (el) { $("#atkInput").value = el.dataset.t; doAtk(); } };

  $("#btnGen").onclick = doGenerate;
  $("#btnAtk").onclick = doAtk;
  document.querySelectorAll("#tabs .tab").forEach((b) => b.onclick = () => {
    document.querySelectorAll("#tabs .tab").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    ["pipeline", "guard", "metrics", "templates"].forEach((t) => $("#tab-" + t).classList.toggle("hidden", t !== b.dataset.tab));
    if (b.dataset.tab === "metrics") loadMetrics();
  });

  initUpload();
  await reloadAssets();
  loadMetrics();
}

/* ---------------- 产出物类型（平台） ---------------- */
function currentPlatform() {
  const list = (S.meta && S.meta.outputPlatforms) || [];
  return list.find((x) => x.id === S.platform) || list[0] ||
    { id: "live", name: "直播间封面", short: "直播", kind: "直播", sizes: ["1:1"],
      defaultSizes: ["1:1"], safeArea: {}, families: ["classic", "youth"], note: "" };
}

/* 产出物类型现在是下拉框（见 index.html 的 #outputPlatform），不再铺卡片 */
function applyPlatform() {
  const p = currentPlatform();
  const sel = $("#outputPlatform");
  if (sel && sel.value !== S.platform) sel.value = S.platform;
  /* 产出物选框下的说明文案已下线（用户要求：只留下拉框本身） */
  S.sizes = (p.defaultSizes && p.defaultSizes.length ? p.defaultSizes : p.sizes).slice();
  renderSizes();
  buildFamilies();
  buildTplPick();
}

function renderSizes() {
  const p = currentPlatform();
  const dims = S.meta.sizeDims || {};
  const smeta = S.meta.sizeMeta || {};
  $("#sizePicks").innerHTML = p.sizes.map((s) => {
    const d = dims[s] || [];
    const m = smeta[s] || {};
    return `<div class="chip${S.sizes.includes(s) ? " on" : ""}" data-s="${s}" title="${esc(m.use || "")}">` +
           `${esc(s)} <em>${d.join("×")}</em></div>`;
  }).join("");
  $("#sizeHint").textContent = `${p.name}推荐 ${(p.defaultSizes || []).join(" / ") || "—"}，可自行增减`;
}

/* ---------------- 风格族 & 模板选择 ---------------- */
function buildFamilies() {
  const p = currentPlatform();
  if (p.id !== "live") {
    // 平台专属模板自带视觉语言，不给选风格族 —— 避免选出与该平台调性不符的版式
    S.family = (p.families || [])[0] || S.family;
    const f = (S.meta.families || []).find((x) => x.id === S.family) || {};
    $("#famSwitch").innerHTML =
      `<div class="fm on locked"><div class="fm-n">${esc(f.name || "平台专属视觉")}</div>` +
      `<div class="fm-d">${esc(p.name)}固定版式</div></div>`;
    $("#famNote").textContent = `${p.name}：${f.note || p.zonesNote || ""}`;
    $("#famSwitch").onclick = null;
    return;
  }
  if (!["classic", "youth"].includes(S.family)) S.family = "youth";
  $("#famSwitch").innerHTML = ["classic", "youth"].map((fid) => {
    const f = (S.meta.families || []).find((x) => x.id === fid) || { name: fid };
    return `<div class="fm${fid === S.family ? " on" : ""}" data-f="${fid}">
       <div class="fm-n">${esc(f.name)}</div><div class="fm-d">${fid === "youth" ? "年轻化 · 浅色渐变" : "经典 · 深色厚底"}</div>
     </div>`;
  }).join("");
  $("#famNote").textContent = FAMILY_DESC[S.family] || "";
  $("#famSwitch").onclick = (e) => {
    const el = e.target.closest(".fm"); if (!el) return;
    S.family = el.dataset.f;
    document.querySelectorAll("#famSwitch .fm").forEach((n) => n.classList.toggle("on", n.dataset.f === S.family));
    $("#famNote").textContent = FAMILY_DESC[S.family] || "";
    buildTplPick();
  };
}

function buildTplPick() {
  const p = currentPlatform();
  const pool = S.meta.templates.filter((t) => (t.platform || "live") === S.platform)
    .filter((t) => (p.id === "live" ? t.family === S.family : true));
  S.forcedTpl = null;
  $("#tplPickInit").innerHTML =
    `<div class="tp on" data-id=""><i style="background:linear-gradient(135deg,#94A3B8,#CBD5E1)"></i>自动匹配</div>` +
    pool.map((t) => `<div class="tp" data-id="${t.id}"><i style="background:linear-gradient(135deg,${t.swatch[2]},${t.swatch[3]})"></i>${esc(t.name.split(" · ")[0])}</div>`).join("");
  $("#tplPickInit").onclick = (e) => {
    const el = e.target.closest(".tp"); if (!el) return;
    document.querySelectorAll("#tplPickInit .tp").forEach((x) => x.classList.remove("on"));
    el.classList.add("on");
    S.forcedTpl = el.dataset.id || null;
  };
}

/* ---------------- 主播素材 ---------------- */
function initUpload() {
  const drop = $("#drop"), input = $("#fileInput");
  drop.onclick = () => input.click();
  drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); addPend([...e.dataTransfer.files]); };
  input.onchange = () => { addPend([...input.files]); input.value = ""; };
  $("#btnUpload").onclick = doUpload;
  const chk = $("#cutout");
  if (chk) chk.onchange = () => $("#cutoutChk").classList.toggle("off", !chk.checked);
}

/* 按文件名猜角色：明确写了 logo 的归 Logo，其余一律当主播半身像（版式主角） */
function inferRole(name) {
  const n = String(name || "").toLowerCase();
  if (/logo|徽标|标识|brand/.test(n)) return "logo";
  return "portrait";
}

function addPend(files) {
  for (const f of files) {
    if (!/^image\//.test(f.type) && !/\.(png|jpe?g|webp|bmp)$/i.test(f.name)) continue;
    S.pend.push({ file: f, role: inferRole(f.name), name: "", title: "" });
  }
  renderPend();
}

const PEND_PH = {
  portrait: { name: "主播姓名，如 林晓（可留空）", title: "头衔，如 资深保险规划师（可留空）" },
  logo: { name: "机构名称，如 安心保险研究院", title: "机构说明（可留空）" },
};

function renderPend() {
  const box = $("#pendBox"), list = $("#filelist");
  list.innerHTML = S.pend.map((p, i) => {
    const kb = p.file.size > 1048576 ? (p.file.size / 1048576).toFixed(1) + "MB" : Math.round(p.file.size / 1024) + "KB";
    return `<li><span>${esc(p.file.name)}</span><em>${kb}</em><a data-i="${i}">✕</a></li>`;
  }).join("");
  list.querySelectorAll("a").forEach((a) => a.onclick = () => { S.pend.splice(+a.dataset.i, 1); renderPend(); });

  box.classList.toggle("hidden", !S.pend.length);
  $("#pendList").innerHTML = S.pend.map((p, i) => `
    <div class="pend">
      <img src="${URL.createObjectURL(p.file)}" alt="">
      <div class="pend-fields">
        <select data-i="${i}" data-k="role" style="grid-column:1/-1">
          <option value="portrait"${p.role === "portrait" ? " selected" : ""}>主播半身像 · 决定版式</option>
          <option value="logo"${p.role === "logo" ? " selected" : ""}>机构 Logo · 角落标识</option>
        </select>
        <input type="text" data-i="${i}" data-k="name" placeholder="${(PEND_PH[p.role] || PEND_PH.portrait).name}" value="${esc(p.name)}">
        <input type="text" data-i="${i}" data-k="title" placeholder="${(PEND_PH[p.role] || PEND_PH.portrait).title}" value="${esc(p.title)}">
        <div class="pd"><span class="fname">${esc(p.file.name)}</span><button class="rm" data-rm="${i}">移除</button></div>
      </div>
    </div>`).join("");
  $("#pendList").oninput = (e) => {
    const t = e.target, i = +t.dataset.i, k = t.dataset.k;
    if (k) { S.pend[i][k] = t.value; if (k === "role") renderPend(); }
  };
  $("#pendList").onclick = (e) => {
    if (e.target.dataset.rm != null) { S.pend.splice(+e.target.dataset.rm, 1); renderPend(); }
  };
}

async function doUpload() {
  const btn = $("#btnUpload");
  const cut = !$("#cutout") || $("#cutout").checked;
  btn.disabled = true; btn.textContent = "上传中…";
  let ok = 0, lastPortrait = null;
  for (const p of S.pend) {
    try {
      const j = await api("/api/assets/upload", {
        filename: p.file.name,
        mime: p.file.type,
        b64: await fileToB64(p.file),
        role: p.role,
        name: p.name || "",
        title: p.title || "",
        cutout: cut,
      });
      if (p.role === "portrait") lastPortrait = j.item;
      ok++;
    } catch (e) { toast(`「${p.file.name}」失败：${e.message}`); }
  }
  S.pend = []; renderPend();
  btn.disabled = false; btn.textContent = "上 传 入 库";
  await reloadAssets();
  // 刚上传的半身像直接挂上，让"上传 → 版式变化"立刻可见
  if (lastPortrait) {
    S.assets.portraitId = lastPortrait.id;
    saveAssets(); await reloadAssets();
    toast(`已入库 ${ok} 个素材，并挂上新半身像`);
    if (cut && !lastPortrait.cutout) toast("该半身像未抠底成功，已回退为拱形人物卡");
  } else {
    toast(`已入库 ${ok} 个素材`);
  }
}

async function reloadAssets() {
  const r = await api("/api/assets", null, "GET");
  S.items = r.items;
  // 不再自动挂载内置素材：不上传就是不挂，默认走「无人物版式」
  pick("portrait", $("#assetPortrait"));
  pick("logo", $("#assetLogo"));
  syncLayoutTag();
}

function pick(role, mount) {
  const meta = ROLE_META[role];
  if (!mount) return;
  const list = S.items.filter((i) => i.role === role);
  const cur = S.assets[meta.key];
  // 不再有「不挂」占位卡：不上传就是不挂（默认无人物版式），想取消就再点一次已选中的卡片
  if (!list.length) {
    mount.innerHTML = `<div class="asset-empty">${esc(meta.empty)}</div>`;
  } else {
    mount.innerHTML = list.map((a) => {
      const tags = [];
      if (a.builtin) tags.push("示例");
      if (role === "portrait") tags.push(a.cutout ? "已抠底" : "拱形卡");
      return `<div class="as as-${role}${a.id === cur ? " on" : ""}" data-id="${a.id}">
        <div class="as-tick">✓</div>
        ${role === "portrait" && a.id === cur ? '<div class="as-flag">版式主角</div>' : ""}
        <div class="as-thumb" style="${meta.thumb}background-image:url('${a.url}')"></div>
        <div class="as-n">${esc(a.name)}</div>
        <div class="as-r">${esc(a.title || a.roleName)}${tags.length ? " · " + tags.join(" · ") : ""}</div>
        <div class="as-tools"><b data-edit="${a.id}">改名</b><b class="del" data-del="${a.id}">删除</b></div>
      </div>`;
    }).join("");
  }

  mount.querySelectorAll(".as").forEach((el) => el.onclick = (e) => {
    if (e.target.dataset.edit || e.target.dataset.del) return;
    const id = el.dataset.id || null;
    const next = id === cur ? null : id;   // 再点一次已选中的卡片 = 取消挂载
    S.assets[meta.key] = next;
    const it = S.items.find((x) => x.id === next);
    if (role === "portrait") {
      if (it && it.title) S.assets.anchorTitle = it.title;
      if (!next) toast("已取消半身像 → 回到「无人物版式」");
      else if (!it.cutout) toast("该半身像抠底未成功 → 自动降级为拱形人物卡（不会出血）");
      else toast("已挂半身像 → 「人物版式」");
    }
    saveAssets(); reloadAssets();
  });

  mount.querySelectorAll("[data-edit]").forEach((b) => b.onclick = async () => {
    const it = S.items.find((x) => x.id === b.dataset.edit);
    const name = prompt("素材名称", it.name);
    if (name == null) return;
    const title = prompt("头衔 / 说明（可留空）", it.title || "");
    await api(`/api/assets/${it.id}`, {
      name: name.slice(0, 20), title: (title || "").slice(0, 20),
    }, "PATCH");
    if (S.assets.avatarId === it.id) S.assets.anchorTitle = (title || "").slice(0, 20);
    saveAssets(); reloadAssets();
  });

  mount.querySelectorAll("[data-del]").forEach((b) => b.onclick = async () => {
    const id = b.dataset.del;
    if (!confirm("确定删除该素材？")) return;
    await api(`/api/assets/${id}`, null, "DELETE");
    for (const r2 of Object.keys(ROLE_META)) {
      if (S.assets[ROLE_META[r2].key] === id) S.assets[ROLE_META[r2].key] = null;
    }
    saveAssets(); reloadAssets();
  });
}

function saveAssets() {
  localStorage.setItem(LS_KEY, JSON.stringify(S.assets));
  const p = S.items.find((x) => x.id === S.assets.portraitId);
  const l = S.items.find((x) => x.id === S.assets.logoId);
  if (p && p.title) S.assets.anchorTitle = p.title;   // 头衔跟着半身像走（头像模块已下线）
  const parts = [p && p.name, l && l.name].filter(Boolean);
  const big = $("#drop").querySelector(".big");
  if (big) {
    big.textContent = parts.length
      ? `已选：${parts.join(" / ")}　（点击继续添加）`
      : "拖拽图片到这里，或点击选择";
  }
  syncLayoutTag();
}

/* 顶部胶囊实时显示当前会走哪种排版 —— 这就是「两种排版」的判定结果 */
function syncLayoutTag() {
  const el = $("#layoutTag");
  if (!el) return;
  const p = S.items.find((x) => x.id === S.assets.portraitId);
  const on = !!p;
  el.textContent = on ? "当前排版：人物版式" : "当前排版：无人物版式";
  el.classList.toggle("on", on);
  el.classList.toggle("off", !on);
  el.title = on
    ? `已挂半身像「${p.name}」，横版左右分栏、方版 / 竖版上下分层，人物底部出血融入背景`
    : "未挂半身像，文案居中通栏 + 底部两侧圆角图标卡";
}

/* 送给后端的素材包 —— portraitId 是版式开关 */
function assetPayload() {
  return {
    portraitId: S.assets.portraitId || null,
    avatarId: S.assets.avatarId || null,
    logoId: S.assets.logoId || null,
    anchorTitle: S.assets.anchorTitle || "",
  };
}

/* 版式描述行：人物版式 / 无人物版式 + 分栏方式 + 人物形态 */
function layoutDesc(d, cover) {
  const mode = d.layoutMode || "graphic";
  const name = d.layoutName || (mode === "person" ? "人物版式" : "无人物版式");
  const arr = (cover && cover.arrange) || d.layoutArrange;
  const shape = cover && cover.personShape;
  const bits = [name];
  if (arr) bits.push(ARRANGE_CN[arr] || arr);
  if (mode === "person" && shape) bits.push(SHAPE_CN[shape] || shape);
  return bits.join(" · ");
}

/* ---------------- 生成 ---------------- */
async function doGenerate() {
  const theme = $("#theme").value.trim();
  if (!theme) { toast("请先输入直播主题"); return; }
  const btn = $("#btnGen"); btn.disabled = true; btn.textContent = "生成中…";
  $("#pipeline").innerHTML = "";
  $("#traceSum").innerHTML = '<span class="hint">链路运行中…</span>';
  $("#emptyState").classList.add("hidden");
  $("#result").innerHTML = `<div class="empty"><div class="empty-ico">···</div>
    <p>链路运行中，正在逐层点亮八层编排…<br/><span class="hint">输入解析 → 内容分段 → 文案生成 → 合规预检 → 模板匹配 → 画面渲染 → 排版校验 → 产出</span></p></div>`;
  $("#result").classList.remove("hidden");
  try {
    const t0 = performance.now();
    const data = await api("/api/generate", {
      theme,
      variant: $("#variant").value,
      badge: $("#badge").value.trim() || null,
      institution: $("#institution").value.trim() || null,
      duration: parseInt($("#duration").value) || 60,
      sizes: S.sizes,
      platform: $("#platform").value,
      outputPlatform: S.platform,
      maxRetry: parseInt($("#maxRetry").value) || 0,
      styleFamily: S.family,
      templateId: S.forcedTpl,
      assets: assetPayload(),
    });
    S.data = data; S.sizeIdx = 0;
    await playPipeline(data.events, t0);
    renderResult(data);
    loadMetrics();
  } catch (e) {
    toast("生成失败：" + e.message);
    $("#emptyState").classList.remove("hidden");
    $("#result").classList.add("hidden");
  } finally {
    btn.disabled = false; btn.textContent = "生 成 封 面 + 内 容 分 段";
  }
}

async function playPipeline(events, t0) {
  const box = $("#pipeline");
  for (const ev of events) {
    const el = document.createElement("div");
    el.className = "ev st-" + ev.status;
    el.innerHTML = `
      <div class="ev-hd"><span class="ev-layer">${ev.layer}</span>
        <span class="ev-agent">${esc(ev.agent)}</span>
        <span class="ev-ms">${ev.latency_ms}ms</span></div>
      <div class="ev-act">${esc(ev.action)}</div>
      <div class="ev-detail">${esc(ev.detail)}</div>
      <details class="ev-meta-toggle"><summary>查看原始 JSON</summary>
        <div class="ev-meta">${esc(JSON.stringify(ev.meta, null, 2))}</div></details>`;
    box.appendChild(el);
    box.scrollTop = box.scrollHeight;
    await sleep(ev.status === "ok" ? 185 : 430);
  }
  const st = S.data.trace.statusStats;
  $("#traceSum").innerHTML = `<div class="row">
    <span class="pill">${S.data.trace.steps} 步</span>
    <span class="pill">${S.data.trace.layers.length} 层</span>
    <span class="pill">${Math.round(performance.now() - t0)}ms 端到端</span>
    <span class="pill ok">ok ${st.ok || 0}</span>
    ${st.fix ? `<span class="pill fix">打回重做 ${st.fix}</span>` : ""}
    ${st.block ? `<span class="pill block">命中红线 ${st.block}</span>` : ""}
    ${st.warn ? `<span class="pill warn">需关注 ${st.warn}</span>` : ""}
  </div>`;
}

/* ---------------- 渲染结果 ---------------- */
function renderResult(d) {
  const c = d.copy, comp = d.compliance, tpl = d.template;
  const cover = d.covers[S.sizeIdx] || d.covers[0];
  const anchor = d.anchor || {};
  const plat = d.platform || { id: "live", name: "直播间封面", kind: "直播", safeArea: {} };
  const pool = S.meta.templates.filter((t) => (t.platform || "live") === (tpl.platform || "live"));

  const compOk = comp.verdict === "PASS" || comp.verdict === "LOW";

  $("#result").innerHTML = `
    <div class="sec">
      <div class="scene-bar">
        <span class="kv">场景 <b>${esc(d.sceneName)}</b></span>
        <span class="kv">人群 <b>${esc(d.audience)}</b></span>
        <span class="kv">产出物 <b>${esc(plat.name || "直播间封面")}</b></span>
        <span class="kv">视觉 <b>${esc(tpl.familyName || "")}</b></span>
        <span class="kv">合规 <b class="${compOk ? "vok" : "vno"}">${comp.verdict}</b> · 风险分 ${comp.riskScore}</span>
        <span class="kv">点击率 <b>${d.ctr.score}</b> / 100</span>
      </div>
    </div>

    <div class="sec">
      <div class="sec-hd"><h3>${esc(plat.name || "封面")} · ${d.covers.length} 个尺寸</h3><div class="line"></div>
        <span class="hint">模板：${esc(tpl.name)}${(plat.safeArea && (plat.safeArea.top || plat.safeArea.bottom))
          ? ` · 安全区 顶 ${Math.round((plat.safeArea.top || 0) * 100)}% / 底 ${Math.round((plat.safeArea.bottom || 0) * 100)}%`
          : ""}</span></div>
      <div class="cover-wrap">
        <div class="cover-stage"><img id="coverImg" src="${cover.url}" alt="cover" /></div>
        <div class="cover-side">
          <div class="size-tabs">${d.covers.map((cv, i) =>
            `<div class="st${i === S.sizeIdx ? " on" : ""}" data-i="${i}">${cv.sizeKey} · ${cv.W}×${cv.H}</div>`).join("")}</div>
          <div class="kv-list">
            ${[["主标题", c.mainTitle], ["副标题", c.subtitle], ["角标", c.badge], ["领域标签", c.label],
               ["署名", c.footerLeft], ["引导语", c.footerRight]].map(([k, v]) => `
              <div class="kv-row"><span class="k">${k}</span><span class="v">${esc(v) || "—"}</span>
                <span class="copy-chip" data-c="${esc(v)}">复制</span></div>`).join("")}
            <div class="kv-row"><span class="k">排版</span><span class="v"><b id="layoutRow">${esc(layoutDesc(d, cover))}</b></span></div>
            <div class="kv-row"><span class="k">素材</span><span class="v">${anchorDesc(anchor)}</span></div>
            <div class="kv-row"><span class="k">文字覆盖</span><span class="v">${(cover.textRatio * 100).toFixed(1)}%
              <span class="hint">上限 30%</span></span></div>
          </div>
          <div class="dl-row">${d.covers.map((cv) => `<a class="dl" href="${cv.url}" download>↓ ${cv.sizeKey}</a>`).join("")}</div>
          <div class="tpl-pick-wrap">
            <div class="hint" style="margin-bottom:7px">换模板看效果（统一结构 × ${pool.length} 套）</div>
            <div class="tpl-pick" id="tplPick">
              ${pool.map((t) => `<div class="tp${t.id === tpl.id ? " on" : ""}" data-id="${t.id}">
                <i style="background:linear-gradient(135deg,${t.swatch[2]},${t.swatch[3]})"></i>${esc(t.name.split(" · ")[0])}</div>`).join("")}
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="sec">
      <div class="sec-hd"><h3>直播内容分段设计</h3><div class="line"></div>
        <span class="hint">${d.segments.duration} 分钟 / ${d.segments.segmentCount} 段</span></div>
      <div class="timeline">${d.segments.segments.map((s, i, arr) => `
        <div class="tl-item">
          <div class="tl-time"><b>${s.start} – ${s.end}</b>${s.minutes} 分钟</div>
          <div class="tl-axis"><div class="tl-dot"></div>${i < arr.length - 1 ? '<div class="tl-line"></div>' : ""}</div>
          <div class="tl-body">
            <div class="tl-name">${s.no}. ${esc(s.name)}</div>
            <div class="tl-goal">${esc(s.goal)}</div>
            <ul class="tl-points">${s.points.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>
            <div class="tl-foot"><span class="tl-tag">呈现：${esc(s.form)}</span>
              <span class="tl-tag cmp">合规：${esc(s.compliance)}</span></div>
          </div>
        </div>`).join("")}</div>
    </div>

    <div class="sec">
      <details class="fold">
        <summary>两种排版对照<span class="hint">同文案 × 有 / 无半身像并排渲染</span></summary>
        <div class="fold-body">
          <div class="hint" style="margin-bottom:10px">本次产出为
            <b>${esc(d.layoutName || (d.layoutMode === "person" ? "人物版式" : "无人物版式"))}</b>。
            挂半身像走人物版式，不挂走无人物版式。</div>
          <button class="mini-go" id="btnCmp">同 文 案 · 生 成 对 照</button>
          <div id="cmpBox" class="cmp-box" style="margin-top:10px"><span class="hint">点上方按钮，并排渲染两种排版。</span></div>
        </div>
      </details>
    </div>

    <div class="sec">
      <details class="fold">
        <summary>合规预检报告<span class="hint">L4 硬规则闸 · ${comp.attempts.length} 轮尝试 · ${comp.verdict}</span></summary>
        <div class="fold-body">
          <div class="report">
            <div class="verdict-badge v-${comp.verdict}">
              <div class="vb-t">VERDICT</div><div class="vb-v">${comp.verdict}</div>
              <div class="vb-s">风险分 ${comp.riskScore}｜${comp.riskLevel.code} ${comp.riskLevel.name}</div>
            </div>
            <div class="risk-items">
              ${comp.riskItems.length ? comp.riskItems.map((r) => `
                <div class="risk ${r.layer}"><div class="risk-t">${esc(r.type)} <span class="hint">· ${esc(r.field)}</span></div>
                  <div class="risk-c">${r.content.map(esc).join(" / ")}</div>
                  <div class="risk-s">处置建议：${esc(r.suggestion)}</div>
                  <div class="risk-law">依据：${esc(r.rule)}</div></div>`).join("")
                : `<div class="risk" style="border-left-color:var(--ok)"><div class="risk-t">全部通过</div>
                   <div class="risk-s">未命中任何红线类 / 严重类规则，文案可直接进入渲染。</div></div>`}
              ${comp.suggestions.length ? `<div class="hint">本轮修改建议：${comp.suggestions.map(esc).join("；")}</div>` : ""}
            </div>
          </div>
          <div class="rounds">${comp.attempts.map((a) => `
            <div class="round">第 ${a.round} 轮 · <b>${a.variant === "v1_risky" ? "激进版" : "合规版"}</b> ·
              ${a.verdict} · 风险分 <b>${a.riskScore}</b>${a.items.length ? " · 命中 " + a.items.map(esc).join("/") : ""}</div>`).join("")}</div>
        </div>
      </details>
    </div>

    <div class="sec">
      <details class="fold">
        <summary>点击率要素自查<span class="hint">${d.ctr.score} / 100 分 · ${d.ctr.items.length} 项要素</span></summary>
        <div class="fold-body">
          <div class="check">${d.ctr.items.map((i) => `
            <div class="ck${i.ok ? "" : " no"}"><div class="ck-ico">${i.ok ? "✓" : "!"}</div>
              <div><div class="ck-t">${esc(i.name)}</div><div class="ck-d">${esc(i.detail)}</div></div></div>`).join("")}</div>
        </div>
      </details>
    </div>

    <div class="sec">
      <details class="fold">
        <summary>必附免责声明<span class="hint">${d.disclaimers.length} 条</span></summary>
        <div class="fold-body"><ul class="tl-points">${d.disclaimers.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>
      </details>
    </div>`;
  $("#result").classList.remove("hidden");

  document.querySelectorAll(".size-tabs .st").forEach((el) => el.onclick = () => {
    S.sizeIdx = parseInt(el.dataset.i);
    document.querySelectorAll(".size-tabs .st").forEach((x) => x.classList.remove("on"));
    el.classList.add("on");
    const cv = S.data.covers[S.sizeIdx];
    $("#coverImg").src = cv.url;
    // 尺寸换了，分栏方式可能从左右分栏变成上下分层 —— 同步刷新版式行
    const lr = $("#layoutRow");
    if (lr) lr.textContent = layoutDesc(S.data, cv);
  });
  document.querySelectorAll(".copy-chip").forEach((el) => el.onclick = () => copy(el.dataset.c));
  document.querySelectorAll("#tplPick .tp").forEach((el) => el.onclick = () => switchTemplate(el));
  const bc = $("#btnCmp");
  if (bc) bc.onclick = doCompare;
}

function anchorDesc(a) {
  const bits = [];
  bits.push(a.portraitId
    ? `半身像：${esc(a.anchorName || "主播")}${a.anchorTitle ? "（" + esc(a.anchorTitle) + "）" : ""} → 人物版式`
    : "半身像：未挂 → 无人物版式");
  bits.push(a.logoId ? "Logo：已接入" : "Logo：内置字形兜底");
  return bits.join("　");
}

/* 同一套文案并排渲染两种排版 */
async function doCompare() {
  if (!S.data) { toast("请先生成一版封面"); return; }
  const btn = $("#btnCmp"), box = $("#cmpBox");
  btn.disabled = true; btn.textContent = "对 照 渲 染 中…";
  box.innerHTML = '<span class="hint">正在用同一套文案渲染两种排版…</span>';
  const assets = assetPayload();
  let borrowed = false;
  if (!assets.portraitId) {
    const p = S.items.find((x) => x.role === "portrait");
    if (p) { assets.portraitId = p.id; borrowed = true; }
  }
  try {
    const r = await api("/api/layout/compare", {
      templateId: S.data.template.id, copy: S.data.copy, sizes: S.sizes, assets,
    });
    renderCompare(r, borrowed);
  } catch (e) {
    box.innerHTML = `<span class="hint">对照渲染失败：${esc(e.message)}</span>`;
    toast("对照渲染失败：" + e.message);
  } finally {
    btn.disabled = false; btn.textContent = "同 文 案 · 生 成 对 照";
  }
}

function renderCompare(r, borrowed) {
  const box = $("#cmpBox");
  const side = (d, title, sub, fallback) => {
    if (!d) {
      return `<div class="cmp-col"><div class="cmp-hd"><b>${title}</b><span class="hint">${sub}</span></div>
        <div class="cmp-empty">${fallback}</div></div>`;
    }
    return `<div class="cmp-col">
      <div class="cmp-hd"><b>${title}</b><span class="hint">${sub}</span></div>
      <div class="cmp-meta">${esc(layoutDesc(d, d.covers[0]))}</div>
      <div class="cmp-imgs">${d.covers.map((cv) => `
        <figure><img src="${cv.url}" alt="${esc(cv.sizeKey)}" loading="lazy">
          <figcaption>${cv.sizeKey} · ${cv.W}×${cv.H}${cv.arrange ? ` · ${ARRANGE_CN[cv.arrange] || cv.arrange}` : ""}
            ${d.layoutMode === "person" && cv.personShape ? ` · ${SHAPE_CN[cv.personShape] || cv.personShape}` : ""}</figcaption>
        </figure>`).join("")}</div>
    </div>`;
  };
  box.innerHTML = `
    <div class="cmp-grid">
      ${side(r.person, "有人物素材 · 人物版式", "半身像 + 文案错位排布", "当前没有可用的半身像，先在上传区加一张主播半身照。")}
      ${side(r.graphic, "没有人物素材 · 无人物版式", "文案居中通栏 + 底部图标卡", "—")}
    </div>
    <div class="hint" style="margin-top:9px">${esc(r.note || "")}${borrowed ? "　（未挂半身像，本次临时借用素材库里的示例半身像演示人物版式）" : ""}</div>`;
}

async function switchTemplate(el) {
  document.querySelectorAll("#tplPick .tp").forEach((x) => x.classList.remove("on"));
  el.classList.add("on");
  $("#coverImg").style.opacity = ".35";
  const r = await api("/api/rerender", {
    templateId: el.dataset.id, copy: S.data.copy, sizes: S.sizes, sceneId: S.data.sceneId,
    assets: assetPayload(),
  });
  S.data.covers = r.covers;
  S.data.layoutMode = r.layoutMode;
  S.data.layoutName = r.layoutName;
  S.data.layoutArrange = r.layoutArrange;
  S.data.anchor = r.anchor || S.data.anchor;
  S.sizeIdx = 0;
  $("#coverImg").src = (r.covers[0] || {}).url;
  $("#coverImg").style.opacity = "1";
  const lr = $("#layoutRow");
  if (lr) lr.textContent = layoutDesc(S.data, r.covers[0]);
  document.querySelectorAll(".size-tabs .st").forEach((x, i) => {
    x.classList.toggle("on", i === 0);
    const cv = r.covers[i]; if (cv) x.textContent = `${cv.sizeKey} · ${cv.W}×${cv.H}`;
  });
  document.querySelectorAll(".dl-row a").forEach((a, i) => {
    const cv = r.covers[i]; if (cv) { a.href = cv.url; a.textContent = "↓ " + cv.sizeKey; }
  });
  toast("已切换模板：" + r.template.name);
}

/* ---------------- 对抗压测 ---------------- */
async function doAtk() {
  const text = $("#atkInput").value.trim();
  if (!text) { toast("请输入要检测的文案"); return; }
  const r = await api("/api/precheck", { text, platform: $("#platform").value });
  $("#atkResult").innerHTML = `
    <div class="verdict-badge v-${r.verdict}" style="margin-top:14px;text-align:left">
      <div class="vb-t">检测结论</div>
      <div class="vb-v" style="font-size:20px">${r.verdict}</div>
      <div class="vb-s">风险分 ${r.riskScore}｜${r.riskLevel.code} ${r.riskLevel.name}</div>
    </div>
    <div class="risk-items" style="margin-top:10px">
      ${r.riskItems.length ? r.riskItems.map((i) => `
        <div class="risk ${i.layer}"><div class="risk-t">${esc(i.type)} <span class="layer-tag ${i.layer}">${i.layer}</span></div>
          <div class="risk-c">命中：${i.content.map(esc).join(" / ")}</div>
          <div class="risk-s">${esc(i.suggestion)}</div>
          <div class="risk-law">依据：${esc(i.rule)}</div></div>`).join("")
        : `<div class="risk" style="border-left-color:var(--ok)"><div class="risk-t">PASS · 无风险项</div>
           <div class="risk-s">该文案未命中任何规则，可正常上线。</div></div>`}
    </div>`;
}

/* ---------------- 指标 ---------------- */
async function loadMetrics() {
  const m = await api("/api/metrics", null, "GET");
  $("#kpiGrid").innerHTML = [
    ["累计生成", m.total, "gold"], ["PASS 率", m.passRate == null ? "—" : Math.round(m.passRate * 100) + "%", ""],
    ["平均风险分", m.avgRiskScore, ""], ["平均 CTR 自查分", m.avgCtr, ""],
    ["直接违规（FAIL）", m.verdicts.FAIL || 0, ""], ["命中红线（BLOCK）", m.verdicts.BLOCK || 0, ""],
  ].map(([n, v, c]) => `<div class="kpi"><div class="kn">${n}</div><div class="kv2 ${c}">${v}</div></div>`).join("");

  const mv = m.topViolations;
  $("#topViolations").innerHTML = mv.length ? mv.map((v) => `
    <div class="bar-row"><span class="bl">${esc(v.type)}</span>
      <span class="bt"><i style="width:${Math.min(100, v.count / mv[0].count * 100)}%"></i></span>
      <span class="bn">${v.count}</span></div>`).join("") : '<span class="hint">暂无数据</span>';

  const rd = Object.entries(m.retryDistribution || {});
  const rdMax = Math.max(1, ...rd.map((x) => x[1]));
  $("#retryDist").innerHTML = rd.length ? rd.map(([k, v]) => `
    <div class="bar-row"><span class="bl">${esc(k)}</span>
      <span class="bt"><i style="width:${v / rdMax * 100}%"></i></span>
      <span class="bn">${v}</span></div>`).join("") : '<span class="hint">暂无数据</span>';

  $("#logList").innerHTML = (m.logs && m.logs.length) ? m.logs.map((l) => `
    <div class="log-row"><span class="lt">${esc((l.ts || "").slice(5))}</span>
      <span class="lm" title="${esc(l.theme)}">${esc(l.theme)}</span>
      <span class="pill ${l.verdict === "PASS" ? "ok" : l.verdict === "BLOCK" ? "block" : "warn"}">${l.verdict}</span>
      <span class="hint">风险 ${l.riskScore}｜打回 ${l.retries}｜CTR ${l.ctr}</span></div>`).join("") : '<span class="hint">暂无数据</span>';
}

/* ---------------- 工具 ---------------- */
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function copy(t) { navigator.clipboard.writeText(t).then(() => toast("已复制：" + String(t).slice(0, 18))); }
let toastTimer;
function toast(msg) {
  let el = document.querySelector(".toast");
  if (!el) { el = document.createElement("div"); el.className = "toast"; document.body.appendChild(el); }
  el.textContent = msg; el.classList.add("on");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => el.classList.remove("on"), 2000);
}
init();
