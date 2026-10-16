"""固定的照片→人工描边→校验导出工作流；不会自动为新照片猜测家具路径。"""
from copy import deepcopy
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET

from svg_isometric.common import read_json, sha256, write_json
from svg_isometric.create_photo_template import create, photo_dimensions
from svg_isometric.create_template import SVG, INK, element

ROOT = Path(__file__).resolve().parents[2]
NS = f'{{{SVG}}}'
NUMBER = r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?'
ARITY = dict(M=2, L=2, H=1, V=1, C=6, S=4, Q=4, T=2, A=7, Z=0)


def check_path(data):
    """检查标准SVG路径语法；原样保留曲线、开放路径和坐标，不替人闭合。"""
    tokens = re.findall(rf'[MmLlHhVvCcSsQqTtAaZz]|{NUMBER}', data)
    compact = re.sub(r'[\s,]', '', data)
    if ''.join(tokens) != compact or not tokens or tokens[0].upper() != 'M':
        raise ValueError('路径格式非法或未以M/m开始')
    i = 0; segments = 0; command = None
    while i < len(tokens):
        if tokens[i].isalpha():
            command = tokens[i]; i += 1
            if command.upper() == 'Z':
                command = None
                continue
        if command is None:
            raise ValueError('闭合命令之后缺少新的路径命令')
        n = ARITY[command.upper()]
        if i + n > len(tokens) or any(t.isalpha() for t in tokens[i:i+n]):
            raise ValueError('路径命令缺少坐标')
        v = [float(t) for t in tokens[i:i+n]]
        if not all(math.isfinite(t) for t in v):
            raise ValueError('路径含NaN/Inf或超大数值')
        if command.upper() == 'A' and (v[0] < 0 or v[1] < 0 or v[3] not in (0, 1) or v[4] not in (0, 1)):
            raise ValueError('椭圆弧半径或标志非法')
        if command.upper() != 'M': segments += 1
        i += n
        if command.upper() == 'M': command = 'l' if command == 'm' else 'L'
    if segments == 0:
        raise ValueError('路径只有移动点，没有实际线段；请先描边')


def style(node):
    result = {k: node.get(k) for k in ('stroke', 'fill', 'display', 'visibility', 'opacity') if node.get(k) is not None}
    for item in node.get('style', '').split(';'):
        if ':' in item:
            key, value = item.split(':', 1); result[key.strip()] = value.strip()
    return result


def safe_tree(root):
    """本流程只需要SVG内联样式及路径，不接纳脚本、外部资源或外部样式。"""
    for node in root.iter():
        tag = node.tag.rsplit('}', 1)[-1]
        if tag in ('script', 'foreignObject', 'use', 'style', 'animate', 'set', 'animateTransform'):
            raise ValueError(f'不支持SVG元素 {tag}；请保存为普通、内联样式的SVG路径')
        for key, value in node.attrib.items():
            local = key.rsplit('}', 1)[-1]
            if local.lower().startswith('on') or 'url(' in value.lower():
                raise ValueError('SVG包含事件或资源引用；本流程需要独立的普通描边')
            if local == 'href' and not (tag == 'image' and value.startswith('data:image/')):
                raise ValueError('SVG含外部资源引用，请将照片嵌入或移除引用')
            if local == 'transform':
                rest = value
                pattern = r'(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^()]*)\)'
                for match in re.finditer(pattern, value):
                    name, args = match.groups()
                    numbers = re.findall(NUMBER, args)
                    if re.sub(r'[\s,]', '', args) != ''.join(numbers):
                        raise ValueError('SVG变换参数格式非法')
                    valid_count = {'matrix':(6,), 'translate':(1,2), 'scale':(1,2), 'rotate':(1,3), 'skewX':(1,), 'skewY':(1,)}
                    if len(numbers) not in valid_count[name] or not all(math.isfinite(float(v)) for v in numbers):
                        raise ValueError('SVG变换含非法数值')
                    rest = rest.replace(match.group(), '', 1)
                if rest.strip(): raise ValueError('SVG含不支持的坐标变换')


