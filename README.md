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
