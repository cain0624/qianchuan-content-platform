/* bridge.js —— 运行时选址 + 浏览器内引擎装载
 *
 * 这份文件让**同一套 index.html / app.js 在两种环境下都能跑**：
 *   · 本地：有 FastAPI 后端（server.py），走普通 fetch；
 *   · 线上（GitHub Pages）：没有后端，把整个 Python 渲染引擎装进浏览器（Pyodide）。
 *
 * 判断方式是探测 ./api/health —— 要求 200 **且** content-type 是 JSON。
 * 必须带上 content-type 这一条：静态托管对不存在的路径会做 SPA 兜底，
 * 把 index.html 以 200 吐回来，只看状态码会误判成「有后端」。
 *
 * 装载顺序上有一个容易踩的坑：这个文件必须在 app.js **之前**、
 * 且必须**同步**把 window.ANTINSURE_RUNTIME 挂上去。因为 app.js 一解析到就会
 * 调 init()，如果那时运行时常量还不存在，它就会掉进 fetch 分支去请求不存在的
 * /api/*，拿到一堆 404。所以这里是「同步挂载一个对象 + 异步填好里面的 ready」。
 */
(function () {
  "use strict";

  var PYODIDE_URL = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/";
  var FS_ROOT = "/py";                 // 虚拟文件系统里的项目根
  var MANIFEST = "./py-manifest.json";
  var REQ_FILE = "/tmp/_facade_req.json";

  var _resolveReady;
  var readyPromise = new Promise(function (r) { _resolveReady = r; });

  var RT = {
    mode: null,            // 'http' | 'bridge'
    ready: readyPromise,   // Promise<'http'|'bridge'>
    call: null,            // (path, body, method) => Promise<data>
    pyodide: null,
    progress: [],
    onProgress: null,      // (text, pct) => void
    error: null,
  };
  window.ANTINSURE_RUNTIME = RT;

  // ---------------------------------------------------------------- 小工具
  function say(text, pct) {
    RT.progress.push(text);
    if (RT.onProgress) { try { RT.onProgress(text, pct); } catch (e) { /* 回调出错不影响装载 */ } }
  }

  function loadScript(src) {
    return new Promise(function (res, rej) {
      var s = document.createElement("script");
      s.src = src;
      s.onload = res;
      s.onerror = function () { rej(new Error("脚本加载失败：" + src)); };
      document.head.appendChild(s);
    });
  }

  /* 带 gzip 的取文件。字体这种大文件预压缩后能少传 20%~30%，
   * 浏览器侧用原生 DecompressionStream 解开，不引入任何第三方库。 */
  async function fetchBytes(url, gzipped) {
    var r = await fetch(url);
    if (!r.ok) throw new Error(url + " → HTTP " + r.status);
    if (gzipped && typeof DecompressionStream === "function" && r.body) {
      var stream = r.body.pipeThrough(new DecompressionStream("gzip"));
      return new Uint8Array(await new Response(stream).arrayBuffer());
    }
    return new Uint8Array(await r.arrayBuffer());
  }

  /* 依次尝试多个 base，第一个成功即返回。
   * 字体走 jsdelivr（能从 GitHub 仓库直接取），失败再回落站点自身的相对路径。 */
  async function fetchFrom(bases, rel, gzipped) {
    var lastErr = null;
    for (var i = 0; i < bases.length; i++) {
      var url = bases[i] + rel + (gzipped ? ".gz" : "");
      try {
        return await fetchBytes(url, gzipped);
      } catch (e) { lastErr = e; }
    }
    throw lastErr || new Error("取不到 " + rel);
  }

  // ---------------------------------------------------------------- 环境探测
  async function probe() {
    try {
      var r = await fetch("./api/health", { method: "GET", cache: "no-store" });
      var ct = (r.headers.get("content-type") || "").toLowerCase();
      if (r.ok && ct.indexOf("json") >= 0) return "http";
    } catch (e) { /* 没有后端，正常 */ }
    return "bridge";
  }

  // ---------------------------------------------------------------- http 模式
  /* 有真后端时，这里负责把「统一调用形态」翻译成 HTTP。
   * 目标：app.js 里只有一种写法，不需要知道自己在跟谁说话。
   * 只有上传是特殊形态 —— 后端收 multipart，而前端统一传 base64，
   * 所以在这里转一次，省得为了统一去改后端的接口。 */
  function b64ToBlob(b64, mime) {
    var bin = atob(b64);
    var arr = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
    return new Blob([arr], { type: mime || "application/octet-stream" });
  }

  async function httpCall(path, body, method) {
    method = method || "POST";
    var init = { method: method, cache: "no-store" };
    if (path === "/api/assets/upload" && body && body.b64) {
      var fd = new FormData();
      fd.append("file", b64ToBlob(body.b64, body.mime), body.filename || "asset.png");
      fd.append("role", body.role || "portrait");
      fd.append("name", body.name || "");
      fd.append("title", body.title || "");
      fd.append("cutout", body.cutout === false ? "0" : "1");
      init.body = fd;
    } else if (method !== "GET" && method !== "DELETE") {
      init.headers = { "Content-Type": "application/json" };
      init.body = JSON.stringify(body || {});
    } else if (body && Object.keys(body).length) {
      var qs = new URLSearchParams(body).toString();
      if (qs) path += (path.indexOf("?") >= 0 ? "&" : "?") + qs;
    }
    var r = await fetch(path, init);
    var ct = (r.headers.get("content-type") || "").toLowerCase();
    if (ct.indexOf("json") < 0) throw new Error("后端返回了非 JSON 内容（HTTP " + r.status + "）");
    var data = await r.json();
    if (!r.ok) {
      var e = new Error((data && data.detail) || ("HTTP " + r.status));
      e.status = r.status;
      throw e;
    }
    return data;
  }

  // ---------------------------------------------------------------- 虚拟文件系统
  function mkdirp(py, path) {
    var parts = path.split("/").filter(Boolean);
    var cur = "";
    for (var i = 0; i < parts.length; i++) {
      cur += "/" + parts[i];
      try { py.FS.mkdir(cur); } catch (e) { /* 已存在 */ }
    }
  }

  function writeFS(py, rel, bytes) {
    var full = FS_ROOT + "/" + rel;
    mkdirp(py, full.split("/").slice(0, -1).join("/"));
    py.FS.writeFile(full, bytes);
  }

  async function installFile(py, bases, rel, opts) {
    opts = opts || {};
    var bytes = await fetchFrom(bases, rel, opts.gzip);
    writeFS(py, rel, bytes);
    return bytes.length;
  }

  // ---------------------------------------------------------------- 素材持久化
  /* 虚拟文件系统是内存的，刷新页面就没了 —— 用户在界面上传的半身像会丢。
   * 所以把整个 assets/ 目录（含处理后的图 + assets.json 索引）镜像到 IndexedDB。
   * 只镜像 assets/：它是唯一由用户操作产生、值得跨会话保留的状态。 */
  var IDB_NAME = "insurance-cover-agent";
  var IDB_STORE = "assets";

  function idb() {
    return new Promise(function (res, rej) {
      if (!window.indexedDB) return rej(new Error("no indexedDB"));
      var req = indexedDB.open(IDB_NAME, 1);
      req.onupgradeneeded = function () {
        var db = req.result;
        if (!db.objectStoreNames.contains(IDB_STORE)) db.createObjectStore(IDB_STORE);
      };
      req.onsuccess = function () { res(req.result); };
      req.onerror = function () { rej(req.error); };
    });
  }

  function idbSave(files) {
    return idb().then(function (db) {
      return new Promise(function (res, rej) {
        var tx = db.transaction(IDB_STORE, "readwrite");
        tx.objectStore(IDB_STORE).put(files, "snapshot");
        tx.oncomplete = res;
        tx.onerror = function () { rej(tx.error); };
      });
    });
  }

  function idbLoad() {
    return idb().then(function (db) {
      return new Promise(function (res, rej) {
        var tx = db.transaction(IDB_STORE, "readonly");
        var q = tx.objectStore(IDB_STORE).get("snapshot");
        q.onsuccess = function () { res(q.result || null); };
        q.onerror = function () { rej(q.error); };
      });
    }).catch(function () { return null; });
  }

  /* 把 assets/ 整个读出来（跳过 _builtin/，那份是随分发包来的只读源） */
  function snapshotAssets(py) {
    var out = {};
    function walk(dir) {
      var items;
      try { items = py.FS.readdir(dir); } catch (e) { return; }
      for (var i = 0; i < items.length; i++) {
        var n = items[i];
        if (n === "." || n === "..") continue;
        var full = dir + "/" + n;
        var st;
        try { st = py.FS.stat(full); } catch (e) { continue; }
        if (py.FS.isDir(st.mode)) {
          if (full === FS_ROOT + "/assets/_builtin") continue;
          walk(full);
        } else {
          out[full.slice(FS_ROOT.length + 1)] = py.FS.readFile(full);
        }
      }
    }
    walk(FS_ROOT + "/assets");
    return out;
  }

  // ---------------------------------------------------------------- 结果里的资源 URL
  /* 响应里的图片是 `/outputs/xxx/cover.png` 这种**绝对 URL**（本来是给 <img src> 用的）。
   * 浏览器里没有 HTTP 服务，得从虚拟文件系统读字节、转成 blob URL。
   * 每次生成前释放上一轮的 blob，否则连点几次就会攒下几十 MB 的 Blob。 */
  var RES_RE = /^\/(?:outputs|assets)\/.+\.(?:png|jpe?g|webp)$/i;
  var liveBlobs = [];

  function releaseBlobs() {
    for (var i = 0; i < liveBlobs.length; i++) {
      try { URL.revokeObjectURL(liveBlobs[i]); } catch (e) { /* ignore */ }
    }
    liveBlobs = [];
  }

  function toBlobUrl(py, url) {
    var rel = url.replace(/^\//, "");
    var bytes;
    try { bytes = py.FS.readFile(FS_ROOT + "/" + rel); } catch (e) { return url; }
    var ext = (rel.split(".").pop() || "png").toLowerCase();
    var mime = ext === "png" ? "image/png" : (ext === "webp" ? "image/webp" : "image/jpeg");
    var blobUrl = URL.createObjectURL(new Blob([bytes], { type: mime }));
    liveBlobs.push(blobUrl);
    return blobUrl;
  }

  function rewriteUrls(py, node) {
    if (typeof node === "string") return RES_RE.test(node) ? toBlobUrl(py, node) : node;
    if (Array.isArray(node)) {
      for (var i = 0; i < node.length; i++) node[i] = rewriteUrls(py, node[i]);
      return node;
    }
    if (node && typeof node === "object") {
      for (var k in node) {
        if (Object.prototype.hasOwnProperty.call(node, k)) node[k] = rewriteUrls(py, node[k]);
      }
      return node;
    }
    return node;
  }

  // ---------------------------------------------------------------- 加载遮罩
  var OVERLAY_ID = "engine-loading";

  function showOverlay() {
    if (document.getElementById(OVERLAY_ID)) return;
    var st = document.createElement("style");
    st.textContent =
      "#" + OVERLAY_ID + "{position:fixed;inset:0;z-index:9999;display:flex;flex-direction:column;" +
      "align-items:center;justify-content:center;gap:18px;background:linear-gradient(160deg,#F7FAFF,#EAF1FF);" +
      "font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;color:#1E293B;}" +
      "#" + OVERLAY_ID + " .bo{width:52px;height:52px;border-radius:50%;border:4px solid #D6E2FF;" +
      "border-top-color:#2B6BFF;animation:eospin 1s linear infinite;}" +
      "@keyframes eospin{to{transform:rotate(360deg)}}" +
      "#" + OVERLAY_ID + " .bt{font-size:17px;font-weight:700;letter-spacing:1px;}" +
      "#" + OVERLAY_ID + " .bs{font-size:13px;color:#64748B;min-height:20px;}" +
      "#" + OVERLAY_ID + " .bp{width:260px;height:6px;border-radius:99px;background:#DDE7FA;overflow:hidden;}" +
      "#" + OVERLAY_ID + " .bp i{display:block;height:100%;width:0;border-radius:99px;" +
      "background:linear-gradient(90deg,#4C7DFF,#8B5CF6);transition:width .35s;}" +
      "#" + OVERLAY_ID + " .bn{font-size:12px;color:#94A3B8;max-width:440px;text-align:center;line-height:1.7;}";
    document.head.appendChild(st);

    var el = document.createElement("div");
    el.id = OVERLAY_ID;
    el.innerHTML =
      '<div class="bo"></div>' +
      '<div class="bt">正在启动浏览器内渲染引擎</div>' +
      '<div class="bp"><i></i></div>' +
      '<div class="bs">准备中…</div>' +
      '<div class="bn">这个站点没有服务器后端，封面是把 Python + Pillow 渲染引擎' +
      '（WebAssembly）装进浏览器里跑的。首次打开需要下载运行时，之后会走缓存。</div>';
    document.body.appendChild(el);

    RT.onProgress = function (text, pct) {
      var s = el.querySelector(".bs");
      if (s) s.textContent = text;
      if (pct != null) {
        var b = el.querySelector(".bp i");
        if (b) b.style.width = pct + "%";
      }
    };
  }

  function hideOverlay() {
    var el = document.getElementById(OVERLAY_ID);
    if (el) el.remove();
  }

  // ---------------------------------------------------------------- 装载
  var manifest = null;
  var fontsLoaded = {};

  async function boot() {
    showOverlay();
    say("下载 Python 运行时（约 10MB，仅首次）…", 5);
    await loadScript(PYODIDE_URL + "pyodide.js");
    var py = await loadPyodide({ indexURL: PYODIDE_URL });
    RT.pyodide = py;
    say("装载 Pillow 图像库…", 34);
    await py.loadPackage("pillow");

    say("准备数据与引擎代码…", 48);
    manifest = await (await fetch(MANIFEST, { cache: "no-store" })).json();
    var bases = manifest.bases || ["./"];

    for (var i = 0; i < manifest.core.length; i++) {
      var pct = 48 + Math.round((i / manifest.core.length) * 20);
      say("装载 " + manifest.core[i].split("/").pop() + "…", pct);
      // 代码与数据走**同源**相对路径，不走 CDN：CDN 对分支有缓存，
      // 更新一次站点后可能拿到上一版 Python 代码，那种"改了没生效"极难排查。
      // 只有字体这种又大又几乎不变的文件才交给 CDN 加速。
      await installFile(py, ["./"], manifest.core[i], { gzip: false });
    }

    say("恢复素材库…", 70);
    var snap = await idbLoad();
    if (snap) {
      for (var rel in snap) {
        if (Object.prototype.hasOwnProperty.call(snap, rel)) {
          writeFS(py, rel, new Uint8Array(snap[rel]));
        }
      }
    }

    say("初始化引擎…", 88);
    py.runPython(
      "import sys\n" +
      "if " + JSON.stringify(FS_ROOT) + " not in sys.path: sys.path.insert(0, " + JSON.stringify(FS_ROOT) + ")\n" +
      "import os\n" +
      "os.makedirs(" + JSON.stringify(FS_ROOT + "/outputs") + ", exist_ok=True)\n" +
      "import api_facade"
    );
    var ping = JSON.parse(py.runPython("api_facade.ping()"));
    if (!ping.ok) throw new Error("引擎自检失败：" + ping.error);

    RT.call = makeBridgeCall(py, bases);
    RT.mode = "bridge";
    say("引擎就绪", 100);
    hideOverlay();
    _resolveReady("bridge");
  }

  async function ensureFonts(py, bases, families) {
    var todo = families.filter(function (f) { return !fontsLoaded[f]; });
    for (var i = 0; i < todo.length; i++) {
      var fam = todo[i];
      var list = (manifest.fonts && manifest.fonts[fam]) || [];
      for (var j = 0; j < list.length; j++) {
        var f = list[j];
        say("加载字体 " + f.rel.split("/").pop() + "…", null);
        await installFile(py, bases, f.rel, { gzip: !!f.gz });
      }
      fontsLoaded[fam] = true;
    }
  }

  function makeBridgeCall(py, bases) {
    /* 单次调用。参数走虚拟文件系统里的一个 JSON 文件，
     * 不在 Python 源码里拼字符串（见 api_facade.call_fs 的注释）。 */
    function once(path, body, method) {
      var req = JSON.stringify({ path: path, payload: body || {}, method: method || "POST" });
      mkdirp(py, "/tmp");
      py.FS.writeFile(REQ_FILE, new TextEncoder().encode(req));
      var raw = py.runPython("import api_facade\napi_facade.call_fs(" + JSON.stringify(REQ_FILE) + ")");
      return JSON.parse(raw);
    }

    return async function call(path, body, method) {
      // 让浏览器先把 loading 态渲染出来：下面执行的 Python 是同步的，
      // 一旦开始，主线程完全卡住，UI 没有任何更新机会。
      await new Promise(function (r) { setTimeout(r, 0); });

      if (!fontsLoaded.sans) {
        await ensureFonts(py, bases, ["sans"]);
        if (RT.onProgress) RT.onProgress("渲染中…", null);
      }
      var env = once(path, body, method);
      if (!env.ok && typeof env.error === "string" &&
          env.error.indexOf("serif") >= 0 && !fontsLoaded.serif) {
        await ensureFonts(py, bases, ["serif"]);
        env = once(path, body, method);
      }
      if (!env.ok) {
        var err = new Error(env.error || "引擎执行失败");
        err.kind = env.kind;
        err.traceback = env.traceback;
        throw err;
      }
      if (path.indexOf("/api/generate") === 0 || path.indexOf("/api/rerender") === 0 ||
          path.indexOf("/api/layout/compare") === 0) {
        releaseBlobs();
      }
      if (path.indexOf("/api/assets") === 0 && method && method !== "GET") {
        try { await idbSave(snapshotAssets(py)); } catch (e) { /* 存不下不影响本次使用 */ }
      }
      return rewriteUrls(py, env.data);
    };
  }

  // ---------------------------------------------------------------- 启动
  (async function main() {
    var mode = await probe();
    if (mode === "http") {
      RT.mode = "http";
      RT.call = httpCall;
      _resolveReady("http");
      return;
    }
    try {
      await boot();
    } catch (e) {
      RT.error = String((e && e.message) || e);
      RT.mode = "bridge";
      say("引擎装载失败：" + RT.error, 100);
      hideOverlay();
      RT.call = function () { return Promise.reject(new Error(RT.error)); };
      _resolveReady("bridge");
    }
  })();
})();
