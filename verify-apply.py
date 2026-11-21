#!/usr/bin/env python3
"""Validate a frozen source transfer, apply it, and verify exact source bytes."""
import argparse
import base64
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess


def digest(data):
    return hashlib.sha256(data).hexdigest()


def fail(message):
    raise SystemExit(message)


def safe_target(root, name):
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts or "\\" in name:
        fail("Invalid manifest path")
    target = root.joinpath(*relative.parts)
    if not target.resolve().is_relative_to(root):
        fail("Manifest path escapes its checkout")
    return target


def git(app, *args, input=None):
    result = subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "core.safecrlf=false", *args],
                            cwd=app, input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        fail("Git transfer verification failed: " + result.stderr.decode("utf-8", errors="replace")[:2000])
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-repo", required=True)
    parser.add_argument("--transfer-dir", required=True)
    parser.add_argument("--manifest", default="manifest.json")
    parser.add_argument("--verify-only", action="store_true", help="Check already-applied source without mutation")
    args = parser.parse_args()
    app = Path(args.app_repo).resolve()
    transfer = Path(args.transfer_dir).resolve()
    manifest_bytes = safe_target(transfer, args.manifest).read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest["sensitiveAdditions"]["findingCount"] != 0:
        fail("Source transfer has unresolved sensitive additions")
    chunks = []
    for item in manifest["uploadChunks"]:
        data = safe_target(transfer, item["name"]).read_bytes()
        if len(data) != item["bytes"] or digest(data) != item["sha256"]:
            fail("Transfer chunk mismatch: " + item["name"])
        chunks.append(data)
    compressed = base64.b64decode(b"".join(chunks), validate=True)
    if len(compressed) != manifest["compressedBytes"] or digest(compressed) != manifest["compressedSha256"]:
        fail("Compressed transfer mismatch")
    patch = gzip.decompress(compressed)
    if len(patch) != manifest["patchBytes"] or digest(patch) != manifest["patchSha256"]:
        fail("Source patch mismatch")

    for item in manifest["files"]:
        safe_target(app, item["path"])
    if not args.verify_only:
        head = git(app, "rev-parse", "HEAD").decode("ascii").strip()
        if head != manifest["baselineCommit"]:
            fail("App checkout is not the frozen baseline commit")
        for item in manifest["files"]:
            target = safe_target(app, item["path"])
            if item["baselineBlobSha1"] is None:
                if target.exists():
                    fail("New source path already exists: " + item["path"])
            else:
                data = target.read_bytes()
                if digest(data) != item["baselineSha256"]:
                    fail("Baseline source mismatch: " + item["path"])
        git(app, "apply", "--whitespace=nowarn", "--check", "-", input=patch)
        git(app, "apply", "--whitespace=nowarn", "-", input=patch)

    for item in manifest["files"]:
        data = safe_target(app, item["path"]).read_bytes()
        if len(data) != item["bytes"] or digest(data) != item["sha256"]:
            fail("Current source mismatch: " + item["path"])
    result = {"status": "VERIFIED_CURRENT_SOURCE", "baselineCommit": manifest["baselineCommit"],
              "fileCount": len(manifest["files"]), "patchSha256": manifest["patchSha256"],
              "manifestSha256": digest(manifest_bytes), "mode": "verify-only" if args.verify_only else "apply-and-verify"}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
