"""3DSVG Studio：按已有图层/分组或显式配置拆分SVG；不识别、猜测或补全新零件。"""
from copy import deepcopy
from pathlib import Path
import argparse
import hashlib
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

SVG='http://www.w3.org/2000/svg'; INK='http://www.inkscape.org/namespaces/inkscape'
NS='{'+SVG+'}'; ET.register_namespace('',SVG); ET.register_namespace('inkscape',INK)
NUMBER=r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?'
SHAPES={'path','rect','circle','ellipse','line','polyline','polygon'}
SKIP={'reference','guides','dimensions','background','参考','参考图','辅助线','尺寸','背景'}


def local(node): return node.tag.rsplit('}',1)[-1]
def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read_json(path): return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def read_svg(path):
    data=Path(path).read_bytes()
    if len(data)>32*1024*1024: raise ValueError('SVG超过32MB，请先清理不需要的嵌入图片')
    # 不展开DTD/实体，也不执行SVG脚本或外部资源。
    if re.search(br'<!\s*(?:DOCTYPE|ENTITY)',data,re.I): raise ValueError('不支持含DTD或实体声明的SVG')
    root=ET.fromstring(data)
    if root.tag!=NS+'svg': raise ValueError('根节点必须为标准SVG')
    ids={}
    for node in root.iter():
        tag=local(node)
        if tag in ('script','foreignObject','animate','animateTransform','animateMotion','set'):
            raise ValueError(f'不支持活动SVG元素：{tag}')
        id_=node.get('id')
        if id_:
            if id_ in ids: raise ValueError(f'SVG存在重复ID：{id_}')
            ids[id_]=node
        for key,value in node.attrib.items():
            k=key.rsplit('}',1)[-1]
            if k.lower().startswith('on'): raise ValueError('不支持SVG事件属性')
            if k=='href' and not (value.startswith('#') or (tag=='image' and value.startswith('data:image/png;base64,'))):
                raise ValueError('SVG含外部资源链接；请先嵌入PNG或转为普通路径')
            check_urls(value)
        if tag=='style':
            text=node.text or ''
            if '@import' in text.lower(): raise ValueError('不支持外部CSS导入')
            check_urls(text)
    return root,ids


def check_urls(text):
    for value in re.findall(r'url\(\s*[\"\']?([^\)\"\']+)',text,re.I):
        if not value.strip().startswith('#'): raise ValueError('SVG含外部url资源；请保存为独立SVG')


def vectors(node):
    return [p for p in node.iter() if local(p) in SHAPES and (local(p)!='path' or p.get('d','').strip())]


def list_groups(root):
    rows=[]
    for node in root.iter(NS+'g'):
        if node.get('id'):
            rows.append(dict(id=node.get('id'),label=node.get('{'+INK+'}label',node.get('id')),
                             is_layer=node.get('{'+INK+'}groupmode')=='layer',vector_count=len(vectors(node))))
    return rows


def auto_parts(root):
    def ignored(node):
        return node.get('id','').lower() in SKIP or node.get('{'+INK+'}label','').lower() in SKIP
    def discover(parent,layer_mode):
        found=[]
        for node in parent:
            if local(node)!='g' or ignored(node): continue
            if layer_mode and node.get('{'+INK+'}groupmode')!='layer':
                found.extend(discover(node,True)); continue
            nested=discover(node,True) if layer_mode else []
            if nested:
                # 父层中另有路径时，不能悄悄丢掉，也不能与子层重复导出。
                covered={id(v) for child in nested for v in vectors(child)}
                own={id(v) for v in vectors(node)}
                if own-covered: raise ValueError('嵌套图层同时包含直属几何，请用--config明确指定零件')
                found.extend(nested)
            elif vectors(node): found.append(node)
        return found
    nodes=discover(root,True)
    if not nodes: nodes=discover(root,False)
    if not nodes: raise ValueError('没有可用部件图层/分组；先在Inkscape分组并命名，或用--config按路径ID指定')
    return [dict(name=node.get('id') or f'part_{i+1:02d}',label=node.get('{'+INK+'}label',node.get('id','Part')),
                 elements=[node.get('id')]) for i,node in enumerate(nodes)]


