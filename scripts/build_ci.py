"""Build and smoke-test a validated package recipe in disposable containers."""

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid


def run(*command):
    subprocess.run(command, check=True)


def run_recipe(*command):
    token = uuid.uuid4().hex
    print(f"::stop-commands::{token}", flush=True)
    try:
        run(*command)
    finally:
        print(f"::{token}::", flush=True)


def export_file(container, remote, destination, limit):
    with subprocess.Popen(("docker", "cp", f"{container}:{remote}", "-"),
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
        try:
            with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
                item = archive.next()
                if item is None or not item.isfile() or item.size > limit or item.name != Path(remote).name:
                    raise ValueError(f"Invalid exported file for {remote}")
                with archive.extractfile(item) as data, destination.open("xb") as output:
                    shutil.copyfileobj(data, output)
                if archive.next() is not None:
                    raise ValueError(f"Unexpected entries in export for {remote}")
            if process.wait() != 0:
                raise ValueError(f"Could not export {remote}")
        finally:
            if process.poll() is None:
                process.kill()


def docker_cleanup(*command):
    subprocess.run(("docker", *command), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def mount(source, destination):
    return ["--mount", f"type=bind,src={source},dst={destination},readonly"]


def stage(name):
    print(f"::notice title=Package CI stage::{name}", flush=True)


def test_dependencies(recipe, target, filename, recipe_path):
    relative = f"ci/{target}/{filename}"
    path = recipe / relative
    if not path.exists() and not path.is_symlink():
        return []
    dependencies = recipe_path(recipe, relative).read_text().split()
    if any(not re.fullmatch(r"[a-z0-9][a-z0-9+.-]+", item) for item in dependencies):
        raise ValueError(f"{filename} must list Debian package names")
    return dependencies


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("platform", "recipe", "policy", "reports"):
        parser.add_argument(f"--{flag}", type=Path, required=True)
    args = parser.parse_args()
    platform = args.platform.resolve()
    recipe = args.recipe.resolve()
    reports = args.reports.resolve()
    reports.mkdir(parents=True, exist_ok=True)
    policy = json.loads(args.policy.read_text())
    sys.path.insert(0, str(platform / "scripts"))
    from build_environment import offline_resolver_config, runner_ca_bundle
    from package_recipe import download_source_assets, recipe_path
    from platform_policy import apt_source_list_b64, target_profile

    target = policy["targetId"]
    profile = target_profile(json.loads((platform / "packages.json").read_text()), target)
    platform_arg = profile["build"]["containerPlatform"]
    binaries = policy["binaryPackages"]
    ca_args, _, _ = runner_ca_bundle()
    script_dir = Path(__file__).resolve().parent
    image = f"pkg-ci-{uuid.uuid4().hex[:12]}"
    job = image + "-build"
    install = image + "-install"
    for hook in ("upstream.sh", "smoke.sh"):
        relative = f"ci/{target}/{hook}"
        if (recipe / relative).exists() or (recipe / relative).is_symlink():
            recipe_path(recipe, relative)
    dependencies = test_dependencies(recipe, target, "dependencies.txt", recipe_path)
    smoke_dependencies = test_dependencies(recipe, target, "smoke-dependencies.txt", recipe_path)

    with tempfile.TemporaryDirectory(prefix="package-ci-") as folder:
        temp = Path(folder)
        try:
            stage("Fetch and verify pinned source")
            download_source_assets(policy, temp / "source")
            stage("Prepare the pinned build environment")
            run("docker", "build", "--platform", platform_arg, "-t", image,
                "-f", str(platform / "build/packaging.Containerfile"), *ca_args,
                "--build-arg", f"BASE_IMAGE={profile['baseImage']}",
                "--build-arg", f"APT_SOURCES_B64={apt_source_list_b64(profile)}",
                "--build-arg", f"SOURCE_PACKAGE={policy['source']}",
                "--build-arg", f"EXPECTED_VERSION={policy['baseVersion']}",
                "--build-arg", f"BINARY_PACKAGES={' '.join(binaries)}",
                "--build-arg", "BUILDER_PACKAGES=",
                str(temp / "source"))
            if dependencies:
                shutil.copyfile(script_dir / "test-deps.Containerfile", temp / "Dockerfile")
                run("docker", "build", "--platform", platform_arg, "-t", image,
                    "--build-arg", f"BUILDER_IMAGE={image}",
                    "--build-arg", f"TEST_DEPENDENCIES={' '.join(dependencies)}", str(temp))
            stage("Apply patches, build and run tests")
            isolated = ["--network", "none", "--security-opt", "no-new-privileges:true",
                        "--pids-limit", "512", "--cap-drop", "NET_RAW"]
            try:
                run_recipe("docker", "run", "--name", job, "--platform", platform_arg,
                    *isolated, *offline_resolver_config(temp),
                    *mount(recipe, "/recipe"), *mount(args.policy.resolve(), "/policy.json"),
                    *mount(temp / "source", "/originals"), *mount(script_dir / "build.sh", "/build.sh"),
                    *mount(platform / "verification/materialize-package.py", "/materialize-package.py"),
                    image, "bash", "/build.sh", target)
            finally:
                try:
                    export_file(job, "/reports/stage", reports / "stage", 128)
                except (ValueError, tarfile.TarError):
                    pass
            selected = temp / "install"
            selected.mkdir()
            for binary in binaries:
                version = policy["version"].split(":")[-1]
                for arch in (profile["architecture"], "all"):
                    name = f"{binary}_{version}_{arch}.deb"
                    try:
                        export_file(job, f"/reports/debs/{name}", selected / name, 256 * 1024 * 1024)
                    except tarfile.ReadError:
                        continue
                if len(list(selected.glob(f"{binary}_*.deb"))) != 1:
                    raise ValueError(f"Expected exactly one output for {binary}")
            stage("Install candidate packages in a clean target container")
            shutil.copyfile(script_dir / "install.Containerfile", temp / "Dockerfile")
            run("docker", "build", "--platform", platform_arg, "-t", install, *ca_args,
                "--build-arg", f"BASE_IMAGE={profile['baseImage']}",
                "--build-arg", f"APT_SOURCES_B64={apt_source_list_b64(profile, include_sources=False)}",
                "--build-arg", f"SMOKE_DEPENDENCIES={' '.join(smoke_dependencies)}",
                "-f", str(temp / "Dockerfile"), str(selected))
            run_recipe("docker", "run", "--rm", "--platform", platform_arg, *isolated,
                *mount(selected, "/candidate"), *mount(recipe, "/recipe"),
                *mount(script_dir / "smoke.sh", "/smoke.sh"), install,
                "bash", "/smoke.sh", target, policy["version"],
                " ".join(smoke_dependencies), *binaries)
            if not (recipe / f"ci/{target}/smoke.sh").is_file():
                print(f"::warning::No ordinary-use smoke hook configured for {target}")
        finally:
            docker_cleanup("rm", "-f", job)
            docker_cleanup("image", "rm", install, image)


if __name__ == "__main__":
    main()
