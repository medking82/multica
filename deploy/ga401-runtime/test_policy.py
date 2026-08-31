import copy
import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("policy", Path(__file__).with_name("verify-runtime.py"))
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


def valid_config():
    return {
        "name": policy.PROJECT,
        "services": {"runtime": {
            "image": policy.IMAGE, "user": "1000:1000", "init": True, "read_only": True,
            "cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true", "seccomp=./seccomp-profile.json"],
            "cpus": 6, "mem_limit": 8 * 1024**3, "memswap_limit": 8 * 1024**3,
            "pids_limit": 512, "shm_size": 1024**3,
            "environment": {"MULTICA_DAEMON_DEVICE_NAME": "GA401-Isolated",
                            "MULTICA_DAEMON_MAX_CONCURRENT_TASKS": "2"},
            "volumes": [{"type": "volume", "source": "runtime-home", "target": "/home/agent"}],
            "networks": {"default": None},
        }},
        "volumes": {"runtime-home": {"name": policy.VOLUME}},
        "networks": {"default": {"name": policy.NETWORK}},
    }


def valid_inspect():
    return [{
        "Config": {"Image": policy.IMAGE, "User": "1000:1000", "Env": [],
                   "Labels": {"io.hankee.owner": policy.PROJECT}},
        "HostConfig": {"ReadonlyRootfs": True, "CapDrop": ["ALL"], "Init": True,
                       "NetworkMode": policy.NETWORK, "IpcMode": "private",
                       "Memory": 8 * 1024**3, "MemorySwap": 8 * 1024**3,
                       "NanoCpus": 6 * 10**9, "PidsLimit": 512,
                       "SecurityOpt": ["no-new-privileges:true", "seccomp=" +
                                       Path(__file__).with_name("seccomp-profile.json").read_text()],
                       "Binds": [policy.VOLUME + ":/home/agent:rw"]},
        "Mounts": [{"Type": "volume", "Name": policy.VOLUME, "Destination": "/home/agent"}],
        "NetworkSettings": {"Networks": {policy.NETWORK: {}}},
    }]


class IsolationPolicyTests(unittest.TestCase):
    def test_accepts_engine_named_volume(self):
        policy.check_inspect(valid_inspect())

    def test_rejects_engine_host_mounts_and_privileges(self):
        for key, value in (("Binds", ["/:/home/agent:rw"]), ("Privileged", True),
                           ("CapAdd", ["SYS_ADMIN"]), ("PidMode", "host"),
                           ("SecurityOpt", ["no-new-privileges:true", 'seccomp={"defaultAction":"SCMP_ACT_ALLOW"}']),
                           ("PortBindings", {"9222/tcp": [{"HostPort": "9222"}]})):
            with self.subTest(key=key), self.assertRaises(ValueError):
                items = valid_inspect()
                items[0]["HostConfig"][key] = value
                policy.check_inspect(items)

    def test_accepts_bounded_config(self):
        policy.check_config(valid_config())

    def test_accepts_compose_byte_strings(self):
        config = valid_config()
        for key in ("mem_limit", "memswap_limit", "shm_size"):
            config["services"]["runtime"][key] = str(config["services"]["runtime"][key])
        policy.check_config(config)

    def test_rejects_boundary_expansions(self):
        changes = [
            ("user", "0:0"), ("read_only", False), ("cap_add", ["SYS_ADMIN"]),
            ("privileged", True), ("ports", ["9222:9222"]), ("network_mode", "host"),
            ("pid", "host"), ("ipc", "host"), ("group_add", ["docker"]),
            ("security_opt", ["seccomp=unconfined"]), ("mem_limit", 0),
            ("volumes", [{"type": "bind", "source": "/", "target": "/host"}]),
            ("environment", {"OPENAI_API_KEY": "not-a-real-credential"}),
            ("entrypoint", ["/bin/bash"]), ("extra_hosts", ["host:host-gateway"]),
        ]
        for key, value in changes:
            with self.subTest(key=key):
                config = copy.deepcopy(valid_config())
                config["services"]["runtime"][key] = value
                with self.assertRaises(ValueError):
                    policy.check_config(config)

    def test_rejects_existing_external_volumes(self):
        config = valid_config()
        config["volumes"]["runtime-home"]["external"] = True
        with self.assertRaises(ValueError):
            policy.check_config(config)

    def test_rejects_production_network(self):
        config = valid_config()
        config["networks"]["default"]["name"] = "multica_default"
        with self.assertRaises(ValueError):
            policy.check_config(config)

    def test_browser_sandbox_and_seccomp_stay_enabled(self):
        directory = Path(__file__).parent
        browser = json.loads((directory / "browser-config.json").read_text())
        self.assertIs(browser["browser"]["launchOptions"]["chromiumSandbox"], True)
        seccomp = json.loads((directory / "seccomp-profile.json").read_text())
        self.assertEqual(seccomp["defaultAction"], "SCMP_ACT_ERRNO")
        unconditional = {name for rule in seccomp["syscalls"]
                         if rule["action"] == "SCMP_ACT_ALLOW"
                         and not rule.get("includes") and not rule.get("excludes")
                         for name in rule["names"]}
        self.assertTrue({"clone", "setns", "unshare", "chroot"} <= unconditional)
        self.assertTrue({"mount", "reboot", "init_module", "open_by_handle_at"}.isdisjoint(unconditional))


if __name__ == "__main__":
    unittest.main()
