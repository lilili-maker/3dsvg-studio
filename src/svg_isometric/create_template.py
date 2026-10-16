"""有色部件图层与嵌入参考图；没有人工描边时只输出模板。"""
from pathlib import Path
import argparse
import base64
import json
import math
import sys
import xml.etree.ElementTree as ET

if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from svg_isometric.common import read_json,read_cloud,write_json,sha256,cli
from svg_isometric.dimensions import resolve_dimensions
import cv2
import numpy as np

SVG='http://www.w3.org/2000/svg';INK='http://www.inkscape.org/namespaces/inkscape'
SOD='http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd';XLINK='http://www.w3.org/1999/xlink'
ET.register_namespace('',SVG);ET.register_namespace('inkscape',INK);ET.register_namespace('sodipodi',SOD);ET.register_namespace('xlink',XLINK)
PARTS=[('backrest','Backrest','#00ff4a'),('seat','Seat','#9500ff'),
       ('left_armrest','Left Armrest','#ff0000'),('right_armrest','Right Armrest','#ff0000'),
       ('support','Support','#009eac'),('base','Base','#df7c16'),('wheels','Wheels','#2868cb')]
LAYERS=[('reference','Reference'),*[(p,n) for p,n,_ in PARTS],('dimensions','Dimensions'),('guides','Guides')]


def element(parent,tag,**attrs):
    return ET.SubElement(parent,f'{{{SVG}}}{tag}',{k:str(v) for k,v in attrs.items()})


def add_dimensions(group,records):
    for record in records:
        q=np.asarray(record['dimension_endpoints_svg_mm']);p=np.asarray(record['endpoints_svg_mm'])
        g=element(group,'g',id='dimension_'+record['id']);g.set('data-source',record['source'])
        # 所有线段仅表达标注位置；文字数值来自三维计算或人工实测，而非此处的线长。
        d=f'M {p[0,0]:.12g} {p[0,1]:.12g} L {q[0,0]:.12g} {q[0,1]:.12g} M {p[1,0]:.12g} {p[1,1]:.12g} L {q[1,0]:.12g} {q[1,1]:.12g} M {q[0,0]:.12g} {q[0,1]:.12g} L {q[1,0]:.12g} {q[1,1]:.12g}'
        element(g,'path',d=d,fill='none',stroke='#374151',**{'stroke-width':'.35'})
        delta=q[1]-q[0];delta=delta/np.linalg.norm(delta);normal=np.array([-delta[1],delta[0]])
        for end,sign in [(q[0],1),(q[1],-1)]:
            a=end+sign*delta*3+normal*1;b=end+sign*delta*3-normal*1
            element(g,'path',d=f'M {a[0]:.12g} {a[1]:.12g} L {end[0]:.12g} {end[1]:.12g} L {b[0]:.12g} {b[1]:.12g}',
                    fill='none',stroke='#374151',**{'stroke-width':'.35'})
        mid=q.mean(axis=0)-np.array([0,2.])
        label=element(g,'text',x=f'{mid[0]:.12g}',y=f'{mid[1]:.12g}',fill='#263244',**{'font-size':'3.5','text-anchor':'middle'})
        label.text=f"{record['label']}: {record['value_mm']:.1f} mm [{'3D points' if record['source']=='pointcloud' else 'manual measurement'}]"


