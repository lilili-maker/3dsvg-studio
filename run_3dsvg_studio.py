"""3DSVG Studio 精简版：PLY → 正交斜视参考 PNG → Inkscape 人工描边模板。"""
import argparse
from datetime import datetime
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

if __name__ == '__main__':
    os.environ.setdefault('OMP_NUM_THREADS', '4')
    os.environ.setdefault('OPENBLAS_NUM_THREADS', '4')

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'src'))
import numpy as np
import open3d as o3d
from svg_isometric.common import read_cloud, cloud_stats, sha256, write_json, write_png, cli
from svg_isometric.crop_pointcloud import crop
from svg_isometric.project_pointcloud import furniture_transform, view_basis, project_points, render
from svg_isometric.create_template import create, SVG, INK
from svg_isometric.validate_template import verify
from svg_isometric.compat import write_ply_checked

APP_NAME = '3DSVG Studio'


def prepare_cloud(path, unit, bounds=None, voxel_size=0., remove_outliers=False):
    """单位先转换为米，再裁剪/降采样；只改变内存副本，不修改输入PLY。"""
    if unit not in ('m', 'mm'):
        raise ValueError('PLY通常不记录单位。请明确指定 --unit m 或 --unit mm，不能猜测。')
    path = Path(path)
    if path.suffix.lower() != '.ply': raise ValueError('--input 必须是 PLY 点云文件')
    if not np.isfinite(voxel_size) or voxel_size < 0: raise ValueError('--voxel-size 必须为有限非负米数')
    cloud = read_cloud(path)
    p = np.asarray(cloud.points)
    source = dict(point_count=len(p), has_rgb=cloud.has_colors(), unit=unit,
                  xyz_min=p.min(axis=0).tolist(), xyz_max=p.max(axis=0).tolist())
    # mm输入的XYZ同步按0.001缩放，不能只修改标签或SVG页面单位。
    factor = 1. if unit == 'm' else .001
    cloud.scale(factor, center=(0., 0., 0.))
    normalized = cloud_stats(cloud)
    config = None
    if bounds is not None:
        if len(bounds) != 6: raise ValueError('--crop 需要六个米制边界：xmin xmax ymin ymax zmin zmax')
        config = dict(unit='m', **dict(zip(('xmin', 'xmax', 'ymin', 'ymax', 'zmin', 'zmax'), bounds)))
        cloud = crop(cloud, config)
    after_crop = len(cloud.points)
    if voxel_size: cloud = cloud.voxel_down_sample(voxel_size)
    removed = 0
    if remove_outliers:
        if len(cloud.points) <= 20: raise ValueError('统计去噪需要超过20个点，请关闭 --remove-outliers')
        before = len(cloud.points)
        cloud, _ = cloud.remove_statistical_outlier(nb_neighbors=20, std_ratio=3.)
        removed = before - len(cloud.points)
    result = cloud_stats(cloud)
    return cloud, dict(original=source, conversion_to_m=factor, normalized=normalized,
                       crop=config, points_after_crop=after_crop, voxel_size_m=voxel_size,
                       statistical_outlier_filter=remove_outliers, removed_outliers=removed, cleaned=result)


def render_reference(cloud, transform, azimuth=-45., elevation=35.264389682754654,
                     width=1600, padding_mm=25., point_radius=1, mode='auto', center_m=None):
    """复用NumPy平行投影与点深度缓冲；width为实际输出宽度，不是透视截图。"""
    if type(width) is not int or not 128 <= width <= 6000: raise ValueError('--width 须为128～6000整数像素')
    basis, angles = view_basis('custom_axonometric', azimuth, elevation)
    _, _, probe = project_points(np.asarray(cloud.points), transform, basis, resolution=128,
                                 padding_mm=padding_mm, center_m=center_m)
    # 原函数的resolution表示长边。按画布宽高换算，使最终宽度恰好为width，且不拉伸图像。
    canvas = np.asarray(probe['canvas_size_mm'])
    long_side = int(math.floor(max(canvas) / canvas[0] * (width - 1) + 1e-10)) + 1
    if long_side > 6000:
        raise ValueError('该视角下图像过高（超过6000像素）；请减小 --width 或调整视角/留白')
    images, meta = render(cloud, transform, basis, resolution=long_side, padding_mm=padding_mm,
                          center_m=center_m, point_radius=point_radius)
    chosen = ('rgb' if cloud.has_colors() else 'monochrome') if mode == 'auto' else mode
    if chosen not in images or images[chosen] is None: raise ValueError('RGB模式需要点云颜色；请用 --mode monochrome 或 auto')
    if meta['image_width_px'] != width:
        raise ValueError('参考图宽度发生数值退化，请稍微调整width或padding-mm；没有执行非等比例缩放')
    meta.update(**angles, requested_width_px=width, reference_mode=chosen)
    return images[chosen], meta


