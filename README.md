# My FlareStore repo — setup guide

A "repo" (source) for FlareStore is one JSON file (`apps.json`, AltStore format)
hosted at a public URL. FlareStore reads it and shows your apps in a store UI.
The `.ipa` files themselves live somewhere with direct download links —
GitHub Releases is the easy free option.

## One-time setup (GitHub)

1. Create a free GitHub account if you don't have one, then create a new
   **public** repository (name it anything, e.g. `my-apps`).
2. Upload `apps.json` to the repo (and an `icons/` folder with a PNG per app
   if you want icons in the store list).
3. Get the raw URL of the JSON. It looks like:
   `https://raw.githubusercontent.com/YOUR-NAME/my-apps/main/apps.json`
   (That exact pattern is what other projects use for their sources.)

## Adding an app

1. On your PC, get the **.ipa file itself**. If a download came as a .zip,
   unzip it on the PC — this is the last time you ever unzip anything.
2. In your GitHub repo: **Releases → Create a new release**. Tag it
   (e.g. `delta-v2.1`), drag the .ipa in as an asset, publish.
3. Right-click the uploaded .ipa on the release page → Copy link. That is the
   `downloadURL` — a direct link to the file, which is what repos require.
4. Edit `apps.json` (edit button right on GitHub, or use FlareStore's visual
   Repository Creator at flarestore.app/repo-creator and re-upload):
   - `name`, `bundleIdentifier` (must match the IPA's bundle ID),
     `iconURL`, description.
   - Under `versions`, add an entry at the TOP for the new release:
     `version`, `date` (YYYY-MM-DD), `size` (file size in bytes), and the
     `downloadURL` you copied.
5. Commit. The repo is live the moment the JSON changes — no approval step.

## Add the repo to FlareStore (once)

- In FlareStore, open the Repositories/Sources area, tap add, and paste your
  raw `apps.json` URL. (Opening `flarestore://addRepo=YOUR-URL` in Safari
  also works.)
- Your apps now appear in FlareStore. Tap to sign and install, on the phone,
  no Files app, no unzipping.
- When you add a new version to the JSON later, FlareStore shows it as an
  update automatically.

## Auto-updating apps (Madeira is pre-wired)

This kit already tracks **Madeira** (github.com/willfaust/Madeira) so it
keeps itself current in your repo:

- `sync.json` lists the projects being watched (just Madeira for now).
- `scripts/sync_apps.py` checks each project's latest GitHub release. When
  the version changes, it downloads the IPA, reads the bundle ID, version,
  and minimum iOS from the IPA itself, and prepends the new version to
  `apps.json`. The download link always points at the project's own release
  file — the IPA is never copied into your repo.
- `.github/workflows/update-apps.yml` runs that script on GitHub's servers
  once a day and commits any change. It also fixes Madeira's icon link to
  point at your repo on its first run.

Setup notes:

- Upload ALL the kit files, keeping the folder layout — including
  `.github/workflows/update-apps.yml`. If the GitHub uploader skips the
  hidden `.github` folder, use **Add file → Create new file** and type that
  exact path, then paste the file's contents.
- Run it once by hand to prove it works: **Actions tab → Update apps →
  Run workflow**, then refresh your source in FlareStore.
- If a run fails with a 403 error: repo **Settings → Actions → General →
  Workflow permissions → "Read and write permissions"** → Save, then re-run.
- To watch another project later, add one entry to `sync.json`
  (`{"repo": "owner/project", "name": "App", "bundleIdentifier": "..."}`)
  — or ask Muse to add it.

## Rules that trip people up

- `downloadURL` must end at the raw .ipa. Google Drive / Dropbox page links
  do not work; GitHub Release asset links do.
- Keep each app's `bundleIdentifier` identical across versions or the update
  will install as a separate app.
- Icons: a direct URL to a PNG (your repo's `icons/` folder via the raw URL
  works).
- The JSON must stay valid — if FlareStore refuses the source, paste the file
  into any online JSON validator to find the typo.

## Alternative

Skip GitHub entirely: build the JSON once in FlareStore's Repository Creator
(flarestore.app/repo-creator) and host the exported file anywhere stable.
GitHub is still the best home for the .ipa files themselves because release
links are direct and permanent.