def filename(name):
    if not isinstance(name,str): raise ValueError('零件name必须是字符串')
    name=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',name).strip(' .')
    if not name or name.upper().split('.')[0] in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}:
        raise ValueError('零件文件名为空或使用Windows保留名称')
    return name


def subset(node,selected):
    """保留原有祖先组，不能只复制path而丢失继承颜色和嵌套transform。"""
    if node.get('id') in selected: return deepcopy(node)
    result=ET.Element(node.tag,dict(node.attrib));result.text=node.text;result.tail=node.tail
    for child in node:
        match=subset(child,selected)
        if match is not None: result.append(match)
    return result if len(result) else None


def write_svg(path,root):
    with path.open('xb') as stream: ET.ElementTree(root).write(stream,encoding='utf-8',xml_declaration=True)


def path_geometry(data):
    """用于可选裁画布：支持常见绝对/相对直线和贝塞尔；A弧线不改写、不近似。"""
    import numpy as np
    from matplotlib.path import Path as MP
    tokens=re.findall(rf'[MmLlHhVvCcSsQqTtAaZz]|{NUMBER}',data)
    if ''.join(tokens)!=re.sub(r'[\s,]','',data) or not tokens or tokens[0].upper()!='M': raise ValueError('路径语法不能用于裁画布')
    arity=dict(M=2,L=2,H=1,V=1,C=6,S=4,Q=4,T=2,A=7)
    vertices=[];codes=[];point=np.zeros(2);start=point.copy();previous='';handle=None;i=0;command=None
    while i<len(tokens):
        if tokens[i].isalpha():
            command=tokens[i];i+=1
            if command.upper()=='Z':
                vertices.append(start.copy());codes.append(MP.CLOSEPOLY);point=start.copy();previous='Z';handle=None;command=None;continue
        if command is None: raise ValueError('路径参数缺少命令')
        op=command.upper()
        if op=='A': raise ValueError('含A/a弧线，保留原画布；路径没有被改写')
        n=arity[op]
        if i+n>len(tokens): raise ValueError('路径参数不完整')
        values=np.array([float(x) for x in tokens[i:i+n]]);i+=n
        if not np.isfinite(values).all(): raise ValueError('非有限路径坐标')
        if op in ('H','V'):
            end=point.copy();axis=0 if op=='H' else 1
            end[axis]=values[0]+(point[axis] if command.islower() else 0)
            points=[end];new_codes=[MP.LINETO];new_handle=None
        else:
            q=values.reshape(-1,2)
            if command.islower(): q=q+point
            if op in ('M','L'):points=q;new_codes=[MP.MOVETO if op=='M' else MP.LINETO];new_handle=None
            elif op in ('C','Q'):
                points=q;new_codes=[MP.CURVE4 if op=='C' else MP.CURVE3]*len(q);new_handle=q[-2]
            elif op=='S':
                control=2*point-handle if previous in ('C','S') else point
                points=[control,*q];new_codes=[MP.CURVE4]*3;new_handle=q[0]
            elif op=='T':
                control=2*point-handle if previous in ('Q','T') else point
                points=[control,*q];new_codes=[MP.CURVE3]*2;new_handle=control
        vertices.extend(points);codes.extend(new_codes);point=np.asarray(points[-1]);handle=new_handle;previous=op
        if op=='M':start=point.copy();command='l' if command.islower() else 'L'
    if not vertices: raise ValueError('空路径')
    return MP(vertices,codes)