def save_cleaned(path, cloud):
    if cloud.has_colors(): return write_ply_checked(path, cloud)
    # 旧写入器要求RGB；无颜色PLY只做坐标/点数回读，不伪造RGB数据。
    with path.open('xb'): pass
    if not o3d.io.write_point_cloud(str(path), cloud): raise OSError(f'无法保存点云：{path}')
    check = read_cloud(path)
    if len(check.points) != len(cloud.points) or not np.allclose(np.asarray(check.points), np.asarray(cloud.points), atol=1e-10, rtol=0):
        raise ValueError('cleaned.ply回读坐标检查失败')
    return dict(point_count=len(check.points), readback_verified=True, has_rgb=False)


def run(args):
    started = time.perf_counter()
    output = Path(args.output).resolve(); source = Path(args.input).resolve()
    if output.exists():
        raise FileExistsError(f'输出目录已经存在，禁止覆盖（包括final.svg）：{output}；请指定新目录')
    if not source.is_file(): raise FileNotFoundError(f'找不到输入点云：{source}')
    fingerprint = sha256(source)
    cloud, processing = prepare_cloud(source, args.unit, args.crop, args.voxel_size, args.remove_outliers)
    # unchanged只保留输入轴，不声称这是家具坐标。camera转换必须由用户显式选择。
    frame = 'camera' if args.input_frame == 'camera' else 'furniture'
    transform = furniture_transform(frame, args.rotation, args.origin)
    image, meta = render_reference(cloud, transform, args.azimuth, args.elevation,
                                   args.width, args.padding_mm, args.point_radius, args.mode, args.center)
    warnings = ['只包含实际采集到的点，不补全遮挡或缺失区域。',
                '这是空图层描边模板，尚无最终家具矢量轮廓。',
                'SVG使用投影毫米；斜视二维线长不等于任意三维真实长度。']
    if args.input_frame == 'unchanged': warnings.append('保留输入坐标轴，尚未确认为物体坐标；需要时指定input-frame和rotation。')
    if args.input_frame == 'camera': warnings.append('相机光学轴到参考轴的转换仅为初始约定，不是自动求得的真实家具姿态。')
    if args.crop is None: warnings.append('未指定裁剪；不会自动识别物体或删除背景。')
    if args.rotation != [0., 0., 0.]: warnings.append('人工姿态校正只作用于投影；cleaned.ply保留原坐标轴，单位为米。')
    output.mkdir(parents=True, exist_ok=False)
    cleaned, reference, template = output/'cleaned.ply', output/'reference.png', output/'template.svg'
    save_cleaned(cleaned, cloud); write_png(reference, image)
    meta.update(app_name=APP_NAME, created_at=datetime.now().astimezone().isoformat(), data_source=args.data_source,
                input_pointcloud=str(cleaned), input_pointcloud_sha256=sha256(cleaned),
                original_input=str(source), original_input_sha256=fingerprint, original_input_unit=args.unit,
                cleaned_coordinate_unit='m', input_coordinate_frame=args.input_frame,
                axis_alignment_interpretation='User-selected initial axis convention and Rz@Ry@Rx; not automatic furniture orientation',
                rotation_deg=args.rotation, origin_input_m=args.origin, source_cloud=processing['cleaned'],
                processing=processing, warnings=warnings, reference_image=str(reference), reference_sha256=sha256(reference),
                manual_furniture_path_count=0, final_svg_created=False)
    # 复用已验证的模板及PNG映射检查；辅助报告仅在临时目录内生成。
    with tempfile.TemporaryDirectory(prefix='3dsvg_template_') as folder:
        temporary = Path(folder); metadata_path = temporary/'projection.json'
        write_json(metadata_path, meta)
        create(metadata_path, temporary/'template.svg', show_axes=not args.hide_axes)
        validation = verify(temporary/'template.svg')
        tree = ET.parse(temporary/'template.svg'); root = tree.getroot()
        root.find(f'{{{SVG}}}title').text = '3DSVG Studio — editable orthographic tracing template'
        # 使用一个Armrests图层；这是可选人工部件分类，不限制输入必须为椅子。
        left = root.find(f'{{{SVG}}}g[@id="left_armrest"]')
        left.set('id', 'armrests'); left.set(f'{{{INK}}}label', 'Armrests')
        root.remove(root.find(f'{{{SVG}}}g[@id="right_armrest"]'))
        record = root.find(f'{{{SVG}}}metadata[@id="isometric_project_metadata"]')
        document = json.loads(record.text); document['app_name'] = APP_NAME
        document['parts'] = [p for p in document['parts'] if p['id'] != 'right_armrest']
        for part in document['parts']:
            if part['id'] == 'left_armrest': part.update(id='armrests', name='Armrests')
        # 输出内引用采用相对路径；原始文件绝对路径只用于来源追溯，不是加载依赖。
        meta['input_pointcloud'] = 'cleaned.ply'; meta['reference_image'] = 'reference.png'
        document['projection'] = meta
        record.text = json.dumps(document, ensure_ascii=False, allow_nan=False)
        with template.open('xb') as stream: tree.write(stream, encoding='utf-8', xml_declaration=True)
    # 实际回读交付SVG，确认合并后的必需图层、嵌入图和空人工路径数。
    delivered = ET.parse(template).getroot()
    groups = {g.get('id'): g for g in delivered.findall(f'{{{SVG}}}g')}
    required = ['reference','backrest','seat','armrests','support','base','wheels']
    if any(name not in groups for name in required): raise ValueError('交付SVG缺少必要图层')
    if any(list(groups[name].iter(f'{{{SVG}}}path')) for name in required[1:]): raise ValueError('空描边图层不应出现自动轮廓')
    if sha256(source) != fingerprint: raise ValueError('处理期间原始输入发生改变，停止报告成功')
    meta.update(template_sha256=sha256(template), required_layers=required,
                png_svg_mapping_verified=validation['uniform_png_scale_verified'],
                runtime_seconds=time.perf_counter()-started)
    write_json(output/'projection.json', meta)
    print(f'\n{APP_NAME}：模板已生成，等待Inkscape人工描边。')
    print(f"点数：{processing['original']['point_count']} → {len(cloud.points)}；输入单位：{args.unit}；cleaned.ply单位：m")
    print('输入XYZ范围（米）：', processing['normalized']['xyz_min_m'], '→', processing['normalized']['xyz_max_m'])
    print('输出XYZ范围（米）：', processing['cleaned']['xyz_min_m'], '→', processing['cleaned']['xyz_max_m'])
    print(f"参考图：{meta['image_width_px']}×{meta['image_height_px']} px；{meta['mm_per_pixel']:.6g} mm/px；耗时 {meta['runtime_seconds']:.2f} 秒")
    for name in ('cleaned.ply','reference.png','template.svg','projection.json'): print(output/name)
    for warning in warnings: print('提示：', warning)
    print('人工下一步：Inkscape打开template.svg，锁定Reference，选部件层按B描线、N调节点；填充设无。')
    print('完成后隐藏Reference和Guides，另存final.svg；不要用模板覆盖已有描边。')
    return meta


