# 千川内容平台（广告物料合规检测）

审核设计师产出的广告物料（海报/长图/视频/文案）是否符合《广告法》及关联法规。

三种使用形态：**Web 产品**（上传→点击检测→在线报告）、**ZCode skill**（聊天里 `/审核物料`）、**CLI**（批量扫文件夹）。

## 线上部署（Docker）

代码自带 `Dockerfile`，任何支持容器或原生 Python 的平台都能跑：

**Render（免费档）**：
1. 把本仓库推到你的 GitHub
2. Render 控制台 → New → Web Service → 选择该仓库（环境选 Docker）
3. 环境变量添加 `SILICONFLOW_API_KEY=sk-你的key`
4. 部署完成后用分配的域名访问

**其他平台**（Railway / Zeabur / Fly.io / 自己的服务器）：
```bash
docker build -t ad-review .
docker run -p 8900:8900 -e SILICONFLOW_API_KEY=sk-你的key ad-review
```

> ⚠️ 注意：部署后所有访问者都会消耗你 key 的视觉模型额度，建议在硅基流动控制台为 key 设置限额；
> 免费档平台闲置会休眠，首次访问需要等待冷启动。

## Web 产品（本地运行）

本地起服务，浏览器上传物料、点击检测、在线看报告：

```bash
~/Desktop/合规检测系统.command      # 双击（服务已在跑则直接打开页面）
```

或手动启动：

```bash
cd ~/.zcode/skills/ad-review
.venv/bin/python -m uvicorn webapp.app:app --host 127.0.0.1 --port 8900
# 浏览器打开 http://127.0.0.1:8900
```

页面支持：拖拽/点选上传多文件（图片/视频/文档）、直接粘贴文案、点击"开始检测"、
实时进度（规则匹配→案例检索→视觉模型审核）、报告内嵌查看。
历史报告在 `reports/`，索引页用 `python scripts/preview.py` 生成。

## CLI 用法（快捷批量）

1. 设视觉模型 key（硅基流动 SiliconFlow）：
   ```bash
   cp .env.example .env && chmod 600 .env
   # 编辑 .env 填入 SILICONFLOW_API_KEY=sk-xxxx
   ```
   换用通义千问（DashScope）等其他兼容 OpenAI 协议的服务：改 `config.yml` 里的
   `VL_BASE_URL` / `VL_API_KEY_ENV` / `VL_MODEL` 三项即可。

2. 建依赖环境（首次）：
   ```bash
   cd ~/.zcode/skills/ad-review
   uv venv --python 3.12
   uv pip install -r requirements.txt
   ```

3. 在 ZCode 里触发：
   ```
   /审核物料 ./posters
   ```
   或直接跑脚本：
   ```bash
   uv run python scripts/review.py --input ./posters --kb kb --out reports
   ```

## 模型选择注意

硅基流动上 **Qwen3-VL-32B-Instruct 实测不能正确读图**（返回空结果或声称图片是白的），
已在 `config.yml` 中改用 `Qwen/Qwen3-VL-30B-A3B-Instruct`（MoE，读图正常）。
备选：`Qwen/Qwen3-VL-8B-Instruct`（更快）。

## 文件夹约定

把待审物料放进一个文件夹，按扩展名自动分类：
- 文本：`.txt .md .docx .pdf .xlsx .pptx`
- 图片：`.jpg .jpeg .png .webp`
- 视频：`.mp4 .mov .webm`

## 知识库

- `kb/rules/极限词.yml` — 绝对化/极限词清单 + 严重度 + 条款
- `kb/rules/广告法条款.yml` — 《广告法》核心条款索引
- `kb/rules/互联网广告管理办法.yml` — 弹窗/软文标识/AI生成标识等
- `kb/rules/敏感类目.yml` — 医疗/保健食品/金融/教育/酒/房地产/招商加盟特殊红线
- `kb/cases/*.md` — 种子案例库（标 `#种子库/待补` 的需用真实案例补充）

## 输出

`reports/<时间戳>-<物料名>.html`（浏览器直接打开预览）、`.md` 和 `.json`，按 高风险(必改)/中风险(建议)/低风险(提示) 分档。

生成索引页（把 reports/ 下所有报告转 HTML 并列出）：

```bash
uv run python scripts/preview.py    # 产出 reports/index.html，按时间倒序
```

## 注意

- 知识库是种子库，引用种子案例时请如实说明。
- 高风险项务必提示用户过法务，本工具不替代法律意见。
- 视频拆帧回退需要 ffmpeg（`brew install ffmpeg`）；缺 ffmpeg 时优先用 DashScope 原生视频输入，再不行只审首帧。
