# 3DSVG Studio：自动拆分SVG零件

入口：`split_svg_parts.py`。不需要AI、相机或新依赖；默认只用Python标准库，`--trim`使用项目已有NumPy与Matplotlib。不改动输入文件、旧Pipeline或旧的拆分结果。

## 已按零件分组的SVG

```powershell
# 在仓库根目录执行
.\.venv\Scripts\python.exe split_svg_parts.py `
  --input "D:\你的图.svg" `
  --output "output/3dsvg_studio/parts_$(Get-Date -Format 'yyyyMMdd_HHmmss')" `
  --trim --zip
```

默认按Inkscape部件图层拆分；没有图层时按顶层分组拆分。Reference、Guides、Dimensions、Background等已知辅助层自动跳过。没有分组时明确停止，不把每条线自动当成一个真实零件。

原曲线、填充和颜色、描边、隐藏状态、祖先组的transform、原有来源元数据均保留；不把路径坐标烘焙/缩放成另一套几何。包含人工补画的输入仍属于人工补画，本工具不会增加新的几何。

## 当前凳子：按11个实际部件拆分

当前补轮总图中的底座分在两个组，轮子共用一个组，所以需要显式配置，让脚本知道哪些线属于同一零件。配置一次后，同一SVG结构可重复运行：

```powershell
.\.venv\Scripts\python.exe split_svg_parts.py `
  --input examples/chair/final.svg `
  --output "output/3dsvg_studio/chair_parts_$(Get-Date -Format 'yyyyMMdd_HHmmss')" `
  --config config/svg_parts_chair.json --trim --zip
```

这个配置对应**已补轮总图**，不是旧chair_wireframe_draft.svg。不存在的路径ID会报错，防止误套。可先用下面的命令查看任意SVG中的分组和路径ID：

```powershell
.\.venv\Scripts\python.exe split_svg_parts.py --input "D:\你的图.svg" --list
```

## 自己配置部件

```json
{
  "parts": [
    {"name":"01_seat","label":"坐垫","elements":["seat"]},
    {"name":"02_base","label":"整个底座","elements":["rear_base","base"]},
    {"name":"03_wheel","label":"一个轮子","elements":["wheels_00","wheels_01"]}
  ]
}
```

elements是原SVG的**ID**，不是图层显示名称。可以指定整个group或单独path；多个ID合并为一个输出。路径重叠选择、漏填ID、同名文件均会报错；未分配几何数量会写入报告，方便检查漏选。

没有分组时，在Inkscape选择同一零件的所有线条，Ctrl+G分组，在“对象属性”设置唯一ID；然后保存SVG。嵌套图层内若既有直属路径又有子图层，需要显式配置以免漏线或重复分配。

## 输出与画布

输出目录包含每个零件的SVG、split_report.json及README.md。**零件文件统一为001.svg、002.svg……，不含部件名称。** 顺序使用原图图层顺序或配置parts顺序；名称与编号对应关系保留在报告中。`--zip`在目录旁生成同名ZIP。已有目录或ZIP不会覆盖，重新运行使用新目录。

默认保留整图画布、viewBox和单位。`--trim`可把画布裁到该零件并留白，不改变路径坐标或原显示比例；`--padding 12`表示留白12个原SVG坐标单位。SVG像素仍然是像素，不会因裁画布就变成实物毫米。

裁画布支持绝对/相对M/L/H/V/C/S/Q/T/Z路径和translate/scale/rotate/matrix/skew变换。含A/a弧线、CSS规则、文字/形状尚未转路径、裁剪/滤镜、复杂克隆或百分比页面时，原路径照常拆分并保留原画布，报告解释未裁紧的原因。不会用近似折线替代曲线。

支持内部defs资源（例如渐变）；缺失的跨部件克隆引用会明确停止，可在Inkscape取消克隆。外部图片、外链CSS、脚本和DTD不接纳。纯图片不是矢量零件；请把Reference图与实际部件图层分开。

隐藏零件仍保留原隐藏状态，必要时在Inkscape图层面板点眼睛显示。本工具按你提供的结构拆分，不进行语义识别，也不保证每个分组就是一个真实可制造零件。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_split_svg_parts.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
