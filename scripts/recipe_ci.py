"""Resolve and validate package PR build inputs without release policy."""

import argparse
import json
import os
from pathlib import Path
import re
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", type=Path, required=True)
    parser.add_argument("--recipe", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--target")
    args = parser.parse_args()
    sys.path.insert(0, str(args.platform.resolve() / "scripts"))
    from debian_versions import debian_version_compare
    from package_recipe import (
        load_json, patch_applies_to_version, recipe_path, validate_hash_file,
        validate_source_asset,
    )
    from platform_policy import target_profile

    root = args.recipe.resolve()
    manifest = load_json(recipe_path(root, "package-manifest.json"))
    config = load_json(args.platform / "packages.json")
    package = manifest["package"]["source"]
    if not re.fullmatch(r"[a-z0-9][a-z0-9+.-]+", package):
        raise ValueError("Invalid Debian source package name")
    repository = f"getpatched/packaging-{package}"
    if args.repository != repository or manifest["project"]["repository"] != repository:
        raise ValueError("Recipe does not match its calling repository")
    schema = manifest["schemaVersion"]
    if schema not in (1, 2, 3):
        raise ValueError("Unsupported recipe schema")
    refs = manifest["targets"]
    if not 1 <= len(refs) <= 32 or len({item["id"] for item in refs}) != len(refs):
        raise ValueError("Expected 1 to 32 unique recipe target IDs")
    metadata = load_json(recipe_path(root, manifest["patchMetadata"]))
    patches = {item["id"]: item for item in metadata["patches"]}
    if len(patches) != len(metadata["patches"]):
        raise ValueError("Duplicate patch metadata ID")
    matrix = []
    policies = {}
    for ref in refs:
        target_id = ref["id"]
        try:
            profile = target_profile(config, target_id)
            runner = profile["build"]["runner"]
            if runner not in {"ubuntu-24.04", "ubuntu-24.04-arm"}:
                raise ValueError("Target must use an approved GitHub-hosted runner")
            target = load_json(recipe_path(root, ref["file"])) if schema == 3 else ref
            if target["id"] != target_id or (schema == 3 and target["schemaVersion"] != 2):
                raise ValueError("Target file does not match the manifest entry")
            base = target["base"]
            fields = {"distribution": "distribution", "architecture": "architecture",
                      "codename": "codename", "release": "releaseVersion"}
            if any(base[key] != profile[value] for key, value in fields.items()):
                raise ValueError("Target identity does not match the trusted build profile")
            source = target["source"]
            version = source["version"]
            if not re.fullmatch(r"(?:[0-9]+:)?[0-9][A-Za-z0-9.+~:-]*", version):
                raise ValueError("Invalid Debian source version")
            serial = target["releaseSerial"]
            candidate = target["packageVersion"]
            if (source["format"] != "3.0 (quilt)" or version not in target["supportedSourceVersions"]
                    or type(serial) is not int or serial < 1
                    or candidate != f"{version}+getpatched{serial}"
                    or debian_version_compare(candidate, version) <= 0):
                raise ValueError("Invalid source pin or package version ordering")
            distro = base["distribution"]
            filename_version = version.split(":", 1)[-1]
            descriptor = validate_source_asset(source["descriptor"], package, filename_version,
                                               distribution=distro, descriptor=True)
            assets = [validate_source_asset(item, package, version, distribution=distro)
                      for item in source["files"]]
            names = [item["name"] for item in [descriptor, *assets]]
            if (not assets or len(set(names)) != len(names)
                    or any(not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._:+~-]+", name) for name in names)
                    or any(name.endswith(".dsc") for name in names[1:])):
                raise ValueError("Source assets must have unique Debian archive filenames")
            security = target["patches"]
            compatibility = target.get("buildPatches", [])
            selected = security + compatibility
            if len(set(selected)) != len(selected):
                raise ValueError("Duplicate selected patch")
            for patch_id in selected:
                patch = patches[patch_id]
                filename = patch["file"]
                if Path(filename).name != filename or not filename.endswith(".patch"):
                    raise ValueError(f"Invalid filename for patch {patch_id}")
                validate_hash_file(root, f"patches/{filename}", patch["sha256"])
                if sum(patch_applies_to_version(entry, target_id, version)
                       for entry in patch["appliesTo"]) != 1:
                    raise ValueError(f"Patch {patch_id} does not match the target and source pin")
            binaries = target["binaryPackages"]
            if (not binaries or len(set(binaries)) != len(binaries)
                    or any(not re.fullmatch(r"[a-z0-9][a-z0-9+.-]+", item) for item in binaries)):
                raise ValueError("Expected unique Debian binary package names")
            matrix.append({"target": target_id, "runner": runner})
            policies[target_id] = {
                "source": package, "targetId": target_id, "baseVersion": version,
                "version": candidate, "releaseSerial": serial, "patches": security,
                "buildPatches": compatibility, "binaryPackages": binaries,
                "descriptor": descriptor, "sourceFiles": assets,
            }
        except (KeyError, TypeError, ValueError, RuntimeError) as error:
            raise ValueError(f"{repository} / {target_id}: {error}") from error

    if args.target:
        print(json.dumps(policies[args.target]))
    elif os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            print("matrix=" + json.dumps({"include": matrix}, separators=(",", ":")), file=output)
    else:
        print(json.dumps({"include": matrix}))


if __name__ == "__main__":
    try:
        main()
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        sys.exit(f"Recipe validation failed: {str(error)!r}")