def matrix(text):
    import numpy as np
    result=np.eye(3);rest=text
    for match in re.finditer(r'(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^()]*)\)',text):
        name,args=match.groups();tokens=re.findall(NUMBER,args)
        if ''.join(tokens)!=re.sub(r'[\s,]','',args): raise ValueError('变换参数格式不支持裁画布')
        v=[float(x) for x in tokens]
        valid={'matrix':(6,),'translate':(1,2),'scale':(1,2),'rotate':(1,3),'skewX':(1,),'skewY':(1,)}
        if len(v) not in valid[name] or not all(math.isfinite(x) for x in v): raise ValueError('变换参数非法')
        a=np.eye(3)
        if name=='matrix':a=np.array([[v[0],v[2],v[4]],[v[1],v[3],v[5]],[0,0,1.]])
        elif name=='translate':a[:2,2]=[v[0],v[1] if len(v)==2 else 0.]
        elif name=='scale':a[0,0]=v[0];a[1,1]=v[1] if len(v)==2 else v[0]
        elif name=='rotate':
            t=math.radians(v[0]);a[:2,:2]=[[math.cos(t),-math.sin(t)],[math.sin(t),math.cos(t)]]
            if len(v)==3:a[:2,2]=np.array(v[1:])-a[:2,:2]@v[1:]
        else:a[0 if name=='skewX' else 1,1 if name=='skewX' else 0]=math.tan(math.radians(v[0]))
        result=result@a;rest=rest.replace(match.group(),'',1)
    if rest.strip(): raise ValueError('不支持的transform，保留原画布')
    return result


def trim_canvas(root,padding):
    import numpy as np
    from matplotlib.transforms import Affine2D
    if root.get('transform') or root.findall('.//'+NS+'style') or root.findall('.//'+NS+'use'):
        raise ValueError('根变换/CSS规则/克隆需要原画布，保留原画布')
    root_style=dict(x.strip().split(':',1) for x in root.get('style','').split(';') if ':' in x)
    if any(k in root_style for k in ('width','height','transform')):
        raise ValueError('页面尺寸由CSS覆盖，保留原画布')
    vb=np.array(root.get('viewBox','').replace(',',' ').split(),float)
    if vb.shape!=(4,) or not np.isfinite(vb).all() or np.any(vb[2:]<=0): raise ValueError('无有效viewBox，保留原画布')
    physical=[];units=[]
    factor={'':1.,'px':1.,'mm':96/25.4,'cm':96/2.54,'in':96.,'pt':96/72.,'pc':16.,'q':96/101.6}
    for field in ('width','height'):
        match=re.fullmatch(rf'({NUMBER})(px|mm|cm|in|pt|pc|q)?',root.get(field,''))
        if not match: raise ValueError('页面不是固定物理单位，保留原画布')
        unit=match[2] or '';value=float(match[1])*factor[unit]
        if not math.isfinite(value) or value<=0: raise ValueError('页面尺寸非法')
        physical.append(value);units.append(unit)
    ratios=np.array(physical)/vb[2:];aspect=root.get('preserveAspectRatio','xMidYMid meet')
    if 'none' not in aspect:ratios[:]=max(ratios) if 'slice' in aspect else min(ratios)
    bounds=[];stroke_margin=0.
    def visit(node,parent,stroke='1'):
        nonlocal stroke_margin
        tag=local(node)
        if tag in ('defs','metadata','title','desc'): return
        current=parent@matrix(node.get('transform',''))
        attrs=dict(x.strip().split(':',1) for x in node.get('style','').split(';') if ':' in x)
        sw=attrs.get('stroke-width',node.get('stroke-width',stroke))
        if any(node.get(key) or key in attrs for key in ('marker-start','marker-mid','marker-end','vector-effect','stroke-miterlimit')):
            raise ValueError('有标记/特殊描边效果，保留原画布')
        if node.get('clip-path') or node.get('filter') or 'clip-path' in attrs or 'filter' in attrs: raise ValueError('有裁剪/滤镜，保留原画布')
        if tag=='g':
            for child in node:visit(child,current,sw)
        elif tag=='path':
            mp=path_geometry(node.get('d','')).transformed(Affine2D(current))
            b=mp.get_extents().extents
            if not np.isfinite(b).all(): raise ValueError('空路径不能裁画布')
            bounds.append(b)
            # 保守留白覆盖默认miter连接；不修改stroke或几何。
            width=float(sw);stroke_margin=max(stroke_margin,abs(width)*np.linalg.norm(current[:2,:2],ord=2)*4.)
        elif tag not in ('svg',):raise ValueError(f'含{tag}，保留原画布；请转路径后再裁紧')
    for child in root:visit(child,np.eye(3),root_style.get('stroke-width',root.get('stroke-width','1')))
    if not bounds: raise ValueError('没有可裁紧的路径')
    b=np.array(bounds);lo=b[:,:2].min(axis=0)-padding-stroke_margin;hi=b[:,2:].max(axis=0)+padding+stroke_margin;size=hi-lo
    if np.any(size<=0): raise ValueError('零尺寸画布')
    root.set('viewBox',' '.join(f'{x:.12g}' for x in [*lo,*size]))
    for i,field in enumerate(('width','height')):root.set(field,f'{size[i]*ratios[i]/factor[units[i]]:.12g}{units[i]}')


