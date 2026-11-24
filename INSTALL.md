# Installing Hearthside

## Requirements

The product title is **Hearthside: Just You and Me**. Its plugin ID is `hearthside-just-you-and-me`; Nori remains the companion's name.

- Hermes Agent and Hermes Desktop already installed, with a compatible model available in Hermes.
- Python 3.10 or newer to run the local installer.
- The complete downloaded bundle: keep `install.py`, `uninstall.py`, `full/` and `lite/` together.

Use Hermes Agent/Desktop `0.22.5+1741.ge5f2b15` or a later compatible build. The local installer supports macOS, Linux and Windows.

Hearthside installs its own components, not Hermes. Hermes manages the Python dependencies declared in each plugin manifest. A first CLI launch for a new data root can take several minutes while Hermes prepares its dependency environment.

## Optional compiled LaTeX images

MathJax previews are included and do not require TinyTeX. The separate generation of saved LaTeX images requires an existing TinyTeX runtime under the requesting Hermes profile's `runtimes/hearthside-tex/TinyTeX` directory and macOS sandbox support. That runtime is not bundled or installed automatically; without it, this optional compiler remains unavailable.

## Complete setup

Open a terminal in the downloaded repository or extracted bundle and run (on Windows, use `py -3` or `python` in place of `python3`):

```sh
python3 install.py --edition full
```

The installer shows its scope and asks for confirmation. It installs:

- One unified Hearthside package containing its backend, Desktop interface, approved cover and required local images. Hermes pairs its Desktop half with the backend as a single Plugins entry.
- The profile-scoped backend for the default profile and Nori.
- A dedicated `nori` profile with Nori's identity, native **Nori** name/avatar and 50 skills.
- A registered Hearthside project containing `Nori.md`, `Room.md`, `Outfits.md`, `Journal.md` and `AGENTS.md`. `SOUL.md` remains at the profile root.

Hermes keeps unified Desktop halves opt-in. On first setup, open Hearthside's card under **Plugins** and turn on its **Desktop** switch if it is off; existing explicit choices are preserved. Then select Nori in Hermes Desktop's Bots roster. If Hermes asks for a model, choose it through Hermes' normal settings. The installer does not read or clone credentials, choose a provider, switch the global active profile or start a conversation.

The approved cover is available offline in both editions. Installed-card artwork appears in Desktop builds that support the optional plugin `image` metadata; other builds keep their standard card without losing Hearthside functionality.

Backend enablement is saved through Hermes' configuration CLI without restarting or reloading a running gateway. If an existing gateway has not loaded the new backend, apply it through your normal user-controlled restart. Closing only the Desktop window may leave its shared gateway running.

### Existing profiles

An unrelated profile is never adopted or overwritten. If `nori` already belongs to another setup, choose an unused technical profile name:

```sh
python3 install.py --edition full --profile nori-hearthside
```

The native Bot's initial display name remains **Nori**. Later name/avatar changes, edited identity/story files, empty documents and a deliberate working directory are preserved on reruns. Updates also retain the installed technical profile name unless you explicitly choose another with `--profile`.

Full saves its project-owner binding in Hearthside's own Desktop data folder. Its document views use that owner even when the conversation is open in another Bot; they do not switch the global profile or create a second project. Lite uses its packaged story without polling for a dedicated project.

## Lite

For the standalone Desktop integration without creating Nori's dedicated profile/project:

```sh
python3 install.py --edition lite
```

This command installs Lite locally. It does not register a catalog-managed installation. Lite uses the same room, focus garden, and study tools, but does not create the dedicated Bot profile or project.

## Completing Lite with Full

A change from **locally installed Lite** requires explicit approval:

```sh
python3 install.py --edition full --replace-lite
```

The installer backs up the old code and uses the same plugin/storage namespace instead of registering a second Desktop pane. The shared room/garden state remains in that namespace. Study collections remain owned by their original Hermes profile; Full does not silently copy another agent's collections or other private data into Nori. Use a deliberate export/import if you want a collection in another profile.

If Lite was installed through Hermes' catalog/Git channel, first remove its code through that original channel after verifying data retention. The local installer refuses to take over a Hermes-tracked installation or rewrite its provenance records. After switching, use this local installer for Hearthside updates rather than asking the catalog channel to replace Full. The native profile-distribution scaffold alone is not the complete Hearthside installer; running `hermes profile update` directly is not this bundle's update workflow.

## Updating or repairing

### Earlier development identifier

The former development ID was `hearthside-nori`. This build uses a different storage namespace. When the installer detects known code, storage folders or ownership records for that older ID, it stops before installing. It does not silently reset progress, rename personal files or import another namespace. An existing development installation needs a separate, explicit backed-up migration; this installer does not perform that migration automatically. Fresh installations and Full/Lite updates under the new ID use the normal commands below.

Extract a new complete bundle and rerun the same command. Code is backed up; existing and empty profile/story files are not replaced. A previously disabled backend remains disabled on a normal rerun. To enable it deliberately:

```sh
python3 install.py --edition full --enable
```

An interrupted install preserves the profile and user data rather than deleting them. Read the reported failure before retrying; do not use `--force` on an unrelated profile.

## Removing the integration

```sh
python3 uninstall.py
```

Removal deletes only owned Hearthside code, unchanged packaged image files and its unchanged installer-owned project binding. It keeps Nori's profile, identity, avatar, skills, project, journal, study collections and saved progress. A user-controlled gateway restart may be needed to unload already-running code.

To restore the code that existed before this edition was installed, when its backup is still available:

```sh
python3 uninstall.py --restore-previous
```

## Explicit paths and unattended use

`--hermes-home PATH` selects the root Hermes home, not a named profile directory. `--desktop-data-dir PATH` overrides Hermes Desktop's user-data location if yours is nonstandard. These options do not install another Hermes instance.

`--yes` confirms the printed installation/removal scope without a prompt; use it only after reviewing the bundle and intended paths. No administrative privileges are required.

## License

Use and redistribution are governed by [LICENSE](LICENSE). Third-party components retain their licenses and notices in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and the component folders.
