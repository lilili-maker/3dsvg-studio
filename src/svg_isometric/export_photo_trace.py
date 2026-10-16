"""导出已人工绘制的照片路径；不自动补几何、不从照片线长推算实物尺寸。"""
from pathlib import Path
import argparse
import base64
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from svg_isometric.create_template import SVG, INK, element


def export(config_path, output_dir, project_root=None):
    config_path = Path(config_path).resolve()
    project_root = Path(project_root or Path(__file__).resolve().parents[2])
    cfg = json.loads(config_path.read_text(encoding='utf-8-sig'))
    photo = (project_root / cfg['source_photo']).resolve()
    digest = hashlib.sha256(photo.read_bytes()).hexdigest()
    if digest != cfg['source_sha256']:
        raise ValueError('照片SHA256不匹配，不能将已有描边用于另一张照片')
    if cfg['provenance'] != 'manual_visible_photo_trace':
        raise ValueError('须明确标记人工可见照片描边来源')
    w, h = cfg['source_size_px']
    left, top, right, bottom = cfg['crop_px']
    if not (0 <= left < right <= w and 0 <= top < bottom <= h):
        raise ValueError('照片裁切坐标非法')
    # 仅接受本描边数据使用的绝对坐标 M/L/Q/C/Z，校验参数和坐标边界。
    counts = {'M': 2, 'L': 2, 'Q': 4, 'C': 6, 'Z': 0}
    ids = set()
    for part in cfg['parts']:
        if part['id'] in ids or not re.fullmatch(r'#[0-9a-fA-F]{6}', part['color']):
            raise ValueError('部件ID重复或颜色非法')
        ids.add(part['id'])
        for role, d in part['paths']:
            if role not in ('outline', 'detail', 'open', 'partial_open'):
                raise ValueError('未知描边类型')
            tokens = re.findall(r'[MLQCZ]|[-+]?(?:\d*\.\d+|\d+)', d)
            if re.sub(r'[\s,]', '', ''.join(tokens)) != re.sub(r'[\s,]', '', d) or not tokens or tokens[0] != 'M':
                raise ValueError('描边包含不支持的命令或数字')
            i = 0
            while i < len(tokens):
                command = tokens[i]
                n = counts.get(command)
                if n is None or i + n >= len(tokens):
                    raise ValueError('路径命令参数不足')
                values = list(map(float, tokens[i + 1:i + 1 + n]))
                if any(not (0 <= x <= w and 0 <= y <= h) for x, y in zip(values[::2], values[1::2])):
                    raise ValueError('路径坐标超出原照片')
                i += n + 1
            if role in ('open', 'partial_open') and 'Z' in tokens:
                raise ValueError('开放/出画轮廓禁止强行闭合')
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=False)
    page_w, page_h = right - left + 70, bottom - top + 70
    report = dict(workflow='manual_visible_photo_trace', source_photo=str(photo),
                  source_photo_sha256=digest, reference_style='User supplied colored furniture SVG examples',
                  notes=cfg['notes'], dimensions=[], depth_used=False,
                  real_world_scale_calibrated=False, document_units='px',
                  source_photo_to_svg=[[1, 0, 35-left], [0, 1, 35-top]],
                  path_count=sum(len(p['paths']) for p in cfg['parts']),
                  partial_open_path_count=sum(role == 'partial_open' for p in cfg['parts'] for role, _ in p['paths']),
                  part_path_counts={p['id']: len(p['paths']) for p in cfg['parts']},
                  status='editable_draft_not_measured_3d_model')
    for overlay in (False, True):
        root = ET.Element(f'{{{SVG}}}svg', width=f'{page_w}px', height=f'{page_h}px',
                          viewBox=f'0 0 {page_w} {page_h}', version='1.1')
        element(root, 'title').text = 'Stool: photo-traced colored vector draft'
        element(root, 'desc').text = 'Perspective photo tracing; not metric or reconstructed 3D. Missing and clipped structure remains unknown.'
        element(root, 'metadata').text = json.dumps(report, ensure_ascii=False)
        if overlay:
            # 校对版才嵌入照片；主交付SVG没有image元素。
            g = element(root, 'g', id='reference', opacity='.40')
            g.set(f'{{{INK}}}groupmode', 'layer'); g.set(f'{{{INK}}}label', 'Reference for alignment only')
            element(g, 'image', x=35-left, y=35-top, width=w, height=h,
                    href='data:image/png;base64,' + base64.b64encode(photo.read_bytes()).decode('ascii'))
        for part in cfg['parts']:
            group = element(root, 'g', id=part['id'], transform=f'translate({35-left},{35-top})',
                            fill='none', stroke=part['color'], **{'stroke-linecap':'round','stroke-linejoin':'round'})
            group.set(f'{{{INK}}}groupmode', 'layer'); group.set(f'{{{INK}}}label', part['label'])
            for index, (role, d) in enumerate(part['paths']):
                element(group, 'path', id=f"{part['id']}_{index:02d}", d=d,
                        **{'stroke-width':'.55' if role == 'detail' else '1.05',
                           'data-geometry-source':'manual_visible_photo_trace', 'data-role':role})
        target = output / ('alignment_review.svg' if overlay else 'chair_wireframe_draft.svg')
        with target.open('xb') as stream:
            ET.ElementTree(root).write(stream, encoding='utf-8', xml_declaration=True)
    (output/'preview.html').write_text('''<!doctype html><meta charset="utf-8"><title>彩色线框草稿</title>
<style>body{margin:24px;font:16px sans-serif;background:#eee;color:#333}main{max-width:850px;margin:auto}img{display:block;width:760px;max-width:100%;height:auto;background:white}p{line-height:1.6}</style>
<main><h2>凳子彩色矢量线框 · 照片描边草稿</h2><p>已隐藏照片。保留照片透视；不是毫米比例图。下缘截断部分留空。</p>
<img src="chair_wireframe_draft.svg"><p><a href="chair_wireframe_draft.svg">打开 SVG</a> · <a href="alignment_review.svg">照片叠加校对版</a></p></main>''', encoding='utf-8')
    (output/'trace_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    # 保存路径配置快照，便于调整节点后重新导出到新目录。
    (output/'trace_config.json').write_bytes(config_path.read_bytes())
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    try:
        result = export(args.config, args.output_dir)
        print(f"已导出 {result['path_count']} 条真实SVG路径；不含照片的主文件：{Path(args.output_dir).resolve() / 'chair_wireframe_draft.svg'}")
        print('照片透视描边草稿；非三维恢复，不能通过线长测量实物。')
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print(f'错误：{exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
