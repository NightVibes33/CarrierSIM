# CarrierSIM

[![Build CarrierSIM iOS IPA](https://github.com/NightVibes33/CarrierSIM/actions/workflows/carriersim-ios.yml/badge.svg?branch=main)](https://github.com/NightVibes33/CarrierSIM/actions/workflows/carriersim-ios.yml)

CarrierSIM is now an **on-device iPhone app** for testing carrier-bundle switching with the iOS 27 AirLift path.

The app is built as an **unsigned IPA** for sideloading. It uses the same on-device AirLift/RSD approach used by AirCard and the AirLift work in Filza-27, instead of requiring the original desktop Python workflow for normal use.

**Bundle ID:** `com.nightvibes33.carriersim`

## What the iOS app does

CarrierSIM can:

- pair the iPhone with its own local Remote Pairing host;
- connect back to the device over an RSD/loopback tunnel;
- read `CarrierBundleInfoArray` from lockdown, including active SIM/eSIM IMSI values;
- show each SIM slot, MCC/MNC, IMSI, ICCID tail, and the currently selected carrier bundle;
- apply a signed system carrier bundle to a selected SIM by creating the same root-level 15-digit IMSI alias used by the desktop CarrierSIM project;
- restore one IMSI alias or remove all root-level 15-digit IMSI aliases;
- send a device restart request so CommCenter re-evaluates the carrier-bundle selection;
- expose live AirLift activity in the app.

The carrier catalog lives at:

```text
/var/mobile/Library/Carrier Bundles/iPhone
```

CarrierSIM does **not** modify the signed bundle inside:

```text
/System/Library/Carrier Bundles/iPhone
```

Instead, it points the SIM's full IMSI to an existing Apple-signed system bundle.

## AirLift integration

The IPA does not ship a Python interpreter.

The GitHub Actions build pins the AirCard on-device runtime at:

```text
Mak5er/AirCard-iOS
097a058c984ffc33ccb697b9dfe8058be3e86244
```

During the build, `scripts/patch-airlift-for-carriersim.py` adds CarrierSIM-specific FFI calls to AirliftFFI:

```text
al_carriersim_status
al_carriersim_apply
al_carriersim_restore
```

The CarrierSIM transaction uses:

```text
Remote Pairing / RSD
        ↓
com.apple.afc
        ↓
com.apple.streaming_zip_conduit
        ↓
com.apple.atc
        ↓
AirTraffic / Books sync path
        ↓
/var/mobile/Library/Carrier Bundles/iPhone
```

For an apply or restore operation, the runtime:

1. preserves the current Books sync plist;
2. stages a controlled AirLift tree in `/var/mobile/Media`;
3. exports the current carrier catalog into a temporary Media backup;
4. reads that complete exported tree through AFC;
5. changes only the requested root IMSI symlink in the in-memory copy;
6. stages the replacement tree;
7. moves the replacement tree back to the carrier catalog through AirTraffic;
8. removes the temporary Media backup after a confirmed commit;
9. restores the original Books sync plist.

If the replacement cannot be staged after the catalog has been exported, the runtime attempts to put the original tree back automatically.

## Requirements

For the on-device path:

- iPhone running an AirLift-compatible iOS build; the current target is **iOS 27**;
- Developer Mode enabled;
- Apple Books installed, because the AirTraffic path uses the Books sync dataclass;
- Local Network permission for CarrierSIM;
- a working loopback tunnel such as **LocalDevVPN** or a compatible SideStore WireGuard configuration;
- an unsigned-IPA installer/signing workflow such as SideStore.

AirLift compatibility can change between iOS builds. A successful IPA build only verifies compilation and packaging; it does not prove that a specific iOS build still accepts the AirTraffic primitive.

## Install

Open the latest successful **Build CarrierSIM iOS IPA** workflow on the `main` branch and download:

```text
CarrierSIM-unsigned
└── CarrierSIM-unsigned.ipa
```

Sign/install the IPA with your normal sideloading setup.

## First setup

1. Open CarrierSIM.
2. Tap **Pair This iPhone**.
3. Follow the PIN prompt shown by CarrierSIM and approve the pairing in Developer Mode.
4. Start LocalDevVPN or the compatible SideStore loopback tunnel.
5. Return to CarrierSIM.
6. Tap **Scan SIMs**.

CarrierSIM reads the SIM rows directly from the phone. You do not need to type the IMSI manually.

## Applying a carrier bundle

For each detected SIM:

1. choose a bundle;
2. tap **Apply**;
3. wait for the AirLift transaction to finish;
4. tap **Reboot iPhone**.

The app currently exposes these convenient choices:

- `Vodafone_hu`
- `Vodafone_ro`
- `MTS_ua`
- `Telia_az`
- `Nova_is`
- a custom system bundle name

The existing `bundle.yaml` defaults are still useful as a reference:

```yaml
default: Vodafone_hu
25001: Vodafone_ro

25701: MTS_ua
25702: MTS_ua
25704: MTS_ua
25705: MTS_ua
25706: MTS_ua
```

CarrierSIM only points to bundles that already exist in the installed iOS system image. It does not make an unsigned carrier bundle trusted.

## Restore

For one SIM, tap **Restore Stock** on that SIM card.

To remove every root-level 15-digit IMSI alias created by this style of configuration, tap:

```text
Restore All IMSI Links
```

Then reboot the iPhone.

Other carrier catalog files, MCC/MNC aliases, installed IPCC content, and unrelated nodes are preserved.

## Recovery behavior

CarrierSIM keeps the original carrier catalog in a temporary Media backup until the replacement commit has been confirmed.

Temporary names use the form:

```text
carriersim-saved-<token>
```

If the app explicitly reports that automatic recovery failed and that a `carriersim-saved-*` backup remains, do not repeatedly start new writes. Preserve that backup and use the legacy desktop CarrierSIM recovery tooling from the same repository if necessary.

## Build locally

A macOS build machine with Xcode, Rust, Git, Python 3, and XcodeGen is required.

```bash
git clone https://github.com/NightVibes33/CarrierSIM.git
cd CarrierSIM
git checkout main

chmod +x scripts/build-ios-ipa.sh
scripts/build-ios-ipa.sh
```

Output:

```text
CarrierSIM-unsigned.ipa
CarrierSIM-SHA256.txt
```

The build script:

- clones the pinned AirCard source;
- patches its Rust AirliftFFI runtime for CarrierSIM;
- builds arm64 iOS + arm64 simulator static libraries;
- generates `AirliftFFI.xcframework`;
- generates the Xcode project with XcodeGen;
- builds the iPhone app without code signing;
- packages `Payload/CarrierSIM.app` as an unsigned IPA;
- verifies the bundle identifier and IPA archive.

## Repository layout

```text
ios-app/
  CarrierSIMApp.swift
  CarrierSIMViewModel.swift
  ContentView.swift
  PairingController.swift
  Utilities.swift
  Info.plist

scripts/
  build-ios-ipa.sh
  patch-airlift-for-carriersim.py

.github/workflows/
  carriersim-ios.yml

project.yml
```

The older desktop Python implementation and its assets are intentionally still present in the repository. They remain useful for diagnostics, comparison, and recovery, but the iOS app is the primary direction on `main`.

## Credits

CarrierSIM builds on work from:

- **CarrierSIM / ios-bundles** — the original carrier-bundle workflow and catalog logic;
- **AirLift by 0xjohnnydev** — the AirTraffic sandbox-escape research and protocol path;
- **AirCard-iOS by Mak5er** — the on-device Remote Pairing, RSD, AirTraffic, and AirliftFFI implementation;
- **Filza-27 / NFCARD work** — integration and on-device pairing/tunnel patterns used as a reference for this port.

See the repository license files, including `LICENSE-AirLift.txt` and `LICENSE-AirCard.txt`, for the corresponding third-party license notices.
