"""原项目已验证的小型几何/输入/PLY工具，仅移动函数，不带入旧重建Pipeline。"""

from pathlib import Path
from numbers import Real
import importlib.util
import numpy as np
import open3d as o3d

ROOT=Path(__file__).resolve().parents[2]
BOUNDS=('xmin','xmax','ymin','ymax','zmin','zmax')

# Unchanged functions from src/reconstruction/pose_utils.py
def _numeric_array(value, label):
    array = np.asarray(value)
    if array.dtype.kind not in "iuf":
        raise ValueError(f"{label} 必须包含实数，不能包含字符串、布尔值或复数。")
    array = array.astype(np.float64, copy=True)
    if not np.isfinite(array).all():
        raise ValueError(f"{label} 含 NaN / Inf。")
    return array

def validate_rotation_matrix(rotation, *, atol=1e-6):
    """验证 R 属于 SO(3)，返回副本；不投影、不悄悄修复旋转矩阵。"""
    if not np.isfinite(atol) or atol <= 0:
        raise ValueError("atol 必须是有限正数。")
    r = _numeric_array(rotation, "旋转矩阵")
    if r.shape != (3, 3):
        raise ValueError(f"旋转矩阵必须是 3x3，实际 {r.shape}。")
    error = float(np.linalg.norm(r.T @ r - np.eye(3), ord="fro"))
    determinant = float(np.linalg.det(r))
    if error > atol or abs(determinant - 1.0) > atol:
        raise ValueError(f"非法旋转：正交误差={error:.9g}，det(R)={determinant:.9g}，容差={atol:g}。")
    return r

def validate_transform(transform, *, atol=1e-6):
    """验证 4x4 齐次刚体变换；平移数值必须已转换成米。"""
    t = _numeric_array(transform, "齐次变换")
    if t.shape != (4, 4):
        raise ValueError(f"齐次变换必须是 4x4，实际 {t.shape}。")
    validate_rotation_matrix(t[:3, :3], atol=atol)
    if not np.allclose(t[3], [0, 0, 0, 1], atol=atol, rtol=0):
        raise ValueError(f"齐次变换最后一行必须为 [0,0,0,1]，实际 {t[3].tolist()}。")
    return t

def transform_points(T_A_B, points_B):
    """把 B 系中的 (3,) 或 (N,3) 米制点转换到 A 系；不修改输入。"""
    t = validate_transform(T_A_B)
    points = _numeric_array(points_B, "三维点")
    if points.shape != (3,) and not (points.ndim == 2 and points.shape[1] == 3):
        raise ValueError(f"三维点必须是 (3,) 或 (N,3)，实际 {points.shape}。")
    result = points @ t[:3, :3].T + t[:3, 3]
    if not np.isfinite(result).all():
        raise ValueError("转换后的三维点溢出或包含非有限值。")
    return result

# Unchanged functions from src/reconstruction/clean_mesh.py
def validate_crop_config(config):
    if not isinstance(config, dict):
        raise ValueError("裁剪配置必须是 JSON 对象。")
    allowed = set(BOUNDS) | {"unit", "schema_version", "note"}
    if set(config) - allowed:
        raise ValueError(f"未知裁剪配置字段：{sorted(set(config) - allowed)}")
    if config.get("unit") != "m":
        raise ValueError("裁剪配置必须明确声明 unit='m'，不猜测单位。")
    if "schema_version" in config and (type(config["schema_version"]) is not int or config["schema_version"] != 1):
        raise ValueError("裁剪配置 schema_version 必须为整数 1。")
    result = {"schema_version": 1, "unit": "m"}
    for key in BOUNDS:
        value = config.get(key)
        if isinstance(value, bool) or not isinstance(value, Real) or not np.isfinite(value):
            raise ValueError(f"裁剪范围 {key} 必须是您明确提供的有限米制数值，实际 {value!r}。")
        result[key] = float(value)
    for axis in "xyz":
        if result[f"{axis}min"] >= result[f"{axis}max"]:
            raise ValueError(f"裁剪范围须满足 {axis}min < {axis}max。")
    if "note" in config:
        if not isinstance(config["note"], str):
            raise ValueError("裁剪配置 note 必须是字符串。")
        result["note"] = config["note"]
    return result

def make_box(config):
    config = validate_crop_config(config)
    box = o3d.geometry.AxisAlignedBoundingBox([config[f"{axis}min"] for axis in "xyz"],
                                             [config[f"{axis}max"] for axis in "xyz"])
    box.color = [1, .2, .1]
    return box

# Unchanged functions from src/reconstruction/merge_pointclouds.py
def single_frame_module():
    """加载原有阶段 3 函数，不修改原程序或复制其 RGB-D 算法。"""
    spec = importlib.util.spec_from_file_location("single_frame_merge", ROOT / "src/04_single_frame_pointcloud.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def write_ply_checked(path, cloud):
    """排他创建占位后写入本次输出，并实际回读核对坐标和 RGB。"""
    with path.open("xb"):
        pass
    if not o3d.io.write_point_cloud(str(path), cloud, write_ascii=False):
        raise OSError(f"点云写入失败：{path}")
    saved = o3d.io.read_point_cloud(str(path))
    if len(saved.points) != len(cloud.points) or not saved.has_colors():
        raise ValueError(f"PLY 回读点数或 RGB 验证失败：{path}")
    if not np.allclose(np.asarray(saved.points), np.asarray(cloud.points), atol=1e-10, rtol=0):
        raise ValueError(f"PLY 回读坐标验证失败：{path}")
    if not np.allclose(np.asarray(saved.colors), np.asarray(cloud.colors), atol=.5 / 255 + 1e-9, rtol=0):
        raise ValueError(f"PLY 回读颜色超过 8-bit 量化容差：{path}")
    return {"path": str(path), "point_count": len(saved.points), "readback_verified": True,
            "rgb_quantization_tolerance": .5 / 255 + 1e-9}

# Unchanged functions from src/turntable/common.py
def validate_images(color, depth, intrinsics):
    shape = (intrinsics["height"], intrinsics["width"])
    if color is None or depth is None:
        raise ValueError("RGB 或 Depth 图片不存在/解码失败")
    if color.shape != (*shape, 3) or color.dtype != np.uint8:
        raise ValueError("Color 必须是与内参尺寸一致的 uint8 BGR 图片")
    if depth.shape != shape or depth.dtype != np.uint16:
        raise ValueError("Depth 必须是与内参尺寸一致的原始 uint16 PNG")
    if not np.any(depth > 0):
        raise ValueError("Depth 没有有效像素，停止该视角")
