"""Local-only standalone video reference validation and native mention contract."""
import base64
import hashlib
import json
import tempfile
import unittest
from types import SimpleNamespace
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote

from bridge import BridgeError
import video_reference_media as media


def spec(**limits):
    return {'limits': {'videoMin': 0, 'videoMax': 10, 'videoSizeMin': 0,
        'videoSizeMax': 200, 'videoDurationMin': 2, 'videoDurationMax': 30,
        'totalVideoDurationMax': 30, **limits}}


def ref(**values):
    return {'bytes': 100, 'duration': 3, 'sha256': 'a' * 64,
        'url': 'https://jzinnovation.jxbaobian.cn/resourceBucket/video/sample.mp4',
        'name': '视频1.mp4', **values}


def data(raw=b'fixture', mime='video/mp4'):
    return f'data:{mime};base64,' + base64.b64encode(raw).decode()


class VideoReferenceTests(unittest.TestCase):
    def test_multiple_videos_preserve_order_and_repeated_mentions(self):
        request = {'prompt': '@视频2 跟随 @视频1 再次 @视频2', 'videos': [data(b'a'), data(b'b')]}
        self.assertEqual(media.validate_video_references(request, spec()), [b'a', b'b'])
        self.assertEqual(media.video_reference_indexes(request['prompt'], 2), [1, 0, 1])

    def test_missing_unknown_and_out_of_range_mentions_are_rejected(self):
        for prompt in ['@视频1', '@视频0 @视频2', '@视频1 @视频3', '未引用视频']:
            with self.subTest(prompt=prompt), self.assertRaises(BridgeError):
                media.validate_video_references({'prompt': prompt, 'videos': [data(), data()]}, spec())

    def test_catalog_limit_is_used_instead_of_old_single_video_limit(self):
        prompt = ' '.join('@视频' + str(i + 1) for i in range(10))
        self.assertEqual(len(media.validate_video_references({'prompt': prompt, 'videos': [data()] * 10}, spec())), 10)
        with self.assertRaisesRegex(BridgeError, '10'):
            media.validate_video_references({'prompt': prompt + ' @视频11', 'videos': [data()] * 11}, spec())
        with self.assertRaises(BridgeError):
            media.validate_video_references({'prompt': '@视频1', 'videos': [data()]}, spec(videoMax=0))

    def test_only_inline_video_formats_are_accepted(self):
        for value in ['https://example.com/movie.mp4', 'C:/movie.mp4', data(mime='audio/mp4'),
                'data:video/mp4;base64,!!!!', data(raw=b''), None]:
            with self.subTest(value=value), self.assertRaises(BridgeError):
                media.validate_video_references({'prompt': '@视频1', 'videos': [value]}, spec())
        for mime in ['video/mp4', 'video/quicktime', 'video/webm', 'application/octet-stream']:
            self.assertEqual(media.validate_video_references({'prompt': '@视频1', 'videos': [data(mime=mime)]}, spec()), [b'fixture'])

    def test_each_file_size_and_combined_duration_are_enforced(self):
        media.validate_video_limits(spec(), [ref(duration=15), ref(duration=15, sha256='b'*64)])
        media.validate_video_limits(spec(), [ref(duration=30, bytes=200*1024*1024)])
        for refs in [[ref(duration=1.99)], [ref(duration=30.01)], [ref(bytes=200*1024*1024+1)],
                [ref(duration=16), ref(duration=15, sha256='b'*64)], [ref(duration=float('nan'))],
                [ref(duration=True)], [ref(bytes=-1)], [ref(bytes=True)]]:
            with self.subTest(refs=refs), self.assertRaises(BridgeError):
                media.validate_video_limits(spec(), refs)

    def test_uploaded_mention_matches_native_video_contract(self):
        class Tags(HTMLParser):
            def handle_starttag(self, tag, attrs):
                self.attrs = dict(attrs)
        parsed = Tags()
        parsed.feed(media.video_reference_tag(ref()))
        self.assertEqual(parsed.attrs['data-value'], ref()['url'])
        self.assertEqual(json.loads(unquote(parsed.attrs['data-extra-props'])), {
            'fileType': 2, 'fileSizeBytes': 100, 'durationSeconds': 3,
            'materialType': 0, 'materialId': 0, 'assetSource': 'local-upload',
            'name': '视频1.mp4', 'fileName': '视频1.mp4', 'isOpen': False})
        with self.assertRaises(BridgeError):
            media.video_reference_tag(ref(url='https://example.com/movie.mp4'))

    def test_mp4_bytes_are_kept_and_prepared_files_are_rechecked(self):
        raw = b'unchanged mp4 fixture'
        digest = hashlib.sha256(raw).hexdigest()
        metadata = {'duration': 3, 'bytes': len(raw), 'sha256': digest,
                    'width': 640, 'height': 360, 'fps': 24, 'hasAudio': True}
        with tempfile.TemporaryDirectory() as folder, patch('enhancement.probe_video', return_value=metadata):
            refs = media.prepare_video_references([raw], spec(), folder)
            self.assertEqual(Path(refs[0]['path']).read_bytes(), raw)
            self.assertEqual(refs[0]['sourceSha256'], digest)
            media.verify_video_files(refs)
            Path(refs[0]['path']).write_bytes(b'changed')
            with self.assertRaises(BridgeError):
                media.verify_video_files(refs)

    def test_conversion_maps_moving_video_stream_not_attached_cover_art(self):
        raw = b'MOV fixture with cover art before its moving video stream'
        metadata = {'duration': 3, 'bytes': 100, 'sha256': 'a' * 64,
                    'width': 640, 'height': 360, 'fps': 24, 'hasAudio': True}
        # Probe excludes attached_pic streams. The ffmpeg boundary must select
        # that same class of moving streams; lowercase v includes cover art.
        with tempfile.TemporaryDirectory() as folder, \
                patch('enhancement.probe_video', side_effect=[BridgeError('not MP4'), metadata]), \
                patch.object(media, '_probe_convertible', return_value={'duration': 3, 'bytes': len(raw)}), \
                patch.object(media.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as convert:
            refs = media.prepare_video_references([raw], spec(), folder)
            command = convert.call_args.args[0]
            self.assertEqual(command[command.index('-map') + 1], '0:V:0')
            self.assertEqual(refs[0]['width'], 640)
            self.assertEqual(refs[0]['height'], 360)


if __name__ == '__main__':
    unittest.main()
