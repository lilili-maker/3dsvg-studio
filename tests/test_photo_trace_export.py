"""真实可编辑路径与照片模板分开检查，不把嵌入图片算作矢量描边。"""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from svg_isometric.export_photo_trace import export
from svg_isometric.create_template import SVG


class PhotoTraceExportTests(unittest.TestCase):
    def fixture(self, root):
        # 极小合法PNG；本测试仅测试描边导出，不让假图冒充真实扫描照片。
        import cv2
        import numpy as np
        photo = root / 'test.png'
        cv2.imwrite(str(photo), np.zeros((20, 30, 3), np.uint8))
        data = dict(source_photo='test.png', source_sha256=hashlib.sha256(photo.read_bytes()).hexdigest(),
                    source_size_px=[30,20], crop_px=[0,0,30,20], provenance='manual_visible_photo_trace',
                    notes=['synthetic unit test only'], parts=[dict(id='seat', label='Seat', color='#9500ff',
                    paths=[['outline','M 1 1 L 10 1 L 10 10 L 1 10 Z'],['partial_open','M 12 18 L 12 20']])])
        cfg = root/'trace.json'; cfg.write_text(json.dumps(data), encoding='utf-8')
        return cfg, photo, data

    def test_vector_paths_are_image_free_with_open_boundary_and_provenance(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); cfg,photo,data=self.fixture(root); before=photo.read_bytes()
            report=export(cfg,root/'out',root)
            svg=ET.parse(root/'out/chair_wireframe_draft.svg').getroot()
            paths=svg.findall(f'.//{{{SVG}}}path')
            self.assertEqual(len(paths),2); self.assertEqual(report['path_count'],2)
            self.assertEqual(len(svg.findall(f'.//{{{SVG}}}image')),0)
            self.assertNotIn('Z',paths[1].get('d'))
            self.assertEqual(paths[1].get('data-role'),'partial_open')
            self.assertFalse(report['real_world_scale_calibrated']); self.assertEqual(report['dimensions'],[])
            self.assertEqual(photo.read_bytes(),before)
            overlay=ET.parse(root/'out/alignment_review.svg').getroot()
            self.assertEqual(len(overlay.findall(f'.//{{{SVG}}}image')),1)
            self.assertEqual(svg.find(f'.//{{{SVG}}}g').get('transform'),'translate(35,35)')
            original=(root/'out/chair_wireframe_draft.svg').read_bytes()
            with self.assertRaises(FileExistsError):export(cfg,root/'out',root)
            self.assertEqual((root/'out/chair_wireframe_draft.svg').read_bytes(),original)

    def test_wrong_photo_or_forced_closure_rejected_before_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); cfg,photo,data=self.fixture(root)
            bad=copy.deepcopy(data); bad['source_sha256']='wrong'
            cfg.write_text(json.dumps(bad),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'SHA256'):export(cfg,root/'bad',root)
            self.assertFalse((root/'bad').exists())
            data['parts'][0]['paths'][1][1]+=' Z'
            cfg.write_text(json.dumps(data),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'闭合'):export(cfg,root/'bad',root)
            self.assertFalse((root/'bad').exists())

    def test_invalid_path_coordinates_and_commands_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); cfg,photo,data=self.fixture(root)
            for invalid in ['M 1 1 L 40 1','M 1 1 C 2 2','M 1 1 A 4 4 0 0 0 3 3','M nan 1','L 1 1','M 1 1 L 2 2 junk']:
                with self.subTest(path=invalid):
                    data['parts'][0]['paths'][0][1]=invalid
                    cfg.write_text(json.dumps(data),encoding='utf-8')
                    with self.assertRaises(ValueError):export(cfg,root/'bad',root)
                    self.assertFalse((root/'bad').exists())


if __name__=='__main__':unittest.main()
