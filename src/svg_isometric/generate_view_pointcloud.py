"""沿用阶段3的 RGB-D 反投影，不融合，不修改原始深度。"""
from pathlib import Path
import argparse
import shutil
import sys
import tempfile

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from svg_isometric.compat import single_frame_module, write_ply_checked
from svg_isometric.common import cloud_stats, write_json, sha256, cli
import numpy as np


def generate(output, *, dataset=None, frame=0, color=None, depth=None, intrinsics=None,
             min_depth=.2, max_depth=3., voxel_size=.003, remove_outliers=False):
    output = Path(output); report = output.with_suffix('.json')
    if output.suffix.lower() != '.ply' or output.exists() or report.exists():
        raise ValueError('必须使用新的 .ply 和同名 .json 输出路径，禁止覆盖')
    if not np.isfinite(voxel_size) or voxel_size < 0:
        raise ValueError('voxel-size 须为非负米数')
    module = single_frame_module()
    if dataset is not None:
        if any(p is not None for p in (color, depth, intrinsics)):
            raise ValueError('dataset 与单独 RGB/Depth/内参输入不能混用')
        dataset = Path(dataset)
        sources = [dataset/'color'/f'{frame:06d}.png', dataset/'depth'/f'{frame:06d}.png', dataset/'camera_intrinsics.json']
        cloud = module.create_pointcloud(dataset, frame, min_depth, max_depth)
    else:
        if any(p is None for p in (color, depth, intrinsics)):
            raise ValueError('须提供 dataset，或同时提供 color、depth、intrinsics')
        sources = list(map(Path, (color, depth, intrinsics)))
        # 仅适配目录契约；反投影、BGR→RGB、uint16 与深度比例仍完全使用阶段3函数。
        with tempfile.TemporaryDirectory(prefix='svg_trace_rgbd_') as temporary:
            data = Path(temporary); (data/'color').mkdir(); (data/'depth').mkdir()
            for src, dst in zip(sources, [data/'color/000000.png', data/'depth/000000.png', data/'camera_intrinsics.json']):
                shutil.copyfile(src, dst)
            cloud = module.create_pointcloud(data, 0, min_depth, max_depth)
    before = cloud_stats(cloud)
    if voxel_size:
        cloud = cloud.voxel_down_sample(voxel_size)
    removed = 0
    if remove_outliers:
        count = len(cloud.points)
        if count <= 20:
            raise ValueError('点数不足以执行20邻域离群点检查')
        cloud, _ = cloud.remove_statistical_outlier(nb_neighbors=20, std_ratio=3.)
        removed = count - len(cloud.points)
    after = cloud_stats(cloud)
    output.parent.mkdir(parents=True, exist_ok=True)
    saved = write_ply_checked(output, cloud)
    write_json(report, dict(input_sha256={str(p.resolve()): sha256(p) for p in sources},
        raw=before, result=after, output=saved, frame_id=frame if dataset else None,
        coordinate_frame='camera_optical_X_right_Y_down_Z_forward', depth_range_m=[min_depth, max_depth],
        voxel_size_m=voxel_size, removed_outliers=removed, hole_filling=False, multiple_frame_fusion=False))
    return cloud


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--dataset', type=Path); source.add_argument('--color', type=Path)
    p.add_argument('--frame', type=int, default=0); p.add_argument('--depth', type=Path); p.add_argument('--intrinsics', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--min-depth', type=float, default=.2); p.add_argument('--max-depth', type=float, default=3.)
    p.add_argument('--voxel-size', type=float, default=.003)
    p.add_argument('--remove-outliers', action='store_true')
    return p.parse_args(argv)


def main():
    a = vars(parse_args()); cloud = generate(**a); print(cloud_stats(cloud))


if __name__ == '__main__':
    raise SystemExit(cli(main))
