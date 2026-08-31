"""Check rendered Compose/inspect JSON without printing credentials or mutating Docker."""
import hashlib
import json
from pathlib import Path
import sys

PROJECT = "multica-ga401-runtime"
IMAGE = PROJECT + ":20260831-3"
IMAGE_ID = "sha256:fdb46cfe7d838a3c7c246106ca2fa330fcb9578b915b58adb264f2d222562f94"
SECCOMP_SHA256 = "c2b7657251810e440a02bdaa1d6080942ce286c7dfb8b36834805c2fdc7f5805"
VOLUME = PROJECT + "_runtime-home"
NETWORK = PROJECT + "_default"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def pinned_seccomp():
    profile = json.loads(Path(__file__).with_name("seccomp-profile.json").read_text())
    digest = hashlib.sha256(json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    require(digest == SECCOMP_SHA256, "Pinned seccomp content changed")
    return profile


def check_config(config):
    pinned_seccomp()
    require(config.get("name") == PROJECT, "Wrong Compose project")
    require(set(config.get("services", {})) == {"runtime"}, "Unexpected services")
    service = config["services"]["runtime"]
    require(service.get("image") == IMAGE, "Unexpected image")
    require(service.get("pull_policy") == "never" and not service.get("build"),
            "Candidate deployment must not pull or build another image")
    require(service.get("user") == "1000:1000", "Non-root identity required")
    require(service.get("read_only") is True, "Read-only root required")
    require(service.get("init") is True, "Init required")
    require(service.get("cap_drop") == ["ALL"], "Drop all capabilities")
    for key in ("cap_add", "privileged", "ports", "devices", "group_add", "secrets",
                "device_cgroup_rules", "volumes_from", "network_mode", "pid", "ipc",
                "entrypoint", "command", "env_file", "extra_hosts"):
        require(not service.get(key), "Unexpected service option: " + key)
    security = service.get("security_opt", [])
    require("no-new-privileges:true" in security, "No-new-privileges required")
    require(len(security) == 2 and any(
        item.startswith("seccomp=") and item.endswith("seccomp-profile.json")
        for item in security
    ), "Pinned browser seccomp profile required")
    require(float(service.get("cpus", 0)) == 6, "CPU bound changed")
    require(int(service.get("mem_limit", 0)) == 8 * 1024**3, "RAM bound changed")
    require(int(service.get("memswap_limit", 0)) == 8 * 1024**3, "Swap bound changed")
    require(service.get("pids_limit") == 512, "PID bound changed")
    require(int(service.get("shm_size", 0)) == 1024**3, "Private shared memory bound changed")
    require(service.get("environment") == {
        "MULTICA_DAEMON_DEVICE_NAME": "GA401-Isolated",
        "MULTICA_DAEMON_MAX_CONCURRENT_TASKS": "2",
    }, "Unexpected environment; credentials do not belong in Compose")
    mounts = service.get("volumes", [])
    require(len(mounts) == 1, "Exactly one private home volume required")
    require(mounts[0].get("type") == "volume"
            and mounts[0].get("source") == "runtime-home"
            and mounts[0].get("target") == "/home/agent", "Unsafe volume mapping")
    require(set(config.get("volumes", {})) == {"runtime-home"}, "Unexpected volumes")
    volume = config["volumes"]["runtime-home"]
    require(volume.get("name") == VOLUME and not volume.get("external")
            and not volume.get("driver_opts"), "Home volume must be project-owned")
    require(set(config.get("networks", {})) == {"default"}, "Unexpected networks")
    network = config["networks"]["default"]
    require(network.get("name") == NETWORK and not network.get("external")
            and network.get("driver", "bridge") == "bridge", "Private bridge required")
    require(set(service.get("networks", {})) == {"default"}, "Unexpected attachments")


def check_inspect(items):
    require(len(items) == 1, "Exactly one runtime container required")
    item = items[0]
    require(item.get("Image") == IMAGE_ID, "Live image digest differs from verified candidate")
    config, host = item["Config"], item["HostConfig"]
    require(config.get("Image") == IMAGE, "Unexpected live image")
    require(config.get("User") == "1000:1000", "Unexpected live identity")
    require(config.get("Labels", {}).get("io.hankee.owner") == PROJECT, "Wrong owner")
    require(host.get("ReadonlyRootfs") is True, "Live root is writable")
    require(host.get("CapDrop") == ["ALL"] and not host.get("CapAdd"), "Live capabilities")
    require(not host.get("Privileged") and host.get("Init") is True, "Live privilege/init")
    require(host.get("NetworkMode") == NETWORK, "Wrong live network")
    for key in ("PidMode", "PortBindings", "Devices", "GroupAdd", "VolumesFrom"):
        require(not host.get(key), "Unexpected live option: " + key)
    # Compose may serialize a named volume in the legacy Binds API. Its actual
    # type/identity is independently checked in Mounts below; host paths are refused.
    require(not host.get("Binds") or host["Binds"] == [VOLUME + ":/home/agent:rw"],
            "Unexpected legacy mount specification")
    require(host.get("IpcMode") == "private", "Host/shared IPC forbidden")
    require(host.get("Memory") == 8 * 1024**3 and host.get("MemorySwap") == 8 * 1024**3,
            "Live memory limits changed")
    require(host.get("NanoCpus") == 6 * 10**9 and host.get("PidsLimit") == 512,
            "Live CPU/PID limits changed")
    security = host.get("SecurityOpt", [])
    require(any(value in security for value in ("no-new-privileges", "no-new-privileges:true")),
            "Live no-new-privileges missing")
    seccomp_values = [value.removeprefix("seccomp=") for value in security
                      if value.startswith("seccomp=")]
    require(len(seccomp_values) == 1, "Exactly one live seccomp policy required")
    expected_seccomp = pinned_seccomp()
    require(json.loads(seccomp_values[0]) == expected_seccomp, "Live seccomp differs from reviewed profile")
    mounts = item.get("Mounts", [])
    require(len(mounts) == 1 and mounts[0].get("Type") == "volume"
            and mounts[0].get("Name") == VOLUME
            and mounts[0].get("Destination") == "/home/agent", "Unexpected live mounts")
    require(set(item.get("NetworkSettings", {}).get("Networks", {})) == {NETWORK},
            "Unexpected live network attachments")
    forbidden = {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
                 "GOOGLE_API_KEY", "GH_TOKEN", "GITHUB_TOKEN"}
    require(not forbidden.intersection(value.split("=", 1)[0] for value in config.get("Env", [])),
            "Unexpected API/host credential environment")


if __name__ == "__main__":
    try:
        require(len(sys.argv) == 2 and sys.argv[1] in ("config", "inspect"),
                "Usage: verify-runtime.py config|inspect < rendered.json")
        payload = json.load(sys.stdin)
        {"config": check_config, "inspect": check_inspect}[sys.argv[1]](payload)
        print(sys.argv[1] + " isolation policy PASS")
    except (ValueError, KeyError, TypeError) as error:
        print("Isolation policy FAIL: " + str(error), file=sys.stderr)
        sys.exit(1)
