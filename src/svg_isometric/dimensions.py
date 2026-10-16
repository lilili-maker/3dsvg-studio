"""仅从已确认三维采样点或用户实测值标注；投影线长不是三维长度。"""
import numpy as np
from svg_isometric.compat import transform_points
from svg_isometric.common import read_json


def to_svg(points_furniture, metadata):
    p=np.asarray(points_furniture,float)
    if p.ndim!=2 or p.shape[1]!=3 or not np.isfinite(p).all():raise ValueError('测量端点须为有限 (N,3) 米制三维坐标')
    return np.column_stack([p,np.ones(len(p))])@np.asarray(metadata['furniture_to_svg_mm']).T


def resolve_dimensions(config_path,cloud,metadata):
    if config_path is None:return []
    config=read_json(config_path)
    if not isinstance(config,dict) or set(config)-{'pointcloud_sha256','dimensions'} or not isinstance(config.get('dimensions'),list):
        raise ValueError('尺寸配置须包含 dimensions 列表，可选 pointcloud_sha256')
    records=[];names=set();points=np.asarray(cloud.points)
    for item in config['dimensions']:
        if not isinstance(item,dict) or not isinstance(item.get('id'),str) or not item['id'] or item['id'] in names:
            raise ValueError('尺寸 id 须为唯一非空字符串')
        names.add(item['id'])
        if item.get('confirmed') is not True:raise ValueError(f"尺寸 {item['id']} 尚未人工确认，不标注为实测值")
        source=item.get('source');axis=item.get('axis','distance')
        if axis not in ('distance','X','Y','Z'):raise ValueError('axis 须为 distance 或家具 X/Y/Z')
        if source=='pointcloud':
            if config.get('pointcloud_sha256')!=metadata['input_pointcloud_sha256']:
                raise ValueError('按索引测量必须绑定当前处理后点云的 SHA256；防止裁剪后索引错位')
            indices=item.get('point_indices')
            if (not isinstance(indices,list) or len(indices)!=2 or
                any(type(i) is not int or not 0<=i<len(points) for i in indices)):
                raise ValueError('point_indices 须为当前处理后点云中两个有效整数索引')
            anchors=transform_points(metadata['T_furniture_input'],points[indices])
            delta=anchors[1]-anchors[0]
            distance=float(np.linalg.norm(delta)*1000)
            value=distance if axis=='distance' else float(abs(delta['XYZ'.index(axis)])*1000)
            note='Confirmed measured sample points; confirmation does not establish sensor accuracy'
        elif source=='manual_measurement':
            value=item.get('value_mm')
            if isinstance(value,bool) or not isinstance(value,(float,int)) or not np.isfinite(value) or value<=0:
                raise ValueError('人工实测尺寸 value_mm 须为有限正毫米数')
            anchors=np.asarray(item.get('anchor_points_furniture_m'),float)
            if anchors.shape!=(2,3) or not np.isfinite(anchors).all():raise ValueError('实测尺寸须提供两个三维标注定位点 anchor_points_furniture_m；定位点不是自动恢复的几何')
            distance=None;note='User physical measurement; anchor placement is manual, not measured point geometry'
        else:raise ValueError('尺寸 source 须为 pointcloud 或 manual_measurement')
        if value<=0:raise ValueError('零长度尺寸不能标注')
        uv=to_svg(anchors,metadata);offset=np.asarray(item.get('offset_mm',[0.,-12.]),float)
        if offset.shape!=(2,) or not np.isfinite(offset).all():raise ValueError('offset_mm 须为二维有限偏移')
        q=uv+offset;w,h=metadata['canvas_size_mm']
        if np.any(q<1) or np.any(q[:,0]>w-1) or np.any(q[:,1]>h-1):raise ValueError(f"尺寸 {item['id']} 超出画布，请调整 offset_mm 或增加 padding-mm")
        if np.linalg.norm(q[1]-q[0])<1e-6:raise ValueError('测量方向完全沿视线、端点投影重合，请换视角或采用文字标注')
        records.append(dict(id=item['id'],label=item.get('label',item['id']),source=source,axis=axis,value_mm=float(value),
            true_3d_endpoint_distance_mm=distance,projected_endpoint_distance_mm=float(np.linalg.norm(uv[1]-uv[0])),
            points_furniture_m=anchors.tolist(),endpoints_svg_mm=uv.tolist(),dimension_endpoints_svg_mm=q.tolist(),
            confirmed=True,note=note,user_note=item.get('note','')))
    return records
