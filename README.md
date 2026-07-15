# 标书历史信息入库前解析与召回验证系统 MVP

## 项目目标

当前项目不是最终知识库，也不是完整后端服务，而是"入库前解析与召回验证层"。

第一版聚焦 7 件事：

1. PDF 解析是否准确。
2. 表格是否能抽取。
3. 目录/章节是否能重建。
4. Excel 章节规则是否能和 PDF 真实章节匹配。
5. 可复用候选信息是否能被正确抽取。
6. 候选信息是否能被检索召回。
7. 所有流程是否可配置、可审查、可迭代。

## 为什么第一版不建数据库

这一版的关键问题是"解析与召回链路是否可靠"，不是"如何持久化到正式库"。

如果在 PDF 解析、章节重建、规则匹配、候选抽取、召回验证都还不稳定时就先建库，很容易把错误的数据结构和错误的业务判断固化下来。因此当前阶段全部中间结果都先输出为 JSON / JSONL / CSV，方便人工核查、回放、比对和迭代。

当解析、候选抽取、召回验证稳定后，再将这些中间结果映射到数据库。

## 整体流程

```text
Excel 规则表（可选）
  -> section_rules.json
  -> processing_plan.json
  -> PDF 解析 / 表格抽取 / 按需 OCR / PP-Structure / VLM 表格增强
  -> text_blocks / tables / ocr_results / merged_blocks / page_material_stream
  -> reconstructed_sections.json（或 TOC 叶子章节）
  -> section_match_results.json（规则驱动模式）
  -> reusable_candidates.json
  -> modules/ 目录结构（素材打包）
  -> chunks.jsonl
  -> retrieval_eval_report.json
```

## 安装方式

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 可选依赖

```bash
# 向量检索（可选）
pip install -r requirements-vector.txt
```

## GPU Web 服务

项目提供面向可信内网的 GPU 文档提取页面。用户上传一个 PDF、选择物理 GPU 并填写 VLM 参数后，服务会运行 PDF 目录驱动流水线，展示排队状态、解析进度和脱敏日志；任务成功后提供轻量素材 ZIP 下载。

### 1. 运行前提

- Linux 服务器可以执行 `nvidia-smi`。
- 使用 Python 3.10 或更高版本的独立环境。
- 该环境已安装与服务器 CUDA/驱动匹配的 GPU 版 PaddlePaddle 和 PaddleOCR。
- 服务器能够访问填写在页面中的 VLM Endpoint。
- 服务只能运行一个 Uvicorn worker、一个服务实例；同一 GPU 上的任务串行执行，不同 GPU 可以并行。

`requirements.txt` 安装项目通用依赖，但不固定 PaddlePaddle/PaddleOCR 的版本。请按服务器 CUDA 环境安装这两个组件，然后检查：

```bash
cd /bwopt/MODELS/hj/bid_source_v1/bid-document-extractor
/data/miniforge3/envs/ppstructure/bin/python -m pip install -r requirements.txt

nvidia-smi
/data/miniforge3/envs/ppstructure/bin/python -c \
  "import paddle; print(paddle.__version__, paddle.device.get_device())"
/data/miniforge3/envs/ppstructure/bin/python -c \
  "import paddleocr; print('PaddleOCR import OK')"
```

PaddleOCR 是 OCR/文档解析工具库，不是单一模型；项目服务当前通过它的 PP-StructureV3 能力进行版面定位。底层 OCR 或 VLM 模型可以替换，但需要保持当前调用接口兼容，或修改相应适配代码。

### 2. 服务器目录

仓库的父目录是运行根目录。推荐结构如下：

```text
/bwopt/MODELS/hj/bid_source_v1/
├── bid-document-extractor/  # Git 代码仓库
├── data/                    # 手工输入和配置
├── outputs/                 # 每个 Web 任务的解析结果
└── service_data/            # SQLite、上传副本、日志、锁和 ZIP 缓存
```

首次部署时执行：

```bash
cd /bwopt/MODELS/hj/bid_source_v1
git clone https://github.com/GeorgeSama-1/bid-document-extractor.git
mkdir -p data/raw data/configs outputs service_data
cd bid-document-extractor
```

