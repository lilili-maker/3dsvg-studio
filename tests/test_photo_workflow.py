"""工作流测试使用合成图像与人工路径，验证状态、真实矢量输出和不可覆盖约束。"""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import cv2
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from svg_isometric.photo_workflow import new_project,status,export_project,check_path
from svg_isometric.create_template import SVG,element
from svg_isometric.common import read_json,sha256
NS=f'{{{SVG}}}'


class PhotoWorkflowTests(unittest.TestCase):
    def fixture(self,folder):
        root=Path(folder); photo=root/'photo.png'
        cv2.imwrite(str(photo),np.full((80,100,3),180,np.uint8))
        project=new_project(photo,project_path=root/'project')
        return root,photo,project

    def draw(self,project,d='M 40,40 L 80,40 Q 100,50 80,70'):
        tree=ET.parse(project/'edit.svg');layer=tree.getroot().find(NS+'g[@id="seat"]')
        element(layer,'path',id='visible-seat',d=d)
        tree.write(project/'edit.svg',encoding='utf-8',xml_declaration=True)

    def test_new_project_prompts_human_and_refuses_empty_export(self):
        with tempfile.TemporaryDirectory() as folder:
            root,photo,p=self.fixture(folder)
            self.assertIn('MANUAL_STEPS.md',str(p/'MANUAL_STEPS.md'))
            guide=(p/'MANUAL_STEPS.md').read_text(encoding='utf-8')
            self.assertIn('Ctrl+Shift+L',guide); self.assertIn('Shift+Ctrl+F',guide);self.assertIn('--reviewed',guide)
            self.assertEqual(status(p)['visible_path_count'],0)
            with self.assertRaisesRegex(ValueError,'人工校对'):export_project(p)
            with self.assertRaisesRegex(ValueError,'没有可见家具路径'):export_project(p,reviewed=True)
            self.assertFalse((p/'exports').exists())

    def test_open_path_geometry_and_color_preserved_and_photo_removed(self):
        with tempfile.TemporaryDirectory() as folder:
            root,photo,p=self.fixture(folder); photo_hash=sha256(photo)
            d='M 40,40 L 80,40 Q 100,50 80,70';self.draw(p,d)
            result=export_project(p,True);svg=ET.parse(result['svg']).getroot()
            self.assertEqual(result['path_count'],1);self.assertFalse(result['real_world_scale_calibrated'])
            self.assertEqual(len(svg.findall('.//'+NS+'image')),0)
            self.assertEqual(svg.find('.//'+NS+'path').get('d'),d)
            self.assertEqual(svg.find(NS+'g').get('style'),ET.parse(p/'edit.svg').getroot().find(NS+'g[@id="seat"]').get('style'))
            self.assertEqual(sha256(photo),photo_hash)
            self.assertEqual(status(p)['stage'],'exported_current_revision')
            self.assertEqual(read_json(Path(result['svg']).with_name('report.json'))['path_count'],1)

    def test_versions_isolated_and_editing_invalidates_export_status(self):
        with tempfile.TemporaryDirectory() as folder:
            root,photo,p=self.fixture(folder);self.draw(p)
            first=export_project(p,True);old=Path(first['svg']).read_bytes()
            self.draw(p,'M 20 20 l 20 0 0 20')
            self.assertEqual(status(p)['stage'],'needs_manual_review_and_export')
            second=export_project(p,True)
            self.assertNotEqual(first['svg'],second['svg']);self.assertEqual(Path(first['svg']).read_bytes(),old)
            self.assertEqual(second['path_count'],2)
            Path(second['svg']).write_text('changed',encoding='utf-8')
            self.assertEqual(status(p)['stage'],'needs_manual_review_and_export')
            with self.assertRaises(FileExistsError):new_project(photo,project_path=p)

    def test_seed_only_accepts_same_photo_and_mapping(self):
        with tempfile.TemporaryDirectory() as folder:
            root,photo,p=self.fixture(folder);self.draw(p)
            seed=export_project(p,True)['svg']
            second=new_project(photo,seed_svg=seed,project_path=root/'second')
            self.assertEqual(status(second)['visible_path_count'],1)
            cv2.imwrite(str(root/'other.png'),np.zeros((80,100,3),np.uint8))
            with self.assertRaisesRegex(ValueError,'不同照片'):new_project(root/'other.png',seed_svg=seed,project_path=root/'wrong')
            with self.assertRaisesRegex(ValueError,'画布'):new_project(photo,seed_svg=seed,crop_px=[0,0,50,50],project_path=root/'wrong_crop')

    def test_changed_source_or_snapshot_blocks_export(self):
        for which in ('original','snapshot'):
            with tempfile.TemporaryDirectory() as folder:
                root,photo,p=self.fixture(folder);self.draw(p)
                target=photo if which=='original' else p/read_json(p/'workflow.json')['reference_snapshot']
                target.write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError,'缺失/改变'):export_project(p,True)

    def test_manual_dimensions_are_separate_from_pixel_geometry(self):
        with tempfile.TemporaryDirectory() as folder:
            root,photo,p=self.fixture(folder);self.draw(p)
            measurements=dict(dimensions=[dict(id='width',label='Width',source='manual_measurement',confirmed=True,
                            value_mm=532,endpoints_svg_px=[[20,30],[70,30]])])
            (p/'measurements.json').write_text(json.dumps(measurements),encoding='utf-8')
            result=export_project(p,True);svg=ET.parse(result['svg']).getroot()
            self.assertIn('532 mm',svg.find('.//'+NS+'text').text)
            self.assertEqual(len(svg.findall('.//'+NS+'path')),2)
            self.assertEqual(svg.find('.//'+NS+'path').get('d'),'M 40,40 L 80,40 Q 100,50 80,70')
            measurements['dimensions'][0]['source']='pixels'
            (p/'measurements.json').write_text(json.dumps(measurements),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'实测|manual_measurement'):export_project(p,True)

    def test_hidden_or_filled_paths_are_not_accepted_as_visible_line_drawing(self):
        with tempfile.TemporaryDirectory() as folder:
            root,photo,p=self.fixture(folder);self.draw(p)
            tree=ET.parse(p/'edit.svg');seat=tree.getroot().find(NS+'g[@id="seat"]')
            seat.set('style',seat.get('style')+';display:none')
            tree.write(p/'edit.svg',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'没有可见'):export_project(p,True)
            seat.set('style','fill:red;stroke:blue');tree.write(p/'edit.svg',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'无填充'):export_project(p,True)

    def test_scripts_and_unsupported_objects_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root,photo,p=self.fixture(folder);self.draw(p)
            original=(p/'edit.svg').read_bytes()
            for tag in ('script','image','rect'):
                tree=ET.ElementTree(ET.fromstring(original));seat=tree.getroot().find(NS+'g[@id="seat"]')
                element(seat,tag);tree.write(p/'edit.svg',encoding='utf-8')
                with self.assertRaises(ValueError):export_project(p,True)
                self.assertFalse((p/'exports').exists())

    def test_svg_command_syntax_real_inkscape_paths(self):
        for d in ('m 1,2 c 1,2 3,4 5,6 z','M1e1,-2.5 H20 V30 S30,20 40,10 T50,40 A2,3 0 0 1 60,50',
                  'M 1 1 2 2 3 3','M 1 1 L 2 3 M 4 5 l 2 0'):
            check_path(d)
        for d in ('M 1 2','M 1 2 L','M 1 2 C 3 4','M 1 2 A -1 2 0 0 1 3 4','M 1 2 L 1e999 0','M 1 2 Z 3 4','M 1 nan'):
            with self.subTest(d=d):
                with self.assertRaises(ValueError):check_path(d)

    def test_cli_help_requires_no_camera(self):
        result=subprocess.run([sys.executable,str(ROOT/'run_photo_svg_workflow.py'),'--help'],capture_output=True)
        self.assertEqual(result.returncode,0)
        self.assertIn(b'export',result.stdout)


if __name__=='__main__':unittest.main()
