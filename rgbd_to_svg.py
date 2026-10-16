"""RGB+已对齐Depth → 自动前景轮廓SVG初稿；之后人工分零件，不进行三维恢复。"""
from pathlib import Path
import argparse
import base64
import hashlib
import json
import math
import xml.etree.ElementTree as ET
import cv2
import numpy as np

SVG='http://www.w3.org/2000/svg'; INK='http://www.inkscape.org/namespaces/inkscape'
ET.register_namespace('',SVG);ET.register_namespace('inkscape',INK)


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def foreground(color,depth,scale,roi,min_depth,max_depth,iterations=5):
    """深度作为GrabCut先验，RGB决定分割边界；缺失深度不当作已测量几何。"""
    h,w=depth.shape;x0,y0,x1,y1=roi
    z=depth.astype(float)*scale;valid=(depth>0)&(z>=min_depth)&(z<=max_depth)
    rectangle=np.zeros((h,w),bool);rectangle[y0:y1,x0:x1]=True
    if not np.any(valid&rectangle):raise ValueError('ROI与深度范围内没有有效采样，请调整范围或检查深度单位')
    labels=np.full((h,w),cv2.GC_BGD,np.uint8)
    # 无效深度区域仍允许RGB判断；这是分割估计，不是深度补洞。
    labels[rectangle]=cv2.GC_PR_FGD
    labels[rectangle&(depth>0)&~valid]=cv2.GC_BGD
    if not np.any(labels==cv2.GC_BGD):
        raise ValueError('需要明确背景样本；请用--roi圈住物体，并在周围留下背景')
    cv2.setRNGSeed(0)
    cv2.grabCut(color,labels,None,np.zeros((1,65),float),np.zeros((1,65),float),iterations,cv2.GC_INIT_WITH_MASK)
    return np.isin(labels,[cv2.GC_FGD,cv2.GC_PR_FGD]).astype(np.uint8)*255


def contour_paths(mask,roi,epsilon=1.,min_area=10.):
    """区域边界保持多个轮廓；ROI/画面截断形成的人工边界不强行闭合。"""
    contours,hierarchy=cv2.findContours(mask,cv2.RETR_TREE,cv2.CHAIN_APPROX_SIMPLE)
    rows=[];removed=0;x0,y0,x1,y1=roi
    for i,contour in enumerate(contours):
        area=abs(float(cv2.contourArea(contour)))
        if area<min_area:removed+=1;continue
        p=contour[:,0,:]
        if len(p)<3:continue
        cut=[]
        for a,b in zip(p,np.roll(p,-1,axis=0)):
            cut.append(any(a[axis]==bound and b[axis]==bound for axis,bound in ((0,x0),(0,x1-1),(1,y0),(1,y1-1))))
        truncated=any(cut);segments=[]
        if not truncated:segments=[(p,True)]
        else:
            start=cut.index(True)+1;run=[]
            for j in range(len(p)):
                k=(start+j)%len(p);a=p[k];b=p[(k+1)%len(p)]
                if cut[k]:
                    if len(run)>=2:segments.append((np.array(run),False))
                    run=[]
                else:
                    if not run:run=[a]
                    run.append(b)
            if len(run)>=2:segments.append((np.array(run),False))
        for segment,closed in segments:
            q=cv2.approxPolyDP(segment.reshape(-1,1,2),epsilon,closed)[:,0,:]
            if len(q)<(3 if closed else 2):continue
            d='M '+' L '.join(f'{x:g} {y:g}' for x,y in q)+(' Z' if closed else '')
            parent=int(hierarchy[0,i,3]) if hierarchy is not None else -1
            rows.append(dict(d=d,closed=closed,boundary_truncated=truncated,
                             source_contour=i,parent_contour=parent,source_area_px=area,vertices=len(q)))
    return rows,removed