如果仓库已经存在，只需进入仓库并更新：

```bash
cd /bwopt/MODELS/hj/bid_source_v1/bid-document-extractor
git pull origin main
```

不要把运行产生的 `data/`、`outputs/`、`service_data/` 放进仓库。`BID_SOURCE_ROOT` 必须填写父目录 `/bwopt/MODELS/hj/bid_source_v1`，不能填写仓库目录。

### 3. 前台试运行

第一次部署建议先在当前终端启动，确认环境与端口都正常：

```bash
conda activate ppstructure
cd /bwopt/MODELS/hj/bid_source_v1/bid-document-extractor

export BID_SOURCE_ROOT=/bwopt/MODELS/hj/bid_source_v1
export BID_SERVICE_HOST=0.0.0.0
export BID_SERVICE_PORT=8002
export BID_SERVICE_MAX_UPLOAD_BYTES=524288000
export BID_SERVICE_MAX_VLM_WORKERS=128

python -m scripts.run_service
```

看到 Uvicorn 启动信息后，另开终端检查：

```bash
curl http://127.0.0.1:8002/api/system/gpus
```

浏览器访问 `http://服务器IP:8002`。前台服务用 `Ctrl+C` 停止。环境变量赋值后必须使用 `export`，或写在同一条命令前；只执行 `BID_SERVICE_PORT=8002` 不会把变量传给随后启动的 Python 进程。

### 4. 安装为 systemd 服务

前台试运行正常后先按 `Ctrl+C` 停止它，再安装常驻服务。不要同时保留手动进程和 systemd 进程。

```bash
cd /bwopt/MODELS/hj/bid_source_v1/bid-document-extractor

sudo cp deploy/bid-document-extractor.env.example \
  /etc/bid-document-extractor.env
sudo cp deploy/bid-document-extractor.service.example \
  /etc/systemd/system/bid-document-extractor.service

sudoedit /etc/bid-document-extractor.env
sudoedit /etc/systemd/system/bid-document-extractor.service
```

将 `/etc/bid-document-extractor.env` 改为：

```ini
BID_SOURCE_ROOT=/bwopt/MODELS/hj/bid_source_v1
BID_SERVICE_HOST=0.0.0.0
BID_SERVICE_PORT=8002
BID_SERVICE_MAX_UPLOAD_BYTES=524288000
BID_SERVICE_MAX_VLM_WORKERS=128
```

将 unit 中的关键路径和用户改成服务器实际值：

```ini
User=server
WorkingDirectory=/bwopt/MODELS/hj/bid_source_v1/bid-document-extractor
EnvironmentFile=/etc/bid-document-extractor.env
ExecStart=/data/miniforge3/envs/ppstructure/bin/python -m scripts.run_service
```

安装并启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now bid-document-extractor.service
sudo systemctl status bid-document-extractor.service --no-pager
sudo journalctl -u bid-document-extractor.service -f
```

常用管理命令：

```bash
sudo systemctl start bid-document-extractor.service
sudo systemctl stop bid-document-extractor.service
sudo systemctl restart bid-document-extractor.service
sudo systemctl status bid-document-extractor.service --no-pager
sudo journalctl -u bid-document-extractor.service -n 200 --no-pager
```

出现 `Unit bid-document-extractor.service not found` 表示 unit 尚未复制到 `/etc/systemd/system/`，或者复制后没有执行 `sudo systemctl daemon-reload`。完整 unit 示例也可参见 [`deploy/SERVER_INSTALL.md`](deploy/SERVER_INSTALL.md)。

### 5. 浏览器使用方法

1. 打开 `http://服务器IP:8002`，确认页面能够列出 GPU。
2. 选择一个 `.pdf` 文件和物理 GPU。
3. “输出根目录名”只用于解析阶段的逻辑章节路径，通常保留 `PDF` 即可，不会出现在最终 ZIP 中。
4. “版面与 OCR”可启用或关闭 PP-Structure，并可选择它使用 GPU 或 CPU；方向分类、文档展平和文本行方向适合扫描旋转或拍照文档，普通电子 PDF 通常保持关闭。
5. “表格 VLM 增强”可独立启用或关闭。启用时填写 OpenAI 兼容的 Endpoint、模型名和 API Key；关闭后这些字段不再是必填项，流水线也不会发送 VLM 请求。
6. `Timeout` 是单次 VLM 请求超时；`Max tokens` 控制单次输出；`Workers` 控制 VLM 并发。首次验证建议把 Workers 调低，再根据 Endpoint 限流和网络能力增加。
7. 点击“上传并创建任务”，在任务列表中查看 `queued`、`running`、`succeeded`、`failed` 或 `cancelled` 状态。
8. 成功后进入任务详情，点击“下载完整 ZIP”。失败时先查看详情中的错误和脱敏日志。

