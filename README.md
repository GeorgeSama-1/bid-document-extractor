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

项目提供面向单用户可信内网的 GPU 文档提取服务。浏览器访问 `http://172.20.0.160:8000` 后，可一次上传一个 PDF，选择服务器物理 GPU，并配置目录根名、三个 PP-Structure 预处理开关、VLM Endpoint、模型、API Key、timeout、max tokens 和 workers。页面会显示队列位置、阶段进度、脱敏日志、错误和取消操作；任务成功后可浏览结果并下载轻量素材 ZIP。

服务必须使用已经安装 GPU 版 PaddlePaddle、PaddleOCR 及项目依赖的 Python 环境，并以单实例、单 Uvicorn worker 运行。同一张 GPU 上的任务严格 FIFO 串行，不同 GPU 的任务可以并行。服务监听 `0.0.0.0:8000`，不提供登录或 HTTPS，只能通过主机防火墙开放给可信内网，禁止直接暴露到公网。

API Key 只保存在服务进程内存和任务子进程的 `VLM_API_KEY` 环境变量中，不进入 SQLite、命令行参数、日志、API 响应、输出文件或 ZIP。任务结束、取消或服务关闭后会清除内存中的 Key。页面提交成功后也会立即清空密码输入框，不会把 Key 写入 URL 或浏览器存储。

### systemd 部署

先编辑示例中的绝对项目路径、GPU Python 环境和运行用户，再安装并启动：

```bash
cd /ABSOLUTE/PATH/TO/bid_source/bid-document-extractor
/ABSOLUTE/PATH/TO/GPU_ENV/bin/python -m pip install -r requirements.txt
sudo cp deploy/bid-document-extractor.env.example /etc/bid-document-extractor.env
sudo cp deploy/bid-document-extractor.service.example /etc/systemd/system/bid-document-extractor.service
sudoedit /etc/bid-document-extractor.env
sudoedit /etc/systemd/system/bid-document-extractor.service
sudo systemctl daemon-reload
sudo systemctl enable --now bid-document-extractor.service
sudo systemctl status bid-document-extractor.service
sudo journalctl -u bid-document-extractor.service -f
```

完整安装说明见 `deploy/SERVER_INSTALL.md`。不要增加 Uvicorn worker 数量，也不要复制 unit 启动第二实例；`service_data/service.lock` 会阻止多实例破坏进程内队列和密钥隔离。

在 `/etc/bid-document-extractor.env` 中必须把 `BID_SOURCE_ROOT` 设置为仓库父目录，例如 `/bwopt/MODELS/hj/bid_source_v1`。服务把 `data/`、`outputs/` 和 `service_data/` 写在该目录下，仓库内部只保留代码。

任务成功后，页面只提供轻量素材 ZIP 下载，不展示中间文件列表或单文件下载入口。Web 归档直接复用 `scripts/export_material_pack.py` 背后的 `export_lightweight_material_pack()`，收集全部章节的 `material.md` 和 `image_items` 图片，不包含 `table_items/*.json`、图片 JSON、`ordered_material.json`、解析缓存和日志。ZIP 使用上传 PDF 去掉扩展名后的名称作为父目录，例如：

```text
material/
└── history/
    ├── 1、……/
    │   └── material.md
    ├── 2、……/
    ├── 3、补充文件/
    │   └── ……/
    │       ├── material.md
    │       └── image_items/
    └── 4、……/
```

`modules` 和表单中的逻辑 `path_root` 不会出现在 ZIP 中；`material.md` 内的图片引用会改写为可移植的相对路径。归档过程拒绝路径穿越与符号链接。

### 清空 Web 历史

必须先停止服务，避免任务数据库、锁文件和输出目录仍在使用：

```bash
sudo systemctl stop bid-document-extractor.service
cd /ABSOLUTE/PATH/TO/bid_source

# 清除任务数据库、上传副本、日志、锁文件和 ZIP 缓存。
rm -rf -- service_data

# 只清除 Web 服务生成的任务输出，保留其他手工流水线结果。
find outputs -mindepth 1 -maxdepth 1 \
  -type d -name 'job_*' \
  -exec rm -rf -- {} +

sudo systemctl start bid-document-extractor.service
```

如果只想清除浏览器任务历史并保留 `outputs/job_*` 解析结果，只删除 `service_data`。不要删除 `bid-document-extractor` 仓库、原始输入 PDF 或整个 `outputs` 目录。

常见故障检查：

- GPU 列表为空或报错：用 systemd 运行用户执行 `nvidia-smi`，确认驱动和 GPU 权限。
- PaddleOCR 启动失败：确认 unit 的 `ExecStart` 指向安装了 GPU 依赖的 Python 环境。
- 第二实例无法启动：确认旧服务和子进程已经退出，再检查 `systemctl status` 与日志；不要手工删除仍被持有的锁来并行启动。
- 任务排队不动：确认所选 GPU 上前一个任务是否仍在运行，必要时从页面取消并查看脱敏日志。
- 上传被拒绝：检查 PDF 扩展名、`%PDF` 文件头和 `BID_SERVICE_MAX_UPLOAD_BYTES`。

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
