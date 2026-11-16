"""Build and smoke-test a validated package recipe in disposable containers."""

import argparse
import base64
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


def record_container_report(container, work, reports, filename, limit):
    exported = work / f"{container}-{filename}"
    try:
        export_file(container, f"/reports/{filename}", exported, limit)
        shutil.copyfile(exported, reports / filename)
    except tarfile.ReadError:
        # Optional reports can be absent when tests did not run or could not finish.
        pass
    except (ValueError, tarfile.TarError):
        print(f"::warning title=Invalid package CI report::Discarded {filename}", flush=True)


def docker_cleanup(*command):
    subprocess.run(("docker", *command), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def mount(source, destination):
    return ["--mount", f"type=bind,src={source},dst={destination},readonly"]


def stage(reports, name):
    (reports / "stage").write_text(name + "\n")
    print(f"::notice title=Package CI stage::{name}", flush=True)


def pinned_apt_sources(source_list):
    # Timestamped Debian snapshots intentionally retain historical Release metadata.
    # Keep signature and checksum verification, including expiry for live archives.
    source_list = re.sub(
        r"^(deb(?:-src)? \[arch=[a-z0-9]+)(\] https://snapshot(?:-fastly)?\.debian\.org"
        r"/archive/debian(?:-security)?/[0-9]{8}T[0-9]{6}Z/ )",
        r"\1 check-valid-until=no\2", source_list, flags=re.MULTILINE,
    )
    return base64.b64encode(source_list.encode()).decode()


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
    from platform_policy import apt_source_list, target_profile

    target = policy["targetId"]
    profile = target_profile(json.loads((platform / "packages.json").read_text()), target)
    platform_arg = profile["build"]["containerPlatform"]
    binaries = policy["binaryPackages"]
    ca_args, _, _ = runner_ca_bundle()
    script_dir = Path(__file__).resolve().parent
    image = f"pkg-ci-{uuid.uuid4().hex[:12]}"
    job = image + "-build"
    install = image + "-install"
    signer = image + "-signer"
    for hook in ("upstream.sh", "smoke.sh", "autopkgtest.sh"):
        relative = f"ci/{target}/{hook}"
        if (recipe / relative).exists() or (recipe / relative).is_symlink():
            recipe_path(recipe, relative)
    dependencies = test_dependencies(recipe, target, "dependencies.txt", recipe_path)
    smoke_dependencies = test_dependencies(recipe, target, "smoke-dependencies.txt", recipe_path)
    distro_test = (recipe / f"ci/{target}/autopkgtest.sh").is_file()

    with tempfile.TemporaryDirectory(prefix="package-ci-") as folder:
        temp = Path(folder)
        try:
            stage(reports, "source download and checksum verification")
            download_source_assets(policy, temp / "source")
            stage(reports, "pinned build environment")
            descriptors = temp / "descriptors"
            descriptors.mkdir()
            for descriptor in (temp / "source").glob("*.dsc"):
                shutil.copyfile(descriptor, descriptors / descriptor.name)
            run("docker", "build", "--platform", platform_arg, "-t", image,
                "-f", str(script_dir / "build.Containerfile"), *ca_args,
                "--build-arg", f"BASE_IMAGE={profile['baseImage']}",
                "--build-arg", f"APT_SOURCES_B64={pinned_apt_sources(apt_source_list(profile, include_sources=False))}",
                "--build-arg", f"TEST_DEPENDENCIES={' '.join(dependencies)}",
                str(descriptors))
            stage(reports, "start build container")
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
                record_container_report(job, temp, reports, "stage", 128)
                record_container_report(job, temp, reports, "upstream-junit.xml", 2 * 1024 * 1024)
            stage(reports, "binary selection")
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
            stage(reports, "sign test APT repository")
            run_recipe("docker", "run", "--name", signer, "--platform", platform_arg, *isolated,
                *mount(selected, "/candidate"),
                *mount(script_dir / "sign-test-repository.sh", "/sign-repository.sh"),
                image, "bash", "/sign-repository.sh")
            for filename in ("Packages", "InRelease", "archive-keyring.gpg", "signed-apt.txt"):
                export_file(signer, f"/repository/{filename}", selected / filename, 2 * 1024 * 1024)
                (selected / filename).chmod(0o644)
            stage(reports, "clean install dependency preparation")
            shutil.copyfile(script_dir / "install.Containerfile", temp / "Dockerfile")
            run("docker", "build", "--platform", platform_arg, "-t", install, *ca_args,
                "--build-arg", f"BASE_IMAGE={profile['baseImage']}",
                "--build-arg", f"APT_SOURCES_B64={pinned_apt_sources(apt_source_list(profile, include_sources=False))}",
                "--build-arg", f"SMOKE_DEPENDENCIES={' '.join(smoke_dependencies)}",
                "-f", str(temp / "Dockerfile"), str(selected))
            stage(reports, "clean install")
            try:
                run_recipe("docker", "run", "--name", install, "--platform", platform_arg, *isolated,
                    *mount(selected, "/candidate"), *mount(recipe, "/recipe"),
                    *mount(script_dir / "smoke.sh", "/smoke.sh"),
                    *(mount(temp / "source", "/originals") if distro_test else []),
                    install,
                    "bash", "/smoke.sh", target, policy["version"],
                    " ".join(smoke_dependencies), *binaries)
            finally:
                record_container_report(install, temp, reports, "stage", 128)
                record_container_report(install, temp, reports, "autopkgtest-summary.txt", 64 * 1024)
            shutil.copyfile(selected / "signed-apt.txt", reports / "signed-apt.txt")
            if not (recipe / f"ci/{target}/smoke.sh").is_file():
                print(f"::warning::No ordinary-use smoke hook configured for {target}")
        finally:
            docker_cleanup("rm", "-f", job, install, signer)
            docker_cleanup("image", "rm", install, image)


if __name__ == "__main__":
    main()
