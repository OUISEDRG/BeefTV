"""Assess a pinned upstream candidate before selecting it for distribution."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess

from dreama_release import digest, load_verified_manifest, make_package, package_paths

PLUGIN_PATHS = (
    "plugin-packages/", "backend/internal/protocol/",
    "backend/internal/plugin", "docs/content/docs/plugins/",
    "backend/internal/handler/plugin", "agent-host/plugin-",
    "packages/plugin-sdk/", "web/src/lib/plugins/", "web/src/services/api/plugins",
)


def git(repository, *args, allowed=(0,)):
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        capture_output=True, check=False,
    )
    if result.returncode not in allowed:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"Git {args[0]} failed: {detail}")
    return result


def commit(repository, ref):
    return git(repository, "rev-parse", "--verify", "--end-of-options",
               f"{ref}^{{commit}}").stdout.decode("ascii").strip()


def tree(repository, revision):
    paths = {}
    for entry in git(repository, "ls-tree", "-r", "-z", revision).stdout.split(b"\0"):
        if entry:
            identity, name = entry.split(b"\t", 1)
            paths[name.decode("utf-8", errors="surrogateescape")] = identity
    return paths


def change(base, version):
    if base == version:
        return "unchanged"
    if base is None:
        return "added"
    if version is None:
        return "removed"
    return "modified"


def assess(repository, selected, candidate, candidate_pin, customization, production):
    upstream = commit(repository, candidate)
    if upstream != candidate_pin:
        raise ValueError("The candidate does not match the pinned commit")
    selected = commit(repository, selected)
    customization = commit(repository, customization)
    production = commit(repository, production)
    for revision in (upstream, customization):
        if git(repository, "merge-base", "--is-ancestor", selected, revision, allowed=(0, 1)).returncode:
            raise ValueError("The selected upstream is not an ancestor of both compared revisions")

    result = git(repository, "merge-tree", "--write-tree", "--name-only", "-z",
                 customization, upstream, allowed=(0, 1))
    # Git emits the result tree, conflicted paths, an empty field, then diagnostics.
    _, *fields = result.stdout.split(b"\0")
    conflicts = {field.decode("utf-8", errors="surrogateescape")
                 for field in fields[:fields.index(b"")]} if result.returncode else set()
    base_tree, upstream_tree, custom_tree = (
        tree(repository, revision) for revision in (selected, upstream, customization)
    )
    files = []
    for path in sorted(base_tree.keys() | upstream_tree.keys() | custom_tree.keys() | conflicts):
        base, official, custom = (snapshot.get(path) for snapshot in (base_tree, upstream_tree, custom_tree))
        if path in conflicts:
            decision = "conflict"
        elif base == official == custom:
            continue
        elif official == custom:
            decision = "retire-duplicate"
        elif base == official:
            decision = "retain"
        elif base == custom:
            decision = "adopt"
        else:
            decision = "review-overlap"
        files.append({
            "path": path, "upstreamChange": change(base, official),
            "customizationChange": change(base, custom), "decision": decision,
        })
    return {
        "revisions": {"upstream": upstream, "selectedUpstream": selected,
                      "customization": customization, "production": production},
        "candidateRef": candidate,
        "mergeBase": git(repository, "merge-base", customization, upstream).stdout.decode("ascii").strip(),
        "mergeClean": result.returncode == 0,
        "counts": dict(Counter(row["decision"] for row in files)),
        "pluginReview": [row["path"] for row in files if row["path"].startswith(PLUGIN_PATHS)],
        "files": files,
    }


def verify_distribution(root, manifest_pin):
    manifest = load_verified_manifest(root, manifest_pin)
    return {"verified": True, "manifestSha256": manifest_pin, "files": len(manifest["files"])}

def record_build(build, revision, version):
    return {
        "sourceCommit": revision, "version": version,
        "files": {name: digest(build / name) for name in sorted(package_paths(build))},
    }


def verify_build(build, receipt, revision, version):
    if receipt.get("sourceCommit") != revision or receipt.get("version") != version:
        raise ValueError("Build receipt does not match the package source revision and version")
    if receipt.get("files") != record_build(build, revision, version)["files"]:
        raise ValueError("Build payload does not match its receipt")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    compare = commands.add_parser("compare", help="compare selected, candidate and custom Git trees")
    compare.add_argument("--repository", required=True, type=Path)
    compare.add_argument("--selected", required=True)
    compare.add_argument("--candidate", required=True)
    compare.add_argument("--expect-candidate", required=True)
    compare.add_argument("--customization", required=True)
    compare.add_argument("--production", required=True)
    package = commands.add_parser("package", help="package the reviewed desktop and both Bridge sources")
    for name in ("repository", "bridge", "codex", "build", "output"):
        package.add_argument("--" + name, type=Path, required=True)
    package.add_argument("--tag", required=True)
    package.add_argument("--customization", required=True)
    package.add_argument("--build-receipt", type=Path, required=True)
    verify = commands.add_parser("verify", help="verify the manifest pin and exact package contents")
    verify.add_argument("--package", type=Path, required=True)
    verify.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args()
    try:
        if args.command == "compare":
            result = assess(args.repository, args.selected, args.candidate, args.expect_candidate,
                            args.customization, args.production)
        elif args.command == "package":
            receipt = json.loads(args.build_receipt.read_text("utf-8-sig"))
            revision = commit(args.repository, "HEAD")
            version = (args.repository / "VERSION").read_text("utf-8").strip()
            verify_build(args.build, receipt, revision, version)
            result = make_package(args.repository, args.bridge, args.codex, args.build,
                                  args.output, args.tag, args.customization, pin_codex=True,
                                  build_receipt=digest(args.build_receipt))
            manifest_pin = digest(args.output / "release.json")
            manifest = load_verified_manifest(args.output, manifest_pin)
            app_files = {item["path"]: item["sha256"] for item in manifest["files"] if item["area"] == "app"}
            if manifest["sourceCommit"] != revision or any(app_files.get(path) != expected
                                                          for path, expected in receipt["files"].items()):
                raise ValueError("The package does not match the verified desktop build")
            result.update(verified=True, manifestSha256=manifest_pin)
        else:
            result = verify_distribution(args.package, args.manifest_sha256)
    except (OSError, RuntimeError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