def parse_args(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,required=True,help='输入PLY，原文件保持只读')
    p.add_argument('--output',type=Path,default=ROOT/'output/3dsvg_studio',help='新的输出目录，已存在则拒绝覆盖')
    p.add_argument('--unit',choices=['m','mm'],help='必须显式确认PLY坐标单位，不从扩展名猜测')
    p.add_argument('--input-frame',choices=['unchanged','camera','object'],default='unchanged',help='默认保留输入轴且标记未确认；camera为X右Y下Z前，object为已确认X右Y后Z上')
    p.add_argument('--azimuth',type=float,default=-45.,help='观察者方位角：从参考+X朝+Y，度')
    p.add_argument('--elevation',type=float,default=35.264389682754654,help='观察者高于参考XY平面的角度，度')
    p.add_argument('--width',type=int,default=1600,help='参考图实际宽度，128～6000像素；高度按比例确定')
    p.add_argument('--crop',type=float,nargs=6,metavar=('XMIN','XMAX','YMIN','YMAX','ZMIN','ZMAX'),help='姿态校正前的输入坐标系AABB；统一使用米，即使unit=mm')
    p.add_argument('--voxel-size',type=float,default=0.,help='体素尺寸，米；0关闭（默认）')
    p.add_argument('--remove-outliers',action='store_true',help='可选20邻域、3倍标准差去噪，默认关闭')
    p.add_argument('--rotation',type=float,nargs=3,default=[0.,0.,0.],metavar=('RX','RY','RZ'),help='姿态校正角，度；Rz@Ry@Rx作用于初始轴约定之后')
    p.add_argument('--origin',type=float,nargs=3,default=[0.,0.,0.],help='输入坐标中的校正原点，米；先减原点再旋转')
    p.add_argument('--center',type=float,nargs=3,default=[0.,0.,0.],help='校正后参考系的投影中心，米；自动适配画布，不是透视焦点')
    p.add_argument('--padding-mm',type=float,default=25.,help='投影画布四周留白，毫米')
    p.add_argument('--point-radius',type=int,default=1,help='点显示半径0～3像素；不增加实测几何')
    p.add_argument('--mode',choices=['auto','rgb','monochrome','density'],default='auto',help='auto：有颜色用RGB，否则单色；均为白底')
    p.add_argument('--hide-axes',action='store_true',help='隐藏模板辅助坐标轴')
    p.add_argument('--data-source',choices=['real','simulation'],default='real',help='真实扫描/合成测试来源标签')
    a=p.parse_args(argv)
    if a.unit is None: p.error('PLY单位未知，请明确选择 --unit m 或 --unit mm。')
    return a


if __name__=='__main__': raise SystemExit(cli(lambda: run(parse_args())))
