#!/usr/bin/env python3
"""把 dist/ 发布到 GitHub Pages。

为什么用 REST API 而不是 `git push`：
    本机 → github.com 的 smart-http 通道会抖（`Empty reply from server`，或走代理时
    `CONNECT tunnel failed, 502`），而 REST API 往往照样通 —— 两条是不同路径，
    一条不通不代表另一条不通。用 Git Data API 做**一次原子提交**还有个好处：
    多文件只产生一个 commit，不会出现"推到一半"的中间状态。

流程：
    1. 检查仓库，不存在则创建（public，Pages 免费账号只支持 public）
    2. 读当前 main 的 tree 作为 base（空仓库则不带 base）
    3. 每个文件一个 blob → 一个 tree → 一个 commit → 移动 ref
    4. 开启 Pages 并轮询到 built
    5. 用 API 回读 commits/main 做验证（别只信本地 git log）

用法：
    python tools/deploy_github.py --repo cain0624/qianchuan-content-platform
    python tools/deploy_github.py --repo ... --dist dist --branch main
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

API = "https://api.github.com"


def get_token() -> str:
    """从 macOS 钥匙串取 GitHub 凭据。绝不打印 token 本身。"""
    try:
        p = subprocess.run(
            ["git", "credential-osxkeychain", "get"],
            input="protocol=https\nhost=github.com\n\n",
            capture_output=True, text=True, timeout=20)
        for line in p.stdout.splitlines():
            if line.startswith("password="):
                return line[len("password="):].strip()
    except Exception as e:
        print(f"读取钥匙串失败：{e}")
    tok = os.getenv("GITHUB_TOKEN", "").strip()
    if tok:
        return tok
    raise SystemExit("✗ 拿不到 GitHub 凭据（钥匙串为空且未设置 GITHUB_TOKEN）")


class GH:
    def __init__(self, token: str):
        self.token = token

    def __call__(self, method: str, path: str, body=None, ok=(200, 201, 204), timeout=180):
        url = path if path.startswith("http") else API + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "qianchuan-deploy",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode("utf-8")
                return r.status, (json.loads(raw) if raw.strip() else {})
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = {"message": raw[:400]}
            if e.code not in ok:
                return e.code, parsed
            return e.code, parsed
        except Exception as e:
            return 0, {"message": f"{type(e).__name__}: {e}"}


def collect(dist: str) -> list[str]:
    files = []
    for dirpath, dirnames, filenames in os.walk(dist):
        dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__")]
        for fn in filenames:
            if fn in (".DS_Store",) or fn.endswith(".pyc"):
                continue
            full = os.path.join(dirpath, fn)
            files.append(os.path.relpath(full, dist).replace(os.sep, "/"))
    return sorted(files)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="<owner>/<name>")
    ap.add_argument("--dist", default="dist")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--message", default="发布：千川内容平台（浏览器内可运行的封面/图文生成 Agent）")
    ap.add_argument("--skip-pages", action="store_true")
    args = ap.parse_args()

    owner, _, name = args.repo.partition("/")
    if not owner or not name:
        raise SystemExit("--repo 必须是 <owner>/<name>")

    dist = os.path.abspath(args.dist)
    if not os.path.isdir(dist):
        raise SystemExit(f"✗ 找不到发布目录 {dist}，先跑 tools/build_site.py")

    gh = GH(get_token())

    # ---- 账号确认
    st, me = gh("GET", "/user")
    if st != 200:
        raise SystemExit(f"✗ 凭据无效：{st} {me.get('message')}")
    print(f"账号 {me['login']}")

    # ---- 建仓（幂等）
    st, repo = gh("GET", f"/repos/{args.repo}", ok=(200, 404))
    if st == 404:
        print(f"创建仓库 {args.repo} …")
        st, repo = gh("POST", "/user/repos", {
            "name": name,
            "description": "千川内容平台 —— AI 封面/图文生成 Agent（保险金融垂类）。"
                           "无后端也能跑：Python + Pillow 渲染引擎通过 WebAssembly 跑在浏览器里。",
            "private": False,
            "has_issues": True,
            "has_wiki": False,
            "auto_init": False,
        })
        if st not in (200, 201):
            raise SystemExit(f"✗ 建仓失败：{st} {repo.get('message')}")
    else:
        print(f"仓库已存在 {args.repo}")
    print(f"  {repo.get('html_url')}　private={repo.get('private')}")

    # ---- 取 base（空仓库没有 main）
    st, ref = gh("GET", f"/repos/{args.repo}/git/ref/heads/{args.branch}", ok=(200, 404, 409))
    base_commit = ref.get("object", {}).get("sha") if st == 200 else None
    base_tree = None
    if base_commit:
        st2, cm = gh("GET", f"/repos/{args.repo}/git/commits/{base_commit}")
        if st2 == 200:
            base_tree = cm.get("tree", {}).get("sha")
        print(f"基于已有提交 {base_commit[:8]} 增量更新")
    else:
        print("空仓库，创建首个提交")

    # ---- 上传 blob
    files = collect(dist)
    total = sum(os.path.getsize(os.path.join(dist, f)) for f in files)
    print(f"\n上传 {len(files)} 个文件 / {total/1024/1024:.2f} MB")
    tree = []
    for i, rel in enumerate(files, 1):
        full = os.path.join(dist, rel)
        with open(full, "rb") as f:
            content = base64.b64encode(f.read()).decode("ascii")
        st, blob = gh("POST", f"/repos/{args.repo}/git/blobs",
                      {"content": content, "encoding": "base64"})
        if st not in (200, 201):
            raise SystemExit(f"✗ blob 上传失败 {rel}：{st} {blob.get('message')}")
        tree.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        if i % 10 == 0 or i == len(files):
            print(f"  {i}/{len(files)}")

    # ---- tree → commit → ref
    body = {"tree": tree}
    if base_tree:
        body["base_tree"] = base_tree
    st, tr = gh("POST", f"/repos/{args.repo}/git/trees", body, timeout=300)
    if st not in (200, 201):
        raise SystemExit(f"✗ tree 创建失败：{st} {tr.get('message')}")
    print(f"tree {tr['sha'][:8]}（{len(tr.get('tree', []))} 项）")

    cbody = {"message": args.message, "tree": tr["sha"]}
    if base_commit:
        cbody["parents"] = [base_commit]
    st, cm = gh("POST", f"/repos/{args.repo}/git/commits", cbody, timeout=300)
    if st not in (200, 201):
        raise SystemExit(f"✗ commit 创建失败：{st} {cm.get('message')}")
    print(f"commit {cm['sha'][:8]}")

    if base_commit:
        st, r = gh("PATCH", f"/repos/{args.repo}/git/refs/heads/{args.branch}",
                   {"sha": cm["sha"], "force": False})
        if st not in (200, 201):
            # 偶发 500，重试一次
            time.sleep(2)
            st, r = gh("PATCH", f"/repos/{args.repo}/git/refs/heads/{args.branch}",
                       {"sha": cm["sha"], "force": False})
    else:
        st, r = gh("POST", f"/repos/{args.repo}/git/refs",
                   {"ref": f"refs/heads/{args.branch}", "sha": cm["sha"]})
    if st not in (200, 201):
        raise SystemExit(f"✗ ref 更新失败：{st} {r.get('message')}")
    print(f"refs/heads/{args.branch} → {cm['sha'][:8]}")

    # ---- 用 API 回读确认（ground truth，而不是本地 git log）
    time.sleep(2)
    st, head = gh("GET", f"/repos/{args.repo}/commits/{args.branch}")
    if st == 200:
        print(f"✓ 远端 main 现在指向 {head['sha'][:8]}　「{head['commit']['message'].splitlines()[0][:50]}」")
        if head["sha"] != cm["sha"]:
            print("⚠ 远端 sha 与本次提交不一致，请检查")
    else:
        print(f"⚠ 回读失败：{st}")

    # ---- Pages
    if args.skip_pages:
        return 0
    print("\n开启 GitHub Pages …")
    st, pg = gh("GET", f"/repos/{args.repo}/pages", ok=(200, 404))
    if st == 404:
        st, pg = gh("POST", f"/repos/{args.repo}/pages",
                    {"source": {"branch": args.branch, "path": "/"}}, ok=(200, 201, 409))
        if st not in (200, 201):
            print(f"⚠ Pages 开启失败：{st} {pg.get('message')}")
        else:
            print("  已开启")
    elif st == 200:
        print(f"  Pages 已存在：{pg.get('html_url')}（状态 {pg.get('status')}）")
        # 源分支可能不对，纠正一次
        if (pg.get("source") or {}).get("branch") != args.branch:
            gh("PUT", f"/repos/{args.repo}/pages",
               {"source": {"branch": args.branch, "path": "/"}}, ok=(200, 204))
            print("  已把 Pages 源指向 " + args.branch)

    url = f"https://{owner}.github.io/{name}/"
    print(f"\n轮询构建状态（地址 {url}）")
    for i in range(40):
        time.sleep(6)
        st, pg = gh("GET", f"/repos/{args.repo}/pages")
        status = pg.get("status") if st == 200 else f"HTTP {st}"
        print(f"  [{i+1}] {status}")
        if status in ("built", "errored"):
            break
    st, pg = gh("GET", f"/repos/{args.repo}/pages")
    print(f"\n最终状态：{pg.get('status')}　{pg.get('html_url')}")
    print(f"访问地址：{url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
