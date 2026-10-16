# RGB-D自动轮廓初稿 → 人工分零件 → 编号导出

统一入口：`run_workflow.py auto-contours`，同时保留独立入口`rgbd_to_svg.py`。两者调用相同算法，使用已有NumPy和OpenCV，不需要新依赖、AI、GPU、Mesh或相机位姿。不改变原采集文件。

## 使用

先拍好同一时刻且Depth对齐Color的RGB-D，准备color.png、原始uint16 depth.png和camera_intrinsics.json。JSON须包含width、height、depth_scale、depth_scale_unit=meters_per_depth_unit、depth_aligned_to=color。

```powershell
cd .
.\.venv\Scripts\python.exe rgbd_to_svg.py `
  --color "你的采集目录/color.png" `
  --depth "你的采集目录/depth.png" `
  --intrinsics "你的采集目录/camera_intrinsics.json" `
  --roi 185 15 495 480 `
  --output "output/rgbd_auto_svg/run_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
```

使用统一工作流处理附带的唯一椅子示例：

```powershell
.\.venv\Scripts\python.exe run_workflow.py auto-contours `
  --capture-dir examples/chair/capture `
  --output "output/auto_contours_$(Get-Date -Format 'yyyyMMdd_HHmmss')" `
  --roi 185 15 495 480
```

自己拍摄时将capture-dir换成本次采集目录，并按实际照片调整ROI。此路线可直接从capture接入，prepare保持原先功能。

ROI为原RGB像素XMIN YMIN XMAX YMAX，右/下边界不包含在内。**上面的ROI只示范格式，应按你的照片物体范围调整。** 框应包含整个物体，并给边界留余量。脚本不需要精准抠图，但背景与目标颜色/深度接近时仍可能误分。

`--min-depth 0.2 --max-depth 3.0`为候选深度范围（米）。`--epsilon 1`为多边形简化容差（像素）；`--min-area 10`去除小轮廓；`--iterations 5`为GrabCut迭代次数。它们不代表实物尺寸或精度。

## 生成的文件

- `preview.png`：自动前景与轮廓预览，**先检查背景、地面和细腿有无误分**。
- `foreground_mask.png`：自动分割mask，可人工修正为同尺寸0/255二值PNG。
- `contours.svg`：只有真正矢量Path的轮廓初稿，不含照片。
- `editable.svg`：有照片Reference、自动轮廓及空零件层，供Inkscape继续分割。
- `report.json`：输入校验、分割参数、每条轮廓和限制。

背景误分时先调整ROI/深度范围。也可用图像编辑器修改mask，再加 `--mask corrected_mask.png` 输出到新目录；原始depth.png不应修改。脚本不会覆盖旧目录。

## 人工分割零件

1. Inkscape打开editable.svg，保持Reference锁定。
2. 在Automatic contours层选择线条，按N检查节点。需要拆开时，在工具栏使用“在所选节点处打断路径”，再用“路径→拆分”分离子路径。
3. 结合原RGB补画**可见的内部结构线**；外轮廓本身不足以区分椅背、坐垫、支撑和轮子。
4. 选择同一独立零件的所有路径，Ctrl+G分组，在对象属性中设置唯一ID。不要把所有轮子当作一个零件组。
5. 将确认的路径放进各自零件组/层；在另存的final.svg中移除剩余auto_contours候选层。仅隐藏候选层仍可能被拆分脚本导出。删除/修正多余背景边界，隐藏Reference；不能为得到闭合线就猜测缺失的实物结构。
6. 执行现有脚本导出纯编号文件：

```powershell
.\.venv\Scripts\python.exe run_workflow.py split `
  --input "你的final.svg" `
  --output "output/parts_$(Get-Date -Format 'yyyyMMdd_HHmmss')" --trim --zip
```

没有按零件分组时，拆分脚本不会理解结构。可以用配置按路径ID明确划分，详见现有SVG拆分说明。

editable.svg采用原RGB画布，是独立底稿，不符合照片项目photo/edit.svg的导出校验约定；请另存final.svg并使用split，不要直接传入run_photo_svg_workflow.py export。需要AI辅助时，可提供本次RGB和editable.svg，遵循[AI辅助描边指南](AI_ASSISTED_TRACING.md)完成校对。

## 限制

本脚本用Depth范围提供前景先验，再由RGB GrabCut分割边界，结果是**二维自动分割估计**。缺失深度也可能按RGB分类，但不表示补出了三维测量。SVG保留RGB透视和像素坐标，不是正交点云投影、真实等轴测图或毫米标定图。

支持多条分离轮廓和孔洞；孔洞可能来自分割错误，不一定是实物孔洞。检测到沿ROI/画面截断的边界时保留开放路径。细腿、反光面、颜色接近的地面仍需人工检查。算法不提供语义部件识别，也不保证一张图自动得到完整椅子。
