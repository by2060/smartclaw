# PDF 图片页视觉提取说明

`doc_parser` 现已支持在 PDF 纯文本提取失败时，自动启用图片页/扫描页视觉识别兜底。

## 工作原理

1. 先判断 PDF 是否存在需要页级视觉补充的页面：
   - 当页面文本长度达到阈值（默认 50 字符）时，优先按文本页处理
   - 当页面文本很少或基本没有可提取文本时，视为图片页/扫描页
2. 对纯文本 PDF，继续按既有优先级执行 `markitdown -> pymupdf -> pypdf`。
3. 对扫描件、图片页或混合 PDF，进入 `_extract_pdf_with_vision()` 做页级整合：
   - 使用 PyMuPDF 按页读取 PDF
   - 文本页直接保留页面文本
   - 图片页按 `DPI=150` 渲染为 PNG
   - 将 PNG 以 base64 形式发送给多模态模型识别
   - 结果按页顺序整合，图片页内容前会添加 `## 第 N 页（图片识别）`

## 适用场景

- 扫描版合同
- 发票、收据、票据截图
- 嵌入整页图片的 PDF
- 手写笔记、拍照上传的文档页

## 配置项

可通过环境变量调整：

- `SMARTCLAW_DOC_PARSER_PDF_VISION_DPI`：图片页渲染 DPI，默认 `150`
- `SMARTCLAW_DOC_PARSER_PDF_VISION_MAX_SCAN_PAGES`：最多执行视觉识别的扫描/图片页数量，默认 `50`；不会截断整份 PDF 的文本页
- `SMARTCLAW_DOC_PARSER_PDF_VISION_MAX_TOKENS`：每页视觉识别的输出 token 上限，默认 `3000`
- `SMARTCLAW_DOC_PARSER_PDF_VISION_TIMEOUT_S`：单页模型调用超时时间（秒），默认 `30`
- `SMARTCLAW_DOC_PARSER_PDF_VISION_MODEL`：可选，指定视觉模型
- `SMARTCLAW_DOC_PARSER_PDF_VISION_PROVIDER`：可选，指定模型 provider

兼容性说明：

- 旧变量 `SMARTCLAW_DOC_PARSER_PDF_VISION_MAX_PAGES` 仍可兼容读取
- 新实现优先使用独立配置项 `SMARTCLAW_DOC_PARSER_PDF_VISION_MAX_SCAN_PAGES`

## 已知限制

- 识别效果受扫描清晰度、旋转角度、遮挡情况影响
- 大文件或多页扫描件会增加处理时延
- 默认最多处理 50 个扫描/图片页，超出上限的扫描页会被跳过并在结果中提示；其余文本页仍会继续保留
- 多模态模型对复杂表格和低质量手写内容可能存在漏识别或格式偏差
