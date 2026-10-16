# 3DSVG Studio 精简版

单条Python命令：真实PLY → 可选裁剪/去噪 → 姿态校正 → 正交斜视PNG → 人工描边SVG模板。无GUI、相机预览、工作流数据库或新重建算法。适用于家具及其他物体；默认部件层名称只是人工分类，可在Inkscape重命名。

## 依赖与启动

使用项目现有.venv中的NumPy、Open3D、OpenCV及既有辅助模块，不需要Streamlit、GPU或相机。新环境按requirements-svg.txt安装；发布版不携带历史重建功能。

```powershell
# 在当前仓库根目录执行
.\.venv\Scripts\python.exe run_3dsvg_studio.py --help
```

先使用仓库内唯一真实椅子RGB-D生成点云，再选择投影参数：

```powershell
.\.venv\Scripts\python.exe run_workflow.py prepare --capture-dir examples/chair/capture --output output/demo_01
.\.venv\Scripts\python.exe run_3dsvg_studio.py `
  --input output/demo_01/pointcloud.ply `
  --output "output/3dsvg_studio/chair_$(Get-Date -Format 'yyyyMMdd_HHmmss')" `
  --unit m --input-frame camera `
  --azimuth -45 --elevation 35.26438968 --width 1600 `
  --voxel-size 0.003
```

没有提供裁剪框时保留背景。首先看终端XYZ范围，再设置自己的--crop；不要套用其他椅子或旧实验的裁剪范围。工具不自动识别物体。示例的黑色坐垫深度有缺失，不能把投影成功视为完整几何恢复。

其他输入最小命令：

```powershell
.\.venv\Scripts\python.exe run_3dsvg_studio.py --input "D:\你的点云.ply" --output output/3dsvg_studio/new_object_01 --unit m
```

若坐标是毫米，改用 `--unit mm`。不知道单位时先确认来源；不能只因PLY扩展名就认为是米。未传unit会明确停止。

## 参数

| 参数 | 默认/格式 | 含义 |
| --- | --- | --- |
| --input | 必填PLY | 原文件只读 |
| --output | output/3dsvg_studio | 必须是不存在的新目录；不覆盖用户final.svg |
| --unit | 必须明确m/mm | 输入XYZ单位；mm实际乘0.001转米 |
| --input-frame | unchanged | 保留输入轴、标记未确认；camera显式选择光学X右Y下Z前；object表示已确认X右Y后Z上 |
| --crop | 六个数 | xmin xmax ymin ymax zmin zmax；**始终为米、原输入坐标轴、姿态校正之前** |
| --rotation | RX RY RZ，默认0 0 0 | 度；校正Rz@Ry@Rx在初始轴约定之后应用 |
| --origin | X Y Z，默认0 0 0 | 原输入坐标系中的米制原点；先减原点，再旋转 |
| --azimuth | -45度 | 观察者方向从校正后+X朝+Y |
| --elevation | 35.26438968度 | 观察者高于校正后XY面的角度；默认两角为等轴测 |
| --width | 1600像素 | 实际PNG宽度，高度按同一像素尺度计算；宽高均不超过6000 |
| --voxel-size | 0（关闭） | 米制体素降采样；可尝试0.003 |
| --remove-outliers | 默认关闭 | 可选统计去噪，20邻域、3倍标准差；注意细腿/轮子不能误删 |
| --padding-mm | 25 | 四周投影平面留白 |
| --point-radius | 1 | 实测点显示半径0～3像素；调大使点可见，不代表补全几何 |
| --mode | auto | 有色输入用RGB，无色用monochrome；还支持density；白底 |
| --center | X Y Z米 | 校正参考系中的投影中心，自动适配画布，不是透视相机焦点 |
| --hide-axes | 默认显示辅助轴 | 人工完成后也可隐藏Guides层 |
| --data-source | real | simulation只用于合成测试标签 |

省略input-frame时不会把相机坐标冒充家具坐标，只保留原始轴并提示待确认。camera初始转换为 `(x,y,z)→(x,z,-y)`，这不是自动测得的家具朝向。若图像方向不合适，调整rotation；若只是想换观察方向，调整azimuth/elevation，二者含义独立。

## 输出与几何约定

每次仅生成：

```text
输出目录/
  cleaned.ply       # 过滤后的米制点云；保持输入轴，不烘焙姿态校正
  reference.png     # 等比例正交斜视参考图，颜色按点深度缓冲遮挡
  template.svg      # 嵌入PNG，分层、空人工路径
  projection.json  # 单位、输入SHA、范围、裁剪/过滤、完整变换、比例、图像映射及警告
```

PNG与SVG之间为同一毫米/像素比例，保留半像素中心约定；不是改SVG的width/height伪造尺寸。SVG一用户单位为一投影毫米。**三维米、二维投影毫米、PNG像素是不同坐标，不可把斜视线长当作任意三维真实长度。**

cleaned.ply保持米制输入坐标，`T_furniture_input`记录从此坐标到投影参考坐标的姿态校正。`input_to_svg_mm`和`input_to_png_pixel_center`可核对任一点的参考图位置。单视角的盲区不补全；大角度转视图可能暴露缺失面。点大小、降采样和裁剪不能挽救错误的原始深度。

## Inkscape简短教程

1. 打开template.svg，Ctrl+Shift+L显示图层。Reference为嵌入照片式点云底图，保持锁定。
2. 选择Backrest、Seat、Armrests、Support、Base或Wheels层；其他物体可重命名，无该部件就留空。
3. 按B，沿可见轮廓少量点击/拖动节点绘制直线/曲线；Enter结束开放路径。只有完整闭合轮廓才点击起点闭合；不要把缺失区域强行连上。
4. Shift+Ctrl+F：填充设为无，描边设为图层预设颜色；轮廓可从0.6毫米线宽开始调整。按N调整节点。
5. 隐藏Reference和Guides，确认有真正的矢量轮廓；另存为final.svg，保留template.svg。程序不会代替用户完成该文件。
6. 若更改投影视角，旧路径不会自动适应，需新建输出目录、重新描边。实物尺寸需另外测量，不按图上投影线长猜测。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_3dsvg_studio.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试用已知边长三维盒子验证投影、单位、正交旋转、横纵比例、PNG/SVG映射、深度遮挡、图层与不可覆盖；合成数据不冒充真实扫描或完成描边。

仓库只附examples/chair这个实际案例。已知尺寸盒子在单元测试中按需生成，不把其他历史扫描或大型模型作为示例上传。测试数量和执行时间以本次运行及发布验证说明为准。
