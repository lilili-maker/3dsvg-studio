# 椅子 SVG 示例：细节版与精简版

两个版本使用同一张真实 RGB 照片、相同的画布与坐标。细节版保留内部描线；精简版去掉装饰、高光和重复细线，保留外轮廓与必要结构，并统一加粗。

| 版本 | 整图 SVG | 预览 | 编号零件 | 路径数量 |
| --- | --- | --- | --- | ---: |
| 细节版（保留内部细线） | [final.svg](final.svg) | [PNG](detailed_preview.png) | [parts/](parts/) | 43 |
| 精简版（必要粗线条） | [simplified/final.svg](simplified/final.svg) | [PNG](simplified/preview.png) | [simplified/parts/](simplified/parts/) | 26 |

| 细节版 | 精简版 |
| --- | --- |
| ![细节版椅子 SVG](detailed_preview.png) | ![精简版椅子 SVG](simplified/preview.png) |

精简版删除了 17 条细节路径，保留路径的几何坐标、颜色、图层和原有零件归属；结构线宽从 1.05 px 调整为 2 px。坐垫厚度、靠背支撑、气压杆、底座和轮子结构仍然保留。具体删除记录见 [simplification_report.json](simplified/simplification_report.json)。

每版均含 11 份零件 SVG，文件名只使用 `001.svg`～`011.svg`。零件与名称的对应关系在各自的 `parts/README.md` 和 `parts/split_report.json` 中。

## 使用

使用 Inkscape 打开任一整图 SVG，可以继续编辑部件图层；浏览器也可直接查看。两个版本的 SVG 均为真实矢量路径，不依赖 PNG 底图才能显示。建议从精简版开始制作结构示意图，需要细节时再参考细节版。

在仓库根目录，可以重新导出精简版的编号零件；输出使用新的目录，避免覆盖原结果：

```powershell
$partsOutput = "output/chair_simplified_parts_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
.\.venv\Scripts\python.exe run_workflow.py split `
  --input examples/chair/simplified/final.svg `
  --config examples/chair/simplified/parts_config.json `
  --output $partsOutput --trim --zip
```

## 来源与限制

这两份文件是同一把带靠背旋转凳的照片透视线稿，不是两个独立采集案例。原始 RGB-D 在 [capture/](capture/)，来源说明见 [provenance.json](provenance.json)。精简不会修复实际采集中缺失的结构，也没有改变原始照片和深度数据。

两处轮子轮廓沿用原图已标记的人工估计补线（`rear_wheels_00`、`wheel_bottom_completed`），不能当作实测几何。没有实物尺寸标定，SVG 像素线长不能直接换算为实物毫米尺寸。
