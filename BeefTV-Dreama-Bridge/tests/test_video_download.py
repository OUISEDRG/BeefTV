import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
from generation import download_video
from bridge import BridgeError

class VideoDownloadTests(unittest.TestCase):
    def test_downloaded_mp4_is_probed_before_exposing_result(self):
        data=(Path(__file__).parent/'fixtures/sample.mp4').read_bytes()
        with tempfile.TemporaryDirectory() as d,patch('generation.requests.get') as get:
            response=MagicMock(status_code=200); response.iter_content.return_value=[data]
            get.return_value.__enter__.return_value=response
            target=Path(d)/'video.mp4'; meta=download_video('https://jzinnovation.jxbaobian.cn/result.mp4',target)
            self.assertEqual(target.read_bytes(),data); self.assertGreater(meta['duration'],0)
            self.assertEqual(meta['width'],320)
    def test_private_url_and_html_are_never_reported_as_video(self):
        with tempfile.TemporaryDirectory() as d,patch('generation.requests.get') as get:
            target=Path(d)/'video.mp4'
            with self.assertRaises(BridgeError): download_video('http://127.0.0.1/private',target)
            self.assertEqual(get.call_count,0)
            response=MagicMock(status_code=200); response.iter_content.return_value=[b'<html>not video</html>']
            get.return_value.__enter__.return_value=response
            with self.assertRaises((BridgeError,ValueError)): download_video('https://jzinnovation.jxbaobian.cn/result.mp4',target)
            self.assertFalse(target.exists())
