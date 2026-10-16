"""照片人工描边模板。保留透视，像素不是实物毫米，不用失效深度补几何。"""
from pathlib import Path
import argparse
import base64
import json
import math
import shutil
import sys
import xml.etree.ElementTree as ET

if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from svg_isometric.common import read_json,write_json,sha256,cli
from svg_isometric.create_template import SVG,INK,SOD,XLINK,PARTS,LAYERS,element
import cv2
import numpy as np


def photo_dimensions(path,width,height):
    """文字尺寸只读用户实物测量；端点只是画布上的标注位置。"""
    if path is None:return []
    data=read_json(path)
    if not isinstance(data,dict) or set(data)!={'dimensions'} or not isinstance(data['dimensions'],list):
        raise ValueError('尺寸配置须为含 dimensions 列表的对象')
    rows=[];ids=set()
    for item in data['dimensions']:
        if not isinstance(item,dict) or not isinstance(item.get('id'),str) or not item['id'] or item['id'] in ids:
            raise ValueError('每个尺寸须有唯一非空id')
        ids.add(item['id']);value=item.get('value_mm')
        if item.get('source')!='manual_measurement' or item.get('confirmed') is not True:
            raise ValueError('照片尺寸必须标记 source=manual_measurement、confirmed=true，不能从图上线长推算')
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:
            raise ValueError('value_mm 须为真实测得的有限正毫米数')
        ends=np.asarray(item.get('endpoints_svg_px'),float)
        if ends.shape!=(2,2) or not np.isfinite(ends).all() or np.linalg.norm(ends[1]-ends[0])<1e-6:
            raise ValueError('endpoints_svg_px须为画布中两个不同的二维标注定位点')
        if np.any(ends<0) or np.any(ends[:,0]>width) or np.any(ends[:,1]>height):raise ValueError('尺寸标注定位点超出画布')
        rows.append(dict(id=item['id'],label=item.get('label',item['id']),source='manual_measurement',
            value_mm=float(value),endpoints_svg_px=ends.tolist(),note=item.get('note',''),
            position_note='Pixel endpoints are annotation placement only; value is not inferred from projected distance'))
    return rows


def create(input_path,output_dir,crop_px=None,clip_config=None,dimensions_config=None):
    photo=Path(input_path).resolve();output=Path(output_dir).resolve()
    if output.exists():raise FileExistsError('输出目录已存在，禁止覆盖；请指定新的目录')
    image=cv2.imread(str(photo),cv2.IMREAD_COLOR)
    if image is None:raise ValueError('无法读取RGB照片')
    h,w=image.shape[:2];fingerprint=sha256(photo)
    box=np.asarray([0,0,w,h] if crop_px is None else crop_px,float)
    if box.shape!=(4,) or not np.isfinite(box).all() or not (0<=box[0]<box[2]<=w and 0<=box[1]<box[3]<=h):
        raise ValueError('crop-px须为原始照片内的LEFT TOP RIGHT BOTTOM像素边界')
    polygons=[]
    if clip_config:
        config=read_json(clip_config)
        expected=config.get('image_sha256',config.get('input_sha256',{}).get(str(photo)))
        if expected!=fingerprint:raise ValueError('人工遮罩未绑定当前照片SHA256，禁止混用其他照片坐标')
        polygons=config.get('polygons_xy')
        if not isinstance(polygons,list) or not polygons:raise ValueError('遮罩须为非空 polygons_xy 多边形列表')
        for polygon in polygons:
            p=np.asarray(polygon,float)
            if p.ndim!=2 or p.shape[0]<3 or p.shape[1]!=2 or not np.isfinite(p).all() or np.any(p<0) or np.any(p[:,0]>w) or np.any(p[:,1]>h):
                raise ValueError('遮罩多边形须包含至少3个有效的原照片像素坐标')
    margin=35.;page_w=float(box[2]-box[0]+2*margin);page_h=float(box[3]-box[1]+2*margin)
    dx=float(margin-box[0]);dy=float(margin-box[1]);dimensions=photo_dimensions(dimensions_config,page_w,page_h)
    root=ET.Element(f'{{{SVG}}}svg',{'width':f'{page_w:g}px','height':f'{page_h:g}px',
        'viewBox':f'0 0 {page_w:g} {page_h:g}','version':'1.1'})
    element(root,'title').text='Photo-based furniture manual tracing template'
    element(root,'desc').text='Perspective photograph, not orthographic or isometric measurement. Document pixels are not real-world millimeters. Dimensions, if supplied, are separately measured values.'
    project=dict(schema_version=1,workflow='perspective_photo_manual_trace',reference_type='real_RGB_perspective_photo',
        source_photo=str(photo),source_photo_sha256=fingerprint,source_photo_size_px=[w,h],
        page_size_px=[page_w,page_h],crop_box_source_px=box.tolist(),
        source_photo_to_svg=[[1,0,dx],[0,1,dy]],real_world_scale_calibrated=False,
        document_units='px, not physical furniture millimeters',projection_matrix_3d=None,
        depth_used=False,manual_clip_polygons=polygons,manual_clip_note='Reversible approximate image visibility selection; not a measured boundary or depth completion',
        dimensions=dimensions,manual_furniture_path_count=0,final_svg_created=False,
        limitations=['Photo perspective is retained. SVG line length cannot determine physical size.',
          'Any unobserved or clipped structure remains unknown; added paths must be labeled as manual estimates.',
          'Changing the photo/view invalidates existing 2D tracing.'])
    element(root,'metadata',id='photo_trace_project').text=json.dumps(project,ensure_ascii=False,allow_nan=False)
    groups={}
    for id_,label in LAYERS:
        group=element(root,'g',id=id_);group.set(f'{{{INK}}}groupmode','layer');group.set(f'{{{INK}}}label',label);groups[id_]=group
    groups['reference'].set(f'{{{SOD}}}insensitive','true')
    if polygons:
        defs=element(root,'defs');clip=element(defs,'clipPath',id='manual_photo_selection',clipPathUnits='userSpaceOnUse')
        for poly in polygons:
            d='M '+' L '.join(f'{float(x):g} {float(y):g}' for x,y in poly)+' Z'
            element(clip,'path',d=d)
    translated=element(groups['reference'],'g',transform=f'translate({dx:g},{dy:g})')
    img=element(translated,'image',x='0',y='0',width=w,height=h,preserveAspectRatio='xMinYMin meet')
    data='data:image/png;base64,'
    # 输入也允许JPG；先无损编码解码所得RGB为PNG，保留原文件副本与原文件SHA256。
    ok,encoded=cv2.imencode('.png',image)
    if not ok:raise OSError('参考图编码失败')
    data+=base64.b64encode(encoded.tobytes()).decode('ascii');img.set('href',data);img.set(f'{{{XLINK}}}href',data)
    if polygons:img.set('clip-path','url(#manual_photo_selection)')
    for id_,_,color in PARTS:
        groups[id_].set('style',f'fill:none;stroke:{color};stroke-width:1.2;stroke-linecap:round;stroke-linejoin:round')
        groups[id_].set('data-geometry-source','unclassified_manual_trace')
    for row in dimensions:
        a,b=np.asarray(row['endpoints_svg_px']);g=element(groups['dimensions'],'g',id='measurement_'+row['id']);g.set('data-source','manual_measurement')
        element(g,'path',d=f'M {a[0]:g} {a[1]:g} L {b[0]:g} {b[1]:g}',stroke='#394451',fill='none',**{'stroke-width':'1'})
        mid=(a+b)/2
        element(g,'text',x=f'{mid[0]:g}',y=f'{mid[1]-5:g}',fill='#243040',**{'font-size':'10','text-anchor':'middle'}).text=f"{row['label']}: {row['value_mm']:g} mm (manual measurement)"
    element(groups['guides'],'text',x='8',y='15',fill='#b04a13',**{'font-size':'8'}).text='PHOTO REFERENCE — pixels are not measured mm'
    element(groups['guides'],'text',x='8',y=f'{page_h-8:g}',fill='#475569',**{'font-size':'7'}).text='Hide Reference and Guides after tracing. Unknown dimensions remain blank.'
    output.mkdir(parents=True,exist_ok=False);(output/'input').mkdir();(output/'final').mkdir();(output/'reports').mkdir()
    shutil.copyfile(photo,output/'input'/('original'+photo.suffix.lower()))
    with (output/'chair_photo_template.svg').open('xb') as f:ET.ElementTree(root).write(f,encoding='utf-8',xml_declaration=True)
    write_json(output/'chair_photo_project.json',project)
    html='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>照片人工描边模板</title>
