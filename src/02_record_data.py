"""D435i单帧采集使用的原有流配置。"""
import pyrealsense2 as rs

def make_config(serial, preferred, motion_profiles):
    config = rs.config()
    config.enable_device(serial)
    if preferred:
        config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)
        config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
    else:
        # 明确使用视频流重载，避免 format 枚举被当作 stream_index。
        config.enable_stream(rs.stream.color, 0, 0, rs.format.bgr8, 0)
        config.enable_stream(rs.stream.depth, 0, 0, rs.format.z16, 0)
    for profile in motion_profiles:
        config.enable_stream(
            profile.stream_type(), profile.stream_index(), profile.format(), profile.fps()
        )
    return config