页面提交成功后会立即清空 API Key 输入框。Key 只存在于服务进程内存和任务子进程的 `VLM_API_KEY` 环境变量中，不写入 SQLite、命令行、日志、API 响应、输出文件或 ZIP。

PP-Structure 版面分析和 VLM 表格增强默认启用，但都可以在创建任务时关闭。解析时会优先读取 PDF 内置书签目录；没有书签时，会尝试从 PDF 中可提取的印刷目录页推断目录。如果两种方式都无法得到可用目录，任务会提示“当前 PDF 没有可用目录”。纯扫描且没有书签的 PDF 目前不能仅靠正文自动生成可靠目录，需要先补充目录/书签或扩展目录识别逻辑。

### 6. 下载包内容

成功任务只提供轻量素材 ZIP。压缩包包含全部章节的 `material.md` 和 `image_items` 图片，不包含 `table_items/*.json`、图片 JSON、`ordered_material.json`、解析缓存或日志。目录结构为：

```text
<上传的 PDF 文件名，不含 .pdf>/
└── history/
    ├── 1、……/
    │   └── material.md
    ├── 2、……/
    │   └── material.md
    ├── 3、补充文件/
    │   └── 3.1、……/
    │       ├── material.md
    │       └── image_items/
    └── 4、……/
        └── material.md
```

`material.md` 中的图片链接会改写成 ZIP 内可用的相对路径。每次验证应解压到一个新的空目录，不要覆盖解压到旧的 `history/`，否则旧包残留文件会与新包合并，看起来像重复打包。可用下面的命令检查 ZIP 本身是否有重复成员：

```bash
rm -rf /tmp/material-unpack-check
mkdir -p /tmp/material-unpack-check
unzip job_<任务ID>.zip -d /tmp/material-unpack-check
unzip -Z1 job_<任务ID>.zip | sort | uniq -d
```

最后一条命令正常情况下没有输出。

### 7. 更新代码与重启

```bash
sudo systemctl stop bid-document-extractor.service
cd /bwopt/MODELS/hj/bid_source_v1/bid-document-extractor
git pull origin main
/data/miniforge3/envs/ppstructure/bin/python -m pip install -r requirements.txt
sudo systemctl start bid-document-extractor.service
sudo systemctl status bid-document-extractor.service --no-pager
```

仅执行 `git pull` 不会让已经运行的 Python 进程加载新代码，必须重启服务。若当前仍是前台启动，则按 `Ctrl+C` 停止后重新执行 `python -m scripts.run_service`。

### 8. 清空 Web 历史

页面右上角的“清空历史”可以一键删除所有已结束任务的数据库记录、上传副本、日志、ZIP 缓存和对应的 `outputs/job_*` 解析结果；正在排队或运行的任务会保留。任务列表中的“删除”可只删除一个已结束任务。

如果页面无法使用，也可以停服后手工清理：

```bash
sudo systemctl stop bid-document-extractor.service
cd /bwopt/MODELS/hj/bid_source_v1

# 删除浏览器任务历史、上传副本、日志、锁和 ZIP 缓存。
rm -rf -- service_data
mkdir -p service_data

# 删除 Web 任务解析结果，但保留 outputs 下其他手工结果。
find outputs -mindepth 1 -maxdepth 1 \
  -type d -name 'job_*' \
  -exec rm -rf -- {} +

sudo systemctl start bid-document-extractor.service
```

