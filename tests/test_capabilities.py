import unittest
import json
import tempfile
from pathlib import Path

from proteus_mcp.capabilities import functional_coverage,source_fingerprint,acceptance_evidence
from proteus_mcp.server import TOOLS


class CapabilityMapTests(unittest.TestCase):
    def test_every_advertised_adapter_has_an_actual_tool(self):
        coverage = functional_coverage()
        names = {t['name'] for t in TOOLS}
        self.assertFalse(coverage['coverage_complete'])
        self.assertFalse(coverage['installed_or_discovered_is_runtime_verified'])
        areas = coverage['areas']
        self.assertEqual(len(areas),len({a['area'] for a in areas}))
        for area in areas:
            self.assertTrue(set(area['tools']) <= names)
            self.assertEqual(bool(area['tools']),area['implemented'])
            self.assertTrue(area['scope'])
            self.assertTrue(area['not_supported'])

    def test_callers_cannot_mutate_shared_boundaries(self):
        first = functional_coverage()
        first['areas'][0]['not_supported'].clear()
        self.assertTrue(functional_coverage()['areas'][0]['not_supported'])

    def test_source_fingerprint_excludes_caches_and_changes_with_code(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'proteus_mcp').mkdir()
            for path in ('pyproject.toml','run_server.py','proteus_mcp/a.py','proteus_mcp/a.js'):
                (root/path).write_text('a',encoding='utf-8')
            before = source_fingerprint(root)
            (root/'proteus_mcp'/'cache.pyc').write_bytes(b'cache')
            self.assertEqual(before,source_fingerprint(root))
            (root/'proteus_mcp'/'a.js').write_text('b',encoding='utf-8')
            self.assertNotEqual(before['sha256'],source_fingerprint(root)['sha256'])

    def test_installed_package_without_checkout_metadata_can_be_fingerprinted(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'proteus_mcp').mkdir()
            (root/'proteus_mcp'/'a.py').write_text('a',encoding='utf-8')
            self.assertEqual({'proteus_mcp/a.py'},set(source_fingerprint(root)['files']))

    def test_failed_mismatched_or_unpinned_report_does_not_verify_current_code(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root/'_acceptance'/'20261112-000000'/'report.json'
            path.parent.mkdir(parents=True)
            source = {'sha256':'current'}
            valid = {'status':'passed','server_exit_code':0,'stderr':'','checks':{'one':True},
                     'source_before':source,'source_after':source}
            for updates in ({},{'source_before':{}},{'source_after':{'sha256':'old'}},
                            {'status':'failed'},{'stderr':'warning'},{'checks':{'one':False}},{'checks':{}}):
                path.write_text(json.dumps(valid | updates),encoding='utf-8')
                evidence = acceptance_evidence(root,source)
                self.assertEqual(not updates,evidence['status']=='verified_for_current_source')


if __name__ == '__main__':
    unittest.main()