def create(metadata_path,output,*,dimensions_config=None,show_axes=True):
    meta=read_json(metadata_path);output=Path(output);report=output.with_suffix('.json')
    if output.suffix.lower()!='.svg' or output.exists() or report.exists():raise FileExistsError('模板输出必须为新的 .svg；不覆盖现有文件')
    if meta.get('projection_type')!='parallel_orthographic' or meta.get('svg_unit')!='mm':raise ValueError('元数据不是本工具生成的毫米平行投影')
    image=Path(meta['reference_image']);cloud=Path(meta['input_pointcloud'])
    if sha256(image)!=meta['reference_sha256'] or sha256(cloud)!=meta['input_pointcloud_sha256']:raise ValueError('参考图/点云已改变，请重做投影')
    pixels=cv2.imread(str(image),cv2.IMREAD_UNCHANGED)
    if pixels is None or pixels.shape[:2]!=(meta['image_height_px'],meta['image_width_px']):raise ValueError('参考图分辨率不匹配')
    w,h=meta['canvas_size_mm']
    root=ET.Element(f'{{{SVG}}}svg',{'width':f'{w:.12g}mm','height':f'{h:.12g}mm','viewBox':f'0 0 {w:.12g} {h:.12g}','version':'1.1'})
    element(root,'title').text='Furniture axonometric measured-point reference — manual tracing template'
    element(root,'desc').text='Template, not a completed furniture wireframe. 2D paths cannot be reprojected to a different view. Projected lengths are not arbitrary 3D lengths.'
    dimensions=resolve_dimensions(dimensions_config,read_cloud(cloud),meta)
    parts=[dict(id=p,name=n,color=c,visible=True,manual_path_count=0,geometry_source='unclassified_until_user_traces',
                instruction='Mark manually estimated additions separately from observed geometry') for p,n,c in PARTS]
    document=dict(schema_version=1,workflow='single_pointcloud_axonometric_manual_trace',projection=meta,
        parts=parts,dimensions=dimensions,manual_furniture_paths_present=False,template_not_final=True)
    element(root,'metadata',id='isometric_project_metadata').text=json.dumps(document,ensure_ascii=False,allow_nan=False)
    groups={}
    for id_,name in LAYERS:
        group=element(root,'g',id=id_);group.set(f'{{{INK}}}groupmode','layer');group.set(f'{{{INK}}}label',name);groups[id_]=group
    groups['reference'].set(f'{{{SOD}}}insensitive','true')
    x,y,iw,ih=meta['image_rectangle_svg_mm'];data='data:image/png;base64,'+base64.b64encode(image.read_bytes()).decode('ascii')
    img=element(groups['reference'],'image',id='measured_pointcloud',x=f'{x:.12g}',y=f'{y:.12g}',width=f'{iw:.12g}',height=f'{ih:.12g}',preserveAspectRatio='xMinYMin meet')
    img.set('href',data);img.set(f'{{{XLINK}}}href',data)
    for id_,_,color in PARTS:
        groups[id_].set('style',f'fill:none;stroke:{color};stroke-width:0.6;stroke-linejoin:round;stroke-linecap:round')
        groups[id_].set('data-geometry-source','unclassified')
    add_dimensions(groups['dimensions'],dimensions)
    guide=groups['guides'];guide.set('style','fill:none;stroke-width:.4')
    if not show_axes:guide.set('style','display:none')
    if show_axes:
        # 非家具几何：独立角落三轴示意，只表达投影方向。
        basis=np.asarray(meta['view_basis']);anchor=np.array([12.,h-12.]);length=min(10.,min(w,h)/12)
        for i,(name,color) in enumerate(zip('XYZ',['#d43b36','#239c52','#3274ce'])):
            direction=np.array([basis[0,i],-basis[1,i]])
            end=anchor+direction*length
            element(guide,'path',id='axis_'+name,d=f'M {anchor[0]:.12g} {anchor[1]:.12g} L {end[0]:.12g} {end[1]:.12g}',stroke=color)
            element(guide,'text',x=f'{end[0]+1:.12g}',y=f'{end[1]-1:.12g}',fill=color,stroke='none',**{'font-size':'3.5'}).text=name
        element(guide,'text',x='8',y='7',fill='#475569',stroke='none',**{'font-size':'3.2'}).text='Parallel projection — trace measured / estimated geometry separately'
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('xb') as f:ET.ElementTree(root).write(f,encoding='utf-8',xml_declaration=True)
    write_json(report,dict(status='template_ready_for_manual_tracing',svg=str(output.resolve()),svg_sha256=sha256(output),
        document_size_mm=[w,h],embedded_reference=True,part_layers=parts,manual_furniture_path_count=0,
        dimension_records=dimensions,warning='Guides and dimension paths are not furniture outlines. Final output is not created until actual manual paths are supplied.'))
    print('彩色分层模板（尚未描边）：',output.resolve())
    return document


def parse_args(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--metadata',dest='metadata_path',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--dimensions-config',type=Path);p.add_argument('--hide-axes',dest='show_axes',action='store_false')
    return p.parse_args(argv)


if __name__=='__main__':raise SystemExit(cli(lambda:create(**vars(parse_args()))))
