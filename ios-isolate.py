"""Verify production-host isolation on CI; restore only after simulator shutdown."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import subprocess


def run(command, **kwargs):
    return subprocess.run(command, check=True, timeout=45, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["enable", "restore"])
    parser.add_argument("--app-root", type=Path, default=Path.cwd())
    parser.add_argument("--evidence", type=Path, default=Path("build/release-evidence"))
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise RuntimeError("Host changes are restricted to the ephemeral CI job")
    backup = Path(os.environ["RUNNER_TEMP"]) / "mobile-current-ios-hosts.backup"
    if args.action == "restore":
        simulator = os.environ.get("TUTAM_QA_SIMULATOR")
        if simulator:
            listing = json.loads(subprocess.check_output(
                ["xcrun", "simctl", "list", "devices", "-j"], text=True))
            states = [device["state"] for devices in listing["devices"].values()
                      for device in devices if device["udid"] == simulator]
            if states != ["Shutdown"]:
                raise RuntimeError("Refusing to restore networking while CI simulator is active")
        if backup.exists():
            run(["sudo", "cp", str(backup), "/etc/hosts"])
            run(["sudo", "dscacheutil", "-flushcache"])
            if Path("/etc/hosts").read_bytes() != backup.read_bytes():
                raise RuntimeError("Restored hosts file differs from baseline")
        return
    proxy = subprocess.check_output(["scutil", "--proxy"], text=True)
    if re.search(r"(?:HTTPEnable|HTTPSEnable|SOCKSEnable|ProxyAutoConfigEnable|ProxyAutoDiscoveryEnable)\s*:\s*1\b", proxy):
        raise RuntimeError("Enabled system proxy prevents verified host isolation")
    if any(value for key, value in os.environ.items()
           if key.lower() in ("http_proxy", "https_proxy", "all_proxy")):
        raise RuntimeError("Enabled proxy environment prevents verified host isolation")
    if backup.exists():
        raise RuntimeError("Refusing to replace a previous hosts backup")
    hosts = {"quangtu.vn", "www.quangtu.vn", "media.quangtu.vn",
             "quangtu-web.quangtu-ansitoanthu.workers.dev", "quangtu.vercel.app"}
    root = args.app_root.resolve()
    for relative in ["src/lib/browser/native-backend-origin.ts",
                     "src/lib/browser/native-app-links.ts", "capacitor.config.ts"]:
        text = (root / relative).read_text(encoding="utf-8")
        hosts.update(re.findall(r"https://([a-z0-9.-]+)", text))
        hosts.update(re.findall(r"['\"]([a-z0-9.-]+\.quangtu\.vn)['\"]", text))
    if not all(re.fullmatch(r"[a-z0-9.-]+", host) for host in hosts):
        raise RuntimeError("Invalid production hostname")
    original = Path("/etc/hosts").read_bytes()
    backup.write_bytes(original)
    lines = []
    for line in original.decode().splitlines():
        address, separator, comment = line.partition("#")
        parts = address.split()
        if len(parts) > 1 and any(host.lower() in hosts for host in parts[1:]):
            aliases = [host for host in parts[1:] if host.lower() not in hosts]
            if aliases:
                lines.append(parts[0] + "\t" + " ".join(aliases)
                             + ((" #" + comment) if separator else ""))
            elif separator:
                lines.append("#" + comment)
        else:
            lines.append(line)
    lines.append("# Tu Tap CI production-host isolation; not full offline mode")
    for host in sorted(hosts):
        lines.extend(["127.0.0.1\t" + host, "::1\t" + host])
    run(["sudo", "tee", "/etc/hosts"], input="\n".join(lines) + "\n",
        text=True, stdout=subprocess.DEVNULL)
    run(["sudo", "dscacheutil", "-flushcache"])
    proof = {}
    for host in sorted(hosts):
        proof[host] = {}
        for family, name in [(socket.AF_INET, "ipv4"), (socket.AF_INET6, "ipv6")]:
            addresses = sorted({entry[4][0] for entry in socket.getaddrinfo(
                host, 443, family, socket.SOCK_STREAM)})
            if not addresses or not all(ipaddress.ip_address(address).is_loopback
                                        for address in addresses):
                raise RuntimeError("Production hostname is not isolated: " + host)
            proof[host][name] = addresses
    args.evidence.mkdir(parents=True, exist_ok=True)
    (args.evidence / "host-isolation.json").write_text(json.dumps(
        {"scope": "production hosts only; not full offline", "addresses": proof},
        indent=2), encoding="utf-8")
    with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as output:
        output.write("TUTAM_QA_HOSTS_ISOLATED=1\n")


if __name__ == "__main__":
    main()