def validate_references(root):
    ids={n.get('id') for n in root.iter() if n.get('id')}
    for n in root.iter():
        values=[*n.attrib.values()]+([n.text] if local(n)=='style' and n.text else [])
        for value in values:
            for target in re.findall(r'url\(\s*[\"\']?#([^\)\s\"\']+)',value):
                if target not in ids:raise ValueError(f'拆分后缺少内部资源#{target}；请把所需对象放进defs或取消克隆')
        for key,value in n.attrib.items():
            if key.rsplit('}',1)[-1]=='href' and value.startswith('#') and value[1:] not in ids:
                raise ValueError(f'拆分后缺少克隆目标{value}；请在Inkscape取消克隆')


def split_svg(input_path,output_dir,config=None,trim=False,padding=12.,make_zip=False):
    source=Path(input_path).resolve();output=Path(output_dir).resolve();fingerprint=digest(source)
    if output.exists() or (make_zip and output.with_suffix('.zip').exists()): raise FileExistsError('输出目录或ZIP已存在；请指定新路径，不覆盖旧文件')
    if not math.isfinite(padding) or padding<0:raise ValueError('padding必须为有限非负SVG坐标单位数')
    root,ids=read_svg(source)
    definitions=read_json(config)['parts'] if config else auto_parts(root)
    if not isinstance(definitions,list) or not definitions:raise ValueError('配置parts必须是非空列表')
    planned=[];assigned=set();names=set();warnings=[]
    all_vectors=vectors(root)
    for index,part in enumerate(definitions,1):
        name=filename(part['name']);label=part.get('label',name);selected=part.get('elements')
        if name.casefold() in names:raise ValueError('零件文件名重复（Windows不区分大小写）')
        names.add(name.casefold())
        if not isinstance(selected,list) or not selected or any(not isinstance(x,str) or x not in ids for x in selected):
            raise ValueError(f'{name}的elements须列出已存在的SVG元素ID；用--list查看')
        if len(selected)!=len(set(selected)):raise ValueError(f'{name}选择了重复ID')
        picked=[]
        for id_ in selected:
            found=vectors(ids[id_])
            if not found:raise ValueError(f'{id_}没有实际矢量几何；嵌入图片不是零件路径')
            picked.extend(id(v) for v in found)
        if len(picked)!=len(set(picked)) or assigned.intersection(picked):raise ValueError('零件选择发生重叠，不能把同一路径重复分给多个零件')
        assigned.update(picked)
        item=ET.Element(root.tag,dict(root.attrib))
        ET.SubElement(item,NS+'title').text=str(label)
        for node in root:
            if local(node) in ('defs','style','metadata','desc'):item.append(deepcopy(node))
            elif local(node)!='title':
                filtered=subset(node,set(selected))
                if filtered is not None:item.append(filtered)
        if item.findall('.//'+NS+'image'):raise ValueError(f'{name}仍包含图片；请把底图和部件放到不同图层')
        validate_references(item)
        notes=[];cropped=False
        if trim:
            try:trim_canvas(item,padding);cropped=True
            except (ValueError,KeyError,TypeError) as exc:notes.append('未裁紧：'+str(exc))
        # 文件只使用编号；语义名称保留在清单和SVG元数据中，便于对应零件。
        number=f'{index:03d}'
        record=dict(number=number,name=name,label=label,selected_ids=selected,vector_count=len(picked),
                    path_count=len(item.findall('.//'+NS+'path')),file=number+'.svg',canvas_trimmed=cropped,
                    viewBox=item.get('viewBox'),width=item.get('width'),height=item.get('height'),warnings=notes)
        meta_id='split_export_metadata'
        while meta_id in ids:meta_id+='_'
        ET.SubElement(item,NS+'metadata',id=meta_id).text=json.dumps(dict(app_name='3DSVG Studio',source=str(source),source_sha256=fingerprint,
            part=record,geometry_modified=False,note='Original path geometry, ancestors, styles and transformations are retained. Canvas changes do not calibrate physical dimensions.'),ensure_ascii=False)
        planned.append((item,record));warnings.extend(notes)
    if len(planned)==1:warnings.append('仅识别到一个分组；若包含多个零件，请在Inkscape继续分组，或用config按路径ID拆分。')
    output.mkdir(parents=True,exist_ok=False)
    for item,record in planned:
        target=output/record['file'];write_svg(target,item)
        saved=ET.parse(target).getroot();validate_references(saved)
        if ET.tostring(saved,encoding='utf-8')!=ET.tostring(item,encoding='utf-8'):raise ValueError('SVG回读不一致')
        record.update(sha256=digest(target),readback_verified=True)
    if digest(source)!=fingerprint:raise ValueError('输入文件在拆分过程中发生改变')
    report=dict(app_name='3DSVG Studio',source=str(source),source_sha256=fingerprint,source_unchanged=True,
                split_mode='explicit_config' if config else 'existing_layers_or_groups',part_count=len(planned),parts=[p[1] for p in planned],
                assigned_vector_count=len(assigned),unassigned_vector_count=len({id(v) for v in all_vectors}-assigned),
                geometry_modified=False,automatic_geometry_completion=False,filename_convention='001.svg, 002.svg, ...',warnings=warnings)
    with (output/'split_report.json').open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2,allow_nan=False)
    text=['# 3DSVG Studio：SVG零件拆分','',f'共{len(planned)}份SVG，原文件未修改。','',
          '依据已有图层/分组或显式配置拆分，不自动识别物体。原曲线、颜色、祖先变换、隐藏状态和来源元数据保持。',
          '人工补全标记仍来自输入SVG；本脚本不增加或补齐几何。','',
          '| 文件 | 部件 | 路径数 |','| --- | --- | ---: |']
    text.extend(f"| {r['file']} | {r['label']} | {r['path_count']} |" for _,r in planned)
    text.extend(['','未分配的几何数量：'+str(report['unassigned_vector_count'])+'（可能包括参考坐标轴、尺寸等）。'])
    text.extend('- '+w for w in warnings)
    with (output/'README.md').open('x',encoding='utf-8') as f:f.write('\n'.join(text)+'\n')
    if make_zip:
        with zipfile.ZipFile(output.with_suffix('.zip'),'x',zipfile.ZIP_DEFLATED) as z:
            for p in sorted(output.iterdir()):z.write(p,arcname=p.name)
        with zipfile.ZipFile(output.with_suffix('.zip')) as z:
            if z.testzip():raise ValueError('ZIP完整性检查失败')
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path)
    p.add_argument('--config',type=Path,help='可选JSON：parts列表，name/label/elements；按列表顺序编号001.svg起')
    p.add_argument('--list',action='store_true',help='列出分组、图层及路径ID，不生成文件')
    p.add_argument('--trim',action='store_true',help='裁紧画布但不缩放几何；不支持的复杂效果保留原画布并记录')
    p.add_argument('--padding',type=float,default=12.,help='裁画布留白，SVG原坐标单位，默认12')
    p.add_argument('--zip',dest='make_zip',action='store_true',help='另外生成同名ZIP包')
    a=p.parse_args(argv)
    try:
        if a.list:
            root,_=read_svg(a.input)
            print(json.dumps(dict(groups=list_groups(root),path_ids=[x.get('id') for x in root.iter(NS+'path')]),ensure_ascii=False,indent=2));return 0
        if a.output is None:p.error('拆分需要--output；可用--list先查看分组')
        report=split_svg(a.input,a.output,a.config,a.trim,a.padding,a.make_zip)
        print(f"3DSVG Studio：已拆成 {report['part_count']} 份SVG，回读检查通过；原文件未改动。\n输出：{a.output.resolve()}")
        for row in report['parts']:print(f"  {row['file']}  {row['label']}  ({row['path_count']} paths)")
        for warning in report['warnings']:print('提示：',warning)
        return 0
    except (OSError,ValueError,KeyError,TypeError,ET.ParseError) as exc:
        print('错误：'+str(exc),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
