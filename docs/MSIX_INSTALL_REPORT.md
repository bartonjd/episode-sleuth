# EpisodeSleuth - MSIX Install Report

This report captures the results of side-loading the self-signed MSIX package
on a real Windows 10/11 machine. The MSIX must be built, signed and installed
on Windows: the CI Windows runner produces and signs the package, and a human
performs the install and captures the screenshots below.

> Why not automated end-to-end here: `makeappx.exe`, `signtool.exe` and
> `Add-AppxPackage` are Windows-only. The build/sign half now runs on GitHub's
> Windows runner (see `.github/workflows/ci.yml`); the install half must be run
> on a Windows PC. This template is filled in during that install.

---

## Environment

| Field | Value |
| --- | --- |
| Windows edition / version | _e.g. Windows 11 Pro 23H2 (fill in)_ |
| Machine | _e.g. Desktop, 16 GB RAM (fill in)_ |
| Release tested | _e.g. v1.0b (fill in)_ |
| MSIX file | `EpisodeSleuth_1.0.0.0.msix` |
| Certificate | `episodesleuth_selfsign.cer` |
| Tester | Josh Barton |
| Date | _(fill in)_ |

---

## Steps performed

### 1. Download the assets from the GitHub Release

From the release page, download both files attached by CI:

- `EpisodeSleuth_1.0.0.0.msix`
- `episodesleuth_selfsign.cer`

### 2. Trust the self-signing certificate (one time, elevated PowerShell)

A self-signed package will not install until its certificate is trusted.
Run an **Administrator** PowerShell in the download folder:

```powershell
Import-Certificate -FilePath .\episodesleuth_selfsign.cer `
  -CertStoreLocation Cert:\LocalMachine\TrustedPeople
```

Expected: the cert is added to `Local Machine\Trusted People`.

**[SCREENSHOT 1]** - PowerShell showing the `Import-Certificate` output
(thumbprint + subject `CN=EpisodeSleuth`).

### 3. Install the package

Double-click `EpisodeSleuth_1.0.0.0.msix` to open the App Installer, or run:

```powershell
Add-AppxPackage -Path .\EpisodeSleuth_1.0.0.0.msix
```

Expected: App Installer shows the EpisodeSleuth name, publisher
`CN=EpisodeSleuth` and an **Install** button; the install completes without
error.

**[SCREENSHOT 2]** - The App Installer dialog (name + publisher + Install
button), or the PowerShell prompt returning with no error.

### 4. Verify the app is registered

```powershell
Get-AppxPackage *EpisodeSleuth*
```

Expected: one entry, `Name : Abacus.EpisodeSleuth`, `Version : 1.0.0.0`.

**[SCREENSHOT 3]** - `Get-AppxPackage` output for EpisodeSleuth.

### 5. Launch and smoke-test

Launch EpisodeSleuth from the Start menu. Confirm:

- A single main window opens (no duplicate/second window).
- The Build Library and Settings pages scroll correctly.
- Building a small library shows elapsed time + per-file status.

**[SCREENSHOT 4]** - The running app main window.

---

## Result

| Check | Pass / Fail | Notes |
| --- | --- | --- |
| Certificate imported | | |
| Package installed | | |
| Package registered (`Get-AppxPackage`) | | |
| App launches (single window) | | |
| Library build + status works | | |

**Overall:** _PASS / FAIL_

**Observations / issues:** _(fill in)_
