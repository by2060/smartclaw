# PDF 图片页视觉提取说明

`doc_parser` 现已支持在 PDF 纯文本提取失败时，自动启用图片页/扫描页视觉识别兜底。

## 工作原理

1. 先按既有优先级执行 `markitdown -> pymupdf -> pypdf` 纯文本提取。
2. 当纯文本链路无法产出内容时，进入 `_extract_pdf_with_vision()`：
   - 使用 PyMuPDF 按页读取 PDF
   - 依据规则 `文本长度 >= 50 且无图像对象` 判断为文本页，否则视为图片页/扫描页
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
- `SMARTCLAW_DOC_PARSER_PDF_VISION_MAX_PAGES`：视觉兜底最多处理页数，默认 `50`
- `SMARTCLAW_DOC_PARSER_PDF_VISION_MAX_TOKENS`：每页视觉识别的输出 token 上限，默认 `1200`
- `SMARTCLAW_DOC_PARSER_PDF_VISION_TIMEOUT_S`：单页模型调用超时时间（秒），默认 `30`
- `SMARTCLAW_DOC_PARSER_PDF_VISION_MODEL`：可选，指定视觉模型
- `SMARTCLAW_DOC_PARSER_PDF_VISION_PROVIDER`：可选，指定模型 provider

## 已知限制

- 识别效果受扫描清晰度、旋转角度、遮挡情况影响
- 大文件或多页扫描件会增加处理时延
- 默认仅处理前 50 页扫描内容，超出部分会被跳过并在结果中提示
- 多模态模型对复杂表格和低质量手写内容可能存在漏识别或格式偏差
