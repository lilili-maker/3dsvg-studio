from pathlib import Path
import base64,json,sys,tempfile,unittest
import xml.etree.ElementTree as ET
import cv2,numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from svg_isometric.create_photo_template import create,photo_dimensions
from svg_isometric.create_template import SVG,PARTS
from svg_isometric.common import sha256,read_json


class PhotoTemplateTests(unittest.TestCase):
    def fixture(self,root):
        photo=root/'photo.png';image=np.zeros((80,100,3),np.uint8);image[:]=[25,130,210]
        cv2.imwrite(str(photo),image);return photo,image

    def test_photo_template_is_not_claimed_as_metric_projection_or_final(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);photo,image=self.fixture(root);before=sha256(photo)
            result=create(photo,root/'out',crop_px=[10,10,90,70]);project=read_json(root/'out/chair_photo_project.json')
            self.assertFalse(project['real_world_scale_calibrated']);self.assertIsNone(project['projection_matrix_3d'])
            self.assertFalse(result['final_svg_created']);self.assertEqual(result['dimension_count'],0)
            self.assertEqual(sha256(photo),before)
            svg=ET.parse(root/'out/chair_photo_template.svg').getroot();self.assertTrue(svg.get('width').endswith('px'))
            for part,_,color in PARTS:
                g=svg.find(f'{{{SVG}}}g[@id="{part}"]');self.assertIn(color,g.get('style'));self.assertEqual(len(g),0)
            element=svg.find(f'.//{{{SVG}}}image');png=base64.b64decode(element.get('href').split(',',1)[1])
            actual=cv2.imdecode(np.frombuffer(png,np.uint8),cv2.IMREAD_COLOR);np.testing.assert_array_equal(actual,image)
            with self.assertRaises(FileExistsError):create(photo,root/'out')

    def test_clip_is_reversible_and_bound_to_correct_photo(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);photo,_=self.fixture(root);cfg=root/'mask.json'
            cfg.write_text(json.dumps(dict(image_sha256=sha256(photo),polygons_xy=[[[10,10],[60,10],[60,60]]])),encoding='utf-8')
            create(photo,root/'out',clip_config=cfg)
            svg=ET.parse(root/'out/chair_photo_template.svg').getroot();self.assertIsNotNone(svg.find(f'.//{{{SVG}}}clipPath'))
            self.assertEqual(svg.find(f'.//{{{SVG}}}image').get('clip-path'),'url(#manual_photo_selection)')
            cfg.write_text(json.dumps(dict(image_sha256='wrong',polygons_xy=[[[1,1],[2,1],[1,2]]])),encoding='utf-8')
            with self.assertRaises(ValueError):create(photo,root/'bad',clip_config=cfg)
            self.assertFalse((root/'bad').exists())

    def test_manual_size_not_computed_from_pixel_line_length(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);photo,_=self.fixture(root);cfg=root/'dims.json'
            item=dict(id='height',label='Height',source='manual_measurement',confirmed=True,value_mm=1000,endpoints_svg_px=[[30,30],[30,60]])
            cfg.write_text(json.dumps(dict(dimensions=[item])),encoding='utf-8')
            create(photo,root/'out',dimensions_config=cfg)
            project=read_json(root/'out/chair_photo_project.json');self.assertEqual(project['dimensions'][0]['value_mm'],1000)
            svg=ET.parse(root/'out/chair_photo_template.svg').getroot();text=' '.join(t.text or '' for t in svg.iter(f'{{{SVG}}}text'))
            self.assertIn('1000 mm (manual measurement)',text)
            item['confirmed']=False;cfg.write_text(json.dumps(dict(dimensions=[item])),encoding='utf-8')
            with self.assertRaises(ValueError):photo_dimensions(cfg,170,150)

    def test_crop_outside_photo_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);photo,_=self.fixture(root)
            with self.assertRaises(ValueError):create(photo,root/'out',crop_px=[-1,0,100,80])
            self.assertFalse((root/'out').exists())


if __name__=='__main__':unittest.main()
