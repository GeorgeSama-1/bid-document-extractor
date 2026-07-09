# PDF 可复用材料抽取交接总结

## 基本上下文

- 仓库路径：`/home/hujing/bid_source`
- 重点文件：`bid_knowledge/parsing/module_packager.py`
- 当前任务主线：通用 PDF 内容抽取与可复用材料打包，不做针对某个标书字段或项目的特例规则。

## 最近处理的问题

### 1. 表格边界与正文混淆

之前存在的问题：

- 表格上方贴得很近的纯文本可能被错误纳入表格。
- 表格内部 OCR 文本又可能重复出现在 `material.md`。
- 表格下方的说明文字，如“编制说明”，有时被塞进表格最后一行。

处理原则：

- 不写死“项目名称”“招标编号”“项目单位”等字段。
- 依赖 OCR 内容、表格区域、几何边界、表格结构和文本重复关系判断。
- 表格外的内容保留为正文。
- 表格内的内容进入表格。
- 被表格覆盖的 OCR 文本不再重复渲染到 `material.md`。

### 2. 连续表格与表格尾部说明

之前存在的问题：

- 连续表格之间夹正文时，容易被误判为同一个表格的一部分。
- 表格尾部说明或投标人/日期等内容可能进入表格最后一行。

处理思路：

- 按表格结构、行形态、几何覆盖范围、OCR 文本重复关系综合判断。
- 不针对具体文案做特例。
- 保持通用 PDF 抽取工具的原则。

### 3. 与 MinerU 的关系

可以这样解释：

- MinerU 更偏底层解析，负责 PDF 页面中的文本、表格、图片、版面区域识别。
- 当前项目在 MinerU 或其他解析结果之上做“可复用材料包”层。
- 我们做的不是重新发明底层 OCR/版面识别，而是把解析结果组织成标书复用端可消费的材料结构。

我们这边额外做的事情：

- 按 Excel 规则和 PDF 真实章节边界归档。
- 生成可复用材料目录。
- 重建文本、表格、图片的阅读顺序。
- 去除重复 OCR 文本。
- 抑制表格内部文字、图片内部文字、页眉页脚等噪声。
- 生成 `material.md`、`ordered_material.json`、`table_items`、`image_items` 等材料包文件。
- 为标书复用端提供可直接打包的轻量输出。

## 标书复用端打包方式

默认轻量包：

```bash
python scripts/export_material_pack.py \
  --output-dir outputs/pdf_toc_run_tech_test2
```

默认生成：

```text
outputs/pdf_toc_run_tech_test2/material_pack/
outputs/pdf_toc_run_tech_test2/material_pack.zip
```

默认包含：

```text
modules/**/material.md
modules/**/image_items/*.png / *.jpg 等图片文件
```

如果复用端需要结构化表格、图片元信息、顺序信息：

```bash
python scripts/export_material_pack.py \
  --output-dir outputs/pdf_toc_run_tech_test2 \
  --include-table-json true \
  --include-image-json true \
  --include-ordered-material-json true \
  --include-manifest true
```

判断方式：

- 只给前端展示和人工复用：默认包通常够用。
- 需要直接读取表格 JSON、图片 JSON、顺序信息：使用带 `--include-*` 的完整命令。

## 章印/装饰图片处理

最新相关提交：

```text
2af53bc fix: filter decorative stamp images from materials
```

这次修复覆盖两层：

### 1. 导出阶段前置剔除

对以下图片直接从材料流中剔除：

- `seal_or_stamp`
- `watermark`
- `decorative_image`
- 通过像素特征识别出的红色章印图

剔除后不会进入：

```text
image_items/
ordered_material.json
material.md
```

### 2. Markdown 渲染阶段兜底

之前 `material.md` 只判断：

```python
material_role == "decorative_image"
```

如果图片没有 `material_role`，但有：

```python
image_kind = "seal_or_stamp"
```

仍可能被写进 `material.md`。

现在 `_is_decorative_material_image` 已改为：

```python
def _is_decorative_material_image(item):
    if material_role == "decorative_image":
        return True
    return _classify_image_kind(item) in {"decorative_image", "seal_or_stamp", "watermark"}
```

这样即使缺少 `material_role`，只要能被分类为章印/水印/装饰图，也不会进入 `material.md`。

## 章印仍出现时的排查路径

如果新窗口继续排查“章印仍出现”，先确认来源：

### 1. 独立图片对象

检查：

```text
modules/**/image_items/*.json
ordered_material.json
material.md
```

重点看字段：

```json
{
  "image_kind": "...",
  "material_role": "...",
  "file_path": "..."
}
```

如果 `ordered_material.json` 里仍有章印图片 item，说明还有某条图片导出路径没有经过 `_drop_decorative_exported_image`。

### 2. Markdown 链接残留

如果 `material.md` 里仍出现：

```markdown
![...](image_items/xxx.png)
```

检查对应 ordered item 是否缺少：

```json
"material_role": "decorative_image"
```

以及 `_is_decorative_material_image` 是否能通过 `image_kind` 或像素判断识别。

### 3. 表格截图或整页截图内部的章印

如果章印不是独立图片，而是盖在表格截图、页面截图、图片区域内部，那么当前独立图片过滤不会移除。

这种情况需要另做：

- 裁剪前图像清洗。
- 表格截图中的章印擦除。
- 或在喂给 VLM 前对图片区域做红章 mask/去除。

这是另一个问题，不属于当前 `image_items` 独立图片过滤逻辑。

## 最近重要提交

```text
2af53bc fix: filter decorative stamp images from materials
c927f59 fix: detect red stamp images by pixels
92c64c8 fix: classify decorative images in materials
b7577b2 fix: handle text crossing table boundaries
050a05a fix: use precise table regions for text suppression
5042cb0 fix: preserve text above table separators
```

## 最近验证

最近一次提交前已执行：

```bash
pytest tests/test_module_packager.py -q
python3 -m compileall bid_knowledge/parsing/module_packager.py
```

结果：

```text
92 passed
compileall 通过
```

## 新窗口继续时的建议

如果继续处理章印问题，建议先确认具体样本中的章印属于哪一种：

1. 独立图片对象。
2. PP-Structure 检出的图片区域。
3. PDF embedded image。
4. 表格截图内部的章印。
5. 整页截图/页眉页脚图内部的章印。

只有第 1 到第 3 类适合继续在 `module_packager.py` 的图片材料过滤层处理。

第 4 和第 5 类应该进入“图像内容清洗/喂模型前预处理”阶段处理。