手工清理时，如果只想清空浏览器任务历史并保留解析结果，只重建 `service_data/`，不要执行 `find outputs ...`。不要删除代码仓库、原始输入 PDF 或整个 `outputs/`。

### 9. 常见故障

- GPU 列表为空：以 unit 中的 `User` 执行 `nvidia-smi`，检查驱动和权限。
- `cannot import name 'UTC' from datetime`：服务器是旧代码或旧依赖，先 `git pull`；当前代码兼容 Python 3.10。
- Paddle/PaddleOCR 导入失败：`ExecStart` 指向了错误的 Python 环境，或该环境没有安装 GPU 组件。
- 端口无法访问：先用 `curl 127.0.0.1:端口` 区分服务问题和防火墙问题；再检查 `BID_SERVICE_PORT` 与防火墙放行端口是否一致。
- 第二实例启动失败：检查是否还有手动启动的 `scripts.run_service` 或旧服务进程；`service_data/service.lock` 会主动拒绝多实例。
- 任务一直排队：同一 GPU 严格按提交顺序执行，检查该卡上前一个任务是否仍在运行。
- 上传被拒绝：文件必须以 `.pdf` 结尾、文件头必须是 `%PDF`，且大小不能超过 `BID_SERVICE_MAX_UPLOAD_BYTES`。
- `No reusable material files were produced`：先确认 `outputs/job_<ID>/modules/` 下确实存在章节 `material.md`，然后确认服务已拉取最新代码并完成重启。
- 下载后看到重复目录：不要把多个 ZIP 覆盖解压到同一个 `history/`；先用 `unzip -Z1 ... | sort | uniq -d` 判断重复是否真的存在于 ZIP 内。

服务没有登录和 HTTPS，只能通过服务器防火墙开放给可信内网，禁止直接暴露到公网。不要增加 Uvicorn worker 数量，也不要复制 unit 启动第二实例。

## 目录结构

```text
bid_source/
├── data/                           # 数据目录（与 bid-document-extractor 平级）
│   ├── raw/                        # 原始标书文件（PDF、Excel）
│   ├── configs/                    # 配置文件
│   └── test_queries.json           # 测试查询
├── outputs/                        # 输出目录（与 bid-document-extractor 平级）
├── service_data/                   # Web 任务数据库、上传副本、日志、锁和 ZIP 缓存
└── bid-document-extractor/         # 核心代码
    ├── bid_knowledge/              # 核心代码
    │   ├── cli.py                  # CLI 入口
    │   ├── config/                 # 规则加载、手动配置、处理计划构建
    │   ├── parsing/                # PDF 解析、表格抽取、OCR、章节重建、素材打包
    │   ├── matching/               # 规则与章节匹配
    │   ├── extraction/             # 候选信息抽取、策略路由、chunk 构建
    │   ├── retrieval/              # BM25/向量检索、召回评估
    │   ├── service/                # MCP Server、素材上下文服务
    │   ├── export/                 # 轻量级素材包导出
    │   ├── schemas/                # Pydantic 数据模型
    │   └── utils/                  # 工具函数
    ├── scripts/                    # 脚本
    ├── tests/                      # 测试
    ├── docs/                       # 文档
    ├── requirements.txt
    ├── requirements-vector.txt
    └── README.md
```

## 两条独立流水线

以下命令默认在 `bid-document-extractor/` 仓库目录执行，但输入文件默认从同级根目录的 `data/` 读取，输出默认写入同级根目录的 `outputs/`。也可以用 `BID_SOURCE_ROOT` 显式指定该根目录。

### Pipeline 1：PDF 目录驱动（推荐，不需要 Excel 规则）

直接从 PDF 的目录（TOC）提取叶子章节，自动打包素材。