<style>body{font:18px sans-serif;background:#eee;color:#243040;margin:20px}img{display:block;width:760px;max-width:100%;height:auto;background:white;border:1px solid #ccc}p{line-height:1.7;max-width:900px}a{color:#1761a5}</style>
<h1>照片人工描边底图</h1><p>这是保留透视的真实RGB照片，不是点云平行投影。背景通过可撤销的SVG遮罩隐藏；原照片完整嵌入，没有补出缺失结构。画布像素不能作为实物毫米标尺，尺寸需另外实测标注。</p>
<p><a href="chair_photo_template.svg" target="_blank">打开SVG模板（用Inkscape编辑）</a> · <a href="input/original'''+photo.suffix.lower()+'''" target="_blank">查看原始照片</a></p>
<img src="chair_photo_template.svg"><p>下一步：在Inkscape中锁定Reference，选择Seat层，用贝塞尔工具描坐垫外轮廓。描完隐藏Reference和Guides，另存到final目录。不要把本模板报告为已经完成的家具线框。</p></html>'''
    with (output/'preview.html').open('x',encoding='utf-8') as f:f.write(html)
    if sha256(photo)!=fingerprint:raise ValueError('原照片在处理中改变')
    report=dict(status='photo_template_ready_awaiting_manual_trace',workflow=project['workflow'],depth_used=False,
        source_photo_unchanged=True,real_world_scale_calibrated=False,manual_furniture_path_count=0,
        dimension_count=len(dimensions),page_size_px=[page_w,page_h],reference_embedded=True,
        svg=str((output/'chair_photo_template.svg').resolve()),project=str((output/'chair_photo_project.json').resolve()),
        final_svg_created=False,original_photo_perspective_preserved=True)
    write_json(output/'reports/photo_template_report.json',report)
    print('照片描边模板已创建（尚未人工描边；不是米制平行投影）：',output.resolve())
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',dest='input_path',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True);p.add_argument('--crop-px',type=float,nargs=4)
    p.add_argument('--clip-config',type=Path);p.add_argument('--dimensions-config',type=Path)
    return create(**vars(p.parse_args()))


if __name__=='__main__':raise SystemExit(cli(main))
