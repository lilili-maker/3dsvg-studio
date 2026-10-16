# 开发与贡献

本项目是本地命令行工作流，目标是帮助用户从实际RGB-D或PLY数据制作可人工编辑的SVG参考底稿和编号零件文件。

## 本地检查

在仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe run_workflow.py auto-contours --capture-dir examples/chair/capture --output output/check_auto --roi 185 15 495 480
.\.venv\Scripts\python.exe run_workflow.py split --input examples/chair/final.svg --output output/check_parts --config examples/chair/parts_config.json --trim --zip
```

重复验证请使用新的输出目录。不要把data、output、虚拟环境、原始录制、设备序列号、账号信息或令牌提交到仓库。

## 修改要求

- 保持输入数据不变，拒绝覆盖用户描边结果。
- 保持米/毫米/像素和照片透视/正交投影的区别明确。
- 自动候选轮廓必须与人工校对结果区分；不得将估计补线描述为真实扫描。
- 零件输出文件名保持 `001.svg`、`002.svg` 等纯编号，名称映射保存在报告。
- 算法改动增加对应的离线验证；硬件功能说明实际测试条件。
- 不引入默认上传用户图像、机器人控制或未经请求的GUI。

提交问题时提供执行命令、依赖版本、完整错误及可共享的最小输入；请先移除个人信息。

当前未附开源许可证。代码与示例素材的复制、修改和再分发需要相应授权。