```bash
# 基础用法
python -m bid_knowledge.cli pdf-toc-pipeline \
  --pdf "2、商务文件.pdf" \
  --out-dir outputs/pdf_toc_run \
  --path-root "商务文件"

# 完整用法（启用 PP-Structure + VLM 表格增强）
CUDA_VISIBLE_DEVICES=6 python -m bid_knowledge.cli pdf-toc-pipeline \
  --pdf "2、商务文件.pdf" \
  --out-dir outputs/pdf_toc_run_business_v11 \
  --path-root "商务文件" \
  --enable-pp-structure true \
  --pp-structure-device gpu \
  --pp-structure-use-doc-orientation-classify false \
  --pp-structure-use-doc-unwarping false \
  --pp-structure-use-textline-orientation false \
  --enable-vlm-table true \
  --vlm-table-endpoint "$VLM_ENDPOINT" \
  --vlm-table-model "$VLM_MODEL" \
  --vlm-table-api-key-env VLM_API_KEY \
  --vlm-table-timeout 1800 \
  --vlm-table-max-tokens 8192 \
  --vlm-table-workers 128 \
  --progress true
```

**参数说明：**

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--pdf` | 输入 PDF 文件路径 | 必需 |
| `--out-dir` | 输出目录 | 必需 |
| `--path-root` | 章节路径前缀 | `PDF` |
| `--enable-pp-structure` | 启用 PP-StructureV3 版面分析 | `false` |
| `--pp-structure-device` | PP-Structure 设备 | `gpu` |
| `--enable-vlm-table` | 启用 VLM 表格增强 | `false` |
| `--vlm-table-endpoint` | VLM API 地址 | 环境变量 `VLM_ENDPOINT` |
| `--vlm-table-model` | VLM 模型名 | 环境变量 `VLM_MODEL` |
| `--vlm-table-api-key-env` | API Key 环境变量名 | - |
| `--vlm-table-timeout` | VLM 请求超时（秒） | `180` |
| `--vlm-table-max-tokens` | VLM 最大 token 数 | `4096` |
| `--vlm-table-workers` | VLM 并发 worker 数 | `1` |
| `--progress` | 显示进度条 | `true` |

### Pipeline 2：规则驱动（需要 Excel 规则表）

按 Excel 规则表匹配 PDF 章节，抽取候选信息。

```bash
python -m bid_knowledge.cli pipeline \
  --rules-xlsx "价格文件-商务文件-技术文件章节分析.xlsx" \
  --pdf "2、商务文件.pdf" \
  --manual-config manual_config.example.json \
  --out-dir outputs/rule_run \
  --enable-ocr false
```

**参数说明：**

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--rules-xlsx` | Excel 规则表路径 | 必需 |
| `--pdf` | 输入 PDF 文件路径 | 必需 |
| `--manual-config` | 手动配置文件 | `None` |
| `--out-dir` | 输出目录 | 必需 |
| `--enable-ocr` | 启用 OCR | `false` |
| `--ocr-endpoint` | OCR API 地址 | 环境变量 `OCR_ENDPOINT` |
| `--ocr-model` | OCR 模型名 | 环境变量 `OCR_MODEL` |
| `--ocr-api-key` | OCR API Key | 环境变量 `OCR_API_KEY` |
| `--enable-pp-structure` | 启用 PP-StructureV3 | `false` |
| `--pp-structure-device` | PP-Structure 设备 | `gpu` |

## 两条流水线的区别

| 特性 | pdf-toc-pipeline | pipeline |
|------|------------------|----------|
| 需要 Excel 规则 | ❌ 不需要 | ✅ 需要 |
| 章节来源 | PDF 目录叶子节点 | Excel 规则匹配 |
| 适用场景 | 快速提取、无规则表 | 有规则表、需要精确匹配 |
| 输出结构 | 相同 | 相同 |

## 单步命令

### 1. 读取规则表

```bash
python -m bid_knowledge.cli load-rules \
  --rules-xlsx data/raw/价格文件-商务文件-技术文件章节分析.xlsx \
  --out outputs/rules/section_rules.json \
  --report outputs/rules/rule_load_report.json
```

### 2. 生成 processing plan

```bash
python -m bid_knowledge.cli build-plan \
  --rules outputs/rules/section_rules.json \
  --manual-config data/configs/manual_config.example.json \
  --out outputs/plan/processing_plan.json
```

### 3. 解析 PDF

```bash
python -m bid_knowledge.cli parse-pdf \
  --pdf data/raw/2、商务文件.pdf \
  --plan outputs/plan/processing_plan.json \
  --out-dir outputs/parsed
```

### 4. 抽取表格

