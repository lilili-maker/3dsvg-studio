"""独立RGB-D自动轮廓脚本：真实算法、SVG路径与原始深度保护检查。"""
from pathlib import Path
import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import cv2
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rgbd_to_svg import foreground,contour_paths,generate,SVG


class AutoContourTests(unittest.TestCase):
    def fixture(self,p):
        color=np.full((100,100,3),245,np.uint8);color[30:70,30:70]=[30,60,190]
        depth=np.full((100,100),3000,np.uint16);depth[30:70,30:70]=1000
        k=dict(width=100,height=100,depth_scale=.001,depth_scale_unit='meters_per_depth_unit',depth_aligned_to='color')
        cv2.imwrite(str(p/'color.png'),color);cv2.imwrite(str(p/'depth.png'),depth)
        (p/'k.json').write_text(json.dumps(k),encoding='utf-8')
        return color,depth

    def test_actual_rgbd_grabcut_on_known_foreground(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder);color,depth=self.fixture(p);before=depth.copy()
            mask=foreground(color,depth,.001,[20,20,80,80],.2,2.)
            expected=np.zeros_like(mask);expected[30:70,30:70]=255
            self.assertGreater(np.count_nonzero((mask>0)&(expected>0))/np.count_nonzero((mask>0)|(expected>0)),.95)
            np.testing.assert_array_equal(depth,before)

    def test_holes_multiple_contours_and_roi_cut_are_preserved(self):
        mask=np.zeros((100,100),np.uint8);mask[20:60,20:60]=255;mask[30:40,30:40]=0;mask[70:90,70:90]=255
        rows,_=contour_paths(mask,[0,0,100,100],epsilon=0,min_area=1)
        self.assertEqual(len(rows),3);self.assertTrue(all(x['closed'] for x in rows))
        clipped=np.zeros_like(mask);clipped[20:60,20:80]=255
        rows,_=contour_paths(clipped,[10,10,80,70],epsilon=0,min_area=1)
        self.assertEqual(len(rows),1);self.assertFalse(rows[0]['closed']);self.assertNotIn('Z',rows[0]['d'])

    def test_svg_contains_real_paths_and_source_depth_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder);self.fixture(p);before=(p/'depth.png').read_bytes()
            result=generate(p/'color.png',p/'depth.png',p/'k.json',p/'out',roi=[20,20,80,80],max_depth=2.)
            root=ET.parse(p/'out/contours.svg').getroot();paths=root.findall('.//{'+SVG+'}path')
            self.assertGreater(len(paths),0);self.assertEqual(len(paths),result['path_count'])
            self.assertEqual(root.get('viewBox'),'0 0 100 100');self.assertFalse(root.findall('.//{'+SVG+'}image'))
            editable=ET.parse(p/'out/editable.svg').getroot();self.assertEqual(len(editable.findall('.//{'+SVG+'}image')),1)
            self.assertEqual((p/'depth.png').read_bytes(),before)
            self.assertFalse(result['semantic_part_segmentation']);self.assertFalse(result['real_world_scale_calibrated'])
            with self.assertRaises(FileExistsError):generate(p/'color.png',p/'depth.png',p/'k.json',p/'out')

    def test_bad_depth_alignment_and_empty_mask_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder);self.fixture(p)
            cv2.imwrite(str(p/'bad.png'),np.zeros((100,100),np.uint8))
            with self.assertRaisesRegex(ValueError,'uint16'):generate(p/'color.png',p/'bad.png',p/'k.json',p/'out')
            with self.assertRaisesRegex(ValueError,'没有可用轮廓'):generate(p/'color.png',p/'depth.png',p/'k.json',p/'out',mask_path=p/'bad.png')
            self.assertFalse((p/'out').exists())


if __name__=='__main__':unittest.main()
