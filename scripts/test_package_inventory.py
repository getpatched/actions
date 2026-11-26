import json
import unittest
from unittest.mock import patch

import package_inventory as inventory


class InventoryTests(unittest.TestCase):
    def test_source_change_invalidates_reviewed_coverage(self):
        rows = [dict(target="target", sourceVersion="1", distroTestConfiguration="dh_auto_test")]
        self.assertEqual(inventory.reviewed_coverage(rows, "target", "1"), rows[0])
        self.assertIsNone(inventory.reviewed_coverage(rows, "target", "2"))
        self.assertIsNone(inventory.reviewed_coverage(rows, "new", "1"))

    def test_pagination(self):
        with patch.object(inventory, 'api', side_effect=[[{}] * 100, [{'name': 'last'}]]) as api:
            self.assertEqual(len(inventory.pages('orgs/getpatched/repos')), 101)
            self.assertIn('page=2', api.call_args.args[0])

    def test_workflow_yaml_ignores_comments_and_reads_all_jobs(self):
        sha = 'a' * 40
        workflow = f'''# uses: getpatched/actions/.github/workflows/package-ci.yml@main
jobs:
  package:
    uses: >-
      getpatched/actions/.github/workflows/package-ci.yml@{sha}
    with:
      tooling_ref: {sha}
  drift:
    uses: getpatched/actions/.github/workflows/package-ci.yml@main
'''
        findings = inventory.workflow_findings(workflow, sha)
        self.assertEqual(len(findings), 2)
        self.assertTrue(all('drift:' in finding for finding in findings))

    def test_discovery_failure_is_reported(self):
        with patch.object(inventory, 'api', side_effect=RuntimeError('denied')):
            report = inventory.inventory([])
        self.assertIn('Discovery failed', report['findings'][0])

    def test_frozen_ref_new_repo_and_unknown_native_coverage(self):
        import base64
        sha = 'a' * 40
        paths = []
        def api(path):
            paths.append(path)
            if path.endswith('commits/main'):
                return {'sha': sha}
            if path.startswith('orgs/'):
                return [dict(name='packaging-new', archived=False, size=1, default_branch='main')]
            if '/branches?' in path:
                return [dict(name='main', commit={'sha': sha}), dict(name='topic', commit={'sha': sha})]
            contents = {'package-manifest.json': json.dumps({'targets':[{'id':'target','file':'targets/test.json'}]}),
                        'targets/test.json': '{"source":{"version":"1"}}'}
            file = path.split('/contents/')[1].split('?')[0]
            return {'content':base64.b64encode(contents.get(file, '').encode()).decode()}
        with patch.object(inventory, 'api', side_effect=api):
            report = inventory.inventory([])
        self.assertEqual(len(report['branches']), 1)
        self.assertEqual(len(report['ignoredBranches']), 1)
        self.assertTrue(any('native coverage unknown' in f for f in report['findings']))
        self.assertTrue(all(f'ref={sha}' in p for p in paths if '/contents/' in p))


if __name__ == '__main__':
    unittest.main()
