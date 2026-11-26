# actions

[![advertisement](https://ads.servead.net/i/9ea207cf-c752-482f-aee1-b0f7eb681465.svg)](https://ads.servead.net/c/9ea207cf-c752-482f-aee1-b0f7eb681465)

Package CI builds the recipe's pinned distributor source with its selected patches
and distro build tests enabled, then installs the exact binaries in a clean target
environment. Package-specific hooks live in the recipe repository.

## Additional upstream suites

Configure `ci/<target>/upstream-tests.json` for bounded commands with automatic
JUnit reporting, or keep an existing `upstream.sh` hook. Configuring both fails.
Commands run from the patched source root, after the native build, inside the
same isolated container with no network or credentials. Add required distro
packages in `ci/<target>/dependencies.txt`.

```json
{
  "schemaVersion": 1,
  "suites": [
    {
      "name": "upstream unit tests",
      "command": ["make", "-C", "build/tests", "check"],
      "timeoutSeconds": 600,
      "env": {"LC_ALL": "C.UTF-8"}
    }
  ]
}
```

Every configured suite runs and must exit successfully. A timeout, missing
command, invalid configuration, or nonzero exit fails Package CI. JUnit records
one case per suite command, not one case per upstream assertion; suite commands
must enforce that their intended tests actually execute. Native harness output
stays in the job log. The workflow uploads `upstream-junit.xml` even on test
failure. Existing shell hooks can emit richer per-test JUnit at that same path.
An absent additional suite is reported as unconfigured, never as a test pass.
Suite commands run sequentially and must wait for their own child processes;
they must not start persistent background services.

## Pinned external test data

Use `ci/<target>/test-assets.json` when an upstream suite needs a corpus omitted
from the distro source. Each asset needs a unique plain `name`, HTTPS `url`,
exact `bytes`, and lowercase `sha256`; the document uses `schemaVersion: 1` and
an `assets` array. Downloads currently allow `download.gnome.org` and
`gitlab.freedesktop.org` and `www.w3.org`, including every redirect. The limit is eight assets
and 512 MiB combined per target. New origins require shared-tooling review.

The runner downloads and verifies each complete file before starting the build.
It never extracts archives on the host. Verified files are mounted read-only at
`/test-assets/<name>` in the existing offline container. Recipe suite commands
extract the required data into a writable build directory and must check that the expected tests
actually execute. A download or checksum failure fails CI with its own stage.
The uploaded `test-assets.json` report records the verified pins; it does not
claim that a suite ran or passed.

### Read-only package inventory

The weekly `Package inventory` workflow discovers all public `packaging-*`
repositories using paginated GitHub API reads. It inspects each default branch,
all `getpatched/*` branches, and any branch retained in the reviewed baseline.
Other topic branches are listed as ignored. Each inspected branch is frozen to
its discovered commit. Recipe code is never executed and no inherited secrets
or write token permissions are requested.

The JSON artifact and job summary flag new repositories/branches, inaccessible
metadata, missing callers, mutable/outdated workflow and tooling pins, and
coverage requiring review. Reconciliation flags reviewed repositories, branches,
or targets that disappear or cannot be inspected. A finding fails the inventory job, without changing
package build or release gates. Scheduled drift callers are inspected separately
and may intentionally use older pins; those findings require maintainer review.

`inventory-baseline.json` retains the rollout's 65 reviewed target observations,
source versions, distro configuration, gaps, and historical execution references.
Explicit `coverageStatus`, `nativeCoverage`, and `additionalGap` fields separate
reviewed selections from known exclusions. Findings do not infer coverage from
wording in prose descriptions.
A matching source version permits displaying historical evidence, never a claim
that today's commit passed. Missing additional upstream configuration does not
mean distro tests are absent. Source changes and unreviewed targets are unknown
until reviewed; update the baseline with recipe/source references and retained
run evidence after review. Run locally with `pip install -r requirements-inventory.txt`
and `GH_TOKEN=… python3 scripts/package_inventory.py` (token optional for public
reads, but unauthenticated rate limits are usually insufficient for the org).
