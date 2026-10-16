"""不依赖AI的照片人工描边工作流入口。所有路径均可用PowerShell执行。"""
from pathlib import Path
import argparse
import sys
from datetime import datetime
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'src'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    for name,help_text in [('new','用已有照片新建分层描边项目'),('capture','连接D435i拍一张，然后新建描边项目')]:
        p=sub.add_parser(name,help=help_text)
        p.add_argument('--output-root',type=Path,default=ROOT/'output/photo_svg_workflows')
        p.add_argument('--project-dir',type=Path,help='指定新项目目录（必须不存在）；不填则自动生成时间戳目录')
        p.add_argument('--crop-px',nargs=4,type=float,metavar=('LEFT','TOP','RIGHT','BOTTOM'),help='可选原照片像素范围；不填则使用完整照片')
        if name=='new':
            p.add_argument('--image',type=Path,required=True)
            p.add_argument('--seed-svg',type=Path,help='仅允许复用同一照片、相同画布映射的已有描边')
    for name,help_text in [('status','检查进度，并提示下一步'),('edit','打开Inkscape编辑；未安装则提示'),('export','去掉照片并校验导出新版本SVG')]:
        p=sub.add_parser(name,help=help_text);p.add_argument('--project',type=Path,required=True)
        if name=='export':p.add_argument('--reviewed',action='store_true',help='确认已人工校对可见结构、没有补造几何并保存edit.svg')
    args=parser.parse_args()
    from svg_isometric.photo_workflow import new_project,status,export_project,open_editor
    try:
        if args.command in ('new','capture'):
            if args.command=='capture':
                from svg_isometric.capture_view import capture
                folder=args.output_root/'captures'/datetime.now().strftime('capture_%Y%m%d_%H%M%S_%f')
                print('人工：先关闭RealSense Viewer。整件家具入框，保持静止；在相机窗口按空格拍摄，q取消。')
                capture(folder)
                if not (folder/'color.png').exists():return 0
                args.image=folder/'color.png'
            project=new_project(args.image,args.output_root,args.crop_px,getattr(args,'seed_svg',None),args.project_dir)
            print(f'\n项目已创建：{project}\n人工步骤：{project / "MANUAL_STEPS.md"}')
            print('下一步：在Inkscape描边/校对；新照片不会自动获得旧家具的路径。')
            print(f'& "{sys.executable}" "{ROOT / "run_photo_svg_workflow.py"}" edit --project "{project}"')
        elif args.command=='status':
            result=status(args.project)
            print(f"阶段：{result['stage']}\n可见家具路径：{result['visible_path_count']}\n已有导出版本：{len(result['exports'])}\n下一步：{result['next_action']}")
            if result['exports']:print('最近输出：',result['exports'][-1]['directory'])
        elif args.command=='edit': print(open_editor(args.project))
        elif args.command=='export':
            result=export_project(args.project,args.reviewed)
            print(f"已导出：{result['svg']}\n家具路径：{result['path_count']}；实测尺寸：{len(result['dimensions'])}\n旧版本保留。新照片需重新描边；本图保留照片透视。")
        return 0
    except (OSError,ValueError,KeyError,RuntimeError,ET.ParseError) as exc:
        print(f'错误：{exc}',file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