def _metadata(root):
    for node in root.findall(NS+'metadata'):
        try:
            data = json.loads(node.text or '{}')
            if isinstance(data, dict) and 'source_photo_sha256' in data: return data
        except json.JSONDecodeError:
            pass
    raise ValueError('SVG缺少本照片来源元数据，请从工作流底稿开始编辑')


def _check_source(root, manifest):
    safe_tree(root)
    if root.tag != NS+'svg': raise ValueError('输入不是标准SVG')
    meta = _metadata(root)
    if meta['source_photo_sha256'] != manifest['source_photo_sha256']:
        raise ValueError('SVG绑定了不同照片，禁止把旧描边套用到新照片')
    actual = [float(x) for x in root.get('viewBox', '').replace(',', ' ').split()]
    expected = [0., 0., *manifest['page_size_px']]
    mapping = meta.get('source_photo_to_svg')
    if actual != expected or mapping != manifest['source_photo_to_svg']:
        raise ValueError('画布或照片映射已改变；请保持原页面，不要缩放页面或更换底图')


def _vectors(root):
    """去底图后只收家具路径；空图层或隐藏路径不能冒充完成的线稿。"""
    groups = []; path_rows = []
    skipped = {'reference', 'guides', 'dimensions'}
    def visit(node, inherited, layer, hidden=False):
        tag = node.tag.rsplit('}', 1)[-1]
        current = dict(inherited); current.update(style(node))
        hidden = hidden or current.get('display') == 'none' or current.get('visibility') in ('hidden', 'collapse')
        try: hidden = hidden or float(current.get('opacity', '1')) <= 0
        except ValueError: raise ValueError('请把SVG透明度设为普通数字0～1')
        if hidden: return
        if tag == 'g':
            for child in node: visit(child, current, layer, hidden)
        elif tag == 'path':
            check_path(node.get('d', ''))
            if current.get('stroke', 'none') == 'none' or current.get('fill', 'black') != 'none':
                raise ValueError('家具路径必须无填充、有描边：Inkscape按Shift+Ctrl+F设置')
            path_rows.append(dict(layer=layer, id=node.get('id'), stroke=current['stroke'],
                                  closed_subpaths=len(re.findall('[zZ]', node.get('d', '')))))
        elif tag not in ('title', 'desc', 'metadata'):
            raise ValueError(f'家具图层含 {tag}；请选中对象，Ctrl+Shift+C转为路径（文字尺寸交给测量配置）')
    root_style = style(root)
    for node in root:
        tag = node.tag.rsplit('}', 1)[-1]
        if node.get('id', '').lower() in skipped: continue
        if tag == 'g':
            before = len(path_rows); visit(node, root_style, node.get('id', 'unnamed'))
            if len(path_rows) > before: groups.append(deepcopy(node))
        elif tag in ('path', 'rect', 'circle', 'ellipse', 'image', 'text', 'polygon', 'polyline', 'line'):
            raise ValueError('请将家具路径放进部件图层；不要画在Reference/Guides/Dimensions层或根节点')
    if not path_rows:
        raise ValueError('还没有可见家具路径：请打开edit.svg，在Seat等部件图层用B描边，Ctrl+S保存后再导出')
    # 从已确认显示的图层复制后，仍去除隐藏节点，避免以后意外显示旧线条。
    def prune(node, inherited=None):
        current = dict(inherited or {}); current.update(style(node))
        for child in list(node):
            s = dict(current); s.update(style(child))
            if s.get('display') == 'none' or s.get('visibility') in ('hidden', 'collapse') or float(s.get('opacity','1')) <= 0:
                node.remove(child)
            else: prune(child, current)
    for group in groups: prune(group, root_style)
    return groups, path_rows


def _write_svg(path, root):
    with Path(path).open('xb') as stream:
        ET.ElementTree(root).write(stream, encoding='utf-8', xml_declaration=True)


def _save_state(project, state):
    # 仅更新本项目的进度文件；每次导出文件单独留档，不覆盖。
    temporary = project/'workflow.json.tmp'
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(state, stream, ensure_ascii=False, indent=2, allow_nan=False)
    os.replace(temporary, project/'workflow.json')


