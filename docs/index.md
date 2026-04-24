# crack-tcaptcha

腾讯 T-Sec 天御验证码（TCaptcha）自动化求解器 —— 纯 HTTP 协议实现，无需 Selenium / Playwright。

## 支持的验证码类型

| 类型 | 求解方式 | 专题页 |
|---|---|---|
| 滑块 `slider` | OpenCV NCC 模板匹配 | [slider.md](slider.md) |
| 图标点击 `icon_click` | ddddocr 检测 + 模板匹配 | [icon-click.md](icon-click.md) |
| 文字点选 `word_click` | 本地 YOLO + Siamese（ddddocr 兜底） | [word-click.md](word-click.md) |
| 图像选择 `image_select` | LLM vision | [image-select.md](image-select.md) |

## 快速导航

- [架构总览](architecture.md) —— 三段式协议、分层结构、pipeline ↔ solver 映射
- [反向工程笔记](reverse-notes.md) —— JSONP、PoW、TDC.js、轨迹格式、检测向量
- 仓库根目录的 `README.md` —— 完整的安装与调用示例（中文）
