"""数学平行投影；不使用透视截图，不补点云孔洞。米制输入、毫米二维坐标。"""
from pathlib import Path
import argparse
import math
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from svg_isometric.compat import validate_transform, validate_rotation_matrix, transform_points
from svg_isometric.common import read_cloud, cloud_stats, read_json, write_json, write_png, sha256, cli
import cv2
import numpy as np

VIEWS = ('isometric', 'custom_axonometric', 'front', 'side', 'top')
CAMERA_TO_FURNITURE = np.array([[1.,0,0],[0,0,1.],[0,-1.,0]])


def furniture_transform(input_frame, rotation_deg=None, origin_m=None, transform_config=None):
    """p_furniture = R @ (p_input-origin)。近似相机轴转换必须由用户检查。"""
    if input_frame not in ('camera', 'furniture'):
        raise ValueError('须明确 input-frame=camera/furniture；PLY 不声明坐标系或单位')
    if transform_config is not None:
        if rotation_deg is not None or origin_m is not None:
            raise ValueError('完整矩阵配置不能与旋转/原点参数混用')
        data = read_json(transform_config)
        if set(data) != {'translation_unit', 'T_furniture_input'} or data['translation_unit'] != 'm':
            raise ValueError('配置须包含 translation_unit=m 和 T_furniture_input（4×4）')
        return validate_transform(data['T_furniture_input'])
    rotation = CAMERA_TO_FURNITURE.copy() if input_frame == 'camera' else np.eye(3)
    if rotation_deg is not None:
        angles = np.asarray(rotation_deg, float)
        if angles.shape != (3,) or not np.isfinite(angles).all():
            raise ValueError('rotation-deg 须为有限的 RX RY RZ，单位度')
        x,y,z = np.deg2rad(angles)
        rx=np.array([[1,0,0],[0,np.cos(x),-np.sin(x)],[0,np.sin(x),np.cos(x)]])
        ry=np.array([[np.cos(y),0,np.sin(y)],[0,1,0],[-np.sin(y),0,np.cos(y)]])
        rz=np.array([[np.cos(z),-np.sin(z),0],[np.sin(z),np.cos(z),0],[0,0,1]])
        rotation = rz @ ry @ rx @ rotation
    origin = np.zeros(3) if origin_m is None else np.asarray(origin_m, float)
    if origin.shape != (3,) or not np.isfinite(origin).all():
        raise ValueError('origin-m 须为输入坐标系中的有限三维米制坐标')
    result=np.eye(4);result[:3,:3]=rotation;result[:3,3]=-rotation@origin
    return validate_transform(result)


def view_basis(view='isometric', azimuth=None, elevation=None):
    """方位角自家具+X朝+Y；观察者在该方向，俯仰角从水平面向上。"""
    if view not in VIEWS:
        raise ValueError('未知投影视图')
    defaults={'isometric':(-45.,math.degrees(math.atan(1/math.sqrt(2)))),
              'front':(-90.,0.),'side':(0.,0.),'top':(-90.,90.)}
    if view=='custom_axonometric' and (azimuth is None or elevation is None):
        raise ValueError('custom_axonometric 须同时指定 azimuth 和 elevation')
    a,e=defaults.get(view,(0.,0.))
    if azimuth is not None:a=azimuth
    if elevation is not None:e=elevation
    if not np.isfinite([a,e]).all() or not -90<=e<=90:
        raise ValueError('角度须有限，elevation 须在[-90,90]度内')
    az,el=np.deg2rad([a,e])
    right=np.array([-np.sin(az),np.cos(az),0.])
    up=np.array([-np.sin(el)*np.cos(az),-np.sin(el)*np.sin(az),np.cos(el)])
    eye=np.array([np.cos(el)*np.cos(az),np.cos(el)*np.sin(az),np.sin(el)])
    basis=validate_rotation_matrix(np.stack([right,up,eye]))
    effective_view=view
    if view in defaults and not np.allclose([a,e],defaults[view],atol=1e-10,rtol=0):effective_view='custom_axonometric'
    return basis,dict(view=effective_view,requested_view=view,azimuth_deg=float(a),elevation_deg=float(e),
        angle_definition='Observer direction: azimuth from furniture +X towards +Y, elevation above XY. Image +u=right, +v=up.')