def make_svg(width,height,rows,metadata,color=None):
    root=ET.Element(f'{{{SVG}}}svg',width=f'{width}px',height=f'{height}px',viewBox=f'0 0 {width} {height}',version='1.1')
    ET.SubElement(root,f'{{{SVG}}}title').text='RGB-D automatic outline draft — manual part grouping required'
    ET.SubElement(root,f'{{{SVG}}}metadata').text=json.dumps(metadata,ensure_ascii=False,allow_nan=False)
    def layer(id_,label,**attrs):
        g=ET.SubElement(root,f'{{{SVG}}}g',id=id_,**attrs)
        g.set(f'{{{INK}}}groupmode','layer');g.set(f'{{{INK}}}label',label);return g
    if color is not None:
        reference=layer('reference','Reference',opacity='.5')
        reference.set('{http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd}insensitive','true')
        ok,png=cv2.imencode('.png',color)
        if not ok:raise OSError('参考图编码失败')
        ET.SubElement(reference,f'{{{SVG}}}image',x='0',y='0',width=str(width),height=str(height),
                      href='data:image/png;base64,'+base64.b64encode(png.tobytes()).decode('ascii'))
    outlines=layer('auto_contours','Automatic contours - review first',fill='none',stroke='#d92828',
                   **{'stroke-width':'1.2','stroke-linecap':'round','stroke-linejoin':'round'})
    for i,row in enumerate(rows):
        ET.SubElement(outlines,f'{{{SVG}}}path',id=f'outline_{i+1:03d}',d=row['d'],
                      **{'data-geometry-source':'rgbd_foreground_segmentation_estimate',
                         'data-boundary-truncated':str(row['boundary_truncated']).lower()})
    if color is not None:
        for i,c in enumerate(('#00a94f','#9500ff','#009eac','#df7c16','#2868cb'),1):
            layer(f'part_{i:03d}',f'Part {i:03d}',fill='none',stroke=c,**{'stroke-width':'1.2'})
    return root


