from pathlib import Path
import sys
import tempfile
import json
import unittest
import cv2
import numpy as np

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from run_workflow import prepare,main,auto_contours


class WorkflowCLITests(unittest.TestCase):
    def capture(self,p):
        p.mkdir();cv2.imwrite(str(p/'color.png'),np.full((8,10,3),[25,50,200],np.uint8))
        cv2.imwrite(str(p/'depth.png'),np.full((8,10),1000,np.uint16))
        k=dict(width=10,height=8,fx=10.,fy=10.,cx=4.5,cy=3.5,depth_scale=.001,
          depth_scale_unit='meters_per_depth_unit',intrinsics_unit='pixels',depth_dtype='uint16',depth_aligned_to='color')
        (p/'camera_intrinsics.json').write_text(json.dumps(k),encoding='utf-8')

    def test_offline_capture_to_two_template_routes(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder);self.capture(p/'capture');before=(p/'capture/depth.png').read_bytes()
            result=prepare(p/'capture',p/'out',width=400)
            self.assertTrue((p/'out/pointcloud.ply').is_file())
            self.assertTrue((p/'out/pointcloud_template/template.svg').is_file())
            self.assertTrue((p/'out/photo/edit.svg').is_file())
            self.assertTrue(result['awaiting_manual_trace'])
            self.assertFalse((p/'out/final.svg').exists())
            self.assertEqual((p/'capture/depth.png').read_bytes(),before)
            with self.assertRaises(FileExistsError):prepare(p/'capture',p/'out')

    def test_photo_route_needs_no_pointcloud_generation(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder);self.capture(p/'capture')
            prepare(p/'capture',p/'photo_only',reference='photo')
            self.assertFalse((p/'photo_only/pointcloud.ply').exists())
            self.assertTrue((p/'photo_only/photo/edit.svg').exists())

    def test_missing_data_is_clear_and_no_output_created(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)
            with self.assertRaises(FileNotFoundError):prepare(p,p/'out')
            self.assertFalse((p/'out').exists())

    def test_auto_contours_cli_runs_actual_algorithm_and_refuses_overwrite(self):
        import xml.etree.ElementTree as ET
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder);self.capture(p/'capture')
            color=np.full((80,100,3),245,np.uint8);color[20:60,30:70]=[30,60,190]
            depth=np.full((80,100),3000,np.uint16);depth[20:60,30:70]=1000
            cv2.imwrite(str(p/'capture/color.png'),color);cv2.imwrite(str(p/'capture/depth.png'),depth)
            k=json.loads((p/'capture/camera_intrinsics.json').read_text());k.update(width=100,height=80)
            (p/'capture/camera_intrinsics.json').write_text(json.dumps(k),encoding='utf-8')
            before=(p/'capture/depth.png').read_bytes()
            args=['auto-contours','--capture-dir',str(p/'capture'),'--output',str(p/'out'),
                  '--roi','20','10','80','70','--max-depth','2','--epsilon','0.5']
            self.assertEqual(main(args),0)
            report=json.loads((p/'out/report.json').read_text(encoding='utf-8'))
            paths=ET.parse(p/'out/contours.svg').findall('.//{http://www.w3.org/2000/svg}path')
            self.assertGreater(len(paths),0);self.assertEqual(len(paths),report['path_count'])
            self.assertEqual(report['roi_xyxy'],[20,10,80,70]);self.assertEqual(report['simplify_epsilon_px'],.5)
            self.assertEqual((p/'capture/depth.png').read_bytes(),before)
            self.assertEqual(main(args),1)

    def test_auto_contours_missing_input_creates_no_output(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)
            with self.assertRaisesRegex(FileNotFoundError,'采集文件'):auto_contours(p,p/'out')
            self.assertFalse((p/'out').exists())

    def test_auto_contours_manual_mask_forwarded(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder);self.capture(p/'capture')
            mask=np.zeros((8,10),np.uint8);mask[2:6,3:7]=255;cv2.imwrite(str(p/'mask.png'),mask)
            report=auto_contours(p/'capture',p/'out',mask=p/'mask.png',min_area=1)
            self.assertEqual(report['segmentation'],'user_binary_mask')
            self.assertFalse(report['semantic_part_segmentation'])


if __name__=='__main__':unittest.main()
