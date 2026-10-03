# CarrierSIM

[![Build CarrierSIM IPA](https://github.com/NightVibes33/CarrierSIM/actions/workflows/carriersim-ios.yml/badge.svg?branch=main)](https://github.com/NightVibes33/CarrierSIM/actions/workflows/carriersim-ios.yml)

CarrierSIM is an **on-device iPhone app** for experimenting with iOS carrier-bundle selection through the AirLift/AirTraffic path.

It is built as an **unsigned IPA** for sideloading and uses the same on-device Remote Pairing + RSD approach used by the AirCard integration in Filza-27.

**Bundle ID:** `com.nightvibes33.carriersim`

## What CarrierSIM does

The iOS app can:

- pair with the same iPhone from inside the app;
- connect back to the device through an RSD/loopback tunnel;
- read `CarrierBundleInfoArray` from lockdown;
- show active SIM/eSIM slots, MCC/MNC, IMSI, ICCID tail, and the currently reported carrier bundle;
- create a root-level 15-digit IMSI alias in the writable carrier catalog;
- point that IMSI alias at an Apple-signed carrier bundle already present in the firmware;
- save the pre-change carrier-bundle selection before the first change;
- restore that saved selection later;
- send a device restart request so CommCenter can re-evaluate the carrier-bundle mapping;
- display live AirLift activity and errors in the app.

CarrierSIM targets:

```text
/var/mobile/Library/Carrier Bundles/iPhone
```

The alias points into:

```text
/System/Library/Carrier Bundles/iPhone
```

CarrierSIM does **not** replace the SIM/eSIM, activate service on another network, change your carrier account, or provision a different carrier. It changes the local carrier-bundle mapping used by iOS.

## AirLift runtime

CarrierSIM does not embed Python.

The build pins the AirCard on-device AirLift runtime to:

```text
Repository: Mak5er/AirCard-iOS
Commit:     097a058c984ffc33ccb697b9dfe8058be3e86244
```

`scripts/build-ios-ipa.sh` fetches that exact revision and applies the CarrierSIM extension from:

```text
scripts/patch-airlift-for-carriersim.py
```

and builds the `AirliftFFI.xcframework` device slice required by the unsigned iPhone IPA.

CarrierSIM adds three FFI operations:

```text
al_carriersim_status
al_carriersim_apply
al_carriersim_restore
```

The existing AirCard runtime also supplies pairing, RSD/Lockdown transport, AirTraffic, syslog support, and device restart support.

## Requirements

For on-device use:

- iPhone/iPad running a compatible iOS build;
- Developer Mode enabled;
- Apple Books installed;
- a loopback tunnel such as LocalDevVPN or a compatible SideStore WireGuard configuration;
- the CarrierSIM unsigned IPA sideloaded with your preferred signing method.

The current Xcode deployment target is iOS 18.0 so the same IPA can be tested on newer releases, including iOS 27.

## Using the app

1. Open CarrierSIM.
2. Tap **Pair This iPhone**.
3. Approve the CarrierSIM Remote Pairing request in **Settings → Privacy & Security → Developer Mode** and enter the displayed PIN.
4. Start LocalDevVPN or the compatible loopback tunnel.
5. Return to CarrierSIM and tap **Scan SIMs**.
6. Choose the SIM/eSIM line and carrier bundle.
7. Tap **Apply**.
8. After a successful AirLift operation, tap **Reboot iPhone** so CommCenter reloads the carrier selection.

CarrierSIM's native runtime exports the current carrier catalog before a change, preserves its files, directories, and symlinks, then commits the modified tree. **Restore Stock** removes the selected 15-digit IMSI alias, while **Restore All IMSI Links** removes all root-level 15-digit IMSI aliases and preserves the rest of the catalog. If a transaction cannot be confirmed, the runtime attempts to restore the exported original catalog.

## Carrier-bundle names

The UI currently exposes several convenient bundle names plus a custom field:

```text
Vodafone_hu
Vodafone_ro
MTS_ua
Telia_az
Nova_is
```

A selected bundle must already exist as a signed system carrier bundle on the installed iOS build. A custom entry can be supplied without the `.bundle` suffix.

## Building the IPA

The GitHub Actions workflow is:

```text
.github/workflows/carriersim-ios.yml
```

It:

1. checks out CarrierSIM;
2. installs the iOS Rust targets and XcodeGen;
3. restores a cached AirLift runtime when available;
4. otherwise downloads the pinned AirCard source and applies `scripts/patch-airlift-for-carriersim.py`;
5. builds `AirliftFFI.xcframework`;
6. generates `CarrierSIM.xcodeproj`;
7. builds `CarrierSIM.app` for `iphoneos` with code signing disabled;
8. verifies the three CarrierSIM FFI symbols in the final app binary;
9. packages `Payload/CarrierSIM.app` into `CarrierSIM-unsigned.ipa`;
10. uploads the IPA and SHA-256 file as the `CarrierSIM-unsigned` Actions artifact.

To build on a Mac manually:

```bash
brew install xcodegen
rustup target add aarch64-apple-ios
chmod +x scripts/build-ios-ipa.sh
scripts/build-ios-ipa.sh
```

## Repository layout

```text
ios-app/                                SwiftUI application
scripts/patch-airlift-for-carriersim.py CarrierSIM AirliftFFI patch
scripts/build-ios-ipa.sh                 pinned native runtime + IPA build
project.yml                              XcodeGen project definition
.github/workflows/carriersim-ios.yml     unsigned IPA CI
carrier.py                               original desktop implementation/reference
```

The original desktop CarrierSIM implementation remains in the repository as a reference for its carrier-catalog logic. The primary `main` workflow is now the iOS IPA app.

## Credits

CarrierSIM builds on AirLift/AirTraffic research and the on-device AirCard runtime. Keep the upstream project licenses and attribution when redistributing derived builds.
