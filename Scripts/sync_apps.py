#!/usr/bin/env python3
"""Keep apps.json in sync with the latest GitHub releases of watched repos.

Stdlib only, so it runs anywhere (GitHub Actions, a PC, Termux).

Reads the watch lists in sync.json (iOS) and sync-tvos.json (Apple TV).
For each watched repo it fetches the
latest release; if that version is not already the top entry in apps.json,
it downloads the .ipa, reads the bundle ID / version / minimum iOS from the
IPA's own Info.plist, and prepends a new versions[] entry pointing at the
upstream release asset. A watch entry may instead carry a "directIpa"
link for a project that publishes its IPA on its own site; the link is
followed to the current versioned file and tracked the same way.
Nothing is copied into this repo - apps.json just tracks the upstream links.

Run: python3 scripts/sync_apps.py
"""

import io
import json
import os
import plistlib
import re
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Each pair is (source file, watch list file). apps.json is the iOS shelf,
# tvos-apps.json is the Apple TV shelf; both live in this one repo.
PAIRS = [
    ("apps.json", "sync.json"),
    ("tvos-apps.json", "sync-tvos.json"),
]
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


def plist_from_bytes(data):
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        plist_name = next(
            n
            for n in zf.namelist()
            if re.match(r"Payload/[^/]+\.app/Info\.plist$", n)
        )
        return plistlib.loads(zf.read(plist_name))


def read_ipa_info(url):
    return plist_from_bytes(download(url))


def fetch_direct(url):
    """Download a file, following redirects.

    Returns (final_url, headers, data). The final URL matters: a stable
    "latest" link usually resolves to a versioned file, and the
    versioned URL is what belongs in the source.
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req) as resp:
        return resp.geturl(), resp.headers, resp.read()


def http_date_to_iso(value):
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def pick_asset(assets, watch_entry):
    """Choose which .ipa in a release belongs to this watch entry.

    A release can hold several IPAs (for example one iOS build and one
    tvOS build). A watch entry may pin an exact asset name ("asset") or
    a substring ("assetContains"); otherwise the largest .ipa wins.
    """
    exact = watch_entry.get("asset")
    if exact:
        for asset in assets:
            if asset["name"] == exact:
                return asset
        return None
    contains = watch_entry.get("assetContains")
    if contains:
        matches = [
            a for a in assets if contains.lower() in a["name"].lower()
        ]
        return max(matches, key=lambda a: a["size"]) if matches else None
    return max(assets, key=lambda a: a["size"])


def latest_release(repo, watch_entry):
    """Fetch the release a watch entry should track.

    Normally that is GitHub's "latest" release, which ignores
    prereleases. A watch entry with "includePrereleases" instead takes
    the newest release of any kind - for projects whose current builds
    are published only as prereleases.
    """
    if watch_entry.get("includePrereleases"):
        releases = gh_get_json(f"{API}/repos/{repo}/releases?per_page=1")
        return releases[0] if releases else None
    return gh_get_json(f"{API}/repos/{repo}/releases/latest")


def sync_one(source, watch_entry):
    repo = watch_entry["repo"]
    print(f"{repo}:")
    release = latest_release(repo, watch_entry)
    if release is None:
        print("  no releases found; skipped")
        return False, None
    tag = release["tag_name"]
    tag_version = tag[1:] if tag[:1] in ("v", "V") else tag

    assets = [
        a for a in release.get("assets", []) if a["name"].lower().endswith(".ipa")
    ]
    if not assets:
        print(f"  no .ipa asset in release {tag}; skipped")
        return False, None
    asset = pick_asset(assets, watch_entry)
    if asset is None:
        wanted = watch_entry.get("asset") or watch_entry.get("assetContains")
        print(f"  no asset matching {wanted!r} in release {tag}; skipped")
        return False, None

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
        if current is not None and version == current:
            # The tag's spelling differs from the IPA's own version (for
            # example a "-beta" suffix in the tag). Already current - do
            # not prepend a duplicate entry.
            print(f"  already at {version} (tag says {tag_version})")
            if app is not None and fix_icon_url(app):
                changed = True
            return changed, None
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


def sync_direct(source, watch_entry):
    """Track an IPA published on a project's own site.

    The watch entry's "directIpa" is a stable link that redirects to the
    current versioned file. The IPA is downloaded, its version is read
    from its own Info.plist, and a new entry - pointing at the versioned
    URL the link resolved to - is prepended when the version changes.
    """
    url = watch_entry["directIpa"]
    print(f"{watch_entry.get('name') or url} (direct link):")
    final_url, headers, data = fetch_direct(url)
    info = plist_from_bytes(data)
    version = info.get("CFBundleShortVersionString")
    if not version:
        print("  IPA has no version string; skipped")
        return False, None

    app = find_app(source, watch_entry)
    current = None
    if app and app.get("versions"):
        current = app["versions"][0].get("version")

    if current == version:
        print(f"  already at {version}")
        if app is not None and fix_icon_url(app):
            return True, None
        return False, None

    if app is None:
        app = {
            "name": info.get("CFBundleDisplayName")
            or info.get("CFBundleName")
            or watch_entry.get("name")
            or "App",
            "bundleIdentifier": info.get("CFBundleIdentifier"),
            "developerName": watch_entry.get("developerName")
            or urllib.parse.urlparse(url).netloc,
            "versions": [],
        }
        source.setdefault("apps", []).append(app)
        print(f"  new app entry: {app['name']} ({app['bundleIdentifier']})")
    entry = {
        "version": version,
        "date": http_date_to_iso(headers.get("Last-Modified"))
        or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "size": len(data),
        "downloadURL": final_url,
    }
    min_os = info.get("MinimumOSVersion")
    if min_os:
        entry["minOSVersion"] = min_os
    app.setdefault("versions", []).insert(0, entry)
    update = {
        "name": app["name"],
        "from": current,
        "to": version,
        "url": final_url,
    }
    print(f"  updated: {current or 'none'} -> {version}")
    return True, update


def sync_pair(source_name, watch_name):
    """Sync one source file against its watch list. Returns update dicts."""
    source_path = os.path.join(ROOT, source_name)
    watch_path = os.path.join(ROOT, watch_name)
    if not os.path.exists(source_path) or not os.path.exists(watch_path):
        print(f"{source_name}: no watch list yet, skipped")
        return []
    with open(watch_path, encoding="utf-8") as fh:
        watch = json.load(fh)["watch"]
    with open(source_path, encoding="utf-8") as fh:
        source = json.load(fh)

    changed = False
    updates = []
    for watch_entry in watch:
        if watch_entry.get("directIpa"):
            one_changed, update = sync_direct(source, watch_entry)
        else:
            one_changed, update = sync_one(source, watch_entry)
        changed = changed or one_changed
        if update:
            updates.append(update)

    if changed:
        with open(source_path, "w", encoding="utf-8") as fh:
            json.dump(source, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        print(f"{source_name} updated")
    else:
        print(f"{source_name} unchanged")
    return updates


def main():
    report_path = os.path.join(ROOT, "update-report.md")
    if os.path.exists(report_path):
        os.remove(report_path)

    updates = []
    for source_name, watch_name in PAIRS:
        updates += sync_pair(source_name, watch_name)

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
