"""检查真实SVG内容、几何继承、配置拆分及防覆盖，不仅检查文件存在。"""
from pathlib import Path
import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
import numpy as np

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from split_svg_parts import split_svg,read_svg,SVG,INK,NS,path_geometry,matrix


class SplitSVGTests(unittest.TestCase):
    def fixture(self,folder,body=None,attrs='width="200mm" height="100mm" viewBox="0 0 200 100"'):
        path=Path(folder)/'input.svg'
        if body is None:
            body='''<g id="reference"><image href="data:image/png;base64,AA=="/></g>
<g id="seat" inkscape:groupmode="layer" inkscape:label="Seat" transform="translate(10,20)" stroke="#9500ff" fill="none">
 <path id="p1" stroke-width="1.2" d="M 0 0 Q 20 -10 40 0 L 40 10"/></g>
<g id="base" inkscape:groupmode="layer" inkscape:label="Base" stroke="#df7c16" fill="none">
 <path id="p2" d="m 10 50 l 40 0"/><path id="p3" d="M 30 50 L 30 70"/></g>
<g id="guides"><path id="guide" d="M 0 0 L 10 0"/></g>'''
        path.write_text(f'<svg xmlns="{SVG}" xmlns:inkscape="{INK}" {attrs}>{body}</svg>',encoding='utf-8')
        return path

    def config(self,folder,parts):
        path=Path(folder)/'parts.json';path.write_text(json.dumps(dict(parts=parts)),encoding='utf-8');return path

    def test_auto_layers_preserves_units_path_style_transform_and_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=self.fixture(root);before=source.read_bytes()
            report=split_svg(source,root/'out')
            self.assertEqual(report['part_count'],2);self.assertEqual(report['assigned_vector_count'],3)
            self.assertEqual(report['unassigned_vector_count'],1)
            doc=ET.parse(root/'out/001.svg').getroot()
            self.assertEqual(doc.get('width'),'200mm');self.assertEqual(doc.get('viewBox'),'0 0 200 100')
            g=doc.find(NS+'g');self.assertEqual(g.get('transform'),'translate(10,20)');self.assertEqual(g.get('stroke'),'#9500ff')
            self.assertEqual(g.find(NS+'path').get('d'),'M 0 0 Q 20 -10 40 0 L 40 10')
            self.assertFalse(doc.findall('.//'+NS+'image'));self.assertEqual(source.read_bytes(),before)

    def test_mapping_splits_paths_and_merges_groups_without_losing_ancestors(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=self.fixture(root)
            cfg=self.config(root,[dict(name='one',elements=['p1','p3']),dict(name='two',elements=['p2'])])
            result=split_svg(source,root/'out',cfg,make_zip=True)
            a=ET.parse(root/'out/001.svg').getroot();b=ET.parse(root/'out/002.svg').getroot()
            self.assertEqual({p.get('id') for p in a.findall('.//'+NS+'path')},{'p1','p3'})
            self.assertEqual(a.find(NS+'g[@id="seat"]').get('transform'),'translate(10,20)')
            self.assertEqual(b.find('.//'+NS+'path').get('d'),'m 10 50 l 40 0')
            self.assertEqual(result['assigned_vector_count'],3)
            with zipfile.ZipFile(root/'out.zip') as z:self.assertIsNone(z.testzip());self.assertIn('001.svg',z.namelist())

    def test_trim_preserves_actual_mm_scale_and_curve_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=self.fixture(root);r=split_svg(source,root/'out',trim=True,padding=2)
            doc=ET.parse(root/'out/001.svg').getroot();vb=np.array(doc.get('viewBox').split(),float)
            self.assertTrue(r['parts'][0]['canvas_trimmed'])
            self.assertAlmostEqual(float(doc.get('width')[:-2])/vb[2],1)
            self.assertAlmostEqual(float(doc.get('height')[:-2])/vb[3],1)
            self.assertEqual(doc.find('.//'+NS+'path').get('d'),'M 0 0 Q 20 -10 40 0 L 40 10')
            self.assertLess(vb[0],10);self.assertGreater(vb[0]+vb[2],50)
            self.assertLess(vb[1],15);self.assertGreater(vb[1]+vb[3],30)

    def test_css_page_dimensions_keep_original_canvas_instead_of_distorting(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=self.fixture(root,attrs='width="200mm" height="100mm" viewBox="0 0 200 100" style="width:600px;height:300px"')
            result=split_svg(source,root/'out',trim=True)
            self.assertFalse(result['parts'][0]['canvas_trimmed'])
            doc=ET.parse(root/'out/001.svg').getroot()
            self.assertEqual(doc.get('style'),'width:600px;height:300px')
            self.assertEqual(doc.get('viewBox'),'0 0 200 100')

    def test_curve_parser_relative_smooth_and_transforms(self):
        p=path_geometry('m 10 10 h 20 v 10 q 10 10 20 0 t 20 0 c 1 2 3 4 5 0 s 3 -4 5 0 z')
        self.assertTrue(np.isfinite(p.get_extents().extents).all())
        np.testing.assert_allclose(matrix('translate(10,20) rotate(90) scale(2)')@np.array([1,0,1]),[10,22,1],atol=1e-12)
        np.testing.assert_allclose(matrix('rotate(90,10,10)')@np.array([11,10,1]),[10,11,1],atol=1e-12)

    def test_arc_is_preserved_and_trim_falls_back_explicitly(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);d='M 20 20 A 10 10 0 0 1 40 20'
            source=self.fixture(root,f'<g id="arc"><path id="a" d="{d}"/></g>')
            result=split_svg(source,root/'out',trim=True)
            doc=ET.parse(root/'out/001.svg').getroot()
            self.assertEqual(doc.find('.//'+NS+'path').get('d'),d)
            self.assertFalse(result['parts'][0]['canvas_trimmed']);self.assertTrue(result['warnings'])
            self.assertEqual(doc.get('viewBox'),'0 0 200 100')

    def test_defs_internal_resources_are_retained_broken_clone_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            source=self.fixture(root,'<defs><linearGradient id="gradient"><stop offset="0" stop-color="red"/></linearGradient></defs><g id="part"><path d="M 0 0 L 20 0 L 20 10 Z" fill="url(#gradient)"/></g>')
            split_svg(source,root/'out')
            doc=ET.parse(root/'out/001.svg').getroot();self.assertIsNotNone(doc.find(NS+'defs'))
            self.assertEqual(doc.find('.//'+NS+'path').get('fill'),'url(#gradient)')
            source=self.fixture(root,'<g id="one"><path id="outside" d="M 0 0 L 20 0"/></g><g id="two"><path d="M 0 0 L 1 1"/><use href="#outside"/></g>')
            with self.assertRaisesRegex(ValueError,'克隆'):split_svg(source,root/'broken')
            self.assertFalse((root/'broken').exists())

    def test_unlayered_paths_require_explicit_mapping_not_semantic_guessing(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=self.fixture(root,'<path id="a" d="M 0 0 L 10 10"/><path id="b" d="M 20 20 L 30 20"/>')
            with self.assertRaisesRegex(ValueError,'分组'):split_svg(source,root/'out')
            cfg=self.config(root,[dict(name='one',elements=['a']),dict(name='two',elements=['b'])])
            self.assertEqual(split_svg(source,root/'out',cfg)['part_count'],2)

    def test_missing_id_overlap_duplicate_name_and_empty_paths_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=self.fixture(root)
            examples=[
              [dict(name='missing',elements=['missing'])],
              [dict(name='one',elements=['base','p2'])],
              [dict(name='one',elements=['p1']),dict(name='two',elements=['p1'])],
              [dict(name='Part',elements=['p1']),dict(name='part',elements=['p2'])],
              [dict(name='CON',elements=['p1'])]]
            for parts in examples:
                with self.subTest(parts=parts):
                    cfg=self.config(root,parts)
                    with self.assertRaises(ValueError):split_svg(source,root/'out',cfg)
                    self.assertFalse((root/'out').exists())
            source=self.fixture(root,'<g id="empty"><path id="nothing" d=""/></g>')
            cfg=self.config(root,[dict(name='empty',elements=['nothing'])])
            with self.assertRaisesRegex(ValueError,'实际矢量'):split_svg(source,root/'out',cfg)

    def test_no_overwrite_and_hidden_state_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=self.fixture(root,'<g id="one" style="display:none;stroke:blue"><path d="M 0 0 L 20 0"/></g>')
            split_svg(source,root/'out')
            self.assertEqual(ET.parse(root/'out/001.svg').getroot().find(NS+'g').get('style'),'display:none;stroke:blue')
            old=(root/'out/001.svg').read_bytes()
            with self.assertRaises(FileExistsError):split_svg(source,root/'out')
            self.assertEqual((root/'out/001.svg').read_bytes(),old)

    def test_filenames_are_only_numbers_with_mapping_retained(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=self.fixture(root)
            result=split_svg(source,root/'out')
            self.assertEqual(sorted(p.name for p in (root/'out').glob('*.svg')),['001.svg','002.svg'])
            self.assertEqual(result['parts'][0]['name'],'seat')
            self.assertEqual(result['parts'][0]['label'],'Seat')
            self.assertEqual(result['parts'][0]['number'],'001')

    def test_untrusted_xml_scripts_and_external_resources_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for body in ('<script>alert(1)</script>','<g onclick="x()"/>','<image href="file:///C:/secret.png"/>','<path style="stroke:url(https://example.org/x)"/>'):
                source=self.fixture(root,body)
                with self.assertRaises(ValueError):read_svg(source)
            source.write_text(f'<!DOCTYPE svg [<!ENTITY a "x">]><svg xmlns="{SVG}">&a;</svg>',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'DTD'):read_svg(source)


if __name__=='__main__':unittest.main()
