#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PIN="097a058c984ffc33ccb697b9dfe8058be3e86244"
PATCH_SHA="$(shasum -a 256 "$ROOT/scripts/patch-airlift-for-carriersim.py" | awk '{print $1}')"
CACHE="$ROOT/.airlift-cache/$PIN-$PATCH_SHA"
WORK="$ROOT/.build/aircard-runtime"
SRC="$WORK/AirCard-iOS"
DERIVED="$ROOT/.build/DerivedData"
OUT="$ROOT/CarrierSIM-unsigned.ipa"

mkdir -p "$ROOT/.build" "$ROOT/.airlift-cache"
rm -rf "$WORK" "$DERIVED" "$OUT"
mkdir -p "$WORK"

if [ -d "$CACHE/AirliftFFI.xcframework" ]; then
  echo "==> Restoring cached CarrierSIM AirliftFFI"
  rm -rf "$ROOT/AirliftFFI.xcframework"
  ditto "$CACHE/AirliftFFI.xcframework" "$ROOT/AirliftFFI.xcframework"
else
  echo "==> Building CarrierSIM AirliftFFI from pinned AirCard runtime"
  git clone --filter=blob:none https://github.com/Mak5er/AirCard-iOS.git "$SRC"
  git -C "$SRC" checkout --detach "$PIN"
  test "$(git -C "$SRC" rev-parse HEAD)" = "$PIN"

  python3 "$ROOT/scripts/patch-airlift-for-carriersim.py" "$SRC"

  export DEVELOPER_DIR="${DEVELOPER_DIR:-$(xcode-select -p)}"
  chmod +x "$SRC/build-ios.sh"
  (
    cd "$SRC"
    ./build-ios.sh
  )

  rm -rf "$ROOT/AirliftFFI.xcframework"
  ditto "$SRC/AirliftFFI.xcframework" "$ROOT/AirliftFFI.xcframework"

  rm -rf "$CACHE"
  mkdir -p "$CACHE"
  ditto "$ROOT/AirliftFFI.xcframework" "$CACHE/AirliftFFI.xcframework"
fi

test -f "$ROOT/AirliftFFI.xcframework/ios-arm64/Headers/airlift.h"
grep -Fq "al_carriersim_status" "$ROOT/AirliftFFI.xcframework/ios-arm64/Headers/airlift.h"
grep -Fq "al_carriersim_apply" "$ROOT/AirliftFFI.xcframework/ios-arm64/Headers/airlift.h"
grep -Fq "al_carriersim_restore" "$ROOT/AirliftFFI.xcframework/ios-arm64/Headers/airlift.h"

if ! command -v xcodegen >/dev/null 2>&1; then
  brew install xcodegen
fi

cd "$ROOT"
xcodegen generate

xcodebuild \
  -project CarrierSIM.xcodeproj \
  -scheme CarrierSIM \
  -configuration Release \
  -sdk iphoneos \
  -derivedDataPath "$DERIVED" \
  CODE_SIGNING_ALLOWED=NO \
  CODE_SIGNING_REQUIRED=NO \
  CODE_SIGN_IDENTITY="" \
  build

APP="$(find "$DERIVED/Build/Products/Release-iphoneos" -maxdepth 1 -type d -name 'CarrierSIM.app' -print -quit)"
test -n "$APP"
test -f "$APP/Info.plist"

BUNDLE_ID="$(plutil -extract CFBundleIdentifier raw -o - "$APP/Info.plist")"
DISPLAY_NAME="$(plutil -extract CFBundleDisplayName raw -o - "$APP/Info.plist")"
test "$BUNDLE_ID" = "com.nightvibes33.carriersim"
test "$DISPLAY_NAME" = "CarrierSIM"

PAYLOAD="$ROOT/.build/package/Payload"
rm -rf "$ROOT/.build/package"
mkdir -p "$PAYLOAD"
ditto "$APP" "$PAYLOAD/CarrierSIM.app"

(
  cd "$ROOT/.build/package"
  /usr/bin/zip -qry "$OUT" Payload
)

unzip -tq "$OUT"
shasum -a 256 "$OUT" | tee "$ROOT/CarrierSIM-SHA256.txt"
ls -lh "$OUT"