def project_points(points, transform, basis, *, resolution=2000, padding_mm=25., center_m=None):
    """返回连续栅格索引和可逆二维仿射映射；三维→二维本身不可逆。"""
    if type(resolution) is not int or not 128<=resolution<=6000:
        raise ValueError('resolution 须为128～6000的整数长边像素数')
    basis=validate_rotation_matrix(basis);transform=validate_transform(transform)
    furniture=transform_points(transform,points)
    if len(furniture)==0:raise ValueError('空点云不能投影')
    center=np.zeros(3) if center_m is None else np.asarray(center_m,float)
    if center.shape!=(3,) or not np.isfinite(center).all():raise ValueError('center-m 须为家具坐标系中的三维有限米制坐标')
    view=(furniture-center)@basis.T
    uv=view[:,:2];lo=uv.min(axis=0);hi=uv.max(axis=0);span=(hi-lo)*1000
    if np.any(span<=1e-6):raise ValueError('投影宽或高为零；点云或投影方向退化')
    if not math.isfinite(padding_mm) or padding_mm<0:raise ValueError('padding-mm 须为有限非负毫米数')
    canvas=span+2*padding_mm;pp=float(max(canvas)/(resolution-1))
    width,height=(np.ceil(canvas/pp-1e-10).astype(int)+1).tolist()
    bounds_min=lo-padding_mm/1000;bounds_max=hi+padding_mm/1000
    index=np.column_stack(((uv[:,0]-bounds_min[0])*1000/pp,(bounds_max[1]-uv[:,1])*1000/pp))
    # SVG 1单位=1mm；图像左上像素的中心应位于SVG原点，不可忽略半像素偏移。
    f_to_view=np.eye(4);f_to_view[:3,:3]=basis;f_to_view[:3,3]=-basis@center
    view_to_svg=np.array([[1000,0,0,-bounds_min[0]*1000],[0,-1000,0,bounds_max[1]*1000]])
    f_to_svg=view_to_svg@f_to_view
    i_to_svg=f_to_svg@transform
    i_to_pixel=i_to_svg/pp;i_to_pixel[:,3]+=.5
    meta=dict(schema_version=1,input_unit='m',svg_unit='mm',projection_type='parallel_orthographic',
        T_furniture_input=transform.tolist(),furniture_to_view=f_to_view.tolist(),
        projection_2x4_furniture_to_uv_m=f_to_view[:2].tolist(),
        furniture_to_svg_mm=f_to_svg.tolist(),input_to_svg_mm=i_to_svg.tolist(),
        input_to_png_pixel_center=i_to_pixel.tolist(),view_basis=basis.tolist(),projection_center_furniture_m=center.tolist(),
        observed_projected_extent_mm=span.tolist(),canvas_size_mm=canvas.tolist(),padding_mm=float(padding_mm),
        projected_bounds_m={'u_min':float(lo[0]),'u_max':float(hi[0]),'v_min':float(lo[1]),'v_max':float(hi[1])},
        image_width_px=width,image_height_px=height,mm_per_pixel=pp,
        image_rectangle_svg_mm=[-pp/2,-pp/2,width*pp,height*pp],
        pixel_index_mapping='PNG pixel center(index+.5) -> SVG mm=(center-.5)*mm_per_pixel',
        quantization_max_per_axis_mm=pp/2,
        scale_note='SVG lengths are projected lengths. Arbitrary 3D lengths are foreshortened, not recovered by measuring SVG.',
        independent_origin=True,orientation_is_user_approximation=True)
    return index,view,meta


def render(cloud, transform, basis, *, resolution=2000, padding_mm=25., center_m=None, point_radius=1):
    cloud_stats(cloud)
    if type(point_radius) is not int or not 0<=point_radius<=3:raise ValueError('point-radius 须为0～3像素；只控制实测点字形')
    pixel,view,meta=project_points(np.asarray(cloud.points),transform,basis,resolution=resolution,padding_mm=padding_mm,center_m=center_m)
    w,h=meta['image_width_px'],meta['image_height_px'];index=np.rint(pixel).astype(int)
    counts=np.bincount(index[:,1]*w+index[:,0],minlength=w*h).reshape(h,w)
    density=np.full((h,w),255,np.uint8);mask=counts>0
    density[mask]=np.rint(210-180*np.log1p(counts[mask])/np.log1p(counts.max())).astype(np.uint8)
    if point_radius:density=cv2.erode(density,np.ones((point_radius*2+1,point_radius*2+1),np.uint8))
    density=cv2.cvtColor(density,cv2.COLOR_GRAY2BGR)
    # 按观察方向排序：view-Z越大越接近观察者。无三角形，不构造虚假遮挡面。
    distance=-view[:,2];zbuffer=np.full(w*h,np.inf);mono=np.full(w*h,255,np.uint8)
    rgb=np.full((w*h,3),255,np.uint8) if cloud.has_colors() else None
    colors=np.rint(np.asarray(cloud.colors)[:,::-1]*255).astype(np.uint8) if rgb is not None else None
    spread=float(np.ptp(view[:,2]));shade=np.full(len(view),80,np.uint8) if spread<1e-12 else np.rint(50+120*(view[:,2].max()-view[:,2])/spread).astype(np.uint8)
    for dy in range(-point_radius,point_radius+1):
        for dx in range(-point_radius,point_radius+1):
            x=index[:,0]+dx;y=index[:,1]+dy;valid=(x>=0)&(x<w)&(y>=0)&(y<h)
            src=np.flatnonzero(valid);cells=y[valid]*w+x[valid]
            order=np.lexsort((src,distance[valid],cells));ordered=cells[order]
            first=np.r_[True,np.diff(ordered)!=0];chosen=src[order[first]];at=ordered[first]
            closer=distance[chosen]<zbuffer[at];chosen=chosen[closer];at=at[closer]
            zbuffer[at]=distance[chosen];mono[at]=shade[chosen]
            if rgb is not None:rgb[at]=colors[chosen]
    mono=cv2.cvtColor(mono.reshape(h,w),cv2.COLOR_GRAY2BGR)
    if rgb is not None:rgb=rgb.reshape(h,w,3)
    meta.update(point_count=len(view),occupied_pixels=int(mask.sum()),point_radius_px=point_radius,
        point_glyph_width_mm=(2*point_radius+1)*meta['mm_per_pixel'],
        visibility='Largest viewer-axis depth sample at each pixel; no invented surfaces or missing-point fill',
        density_note='Log sample count per pixel; point glyph expansion is only display styling, not confidence or interpolation')
    return {'rgb':rgb,'monochrome':mono,'density':density},meta


