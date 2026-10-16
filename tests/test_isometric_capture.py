"""单张相机入口的离线资源释放和无损保存检查；不连接实体相机。"""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch
import cv2
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from svg_isometric import capture_view as c


def snapshot():
    k=dict(width=4,height=3,fx=4.,fy=4.,cx=1.5,cy=1.,depth_scale=.001,
        intrinsics_unit='pixels',depth_scale_unit='meters_per_depth_unit',depth_dtype='uint16',depth_aligned_to='color')
    color=np.full((3,4,3),[20,30,40],np.uint8)
    depth=np.array([[0,100,200,1000],[1001,2000,3000,4000],[1000,900,800,700]],np.uint16)
    return color,depth,k,{}


class CaptureTests(unittest.TestCase):
    def test_saved_raw_depth_and_rgb_unchanged_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'shot';color,depth,k,meta=snapshot();before=depth.copy()
            c.save_snapshot(out,color,depth,k,meta)
            actual=cv2.imread(str(out/'depth.png'),cv2.IMREAD_UNCHANGED)
            self.assertEqual(actual.dtype,np.uint16);np.testing.assert_array_equal(actual,before)
            np.testing.assert_array_equal(depth,before)
            np.testing.assert_array_equal(cv2.imread(str(out/'color.png')),color)
            with self.assertRaises(FileExistsError):c.save_snapshot(out,color,depth,k,meta)

    def test_empty_depth_refused_before_creating_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'shot';color,depth,k,meta=snapshot();depth[:]=0
            with self.assertRaises(ValueError):c.save_snapshot(out,color,depth,k,meta)
            self.assertFalse(out.exists())

    def test_existing_directory_never_opens_camera(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(c.rs,'context') as context:
            with self.assertRaises(FileExistsError):c.capture(Path(folder))
            context.assert_not_called()

    def test_space_and_q_both_release_pipeline_and_windows(self):
        for key in [32,ord('q')]:
            with self.subTest(key=key),tempfile.TemporaryDirectory() as folder:
                out=Path(folder)/'shot';pipeline=Mock();device=Mock();device.get_info.return_value='mock_camera'
                profile=pipeline.start.return_value;profile.get_device.return_value.first_depth_sensor.return_value.get_depth_scale.return_value=.001
                with patch.object(c.rs,'context') as context,patch.object(c.rs,'pipeline',return_value=pipeline),patch.object(c.rs,'pipeline_wrapper'), \
                     patch.object(c.rs,'align'),patch.object(c,'recording_module'),patch.object(c,'receive',return_value=snapshot()), \
                     patch.object(c.cv2,'imshow'),patch.object(c.cv2,'waitKey',return_value=key),patch.object(c.cv2,'destroyAllWindows') as destroy:
                    context.return_value.query_devices.return_value=[device]
                    c.capture(out)
                pipeline.stop.assert_called_once();destroy.assert_called_once()
                self.assertEqual(out.exists(),key==32)

    def test_stream_start_failure_releases_pipeline(self):
        with tempfile.TemporaryDirectory() as folder:
            pipeline=Mock();pipeline.start.side_effect=RuntimeError('occupied')
            with patch.object(c.rs,'context') as context,patch.object(c.rs,'pipeline',return_value=pipeline),patch.object(c.rs,'pipeline_wrapper'), \
                 patch.object(c,'recording_module'),patch.object(c.cv2,'destroyAllWindows') as destroy:
                context.return_value.query_devices.return_value=[Mock()]
                with self.assertRaisesRegex(RuntimeError,'Viewer'):c.capture(Path(folder)/'shot')
            pipeline.stop.assert_called_once();destroy.assert_called_once()


if __name__=='__main__':unittest.main()
