"""Use the real MoviePilot SDK and fake downloader services; never transfer real torrents."""
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[3]
GENERATION = os.environ.get('TRANSFER_TEST_GENERATION', 'v3')
spec = importlib.util.spec_from_file_location('transfer_candidate', ROOT / f'plugins.{GENERATION}/torrenttransfer/__init__.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class TransferLabelsTest(unittest.TestCase):
    def setUp(self):
        self.plugin = object.__new__(module.TorrentTransfer)
        self.plugin._remainoldcat = True
        self.plugin._remainoldtag = True
        self.plugin._preserve_tag_names = ''
        self.plugin._torrent_tags = ['已整理', '转移做种']
        self.plugin._fromdownloader = 'source'
        self.plugin._skipverify = False
        self.source = {'tags': 'H&R, 保留, 勿删, 误删, PT, 站点/观众', 'category': ' 国产剧 '}
        self.client = MagicMock()
        self.client.add_torrent.return_value = SimpleNamespace(hashString='test-hash')
        self.client.get_torrent_id_by_tag.return_value = 'test-hash'

    def transfer(self, target='transmission', source_type='qbittorrent', source=None):
        helper = MagicMock()
        helper.is_downloader.side_effect = lambda kind, service: kind == service.type
        service = SimpleNamespace(type=target, instance=self.client)
        with patch.object(module, 'DownloaderHelper', return_value=helper), \
             patch.object(self.plugin, 'service_info', return_value=SimpleNamespace(type=source_type)), \
             patch.object(module.StringUtils, 'generate_random_str', return_value='temporary-id'):
            result = self.plugin._TorrentTransfer__download(service, b'test-torrent', '/downloads', self.source if source is None else source)
        self.assertEqual(result, 'test-hash')
        return self.client.add_torrent.call_args.kwargs

    def test_empty_selection_preserves_all_and_maps_category(self):
        args = self.transfer()
        self.assertEqual(args['labels'], ['H&R', '保留', '勿删', '误删', 'PT', '站点/观众', '已整理', '转移做种', '分类/国产剧'])
        self.assertEqual(args['download_dir'], '/downloads')
        self.assertTrue(args['is_paused'])
        self.assertNotIn('category', args)

    def test_selection_exact_match_with_chinese_comma(self):
        self.plugin._preserve_tag_names = ' H&R，保留,勿删,误删,保留 '
        self.source['tags'] += ', H&R-old, 请保留'
        self.assertEqual(self.transfer()['labels'], ['H&R', '保留', '勿删', '误删', '已整理', '转移做种', '分类/国产剧'])

    def test_disabled_preservation_still_maps_category(self):
        self.plugin._remainoldtag = False
        self.assertEqual(self.transfer()['labels'], ['已整理', '转移做种', '分类/国产剧'])

    def test_disabled_category_does_not_map(self):
        self.plugin._remainoldcat = False
        self.assertNotIn('分类/国产剧', self.transfer()['labels'])

    def test_empty_category_no_empty_label(self):
        self.source['category'] = None
        self.assertFalse(any(x.startswith('分类/') for x in self.transfer()['labels']))

    def test_missing_source_labels(self):
        self.source['tags'] = None
        self.assertEqual(self.transfer()['labels'], ['已整理', '转移做种', '分类/国产剧'])

    def test_unmatched_selection_still_adds_configured_labels(self):
        self.plugin._preserve_tag_names = '不存在'
        self.assertEqual(self.transfer()['labels'], ['已整理', '转移做种', '分类/国产剧'])

    def test_duplicate_labels_removed(self):
        self.source['tags'] = 'H&R,H&R,已整理,分类/国产剧'
        labels = self.transfer()['labels']
        self.assertEqual(len(labels), len(set(labels)))

    def test_whitespace_selection_means_all(self):
        self.plugin._preserve_tag_names = ' , ， '
        self.assertIn('PT', self.transfer()['labels'])

    def test_qb_category_is_native_and_temp_tag_preserved(self):
        self.plugin._preserve_tag_names = 'H&R'
        args = self.transfer(target='qbittorrent')
        self.assertEqual(args['category'], '国产剧')
        self.assertEqual(args['tag'], ['H&R', '已整理', '转移做种', 'temporary-id'])
        self.assertFalse(args['is_skip_checking'])

    def test_transmission_source_labels_filtered(self):
        self.plugin._preserve_tag_names = 'H&R'
        args = self.transfer(source_type='transmission', source=SimpleNamespace(labels=['H&R', 'PT']))
        self.assertEqual(args['labels'], ['H&R', '已整理', '转移做种'])

    def test_config_loads_and_resets_new_option(self):
        with patch.object(self.plugin, 'stop_service'):
            self.plugin.init_plugin({'enabled': False, 'preserve_tag_names': 'H&R'})
            self.assertEqual(self.plugin._preserve_tag_names, 'H&R')
            self.plugin.init_plugin({'enabled': False})
            self.assertEqual(self.plugin._preserve_tag_names, '')


if __name__ == '__main__':
    unittest.main()