def run(input_path,output_dir,input_frame,view='isometric',azimuth=None,elevation=None,rotation_deg=None,
        origin_m=None,transform_config=None,center_m=None,resolution=2000,padding_mm=25.,mode='rgb',point_radius=1,orientation_confirmed=False,data_source='real'):
    if data_source not in ('real','simulation'):raise ValueError('data-source 须为 real/simulation')
    cloud=read_cloud(input_path);transform=furniture_transform(input_frame,rotation_deg,origin_m,transform_config)
    basis,angles=view_basis(view,azimuth,elevation)
    images,meta=render(cloud,transform,basis,resolution=resolution,padding_mm=padding_mm,center_m=center_m,point_radius=point_radius)
    if mode not in images or images[mode] is None:raise ValueError('RGB模式需点云颜色；无颜色输入请改用 monochrome/density')
    output=Path(output_dir);output.mkdir(parents=True,exist_ok=False)
    for name,image in images.items():
        if image is not None:write_png(output/f'chair_isometric_{name}.png',image)
    reference=output/'chair_isometric_reference.png';write_png(reference,images[mode])
    meta.update(**angles,data_source=data_source,input_pointcloud=str(Path(input_path).resolve()),input_pointcloud_sha256=sha256(input_path),
        input_coordinate_frame=input_frame,source_cloud=cloud_stats(cloud),reference_mode=mode,
        reference_image=str(reference.resolve()),reference_sha256=sha256(reference),
        orientation_user_confirmed=orientation_confirmed,
        furniture_axis_definition={'X':'right width','Y':'front to back','Z':'up'},
        rotation_definition='Optional correction Rz@Ry@Rx after optical-axis approximation; p_furniture=R@(p_input-origin)',
        warnings=['Partial measured surfaces only; no completed furniture outline.',
          'No automatic semantic segmentation, part identification or global registration.',
          'Changing view invalidates existing 2D manual tracing; paths cannot be reprojected as 3D geometry.',
          'Dimensions need confirmed 3D points or physical measurements, not projected line lengths.'])
    if not orientation_confirmed:meta['warnings'].append('Furniture orientation is approximate and awaits manual confirmation.')
    write_json(output/'projection_metadata.json',meta)
    print('斜视参考图：',reference.resolve(),'画布毫米：',meta['canvas_size_mm'],'mm/像素：',meta['mm_per_pixel'])
    return meta


def parse_args(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',dest='input_path',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--input-frame',choices=['camera','furniture'],required=True)
    p.add_argument('--view',choices=VIEWS,default='isometric');p.add_argument('--azimuth',type=float);p.add_argument('--elevation',type=float)
    p.add_argument('--rotation-deg',nargs=3,type=float);p.add_argument('--origin-m',nargs=3,type=float);p.add_argument('--transform-config',type=Path)
    p.add_argument('--center-m',nargs=3,type=float);p.add_argument('--resolution',type=int,default=2000);p.add_argument('--padding-mm',type=float,default=25.)
    p.add_argument('--mode',choices=['rgb','monochrome','density'],default='rgb');p.add_argument('--point-radius',type=int,default=1)
    p.add_argument('--orientation-confirmed',action='store_true')
    p.add_argument('--data-source',choices=['real','simulation'],default='real')
    return p.parse_args(argv)


if __name__=='__main__':raise SystemExit(cli(lambda:run(**vars(parse_args()))))