```bash
python -m bid_knowledge.cli extract-tables \
  --pdf data/raw/2、商务文件.pdf \
  --plan outputs/plan/processing_plan.json \
  --out outputs/parsed/tables.json
```

### 5. 执行 OCR

```bash
python -m bid_knowledge.cli run-ocr \
  --pdf data/raw/2、商务文件.pdf \
  --plan outputs/plan/processing_plan.json \
  --parsed-dir outputs/parsed \
  --ocr-endpoint http://127.0.0.1:8000/v1/chat/completions \
  --ocr-model paddle-ocr \
  --out outputs/parsed/ocr_results.json
```

说明：

- 只对 `processing_plan` 中明确开启的页执行 OCR。
- 不做全量 OCR。
- OCR 失败会记录到 `ocr_results.json`，不会让整条链路直接崩溃。

### 6. 合并 OCR

```bash
python -m bid_knowledge.cli merge-ocr \
  --blocks outputs/parsed/text_blocks.json \
  --ocr outputs/parsed/ocr_results.json \
  --out outputs/parsed/text_blocks_merged.json
```

### 7. 运行 PP-StructureV3

```bash
python -m bid_knowledge.cli run-pp-structure \
  --input data/raw/2、商务文件.pdf \
  --out outputs/parsed/pp_structure_results.json \
  --device gpu
```

### 8. 重建章节

```bash
python -m bid_knowledge.cli build-sections \
  --blocks outputs/parsed/text_blocks_merged.json \
  --toc outputs/parsed/toc.json \
  --rules outputs/rules/section_rules.json \
  --out outputs/structure/reconstructed_sections.json
```

### 9. 匹配章节

```bash
python -m bid_knowledge.cli match-sections \
  --rules outputs/rules/section_rules.json \
  --sections outputs/structure/reconstructed_sections.json \
  --plan outputs/plan/processing_plan.json \
  --out outputs/structure/section_match_results.json
```

### 10. 抽取候选信息

```bash
python -m bid_knowledge.cli extract-candidates \
  --plan outputs/plan/processing_plan.json \
  --matches outputs/structure/section_match_results.json \
  --blocks outputs/parsed/text_blocks_merged.json \
  --tables outputs/parsed/tables.json \
  --out-json outputs/candidates/reusable_candidates.json \
  --out-csv outputs/candidates/candidate_report.csv
```

### 11. 打包素材

```bash
python -m bid_knowledge.cli package-materials \
  --candidates outputs/candidates/reusable_candidates.json \
  --blocks outputs/parsed/text_blocks_merged.json \
  --tables outputs/parsed/tables.json \
  --images outputs/parsed/images.json \
  --out-dir outputs/modules \
  --pdf data/raw/2、商务文件.pdf \
  --plan outputs/plan/processing_plan.json
```

### 12. 构建检索 chunks

```bash
python -m bid_knowledge.cli build-chunks \
  --candidates outputs/candidates/reusable_candidates.json \
  --out outputs/retrieval/chunks.jsonl
```

### 13. 检索测试

```bash
python -m bid_knowledge.cli search \
  --chunks outputs/retrieval/chunks.jsonl \
  --query "投标人基本情况表 公司基础信息" \
  --top-k 5 \
  --method bm25
```

### 14. 批量召回评估

```bash
python -m bid_knowledge.cli eval-retrieval \
  --chunks outputs/retrieval/chunks.jsonl \
  --queries data/test_queries.json \
  --out outputs/retrieval/retrieval_eval_report.json
```

## 输出文件说明

### pdf-toc-pipeline 输出结构

```text
outputs/pdf_toc_run_business_v11/
  parsed/
    document_meta.json
    toc.json
    text_blocks.json
    text_blocks_merged.json
    tables.json
    images.json
    page_layout_masks.json
    page_material_stream.json
    pp_structure_results.json
    table_regions/
    vlm_tables/
  candidates/
    toc_leaf_candidates.json
    toc_leaf_section_paths.json
  modules/
    商务文件/
      <一级模块>/
        <二级模块>/
          material.md
          material_meta.json
          ordered_material.json
          text_items/
          table_items/
          image_items/
          original/
  pdf_toc_pipeline_manifest.json
```

