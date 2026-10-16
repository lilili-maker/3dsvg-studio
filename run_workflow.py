"""3DSVG Studio：拍摄 → 点云/照片/自动轮廓底稿 → 人工校对 → 编号零件SVG。"""
import argparse
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'src'))


def prepare(capture_dir,output,reference='both',width=1600,azimuth=-75.,elevation=15.,crop=None):
    from svg_isometric.generate_view_pointcloud import generate
    from svg_isometric.photo_workflow import new_project
    import run_3dsvg_studio as studio
    source=Path(capture_dir).resolve();out=Path(output).resolve()
    if out.exists():raise FileExistsError('输出目录已存在，请换新目录，保留旧描边：'+str(out))
    if reference not in ('both','pointcloud','photo'):raise ValueError('reference须为both/pointcloud/photo')
    for file in ('color.png','depth.png','camera_intrinsics.json'):
        if not (source/file).is_file():raise FileNotFoundError(f'缺少采集文件：{source/file}')
    result={'output':str(out),'reference':reference,'awaiting_manual_trace':True}
    if reference in ('both','pointcloud'):
        out.mkdir(parents=True,exist_ok=False)
        cloud=out/'pointcloud.ply'
        generate(cloud,color=source/'color.png',depth=source/'depth.png',intrinsics=source/'camera_intrinsics.json',voxel_size=0.)
        argv=['--input',str(cloud),'--output',str(out/'pointcloud_template'),'--unit','m',
              '--input-frame','camera','--width',str(width),'--azimuth',str(azimuth),'--elevation',str(elevation)]
        if crop is not None:argv+=['--crop',*map(str,crop)]
        studio.run(studio.parse_args(argv))
        result['pointcloud_template']=str(out/'pointcloud_template/template.svg')
    else:out.mkdir(parents=True,exist_ok=False)
    if reference in ('both','photo'):
        new_project(source/'color.png',project_path=out/'photo')
        result['photo_project']=str(out/'photo')
        result['photo_template']=str(out/'photo/edit.svg')
    print('\n下一步需要人工操作：')
    print('1. 对比底稿：点云有孔洞时，使用photo/edit.svg照片底稿；它保留透视，不是等轴测量图。')
    print('2. Inkscape选择部件层，用B描线、N调节点，无填充、彩色描边。也可按docs/AI_ASSISTED_TRACING.md请求AI辅助。')
    print('3. 保存并人工校对，隐藏Reference/Guides，另存final.svg；按零件分组后执行split。')
    print('4. 新照片必须重新描边，不能直接复用示例椅子的路径。')
    return result


def auto_contours(capture_dir,output,roi=None,min_depth=.2,max_depth=3.,
                  epsilon=1.,min_area=10.,iterations=5,mask=None):
    """复用独立轮廓脚本，不复制分割算法；输入仍是原始对齐RGB-D。"""
    from rgbd_to_svg import generate
    import cv2
    source=Path(capture_dir).resolve()
    for name in ('color.png','depth.png','camera_intrinsics.json'):
        if not (source/name).is_file():raise FileNotFoundError(f'缺少采集文件：{source/name}')
    try:
        report=generate(source/'color.png',source/'depth.png',source/'camera_intrinsics.json',
                        output,roi,min_depth,max_depth,epsilon,min_area,iterations,mask)
    except cv2.error as exc:
        raise ValueError('OpenCV轮廓提取失败，请检查ROI和前景/背景样本：'+str(exc)) from exc
    print('统一工作流：先检查preview.png，再用Inkscape打开editable.svg修正并分零件。')
    print('在final.svg副本中只保留已校对零件组，移除剩余auto_contours候选层；仅隐藏该层仍可能被split导出。')
    print('本底稿为照片透视、像素坐标；不与点云正交底稿混用，也不自动识别零件。')
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    c=sub.add_parser('capture',help='打开D435i；空格保存单张RGB-D，q取消');c.add_argument('--output',type=Path,required=True)
    c=sub.add_parser('prepare',help='使用已拍的RGB-D生成底稿，不开启相机')
    c.add_argument('--capture-dir',type=Path,required=True);c.add_argument('--output',type=Path,required=True)
    c.add_argument('--reference',choices=['both','pointcloud','photo'],default='both')
    c.add_argument('--width',type=int,default=1600);c.add_argument('--azimuth',type=float,default=-75.)
    c.add_argument('--elevation',type=float,default=15.);c.add_argument('--crop',type=float,nargs=6,help='可选输入轴AABB，xmin xmax ymin ymax zmin zmax，米')
    c=sub.add_parser('auto-contours',help='RGB-D自动提取照片透视轮廓初稿，之后人工校对/分零件')
    c.add_argument('--capture-dir',type=Path,required=True);c.add_argument('--output',type=Path,required=True)
    c.add_argument('--roi',type=int,nargs=4,help='RGB像素XMIN YMIN XMAX YMAX，右/下边界不包含')
    c.add_argument('--min-depth',type=float,default=.2);c.add_argument('--max-depth',type=float,default=3.)
    c.add_argument('--epsilon',type=float,default=1.,help='轮廓简化容差，像素')
    c.add_argument('--min-area',type=float,default=10.,help='过滤小轮廓的像素面积')
    c.add_argument('--iterations',type=int,default=5);c.add_argument('--mask',type=Path,help='可选人工修正的0/255二值PNG')
    c=sub.add_parser('split',help='拆成001.svg起的编号零件文件')
    c.add_argument('--input',type=Path,required=True);c.add_argument('--output',type=Path,required=True)
    c.add_argument('--config',type=Path);c.add_argument('--trim',action='store_true');c.add_argument('--zip',action='store_true')
    a=p.parse_args(argv)
    try:
        if a.command=='capture':
            from svg_isometric.capture_view import capture
            print('人工准备：关闭RealSense Viewer，整件物体入框并保持静止；预览窗口按空格拍摄，q取消。')
            return capture(a.output)
        if a.command=='prepare':prepare(a.capture_dir,a.output,a.reference,a.width,a.azimuth,a.elevation,a.crop)
        if a.command=='auto-contours':
            auto_contours(a.capture_dir,a.output,a.roi,a.min_depth,a.max_depth,a.epsilon,a.min_area,a.iterations,a.mask)
        if a.command=='split':
            from split_svg_parts import split_svg
            report=split_svg(a.input,a.output,a.config,a.trim,make_zip=a.zip)
            print(f"已生成{report['part_count']}个零件：001.svg起；目录：{a.output.resolve()}")
            for row in report['parts']:print(row['file'],row['label'])
        return 0
    except (OSError,ValueError,RuntimeError,KeyError) as exc:
        print(f'错误：{exc}',file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
