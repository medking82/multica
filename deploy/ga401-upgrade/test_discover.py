import unittest
from discover import DiscoveryError, discover

SHA="a"*40; TAG="b"*40; COMMIT="c"*40
class Tests(unittest.TestCase):
    def test_same_sha_is_unchanged(self):
        def f(url):
            if "/releases/latest" in url: return {"tag_name":"v0.4.39","draft":False,"prerelease":False,"published_at":"2026-09-01T00:00:00Z"}
            return {"object":{"sha":SHA,"type":"commit"}}
        self.assertEqual(discover(SHA,f)["status"],"unchanged")
    def test_annotated_tag_is_peeled(self):
        def f(url):
            if "/releases/latest" in url: return {"tag_name":"v0.4.40","draft":False,"prerelease":False,"published_at":"2026-09-01T00:00:00Z"}
            if "/ref/tags/" in url: return {"object":{"sha":TAG,"type":"tag"}}
            return {"object":{"sha":COMMIT,"type":"commit"}}
        out=discover(SHA,f); self.assertEqual(out["upstream_sha"],COMMIT); self.assertEqual(out["status"],"update_available")
    def test_tag_cycle_is_rejected(self):
        def f(url):
            if "/releases/latest" in url: return {"tag_name":"v0.4.39","draft":False,"prerelease":False,"published_at":"2026-09-01T00:00:00Z"}
            return {"object":{"sha":TAG,"type":"tag"}}
        with self.assertRaises(DiscoveryError): discover(SHA,f)
    def test_tag_ref_move_is_rejected(self):
        calls=[]
        def f(url):
            calls.append(url)
            if "/releases/latest" in url: return {"tag_name":"v0.4.39","draft":False,"prerelease":False,"published_at":"2026-09-01T00:00:00Z"}
            if len([x for x in calls if "/ref/tags/" in x]) == 1: return {"object":{"sha":COMMIT,"type":"commit"}}
            return {"object":{"sha":"d"*40,"type":"commit"}}
        with self.assertRaisesRegex(DiscoveryError,"moved"): discover(SHA,f)
    def test_missing_release_flags_rejected(self):
        with self.assertRaises(DiscoveryError): discover(SHA,lambda _: {"tag_name":"v0.4.39"})
    def test_nonobject_release_rejected(self):
        with self.assertRaises(DiscoveryError): discover(SHA,lambda _: [])
    def test_unstable_release_rejected(self):
        with self.assertRaises(DiscoveryError): discover(SHA,lambda _: {"tag_name":"v0.4.40-rc1","draft":False,"prerelease":True})
    def test_invalid_current_sha_rejected(self):
        with self.assertRaises(DiscoveryError): discover("not-a-sha",lambda _: {})
if __name__=="__main__": unittest.main()
