"""排他输出、点云检查与来源记录。"""
from pathlib import Path
import hashlib
import json
import sys

import cv2
import numpy as np
import open3d as o3d


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')


def sha256(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def write_png(path, image):
    ok, data = cv2.imencode('.png', image)
    if not ok:
        raise OSError('PNG 编码失败')
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as f:
        f.write(data.tobytes())


def cloud_stats(cloud):
    p = np.asarray(cloud.points)
    if p.ndim != 2 or p.shape[1] != 3 or len(p) == 0 or not np.isfinite(p).all():
        raise ValueError('点云为空或含非法 XYZ 坐标')
    if cloud.has_colors():
        c = np.asarray(cloud.colors)
        if c.shape != p.shape or not np.isfinite(c).all() or np.any((c < 0) | (c > 1)):
            raise ValueError('点云 RGB 非法')
    return dict(point_count=len(p), xyz_min_m=p.min(axis=0).tolist(), xyz_max_m=p.max(axis=0).tolist(),
                extent_m=np.ptp(p, axis=0).tolist(), has_rgb=cloud.has_colors(), translation_unit='m')


def read_cloud(path):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f'找不到点云：{path}')
    cloud = o3d.io.read_point_cloud(str(path))
    cloud_stats(cloud)
    return cloud


def cli(main):
    try:
        result = main()
        return result if type(result) is int else 0
    except (Exception, KeyboardInterrupt) as e:
        print(f'错误：{str(e) or "用户中断"}', file=sys.stderr)
        return 1
