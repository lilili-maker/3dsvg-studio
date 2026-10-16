"""验证本工具模板的毫米画布、图层与嵌入PNG映射；不把空模板验作最终描边。"""
from pathlib import Path
import argparse
import base64
import hashlib
import json
import sys
import xml.etree.ElementTree as ET
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from svg_isometric.common import write_json,cli
from svg_isometric.create_template import SVG,INK,XLINK,PARTS,LAYERS
import cv2
import numpy as np


def verify(path):
    root=ET.parse(path).getroot()
    if root.tag!=f'{{{SVG}}}svg':raise ValueError('根元素不是 SVG')
    content=root.find(f'{{{SVG}}}metadata[@id="isometric_project_metadata"]')
    if content is None:raise ValueError('缺少本工具的投影项目元数据；不能凭外观猜比例')
    project=json.loads(content.text);meta=project['projection'];w,h=meta['canvas_size_mm']
    for field,value in [('width',w),('height',h)]:
        text=root.get(field,'')
        if not text.endswith('mm') or not np.isclose(float(text[:-2]),value,rtol=1e-10,atol=1e-9):raise ValueError('SVG毫米文档尺寸与投影元数据不符')
    vb=np.array(root.get('viewBox','').replace(',',' ').split(),float)
    if vb.shape!=(4,) or not np.allclose(vb,[0,0,w,h],rtol=1e-10,atol=1e-9):raise ValueError('viewBox不符：要求1用户单位=1毫米')
    if root.get('transform'):raise ValueError('模板根元素包含额外变换，需要重新检查映射')
    groups={g.get('id'):g for g in root.findall(f'{{{SVG}}}g')}
    for id_,label in LAYERS:
        if id_ not in groups or groups[id_].get(f'{{{INK}}}groupmode')!='layer' or groups[id_].get(f'{{{INK}}}label')!=label:
            raise ValueError(f'缺少正确的Inkscape图层：{label}')
    reference=groups['reference']
    if reference.get('transform'):raise ValueError('参考图层额外变换改变了点云与SVG映射')
    image=reference.find(f'{{{SVG}}}image')
    if image is None or image.get('transform'):raise ValueError('参考图片缺失或存在额外变换')
    rect=np.array([float(image.get(k,'nan')) for k in ['x','y','width','height']])
    if not np.allclose(rect,meta['image_rectangle_svg_mm'],rtol=1e-10,atol=1e-9):raise ValueError('参考PNG的实际毫米位置/尺寸不匹配')
    href=image.get('href',image.get(f'{{{XLINK}}}href',''))
    if not href.startswith('data:image/png;base64,'):raise ValueError('参考图必须内嵌PNG，不依赖外部文件路径')
    data=base64.b64decode(href.split(',',1)[1],validate=True)
    if hashlib.sha256(data).hexdigest()!=meta['reference_sha256']:raise ValueError('SVG中嵌入的PNG已被改变')
    pixels=cv2.imdecode(np.frombuffer(data,np.uint8),cv2.IMREAD_UNCHANGED)
    if pixels is None or pixels.shape[:2]!=(meta['image_height_px'],meta['image_width_px']):raise ValueError('嵌入PNG分辨率与元数据不符')
    # PNG物理像素各向同性；不是只改SVG页面宽高的假比例。
    if not np.allclose(rect[2:]/[pixels.shape[1],pixels.shape[0]],meta['mm_per_pixel'],rtol=1e-10):raise ValueError('PNG发生非等比例缩放')
    manual={id_:len([p for p in groups[id_].iter(f'{{{SVG}}}path') if p.get('d','').strip()]) for id_,_,_ in PARTS}
    return dict(status='valid_template_not_final',document_size_mm=[w,h],viewBox=vb.tolist(),
        reference_embedded=True,png_size_px=[pixels.shape[1],pixels.shape[0]],mm_per_pixel=meta['mm_per_pixel'],
        uniform_png_scale_verified=True,layer_count=len(LAYERS),manual_part_path_counts=manual,
        actual_manual_furniture_path_count=sum(manual.values()),
        warning='This checks the reference template mapping. Human curves, closure, hidden geometry and actual physical accuracy require separate review after tracing.')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();result=verify(a.input);write_json(a.output,result);print(result)


if __name__=='__main__':raise SystemExit(cli(main))
