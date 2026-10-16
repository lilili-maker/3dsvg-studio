"""独立单张RGB-D采集：空格保存一对真实帧，q取消，不录制长序列或估计轨迹。"""
from pathlib import Path
import argparse
from functools import lru_cache
import importlib.util
import sys
import time

if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from svg_isometric.common import write_json,write_png,cli
from svg_isometric.compat import validate_images
import cv2
import numpy as np
import pyrealsense2 as rs


@lru_cache(maxsize=1)
def recording_module():
    # 复用已经工作的流配置；单张采集不启用录制器和IMU。
    spec=importlib.util.spec_from_file_location('single_snapshot_recording',Path(__file__).resolve().parents[1]/'02_record_data.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def receive(pipeline,align,scale):
    frames=pipeline.wait_for_frames(timeout_ms=5000)
    raw_color,raw_depth=frames.get_color_frame(),frames.get_depth_frame()
    if not raw_color or not raw_depth:raise RuntimeError('没有有效配对RGB-D帧，请检查USB和相机')
    aligned=align.process(frames);color,depth=aligned.get_color_frame(),aligned.get_depth_frame()
    if not color or not depth:raise RuntimeError('Depth对齐Color后缺少有效帧')
    k=color.profile.as_video_stream_profile().get_intrinsics()
    if not np.allclose(k.coeffs,0,atol=1e-8):raise ValueError('Color含非零畸变；当前针孔接口需先校正，禁止忽略')
    metadata=dict(width=k.width,height=k.height,fx=k.fx,fy=k.fy,cx=k.ppx,cy=k.ppy,
        intrinsics_unit='pixels',depth_scale=scale,depth_scale_unit='meters_per_depth_unit',
        depth_dtype='uint16',depth_aligned_to='color',distortion_model=str(k.model),distortion_coefficients=list(k.coeffs))
    timestamps=dict(color_timestamp_ms=raw_color.get_timestamp(),depth_timestamp_ms=raw_depth.get_timestamp(),
        color_timestamp_domain=str(raw_color.get_frame_timestamp_domain()),depth_timestamp_domain=str(raw_depth.get_frame_timestamp_domain()))
    if timestamps['color_timestamp_domain']!=timestamps['depth_timestamp_domain']:
        raise ValueError('RGB/Depth时间戳时钟域不同，无法直接核对同步')
    if abs(timestamps['color_timestamp_ms']-timestamps['depth_timestamp_ms'])>50:
        raise ValueError('RGB/Depth时间差超过50ms，不能作为同一时刻的配对')
    return np.asanyarray(color.get_data()).copy(),np.asanyarray(depth.get_data()).copy(),metadata,timestamps


def save_snapshot(output_dir,color,depth,intrinsics,metadata):
    validate_images(color,depth,intrinsics)
    if not np.count_nonzero(depth):raise ValueError('原始深度全部为零，本次不保存')
    output=Path(output_dir);output.mkdir(parents=True,exist_ok=False)
    write_png(output/'color.png',color);write_png(output/'depth.png',depth)
    write_json(output/'camera_intrinsics.json',intrinsics)
    z=depth.astype(float)*intrinsics['depth_scale'];valid=(depth>0)&(z>=.2)&(z<=3.)
    write_json(output/'capture_metadata.json',dict(**metadata,data_source='realsense',
        stationary_user_confirmed=True,original_depth_dtype='uint16',depth_processing='Single actual aligned frame; no averaging, filling or normalization',
        valid_depth_ratio_full_image_0p2_to_3m=float(valid.mean()),
        note='Full-image validity does not establish chair surface quality. No background capture or motion estimation required.'))
    print('已保存单张RGB-D：',output.resolve())
    print(f'全图0.2～3米有效深度比例：{valid.mean():.1%}（不代表椅子区域完整度）')


def capture(output_dir):
    if Path(output_dir).exists():raise FileExistsError('输出目录已存在，请指定新的路径；未开启相机')
    pipeline=None
    try:
        context=rs.context();devices=context.query_devices()
        if not len(devices):raise RuntimeError('未检测到RealSense相机，请检查USB连接')
        device=devices[0];serial=device.get_info(rs.camera_info.serial_number)
        print('相机：',device.get_info(rs.camera_info.name),'序列号：',serial)
        pipeline=rs.pipeline(context)
        config=recording_module().make_config(serial,True,[])
        if not config.can_resolve(rs.pipeline_wrapper(pipeline)):
            print('首选640×480/30配置不可用，尝试兼容RGB/Depth配置')
            config=recording_module().make_config(serial,False,[])
        try:profile=pipeline.start(config)
        except RuntimeError as exc:raise RuntimeError(f'相机启动失败，请先关闭RealSense Viewer及其他相机程序：{exc}') from exc
        scale=profile.get_device().first_depth_sensor().get_depth_scale()
        align=rs.align(rs.stream.color)
        print('预热相机。让整把椅子入框并保持静止；在预览窗口按空格拍一张，q取消。')
        for _ in range(20):receive(pipeline,align,scale)
        while True:
            color,depth,k,stamp=receive(pipeline,align,scale)
            preview=color.copy()
            cv2.putText(preview,'SPACE: capture one RGB-D / q: cancel',(8,24),cv2.FONT_HERSHEY_SIMPLEX,.55,(0,255,0),1)
            cv2.imshow('RGB - single capture',preview)
            cv2.imshow('Depth - display only',cv2.applyColorMap(cv2.convertScaleAbs(depth,alpha=.03),cv2.COLORMAP_TURBO))
            key=cv2.waitKey(1)&255
            if key==ord('q'):print('已取消，没有保存图片。');return 0
            if key==32:
                save_snapshot(output_dir,color,depth,k,dict(**stamp,device_serial=serial,captured_host_unix_time_s=time.time()))
                return 0
    finally:
        try:
            if pipeline is not None:pipeline.stop()
        except RuntimeError:pass
        finally:cv2.destroyAllWindows()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output-dir',type=Path,required=True)
    return capture(p.parse_args().output_dir)


if __name__=='__main__':raise SystemExit(cli(main))