def manual_steps(project):
    script = f'& "{ROOT / ".venv/Scripts/python.exe"}" "{ROOT / "run_photo_svg_workflow.py"}"'
    return f'''# 无AI照片描边工作流

本项目：{project}

1. 人工检查照片：整件家具含轮子入框；拍摄距离足够；轮廓与背景易区分；保持家具静止。照片模糊或结构出画时先重拍，不用补线猜测。
2. 运行下面edit命令。在Inkscape打开 {project / 'edit.svg'}。若未安装Inkscape，请安装后重新执行；不要在浏览器中尝试编辑。
3. 按Ctrl+Shift+L打开图层面板。Reference是锁定照片，不要在这层画。选择Seat（紫）、Backrest（绿）、Support（青）、Base（橙）、Wheels（蓝）等部件图层；没有的部件留空。
4. 按B，用少量节点描可见轮廓；点击为直线，按住拖动为曲线。按Enter结束开放路径；只在真实轮廓确实闭合时才回到起点闭合。遮挡、出画部位留空，不猜测补全。
5. 按Shift+Ctrl+F，填充设为无，描边设为对应颜色，线宽建议1～1.2px；细节约0.5～0.6px。按N调整节点。若使用矩形/椭圆工具，Ctrl+Shift+C转路径。不要把照片自动轮廓化后的背景碎线当作家具结构。
6. 隐藏Reference和Guides，检查只剩清晰彩色线框；再显示Reference校对位置。不要调整页面/viewBox或更换照片。Ctrl+S保存edit.svg（普通或Inkscape SVG），不要保存成PNG。
7. 可选实测：用卷尺量高度/坐垫宽度等。在measurements.json中填写真实毫米值、confirmed=true、source=manual_measurement及画布上的标注端点。未知值不填；空列表表示暂不标尺寸。端点像素只决定文字/引线位置，不换算实物长度。
8. 人工确认线条没有画错或编造结构后，运行export --reviewed。脚本校验并去掉照片、参考线，保存新版本的独立SVG和报告。若已导出后又修改edit.svg，再执行同一导出命令，会生成新版本。

```powershell
{script} status --project "{project}"
{script} edit --project "{project}"
{script} export --project "{project}" --reviewed
```

测量文件格式（不要照填任何假尺寸）：
```json
{{"dimensions":[{{"id":"height","label":"Height","source":"manual_measurement","confirmed":true,"value_mm":实际测得的毫米数,"endpoints_svg_px":[[20,60],[20,400]]}}]}}
```
endpoints_svg_px必须在本项目画布内。没有测量时保持 {{"dimensions":[]}} 即可。

同一照片可复用同一项目。新照片必须新建项目并重新描边，不能复用旧照片几何。本流程输出透视照片示意图，无法直接获得隐藏结构、精密尺寸或真正等轴测三维图。
'''


def new_project(image, output_root=None, crop_px=None, seed_svg=None, project_path=None):
    source = Path(image).resolve()
    if not source.is_file(): raise FileNotFoundError(f'找不到照片：{source}')
    project = Path(project_path).resolve() if project_path else Path(output_root or ROOT/'output/photo_svg_workflows').resolve()/datetime.now().strftime('photo_%Y%m%d_%H%M%S_%f')
    # create本身拒绝覆盖，并复用已有真实照片/分层模板逻辑。
    create(source, project, crop_px=crop_px)
    meta = read_json(project/'chair_photo_project.json')
    state = dict(schema_version=1, workflow='photo_manual_vector_workflow', created_at=datetime.now().isoformat(),
                 source_photo=str(source), source_photo_sha256=meta['source_photo_sha256'],
                 source_photo_to_svg=meta['source_photo_to_svg'], page_size_px=meta['page_size_px'],
                 reference_snapshot=str(next((project/'input').iterdir()).relative_to(project)),
                 status='awaiting_manual_trace_and_review', exports=[], seed_svg=None)
    edit = ET.parse(project/'chair_photo_template.svg').getroot()
    if seed_svg:
        seed_path = Path(seed_svg).resolve(); seed = ET.parse(seed_path).getroot()
        _check_source(seed, state); groups, _ = _vectors(seed)
        ids = {g.get('id') for g in groups}
        for node in list(edit):
            if node.tag == NS+'g' and node.get('id') in ids: edit.remove(node)
        for group in groups: edit.append(group)
        state['seed_svg'] = dict(path=str(seed_path), sha256=sha256(seed_path))
        state['status'] = 'awaiting_manual_review'
    _write_svg(project/'edit.svg', edit)
    write_json(project/'measurements.json', dict(dimensions=[]))
    (project/'MANUAL_STEPS.md').write_text(manual_steps(project), encoding='utf-8')
    (project/'workbench.html').write_text('''<!doctype html><meta charset="utf-8"><title>照片描边工作台</title>
<style>body{font:18px sans-serif;margin:30px;background:#eee}img{background:white;width:760px;max-width:100%;height:auto}p{max-width:900px;line-height:1.6}</style>
<h1>照片描边工作台</h1><p>在Inkscape编辑edit.svg，Ctrl+S保存后，刷新此页查看。Reference和Guides的显示状态在Inkscape中切换。本页用于预览，不自动描边。</p>
<p><a href="MANUAL_STEPS.md">逐步人工操作说明</a> · <a href="edit.svg">编辑中的SVG</a></p><img src="edit.svg">''', encoding='utf-8')
    _save_state(project, state)
    return project


