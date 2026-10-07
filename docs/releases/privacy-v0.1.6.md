# v0.1.6 release-file privacy audit

The current release files pass the build-machine privacy audit. The checked set includes the two download ZIPs, Setup and portable executables, current tracked source, listing copy, application/navigation icons, and the three homepage PNGs and their ZIP.

## Checks

- Match local profile, username, machine name, workspace, Python installation, environment paths, local IP addresses and hardware-address strings without writing their values into the report. Only the public GitHub project/support URL is exempt.
- Inspect archive member names, contents, comments and extended metadata. The distribution ZIPs use a neutral timestamp and basic file attributes.
- Decompress PyInstaller entries, the PYZ modules, the standard-library ZIP and nested Python code objects. Reject absolute source filenames.
- Read the current Inno 7 installer without executing it. Verify header/runtime CRCs and every embedded file's SHA-256, then match the program, icon and README to the intended release inputs.
- Check PNG metadata chunks and possible credential material. The homepage illustrations contain no local desktop captures.
- Fail when an archive cannot be inspected; do not count unsupported formats as a clean result.

The scan found project-specific compiler-location literals, a non-generic test username, and branding strings matching a local machine identifier. Those were removed or replaced with generic project text before rebuilding. Public repository URLs are intentionally retained.

## Repeat

With the dependencies in `.build_tools`, run:

```powershell
python tools/package_release.py
python tools/audit_release_privacy.py
```

The report is `release/PRIVACY-AUDIT-v0.1.6.json` and lists relative filenames and SHA-256 digests. It does not record the matched local values.

This audit covers the current v0.1.6 files and current source tree. It does not erase prior Git commits, previous release versions, third-party caches or copies already downloaded. It is evidence from the stated checks, not a proof of universal absence of information in arbitrary binary formats. The Inno reader deliberately supports only this project's current unencrypted Inno 7/LZMA2 format; its format reference is the [upstream source](https://github.com/jrsoftware/issrc/tree/main/Projects/Src).
