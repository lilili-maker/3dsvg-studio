"""精简版使用已知三维盒子验证几何与交付文件，不把合成测试视为真实描边。"""
import base64
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import cv2
import numpy as np
import open3d as o3d

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from run_3dsvg_studio import prepare_cloud,render_reference,run,parse_args
from svg_isometric.project_pointcloud import furniture_transform,view_basis,project_points
from svg_isometric.create_template import SVG,INK
NS=f'{{{SVG}}}'


def box(extents=(.2,.2,.2),colored=True):
    points=np.array([[x,y,z] for x in (0,extents[0]) for y in (0,extents[1]) for z in (0,extents[2])])
    cloud=o3d.geometry.PointCloud(o3d.utility.Vector3dVector(points))
    if colored:cloud.colors=o3d.utility.Vector3dVector(np.tile([1.,0.,0.],(len(points),1)))
    return cloud


class StudioTests(unittest.TestCase):
    def input(self,root,cloud=None):
        path=Path(root)/'input.ply';o3d.io.write_point_cloud(str(path),box() if cloud is None else cloud)
        return path

    def test_known_projection_and_foreshortening(self):
        points=np.array([[0,0,0],[.2,0,0],[0,.2,0],[0,0,.2],[.2,.2,.2]])
        basis,_=view_basis('isometric')
        np.testing.assert_allclose(basis,[[1/np.sqrt(2),1/np.sqrt(2),0],[-1/np.sqrt(6),1/np.sqrt(6),np.sqrt(2/3)],[1/np.sqrt(3),-1/np.sqrt(3),1/np.sqrt(3)]],atol=1e-12)
        _,view,meta=project_points(points,np.eye(4),basis,resolution=600,padding_mm=10.)
        np.testing.assert_allclose(view,points@basis.T,atol=1e-12)
        q=np.column_stack([points,np.ones(len(points))])@np.array(meta['input_to_svg_mm']).T
        self.assertAlmostEqual(q[1,0]-q[0,0],200/np.sqrt(2))
        self.assertAlmostEqual(q[3,1]-q[0,1],-200*np.sqrt(2/3))
        self.assertAlmostEqual(np.linalg.norm(q[1]-q[0]),200*np.sqrt(2/3))
        # 两条平行三维线的投影增量相等。
        delta=np.array([[.1,0,0],[.1,.1,.1]])-np.array([[0,0,0],[0,.1,.1]])
        np.testing.assert_allclose((delta@basis.T)[0],(delta@basis.T)[1],atol=1e-12)

    def test_rotation_origin_inverse_and_camera_convention(self):
        t=furniture_transform('furniture',[0,0,90],[1,0,0])
        np.testing.assert_allclose(t@np.array([2,0,0,1]),[0,1,0,1],atol=1e-12)
        np.testing.assert_allclose(np.linalg.inv(t)@t,np.eye(4),atol=1e-12)
        camera=furniture_transform('camera',[0,0,0],[0,0,0])
        np.testing.assert_allclose(camera@np.array([0,0,1,1]),[0,1,0,1])
        np.testing.assert_allclose(camera@np.array([0,-1,0,1]),[0,0,1,1])

    def test_exact_width_isotropic_pixels_and_repeatability(self):
        for extents in ((.2,.2,.2),(.2,.2,1.),(1.,.2,.1)):
            cloud=box(extents)
            a,meta=render_reference(cloud,np.eye(4),width=400,padding_mm=10.)
            b,same=render_reference(cloud,np.eye(4),width=400,padding_mm=10.)
            self.assertEqual(a.shape[1],400);np.testing.assert_array_equal(a,b)
            self.assertEqual(meta,same)
            rect=meta['image_rectangle_svg_mm']
            np.testing.assert_allclose(np.array(rect[2:])/[a.shape[1],a.shape[0]],meta['mm_per_pixel'])
            high,hmeta=render_reference(cloud,np.eye(4),width=800,padding_mm=10.)
            np.testing.assert_allclose(meta['input_to_svg_mm'],hmeta['input_to_svg_mm'],atol=1e-10)
            self.assertEqual(high.shape[1],800)

    def test_units_mm_and_crop_are_meters_and_source_is_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            cloud=box();cloud.scale(1000,center=(0,0,0));src=self.input(folder,cloud)
            before=src.read_bytes()
            result,report=prepare_cloud(src,'mm',[-.01,.01,-.01,.21,-.01,.21])
            self.assertEqual(len(result.points),4);self.assertEqual(report['conversion_to_m'],.001)
            np.testing.assert_allclose(np.array(report['normalized']['extent_m']),[.2,.2,.2])
            self.assertEqual(src.read_bytes(),before)
            np.testing.assert_allclose(np.asarray(result.points)[:,0],0)

    def test_empty_crop_invalid_coordinates_and_unknown_unit_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            src=self.input(folder)
            with self.assertRaises(ValueError):prepare_cloud(src,'m',[2,3,2,3,2,3])
            with self.assertRaisesRegex(ValueError,'单位'):prepare_cloud(src,None)
            with self.assertRaises(ValueError):prepare_cloud(src,'m',voxel_size=-.1)
            invalid=Path(folder)/'invalid.ply'
            invalid.write_text('ply\nformat ascii 1.0\nelement vertex 2\nproperty float x\nproperty float y\nproperty float z\nend_header\nnan 0 0\n1 2 3\n',encoding='ascii')
            with self.assertRaises(ValueError):prepare_cloud(invalid,'m')
            empty=Path(folder)/'empty.ply'
            empty.write_text('ply\nformat ascii 1.0\nelement vertex 0\nproperty float x\nproperty float y\nproperty float z\nend_header\n',encoding='ascii')
            with self.assertRaises(ValueError):prepare_cloud(empty,'m')

    def test_delivered_svg_embeds_exact_png_with_required_empty_layers(self):
        with tempfile.TemporaryDirectory() as folder:
            src=self.input(folder);before=src.read_bytes();out=Path(folder)/'out'
            args=parse_args(['--input',str(src),'--output',str(out),'--unit','m','--input-frame','object','--width','400','--data-source','simulation'])
            meta=run(args)
            self.assertEqual(sorted(p.name for p in out.iterdir()),['cleaned.ply','projection.json','reference.png','template.svg'])
            root=ET.parse(out/'template.svg').getroot()
            self.assertIn('3DSVG Studio',root.find(NS+'title').text)
            self.assertTrue(root.get('width').endswith('mm'))
            groups={g.get('id'):g for g in root.findall(NS+'g')}
            for name in ['reference','backrest','seat','armrests','support','base','wheels']:
                self.assertEqual(groups[name].get(f'{{{INK}}}groupmode'),'layer')
            for name in ['backrest','seat','armrests','support','base','wheels']:
                self.assertEqual(len(groups[name].findall('.//'+NS+'path')),0)
                self.assertIn('fill:none',groups[name].get('style'))
            embedded=groups['reference'].find(NS+'image')
            data=base64.b64decode(embedded.get('href').split(',',1)[1])
            self.assertEqual(data,(out/'reference.png').read_bytes())
            decoded=cv2.imdecode(np.frombuffer(data,np.uint8),cv2.IMREAD_COLOR)
            self.assertEqual(decoded.shape[1],400)
            rect=np.array([float(embedded.get(key)) for key in ['x','y','width','height']])
            np.testing.assert_allclose(rect,meta['image_rectangle_svg_mm'],atol=1e-8)
            np.testing.assert_allclose(rect[2:]/[decoded.shape[1],decoded.shape[0]],meta['mm_per_pixel'])
            # 一个已知3D点：PNG像素中心与SVG毫米坐标映射一致。
            p=np.r_[np.asarray(box().points)[3],1.]
            q=np.array(meta['input_to_svg_mm'])@p
            png=np.array(meta['input_to_png_pixel_center'])@p
            np.testing.assert_allclose((png-.5)*meta['mm_per_pixel'],q,atol=1e-9)
            self.assertEqual(meta['manual_furniture_path_count'],0);self.assertFalse((out/'final.svg').exists())
            self.assertEqual(src.read_bytes(),before)
            # 用户文件存在时整个目录拒绝覆盖。
            (out/'final.svg').write_text('user artwork',encoding='utf-8')
            old=(out/'template.svg').read_bytes()
            with self.assertRaises(FileExistsError):run(args)
            self.assertEqual((out/'final.svg').read_text(), 'user artwork')
            self.assertEqual((out/'template.svg').read_bytes(),old)

    def test_uncolored_input_uses_monochrome_without_fabricating_rgb(self):
        with tempfile.TemporaryDirectory() as folder:
            src=self.input(folder,box(colored=False));out=Path(folder)/'out'
            result=run(parse_args(['--input',str(src),'--output',str(out),'--unit','m','--width','400']))
            self.assertEqual(result['reference_mode'],'monochrome')
            self.assertFalse(o3d.io.read_point_cloud(str(out/'cleaned.ply')).has_colors())
            self.assertEqual(result['input_coordinate_frame'],'unchanged')

    def test_nearest_measured_point_wins_occlusion(self):
        cloud=box();basis,_=view_basis('isometric')
        # 沿观察方向移近一米，投影(u,v)完全不变；近点绿色应覆盖远点红色。
        original=np.asarray(cloud.points).copy();close=original+basis[2]
        cloud.points=o3d.utility.Vector3dVector(np.vstack([original,close]))
        cloud.colors=o3d.utility.Vector3dVector(np.vstack([np.tile([1,0,0],(8,1)),np.tile([0,1,0],(8,1))]))
        image,meta=render_reference(cloud,np.eye(4),width=400,point_radius=0)
        pixel=np.array(meta['input_to_png_pixel_center'])@np.r_[original[0],1.]
        x,y=np.rint(pixel-.5).astype(int)
        np.testing.assert_array_equal(image[y,x],[0,255,0])


if __name__=='__main__':unittest.main()