def generate(color_path,depth_path,intrinsics_path,output,roi=None,min_depth=.2,max_depth=3.,
             epsilon=1.,min_area=10.,iterations=5,mask_path=None):
    sources=[Path(x).resolve() for x in (color_path,depth_path,intrinsics_path)]
    if mask_path is not None:sources.append(Path(mask_path).resolve())
    hashes={str(p):sha(p) for p in sources};out=Path(output).resolve()
    if out.exists():raise FileExistsError('输出目录已存在，请换新目录，禁止覆盖人工成果')
    color=cv2.imread(str(sources[0]),cv2.IMREAD_COLOR);depth=cv2.imread(str(sources[1]),cv2.IMREAD_UNCHANGED)
    k=json.loads(sources[2].read_text(encoding='utf-8-sig'))
    if color is None or depth is None:raise ValueError('无法读取RGB或Depth图片')
    if depth.ndim!=2 or depth.dtype!=np.uint16:raise ValueError('Depth必须为原始二维uint16，不能使用伪彩色深度图')
    if color.shape[:2]!=depth.shape or depth.shape!=(k['height'],k['width']):raise ValueError('RGB、Depth和内参分辨率不一致')
    if k.get('depth_aligned_to')!='color' or k.get('depth_scale_unit')!='meters_per_depth_unit':
        raise ValueError('内参必须明确Depth已对齐Color，depth_scale单位为meters_per_depth_unit')
    scale=k['depth_scale']
    if isinstance(scale,bool) or not isinstance(scale,(int,float)) or not math.isfinite(scale) or scale<=0:raise ValueError('depth_scale非法')
    if not all(math.isfinite(x) for x in (min_depth,max_depth,epsilon,min_area)) or not 0<=min_depth<max_depth or epsilon<0 or min_area<0:
        raise ValueError('深度范围/轮廓简化参数非法')
    if type(iterations) is not int or not 1<=iterations<=20:raise ValueError('iterations须为1～20整数')
    h,w=depth.shape
    roi=[1,1,w-1,h-1] if roi is None else list(roi)
    if len(roi)!=4 or any(type(v) is not int for v in roi) or not (0<=roi[0]<roi[2]<=w and 0<=roi[1]<roi[3]<=h):
        raise ValueError('roi格式为原RGB像素XMIN YMIN XMAX YMAX，须在图片内')
    if mask_path is None:
        mask=foreground(color,depth,scale,roi,min_depth,max_depth,iterations)
    else:
        mask=cv2.imread(str(sources[3]),cv2.IMREAD_UNCHANGED)
        if mask is None or mask.shape!=(h,w) or mask.dtype!=np.uint8 or not np.isin(mask,[0,255]).all():
            raise ValueError('人工mask须为同尺寸、0/255二值uint8 PNG')
        restricted=np.zeros_like(mask);x0,y0,x1,y1=roi;restricted[y0:y1,x0:x1]=mask[y0:y1,x0:x1];mask=restricted
    rows,removed=contour_paths(mask,roi,epsilon,min_area)
    if not rows:raise ValueError('没有可用轮廓，请调整ROI/深度范围，或修正mask后用--mask重跑')
    report=dict(workflow='rgbd_to_perspective_svg_outline_draft',source_sha256=hashes,
                image_size_px=[w,h],depth_scale_m_per_unit=scale,depth_range_m=[min_depth,max_depth],roi_xyxy=roi,
                segmentation='user_binary_mask' if mask_path is not None else 'RGB GrabCut with depth-range priors',
                simplify_epsilon_px=epsilon,min_area_px=min_area,filtered_small_contours=removed,
                foreground_pixels=int(np.count_nonzero(mask)),path_count=len(rows),
                closed_paths=sum(r['closed'] for r in rows),open_paths=sum(not r['closed'] for r in rows),
                paths=rows,document_unit='px',projection='original_RGB_perspective',real_world_scale_calibrated=False,
                semantic_part_segmentation=False,depth_modified=False,automatic_geometry_completion=False,
                limitations=['Mask boundaries are segmentation estimates, not complete measured object surfaces.',
                             'Depth holes may be classified by RGB; that does not recover missing depth.',
                             'Review floor/background leakage and thin/missing legs before manual part grouping.',
                             'ROI/image cutoff edges are omitted where detected; truncated contours remain open.'])
    out.mkdir(parents=True,exist_ok=False)
    for name,reference in [('contours.svg',None),('editable.svg',color)]:
        root=make_svg(w,h,rows,report,reference)
        with (out/name).open('xb') as f:ET.ElementTree(root).write(f,encoding='utf-8',xml_declaration=True)
        saved=ET.parse(out/name).getroot()
        assert len(saved.findall('.//{'+SVG+'}path'))==len(rows)
    preview=color.copy();preview[mask==0]=(preview[mask==0]*.2).astype(np.uint8)
    for row in rows:
        numbers=np.array([float(n) for n in row['d'].replace('M','').replace('L','').replace('Z','').split()]).reshape(-1,2)
        cv2.polylines(preview,[np.rint(numbers).astype(np.int32)],row['closed'],(0,0,255),1,cv2.LINE_AA)
    for name,image in [('foreground_mask.png',mask),('preview.png',preview)]:
        ok,png=cv2.imencode('.png',image)
        if not ok:raise OSError('PNG写入失败')
        with (out/name).open('xb') as f:f.write(png.tobytes())
    assert all(sha(p)==hashes[str(p)] for p in sources)
    report['original_inputs_unchanged']=True
    with (out/'report.json').open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2,allow_nan=False)
    print(f'自动初稿已生成：{out}\n轮廓{len(rows)}条；开放{report["open_paths"]}条。请先查看preview.png检查背景误分。')
    print('人工下一步：Inkscape打开editable.svg，修正轮廓、补可见内部线并按零件分组；隐藏Reference后另存final.svg。')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--color',type=Path,required=True);p.add_argument('--depth',type=Path,required=True)
    p.add_argument('--intrinsics',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--roi',nargs=4,type=int,help='物体矩形XMIN YMIN XMAX YMAX，RGB原像素；背景复杂时应指定')
    p.add_argument('--min-depth',type=float,default=.2);p.add_argument('--max-depth',type=float,default=3.)
    p.add_argument('--epsilon',type=float,default=1.,help='轮廓多边形简化容差，像素')
    p.add_argument('--min-area',type=float,default=10.,help='去除小轮廓阈值，像素面积')
    p.add_argument('--iterations',type=int,default=5);p.add_argument('--mask',type=Path,help='可选人工修正后的二值前景mask')
    a=p.parse_args()
    try:generate(a.color,a.depth,a.intrinsics,a.output,a.roi,a.min_depth,a.max_depth,a.epsilon,a.min_area,a.iterations,a.mask);return 0
    except (ValueError,OSError,KeyError,cv2.error) as exc:print('错误：'+str(exc));return 1


if __name__=='__main__':raise SystemExit(main())
