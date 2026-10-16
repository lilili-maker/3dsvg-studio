"""显式米制 AABB 点筛选；不自动删地面、轮子或小组件。"""
from pathlib import Path
import argparse
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from svg_isometric.compat import validate_crop_config, make_box
from svg_isometric.compat import write_ply_checked
from svg_isometric.common import read_cloud, cloud_stats, read_json, write_json, sha256, cli
import open3d as o3d


def crop(cloud, config):
    cloud_stats(cloud)
    result = cloud.crop(make_box(validate_crop_config(config)))
    cloud_stats(result)  # 空裁剪明确报错，不生成假结果。
    return result


def run(input_path, output=None, crop_config=None, preview=False, bounds=None):
    original = read_cloud(input_path)
    print('输入点云（相机系米制；旋转前）：', cloud_stats(original))
    if crop_config is not None and bounds is not None:
        raise ValueError('crop-config 与 bounds 不能混用')
    config = read_json(crop_config) if crop_config else None
    if bounds is not None:
        config = dict(unit='m', **dict(zip(('xmin','xmax','ymin','ymax','zmin','zmax'), bounds)))
    if config is None:
        if output:
            raise ValueError('没有明确裁剪范围，不能生成裁剪结果；省略 output 可只检查/预览')
        if preview:
            o3d.visualization.draw_geometries([original])
        return None
    if output is None:
        raise ValueError('裁剪必须指定新的 --output')
    output = Path(output); report = output.with_suffix('.json'); saved_config = output.with_name(output.stem+'_crop_config.json')
    if output.suffix.lower() != '.ply' or any(p.exists() for p in (output, report, saved_config)):
        raise FileExistsError('输出 PLY/报告/裁剪配置已存在或后缀不是 .ply，禁止覆盖')
    config = validate_crop_config(config); cropped = crop(original, config)
    if preview:
        o3d.visualization.draw_geometries([original, make_box(config)], window_name='Original and explicit AABB')
        o3d.visualization.draw_geometries([cropped], window_name='Cropped points; no scaling')
    output.parent.mkdir(parents=True, exist_ok=True)
    saved = write_ply_checked(output, cropped)
    write_json(saved_config, config)
    stats = dict(input=str(Path(input_path).resolve()), input_sha256=sha256(input_path),
        original=cloud_stats(original), cropped=cloud_stats(cropped), crop_config=config, output=saved,
        method='AABB inclusive point selection in original input coordinates; no scaling, no floor removal, no interpolation',
        warning='AABB is not semantic segmentation; foreground and background can overlap in this box')
    write_json(report, stats); print('裁剪结果：', stats['cropped'])
    return cropped


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', dest='input_path', type=Path, required=True)
    p.add_argument('--output', type=Path); p.add_argument('--crop-config', type=Path)
    p.add_argument('--bounds', type=float, nargs=6, metavar=('XMIN','XMAX','YMIN','YMAX','ZMIN','ZMAX'), help='旋转前输入坐标系中的米制边界')
    p.add_argument('--preview', action='store_true')
    return p.parse_args(argv)


if __name__ == '__main__':
    raise SystemExit(cli(lambda: run(**vars(parse_args()))))