def load_project(project):
    project = Path(project).resolve(); state = read_json(project/'workflow.json')
    if state.get('workflow') != 'photo_manual_vector_workflow': raise ValueError('不是本流程创建的项目')
    for photo in (Path(state['source_photo']), project/state['reference_snapshot']):
        if not photo.is_file() or sha256(photo) != state['source_photo_sha256']:
            raise ValueError(f'原照片或项目内参考副本缺失/改变，停止导出：{photo}')
    return project, state


def status(project):
    project, state = load_project(project)
    try:
        root = ET.parse(project/'edit.svg').getroot(); _check_source(root, state)
        _, rows = _vectors(root); error = None
        photo_dimensions(project/'measurements.json', *state['page_size_px'])
    except (ValueError, ET.ParseError) as exc:
        rows = []; error = str(exc)
    edit_hash = sha256(project/'edit.svg'); measurement_hash = sha256(project/'measurements.json')
    latest = state['exports'][-1] if state['exports'] else None
    output_valid = latest and (Path(latest['directory'])/'furniture.svg').is_file() and sha256(Path(latest['directory'])/'furniture.svg')==latest['svg_sha256']
    stage='needs_manual_trace_or_correction' if error else ('exported_current_revision' if latest and output_valid and
                latest['edit_sha256']==edit_hash and latest['measurements_sha256']==measurement_hash else 'needs_manual_review_and_export')
    return dict(project=str(project), stage=stage,
                visible_path_count=len(rows), diagnostic=error, exports=state['exports'],
                next_action=error or '按MANUAL_STEPS.md人工检查线条；完成后执行export --reviewed；需要尺寸时先填measurements.json')


