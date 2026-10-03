#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
AIRLIFT_REPO="https://github.com/Mak5er/AirCard-iOS.git"
AIRLIFT_COMMIT="097a058c984ffc33ccb697b9dfe8058be3e86244"
BUILD_ROOT="$ROOT/.build/airlift-source"

export IPHONEOS_DEPLOYMENT_TARGET="${IPHONEOS_DEPLOYMENT_TARGET:-18.0}"
export RUSTFLAGS="${RUSTFLAGS:-} --remap-path-prefix=${HOME}=/build"

rm -rf "$BUILD_ROOT" "$ROOT/rust-core" "$ROOT/AirliftFFI.xcframework"
mkdir -p "$(dirname "$BUILD_ROOT")"

git clone --filter=blob:none --no-checkout "$AIRLIFT_REPO" "$BUILD_ROOT"
git -C "$BUILD_ROOT" fetch --depth 1 origin "$AIRLIFT_COMMIT"
git -C "$BUILD_ROOT" checkout --detach "$AIRLIFT_COMMIT"

cp -R "$BUILD_ROOT/rust-core" "$ROOT/rust-core"
python3 "$ROOT/scripts/patch-airlift.py"

source "$HOME/.cargo/env" 2>/dev/null || true
rustup target add aarch64-apple-ios aarch64-apple-ios-sim

pushd "$ROOT/rust-core" >/dev/null
cargo build --release --target aarch64-apple-ios
cargo build --release --target aarch64-apple-ios-sim
popd >/dev/null

xcodebuild -create-xcframework \
  -library "$ROOT/rust-core/target/aarch64-apple-ios/release/libairlift_ffi.a" \
  -headers "$ROOT/rust-core/include" \
  -library "$ROOT/rust-core/target/aarch64-apple-ios-sim/release/libairlift_ffi.a" \
  -headers "$ROOT/rust-core/include" \
  -output "$ROOT/AirliftFFI.xcframework"

if ! command -v xcodegen >/dev/null 2>&1; then
  echo "xcodegen is required" >&2
  exit 1
fi

xcodegen generate --spec "$ROOT/project.yml"
echo "CarrierSIM iOS project generated."
