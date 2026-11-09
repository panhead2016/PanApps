#!/usr/bin/env python3
"""Keep apps.json in sync with the latest GitHub releases of watched repos.

Stdlib only, so it runs anywhere (GitHub Actions, a PC, Termux).

Reads the watch list from sync.json. For each watched repo it fetches the
latest release; if that version is not already the top entry in apps.json,
it downloads the .ipa, reads the bundle ID / version / minimum iOS from the
IPA's own Info.plist, and prepends a new versions[] entry pointing at the
upstream release asset. Nothing is copied into this repo - apps.json just
tracks the upstream links.

Run: python3 scripts/sync_apps.py
"""

import io
import json
import os
import plistlib
import re
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPS_JSON = os.path.join(ROOT, "apps.json")
SYNC_JSON = os.path.join(ROOT, "sync.json")
API = "https://api.github.com"
USER_AGENT = "repo-sync/1.0"


def gh_get_json(url):
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
        },
    )
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def download(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req) as resp:
        return resp.read()


def clean_notes(body, limit=600):
    if not body:
        return ""
    text = body.replace("\r\n", "\n")
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)  # markdown headings
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(" .,") + "..."
    return text


def read_ipa_info(url):
    data = download(url)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        plist_name = next(
            n
            for n in zf.namelist()
            if re.match(r"Payload/[^/]+\.app/Info\.plist$", n)
        )
        return plistlib.loads(zf.read(plist_name))


def find_app(source, watch_entry):
    apps = source.get("apps", [])
    bundle_id = watch_entry.get("bundleIdentifier")
    if bundle_id:
        for app in apps:
            if app.get("bundleIdentifier") == bundle_id:
                return app
    name = watch_entry.get("name")
    if name:
        for app in apps:
            if app.get("name") == name:
                return app
    return None


def fix_icon_url(app):
    """Point iconURL at icons/<app>.png in this repo, when running in Actions."""
    gh_repo = os.environ.get("GITHUB_REPOSITORY")
    if not gh_repo:
        return False
    branch = os.environ.get("GITHUB_REF_NAME") or "main"
    slug = re.sub(r"[^a-z0-9]+", "-", app.get("name", "").lower()).strip("-")
    icon_rel = f"icons/{slug}.png"
    if not os.path.exists(os.path.join(ROOT, icon_rel)):
        return False
    url = f"https://raw.githubusercontent.com/{gh_repo}/{branch}/{icon_rel}"
    if app.get("iconURL") != url:
        app["iconURL"] = url
        print(f"  icon: set to {url}")
        return True
    return False


def sync_one(source, watch_entry):
    repo = watch_entry["repo"]
    print(f"{repo}:")
    release = gh_get_json(f"{API}/repos/{repo}/releases/latest")
    tag = release["tag_name"]
    tag_version = tag[1:] if tag[:1] in ("v", "V") else tag

    assets = [
        a for a in release.get("assets", []) if a["name"].lower().endswith(".ipa")
    ]
    if not assets:
        print(f"  no .ipa asset in release {tag}; skipped")
        return False, None
    asset = max(assets, key=lambda a: a["size"])

    app = find_app(source, watch_entry)
    changed = False
    update = None
    current = None
    if app and app.get("versions"):
        current = app["versions"][0].get("version")

    if current == tag_version:
        print(f"  already at {tag_version}")
    else:
        info = read_ipa_info(asset["browser_download_url"])
        version = info.get("CFBundleShortVersionString") or tag_version
        if app is None:
            app = {
                "name": info.get("CFBundleDisplayName")
                or info.get("CFBundleName")
                or watch_entry.get("name")
                or repo.split("/")[-1],
                "bundleIdentifier": info.get("CFBundleIdentifier"),
                "developerName": repo.split("/")[0],
                "versions": [],
            }
            source.setdefault("apps", []).append(app)
            print(f"  new app entry: {app['name']} ({app['bundleIdentifier']})")
        entry = {
            "version": version,
            "date": release.get("published_at"),
            "size": asset["size"],
            "downloadURL": asset["browser_download_url"],
        }
        notes = clean_notes(release.get("body"))
        if notes:
            entry["localizedDescription"] = notes
        min_os = info.get("MinimumOSVersion")
        if min_os:
            entry["minOSVersion"] = min_os
        app.setdefault("versions", []).insert(0, entry)
        changed = True
        update = {
            "name": app["name"],
            "from": current,
            "to": version,
            "url": asset["browser_download_url"],
        }
        print(f"  updated: {current or 'none'} -> {version}")

    if app is not None and fix_icon_url(app):
        changed = True
    return changed, update


def main():
    with open(SYNC_JSON, encoding="utf-8") as fh:
        watch = json.load(fh)["watch"]
    with open(APPS_JSON, encoding="utf-8") as fh:
        source = json.load(fh)

    report_path = os.path.join(ROOT, "update-report.md")
    if os.path.exists(report_path):
        os.remove(report_path)

    changed = False
    updates = []
    for watch_entry in watch:
        one_changed, update = sync_one(source, watch_entry)
        changed = changed or one_changed
        if update:
            updates.append(update)

    if changed:
        with open(APPS_JSON, "w", encoding="utf-8") as fh:
            json.dump(source, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        print("apps.json updated")
    else:
        print("apps.json unchanged")

    if updates:
        lines = ["A new version was added to your FlareStore repo:", ""]
        for update in updates:
            if update["from"]:
                lines.append(
                    f"- **{update['name']}** {update['to']} is ready "
                    f"(was {update['from']})"
                )
            else:
                lines.append(f"- **{update['name']}** {update['to']} was added")
            lines.append(f"  Download: {update['url']}")
        lines += ["", "Open FlareStore and tap Update to install it."]
        with open(report_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        print("update-report.md written")


if __name__ == "__main__":
    main()