### pipeline 输出结构

```text
outputs/rule_run/
  rules/
    section_rules.json
    rule_load_report.json
  plan/
    processing_plan.json
  parsed/
    document_meta.json
    toc.json
    text_blocks.json
    text_blocks_merged.json
    tables.json
    images.json
    ocr_results.json
    page_images/
    page_material_stream.json
  structure/
    reconstructed_sections.json
    section_match_results.json
  candidates/
    reusable_candidates.json
    candidate_report.csv
  modules/
    <按章节路径组织>
  retrieval/
    chunks.jsonl
    retrieval_eval_report.json
```

## 环境变量配置

### OCR 配置

```bash
export OCR_ENDPOINT=http://127.0.0.1:8000/v1/chat/completions
export OCR_MODEL=paddle-ocr
export OCR_API_KEY=your_key
```

### VLM 表格增强配置

```bash
export VLM_ENDPOINT=http://your-vlm-api-endpoint
export VLM_MODEL=your-model-name
export VLM_API_KEY=your-api-key
```

### MCP Server 配置

```bash
export BID_MATERIAL_OUTPUTS_DIR=outputs
export BID_MATERIAL_PROJECTS_CONFIG=data/configs/material_projects.json
```

## MCP Server

提供 3 个工具供 AI Agent 调用：

### 1. get_bid_material_context

按 run_name + section_path/title 获取素材 Markdown。

```json
{
  "run_name": "pdf_toc_run_business_v11",
  "section_path": "商务文件 / 法定代表人授权委托书 / 被授权人身份证",
  "top_k": 5
}
```

### 2. list_bid_materials

列出某次解析的所有素材。

```json
{
  "run_name": "pdf_toc_run_business_v11",
  "limit": 200
}
```

### 3. get_bid_project_material_context

跨多个 run（商务/技术）获取素材。

```json
{
  "project_id": "project_001",
  "section_path": "商务文件 / 法定代表人授权委托书",
  "top_k": 5
}
```

### 启动 MCP Server

```bash
python -m bid_knowledge.service.mcp_server
```

## 素材导出

命令行和 Web 服务共用 `export_lightweight_material_pack()`。命令行默认导出全部 `modules/**/material.md` 和 `image_items` 图片，默认不包含 `table_items/*.json`：

```bash
python scripts/export_material_pack.py \
  --output-dir outputs/pdf_toc_run_business_v11 \
  --package-dir /tmp/material_pack \
  --zip outputs/material_pack.zip \
  --include-material-md true \
  --include-images true \
  --include-table-json false \
  --include-image-json false
```

## 如何做召回测试

1. 先构建 `chunks.jsonl`。
2. 用 `search` 命令做单条查询验证。
3. 用 `eval-retrieval` 对 `data/test_queries.json` 做批量评估。
4. 打开 `retrieval_eval_report.json` 看命中率、命中位置和 top_k 结果。

## 当前 MVP 限制

- 章节重建仍然是"TOC 优先 + 简单标题规则"的初版实现，不保证完美。
- 表格结构还原目前以二维行列为主，没有做复杂跨行跨列恢复。
- OCR 接口假定兼容类 OpenAI Chat Completions 风格返回，复杂自定义协议还需要适配。
- 向量召回是 optional，缺依赖时不会阻断主流程。
- 这一版没有正式业务数据库、多人审核与权限 UI、对象存储；Web 服务中的 SQLite 仅保存非敏感任务状态。

## 后续迭代方向

- 数据库入库映射。
- Web 审核界面。
- PostgreSQL + pgvector。
- MinIO 文件存储。
- 更强 OCR 和版面分析。
- 更强表格结构还原。
- 大模型字段抽取与复用建议。
- 人工审核后正式入库。
- AI 写标书时的章节级检索调用。

## 扩展原则

- 不强绑定数据库。
- 不强绑定某一个 OCR 服务。
- 不强绑定某一种 Excel 列名。
- 不强绑定商务文件。
- 不强绑定某一种候选类型。

当前这套系统的职责，是把历史标书从"原始文件"推进到"可审查、可召回、可评估"的状态。