def export_project(project, reviewed=False):
    if not reviewed:
        raise ValueError('需要人工校对：隐藏照片确认线框、检查缺失结构不补画、Ctrl+S保存，再添加 --reviewed 导出')
    project, state = load_project(project)
    edit_path = project/'edit.svg'; root = ET.parse(edit_path).getroot(); _check_source(root, state)
    groups, rows = _vectors(root)
    w, h = state['page_size_px']; measurement_path = project/'measurements.json'
    dimensions = photo_dimensions(measurement_path, w, h)
    edit_hash, measurement_hash = sha256(edit_path), sha256(measurement_path)
    exports = project/'exports'; exports.mkdir(exist_ok=True)
    for version in range(1,1000000):
        destination = exports/f'version_{version:04d}'
        try: destination.mkdir()
        except FileExistsError: continue
        break
    else: raise ValueError('导出版本数量超限')
    result = ET.Element(NS+'svg', width=f'{w:g}px', height=f'{h:g}px', viewBox=f'0 0 {w:g} {h:g}', version='1.1')
    # 保留Inkscape的根样式/坐标约定，但页面固定；不把编辑底稿中的照片、辅助线或其他对象带入最终版。
    for key in ('style', 'fill', 'stroke', 'transform'): 
        if root.get(key) is not None: result.set(key, root.get(key))
    report = dict(status='exported_after_user_review', workflow='photo_manual_vector_workflow',
                  source_photo=state['source_photo'], source_photo_sha256=state['source_photo_sha256'],
                  source_photo_to_svg=state['source_photo_to_svg'], document_units='px', real_world_scale_calibrated=False,
                  geometry_source='manual_photo_trace', user_review_confirmed=True, depth_used=False,
                  path_count=len(rows), paths=rows, dimensions=dimensions, edit_sha256=edit_hash,
                  measurements_sha256=measurement_hash, exported_at=datetime.now().isoformat(),
                  svg=str(destination/'furniture.svg'),
                  limitations=['Photo perspective retained; path length is not physical furniture size.',
                               'Automated checks do not prove artistic correctness or completeness.',
                               'Open paths preserved; hidden or missing structure is not reconstructed.'])
    element(result,'title').text='Furniture: manually traced photo illustration'
    element(result,'desc').text='Perspective photo illustration. Measured dimensions are separate annotations. No metric geometry calibration.'
    element(result,'metadata',id='workflow_export').text=json.dumps(report,ensure_ascii=False,allow_nan=False)
    for group in groups: result.append(group)
    if dimensions:
        layer = element(result,'g',id='dimensions'); layer.set(f'{{{INK}}}groupmode','layer'); layer.set(f'{{{INK}}}label','Dimensions (measured separately)')
        for row in dimensions:
            (ax,ay),(bx,by)=row['endpoints_svg_px']
            g=element(layer,'g',id='measure_'+row['id'],**{'data-source':'manual_measurement'})
            element(g,'path',d=f'M {ax:g} {ay:g} L {bx:g} {by:g}',fill='none',stroke='#374151',**{'stroke-width':'1'})
            element(g,'text',x=(ax+bx)/2,y=(ay+by)/2-5,fill='#374151',stroke='none',**{'font-size':'10','text-anchor':'middle'}).text=f"{row['label']}: {row['value_mm']:g} mm"
    _write_svg(destination/'furniture.svg',result)
    # 实际检查最终文件，而非仅判断文件存在。
    check=ET.parse(destination/'furniture.svg').getroot()
    assert not check.findall('.//'+NS+'image')
    assert len(check.findall('.//'+NS+'path')) == len(rows)+len(dimensions)
    shutil.copyfile(edit_path,destination/'edit_snapshot.svg')
    shutil.copyfile(measurement_path,destination/'measurements_snapshot.json')
    report['svg_sha256']=sha256(destination/'furniture.svg')
    write_json(destination/'report.json',report)
    (destination/'preview.html').write_text('''<!doctype html><meta charset="utf-8"><title>家具SVG</title>
<style>body{font:18px sans-serif;background:#eee;margin:24px}img{background:white;width:760px;max-width:100%;height:auto}</style>
<p>照片透视人工描边；尺寸文字为单独实测，不能用SVG线长测量家具。</p><img src="furniture.svg">''',encoding='utf-8')
    state['exports'].append(dict(directory=str(destination), edit_sha256=edit_hash, measurements_sha256=measurement_hash, svg_sha256=report['svg_sha256']))
    state['status']='exported'; _save_state(project,state)
    return report


def open_editor(project):
    project,_=load_project(project)
    options=[shutil.which('inkscape'), r'C:\Program Files\Inkscape\bin\inkscape.exe', r'C:\Program Files\Inkscape\inkscape.exe',
             str(Path(os.environ.get('LOCALAPPDATA',''))/'Programs/Inkscape/bin/inkscape.exe')]
    executable=next((p for p in options if p and Path(p).is_file()),None)
    if not executable:
        return f'未找到Inkscape。请安装Inkscape桌面版，然后用它打开：{project / "edit.svg"}\n具体步骤：{project / "MANUAL_STEPS.md"}'
    subprocess.Popen([executable,str(project/'edit.svg')])
    return f'已打开编辑文件：{project / "edit.svg"}；描边/校对后Ctrl+S保存。'
