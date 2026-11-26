"""Read-only GitHub inventory. Remote recipe contents are parsed, never executed."""
import base64
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request

import yaml


def api(path):
    headers = {'Accept': 'application/vnd.github+json'}
    if os.environ.get('GH_TOKEN'):
        headers['Authorization'] = 'Bearer ' + os.environ['GH_TOKEN']
    request = urllib.request.Request('https://api.github.com/' + path, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def pages(path):
    result = []
    for page in range(1, 101):
        batch = api(path + ('&' if '?' in path else '?') + f'per_page=100&page={page}')
        if not isinstance(batch, list):
            raise ValueError('Expected paginated list')
        result.extend(batch)
        if len(batch) < 100:
            return result
    raise ValueError('Pagination exceeded bound')


def workflow_findings(text, latest):
    document = yaml.safe_load(text) or {}
    jobs = document.get('jobs', {})
    findings, found = [], False
    for name, job in jobs.items():
        uses = job.get('uses', '')
        prefix = 'getpatched/actions/.github/workflows/package-ci.yml@'
        if not uses.startswith(prefix):
            continue
        found = True
        for label, pin in [('workflow', uses[len(prefix):]),
                           ('tooling', job.get('with', {}).get('tooling_ref', ''))]:
            if not re.fullmatch('[0-9a-f]{40}', str(pin)):
                findings.append(f'{name}: mutable or missing {label} pin {pin!r}')
            elif pin != latest:
                findings.append(f'{name}: outdated {label} pin {pin}; current shared main {latest}')
    if not found:
        findings.append('Missing shared package CI caller')
    return findings


def reviewed_coverage(rows, target, version):
    return next((r for r in rows if r["target"] == target and r["sourceVersion"] == version), None)


def missing_expected(baseline, repositories, branches):
    """Reconcile reviewed inventory against observed identities, not just additions."""
    observed_repos = set(repositories)
    observed_branches = {(row['repository'], row['branch']) for row in branches}
    observed_targets = {(row['repository'], row['branch'], target['id'])
                        for row in branches for target in row['targets']}
    findings = []
    for repo in sorted({row['repository'] for row in baseline} - observed_repos):
        findings.append(f'{repo}: reviewed repository missing from discovery')
    expected_branches = {(row['repository'], row['branch']) for row in baseline}
    for repo, branch in sorted(expected_branches - observed_branches):
        if repo in observed_repos:
            findings.append(f'{repo}@{branch}: reviewed branch missing or not inspectable')
    expected_targets = {(row['repository'], row['branch'], row['target']) for row in baseline}
    for repo, branch, target in sorted(expected_targets - observed_targets):
        if (repo, branch) in observed_branches:
            findings.append(f'{repo}@{branch}/{target}: reviewed target missing or not inspectable')
    return findings


def coverage_findings(evidence):
    findings = []
    if not evidence.get('observedExecution'):
        findings.append('no retained execution evidence in reviewed baseline')
    status = evidence.get('coverageStatus', 'unknown')
    if status != 'reviewed-selection' or evidence.get('additionalGap'):
        findings.append(f'reviewed coverage {status}: {evidence.get("additionalGap") or evidence["scopeAndGaps"]}')
    return findings


def inventory(baseline):
    report = dict(schemaVersion=1, branches=[], ignoredBranches=[], findings=[])
    def issue(message):
        report['findings'].append(message)
    try:
        latest = api('repos/getpatched/actions/commits/main')['sha']
        report['sharedMain'] = latest
        repos = pages('orgs/getpatched/repos')
    except Exception as error:
        issue(f'Discovery failed: {error}')
        return report
    known = {row['repository'] for row in baseline}
    for repo in repos:
        name = repo['name']
        if not name.startswith('packaging-'):
            continue
        if name not in known:
            issue(f'{name}: new repository requires review')
        if repo['archived'] or repo['size'] == 0:
            issue(f'{name}: archived or empty repository; not inspected')
            continue
        try:
            branches = pages(f'repos/getpatched/{name}/branches')
        except Exception as error:
            issue(f'{name}: branch discovery failed: {error}')
            continue
        for branch in branches:
            b, sha = branch['name'], branch['commit']['sha']
            reviewed = [r for r in baseline if r['repository'] == name and r['branch'] == b]
            if b != repo['default_branch'] and not b.startswith('getpatched/') and not reviewed:
                report['ignoredBranches'].append(dict(repository=name, branch=b))
                continue
            row = dict(repository=name, branch=b, commit=sha, targets=[])
            report['branches'].append(row)
            context = f'{name}@{b}'
            if not reviewed:
                issue(f'{context}: branch requires coverage review')
            def read(path):
                encoded = urllib.parse.quote(path, safe='/')
                try:
                    data = api(f'repos/getpatched/{name}/contents/{encoded}?ref={sha}')
                except urllib.error.HTTPError as error:
                    if error.code == 404:
                        return None
                    raise
                return base64.b64decode(data['content'], validate=False).decode()
            try:
                manifest = json.loads(read('package-manifest.json') or '{}')
                if not manifest.get('targets'):
                    raise ValueError('Missing or empty manifest targets')
                for finding in workflow_findings(read('.github/workflows/package-ci.yml') or '', latest):
                    issue(f'{context}: {finding}')
                for target in manifest['targets']:
                    tid = target['id']
                    spec = json.loads(read(target['file']) or '{}')
                    version = spec.get('source', {}).get('version')
                    evidence = reviewed_coverage(reviewed, tid, version)
                    extra = any(read(f'ci/{tid}/{file}') is not None for file in ['upstream-tests.json', 'upstream.sh'])
                    coverage = dict(id=tid, sourceVersion=version, additionalSuiteConfigured=extra,
                                    reviewedCoverage=evidence)
                    row['targets'].append(coverage)
                    if evidence is None:
                        issue(f'{context}/{tid}: native coverage unknown; source/target requires review')
                    else:
                        # Configuration evidence is never represented as current execution evidence.
                        if evidence.get('additionalSuiteExpected') and not extra:
                            issue(f'{context}/{tid}: reviewed additional suite configuration missing')
                        for finding in coverage_findings(evidence):
                            issue(f'{context}/{tid}: {finding}')
            except Exception as error:
                issue(f'{context}: inspection failed: {error}')
    report['findings'].extend(missing_expected(baseline, [r['name'] for r in repos], report['branches']))
    return report


def main():
    try:
        baseline = json.loads(Path('inventory-baseline.json').read_text())['targets']
        report = inventory(baseline)
    except Exception as error:
        report = dict(schemaVersion=1, branches=[], findings=[f'Inventory failed: {error}'])
    Path('package-inventory.json').write_text(json.dumps(report, indent=2) + '\n')
    summary = f'## Package inventory\n\nInspected {len(report["branches"])} branches; {len(report["findings"])} findings. See package-inventory.json artifact for commits and reviewed coverage.\n'
    summary += '\n'.join('- ' + finding.replace('\n', ' ') for finding in report['findings']) + '\n'
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as output:
            output.write(summary)
    for finding in report['findings']:
        print('::error::' + finding.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A'))
    return int(bool(report['findings']))


if __name__ == '__main__':
    raise SystemExit(main())
