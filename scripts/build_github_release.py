"""按白名单生成可移动的3DSVG Studio发布快照，不删除历史实验或自动推送GitHub。"""
from pathlib import Path
import argparse
import ast
import hashlib
import json
import shutil
import zipfile

ROOT=Path(__file__).resolve().parents[1]
FILES=[
 'README.md','.gitignore','.gitattributes','requirements-svg.txt','CHANGELOG.md','CONTRIBUTING.md',
 '.github/workflows/tests.yml',
 'run_workflow.py','run_3dsvg_studio.py','run_photo_svg_workflow.py','split_svg_parts.py','rgbd_to_svg.py',
 'scripts/build_github_release.py','config/svg_parts_chair.json',
 'docs/AI_ASSISTED_TRACING.md','docs/3DSVG_STUDIO_README.md','docs/SVG_PARTS_SPLITTER.md','docs/PUBLISH_VALIDATION.md','docs/RGBD_AUTO_CONTOURS.md',
 'src/04_single_frame_pointcloud.py',
 *['src/svg_isometric/'+name+'.py' for name in ('__init__','common','capture_view','generate_view_pointcloud',
   'crop_pointcloud','project_pointcloud','dimensions','create_template','validate_template',
   'create_photo_template','photo_workflow','export_photo_trace')],
 *['tests/'+name+'.py' for name in ('test_3dsvg_studio','test_split_svg_parts','test_isometric_capture',
   'test_photo_template','test_photo_workflow','test_photo_trace_export','test_workflow_cli','test_rgbd_auto_contours')]
]
FUNCTIONS={
 'src/reconstruction/pose_utils.py':['_numeric_array','validate_rotation_matrix','validate_transform','transform_points'],
 'src/reconstruction/clean_mesh.py':['validate_crop_config','make_box'],
 'src/reconstruction/merge_pointclouds.py':['single_frame_module','write_ply_checked'],
 'src/turntable/common.py':['validate_images']}
REPLACEMENTS={
 'from reconstruction.pose_utils import ':'from svg_isometric.compat import ',
 'from reconstruction.clean_mesh import validate_crop_config, make_box':'from svg_isometric.compat import validate_crop_config, make_box',
 'from reconstruction.merge_pointclouds import ':'from svg_isometric.compat import ',
 'from turntable.common import validate_images':'from svg_isometric.compat import validate_images'}


def function_source(path,names):
    text=path.read_text(encoding='utf-8-sig');tree=ast.parse(text)
    nodes={node.name:node for node in tree.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))}
    result=[]
    for name in names:
        code=ast.get_source_segment(text,nodes[name])
        assert ast.dump(ast.parse(code).body[0],include_attributes=False)==ast.dump(nodes[name],include_attributes=False)
        result.append(code)
    return '\n\n'.join(result)


def put(root,name,payload):
    path=root/name;path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8',newline='\n') as f:f.write(payload)


def build(output,make_zip=False):
    output=Path(output).resolve()
    if output.exists() or (make_zip and output.with_suffix('.zip').exists()):raise FileExistsError('发布目录或ZIP已存在，请指定新的output')
    files=[ROOT/name for name in FILES]
    for file in files:
        if not file.is_file():raise FileNotFoundError(file)
    example=ROOT/'examples/chair'
    if not example.is_dir():raise FileNotFoundError('缺少唯一示例examples/chair')
    if any(p.suffix.lower() in ('.db3','.bag','.ply','.obj','.stl') for p in example.rglob('*')):
        raise ValueError('示例只发布一组RGB-D及SVG，不发布实验点云/录制/网格')
    fixed={'capture/color.png','capture/depth.png','capture/camera_intrinsics.json','final.svg',
           'preview.png','provenance.json','parts_config.json','parts/README.md','parts/split_report.json',
           'README.md','detailed_preview.png','simplified/final.svg','simplified/preview.png',
           'simplified/parts_config.json','simplified/simplification_report.json',
           'simplified/parts/README.md','simplified/parts/split_report.json'}
    import re
    extras=[p.relative_to(example).as_posix() for p in example.rglob('*') if p.is_file() and
            p.relative_to(example).as_posix() not in fixed and not re.fullmatch(r'(?:simplified/)?parts/\d+\.svg',p.relative_to(example).as_posix())]
    if extras:raise ValueError('示例包含白名单外文件，请先明确整理：'+str(extras))
    output.mkdir(parents=True,exist_ok=False)
    adaptations=[]
    for file in files:
        name=file.relative_to(ROOT).as_posix();text=file.read_text(encoding='utf-8-sig')
        if name.endswith('.py') and name!='scripts/build_github_release.py':
            for old,new in REPLACEMENTS.items():
                if old in text:text=text.replace(old,new);adaptations.append(name+': '+old+' -> '+new)
        put(output,name,text)
    compat=ROOT/'src/svg_isometric/compat.py'
    if compat.is_file():
        put(output,'src/svg_isometric/compat.py',compat.read_text(encoding='utf-8'))
    else:
        chunks=['"""原项目已验证的小型几何/输入/PLY工具，仅移动函数，不带入旧重建Pipeline。"""',
                'from pathlib import Path\nfrom numbers import Real\nimport importlib.util\nimport numpy as np\nimport open3d as o3d',
                "ROOT=Path(__file__).resolve().parents[2]\nBOUNDS=('xmin','xmax','ymin','ymax','zmin','zmax')"]
        for file,names in FUNCTIONS.items():
            chunks.append('# Unchanged functions from '+file+'\n'+function_source(ROOT/file,names))
        put(output,'src/svg_isometric/compat.py','\n\n'.join(chunks)+'\n')
    # 相机入口只需要既有流配置；不把录制器、运动/重建功能带入发布版。
    config='"""D435i单帧采集使用的原有流配置。"""\nimport pyrealsense2 as rs\n\n'+function_source(ROOT/'src/02_record_data.py',['make_config'])+'\n'
    put(output,'src/02_record_data.py',config)
    for file in example.rglob('*'):
        if file.is_file():
            target=output/file.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(file,target)
    put(output,'requirements.txt',(ROOT/'requirements-svg.txt').read_text(encoding='utf-8'))
    manifest=dict(app_name='3DSVG Studio',example_cases=['chair'],
       excluded=['.git','.venv','.venv-gpu','data','output','historical_reconstruction','robot','db3','mesh'],
       adaptations=adaptations,
       note='compat functions are extracted with AST identity checks. No historical source file is modified.',files={})
    for file in sorted(output.rglob('*')):
        if file.is_file():manifest['files'][file.relative_to(output).as_posix()]=dict(size_bytes=file.stat().st_size,sha256=hashlib.sha256(file.read_bytes()).hexdigest())
    put(output,'build_manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    if make_zip:
        with zipfile.ZipFile(output.with_suffix('.zip'),'x',zipfile.ZIP_DEFLATED) as archive:
            for file in sorted(output.rglob('*')):
                if file.is_file():archive.write(file,arcname='3dsvg-studio/'+file.relative_to(output).as_posix())
        with zipfile.ZipFile(output.with_suffix('.zip')) as archive:
            if archive.testzip():raise ValueError('ZIP校验失败')
    print('GitHub发布目录：',output)
    print('文件数：',len(manifest['files'])+1,'；仅包含一个椅子示例。未进行git add/commit/push。')
    return manifest


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--zip',action='store_true')
    args=p.parse_args()
    try:build(args.output,args.zip)
    except (OSError,ValueError,KeyError) as exc:p.exit(1,'错误：'+str(exc)+'\n')
