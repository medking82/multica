#!/usr/bin/env python3
"""Discover the latest published upstream Multica release without mutating state."""
from __future__ import annotations
import argparse, json, re, sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler

API="https://api.github.com"; REPO="multica-ai/multica"; SHA40=re.compile(r"^[0-9a-f]{40}$")
class DiscoveryError(RuntimeError): pass

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise DiscoveryError("redirect refused")

def fetch(path, opener=None):
    if not path.startswith(API+"/"): raise DiscoveryError("non GitHub API URL refused")
    req=Request(path, headers={"Accept":"application/vnd.github+json","User-Agent":"ga401-upstream-discovery/1"})
    try:
        with (opener or build_opener(NoRedirect)).open(req, timeout=20) as r:
            if r.geturl().split("/",3)[:3] != API.split("/",3)[:3]: raise DiscoveryError("unexpected response host")
            return json.load(r)
    except (HTTPError, URLError, TimeoutError, ValueError) as e:
        raise DiscoveryError("GitHub API request failed") from e

def discover(current_sha, fetcher=fetch):
    if not SHA40.fullmatch(current_sha): raise DiscoveryError("current SHA must be 40 lowercase hex characters")
    rel=fetcher(f"{API}/repos/{REPO}/releases/latest")
    if not isinstance(rel,dict): raise DiscoveryError("release response is not an object")
    tag=rel.get("tag_name")
    if rel.get("draft") is not False or rel.get("prerelease") is not False or not rel.get("published_at") or not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+",tag or ""):
        raise DiscoveryError("latest release is not a published stable semver tag")
    ref=fetcher(f"{API}/repos/{REPO}/git/ref/tags/{tag}")
    if not isinstance(ref,dict) or not isinstance(ref.get("object"),dict): raise DiscoveryError("tag ref response is malformed")
    obj=ref.get("object",{}) if isinstance(ref,dict) else {}
    initial_ref=(obj.get("sha"),obj.get("type"))
    seen=set()
    for _ in range(5):
        sha,typ=obj.get("sha"),obj.get("type")
        if not SHA40.fullmatch(sha or ""): raise DiscoveryError("invalid Git object SHA")
        if typ=="commit": break
        if typ!="tag" or sha in seen: raise DiscoveryError("tag did not resolve to a commit")
        seen.add(sha); annotation=fetcher(f"{API}/repos/{REPO}/git/tags/{sha}")
        if not isinstance(annotation,dict) or not isinstance(annotation.get('object'),dict): raise DiscoveryError('malformed annotated tag')
        obj=annotation['object']
    else: raise DiscoveryError("annotated tag peel depth exceeded")
    upstream=obj["sha"]
    moved=fetcher(f"{API}/repos/{REPO}/git/ref/tags/{tag}")
    if not isinstance(moved,dict) or not isinstance(moved.get("object"),dict) or (moved["object"].get("sha"),moved["object"].get("type")) != initial_ref:
        raise DiscoveryError("tag ref moved during discovery")
    return {"current_sha":current_sha,"upstream_sha":upstream,"tag":tag,"status":"unchanged" if upstream==current_sha else "update_available"}

def main(argv=None):
    p=argparse.ArgumentParser(); p.add_argument("--current-upstream",required=True); p.add_argument("--output",required=True); a=p.parse_args(argv)
    try: result=discover(a.current_upstream)
    except DiscoveryError as e: print(f"FAILED: {e}",file=sys.stderr); return 1
    with open(a.output,"x",encoding="utf-8") as f: json.dump(result,f,sort_keys=True); f.write("\n")
    print(json.dumps(result,sort_keys=True)); return 0
if __name__=="__main__": raise SystemExit(main())
