#!/bin/bash
# Builds KeyFlowMac.app directly with swiftc (no Xcode project needed — Command Line Tools are
# enough). Compiles KeyFlowCore as a real separate module first (matching how it's consumed when
# this project is opened in Xcode as an SPM package dependency), then links the App layer against
# it. Run from the repo root: ./build_app.sh
set -euo pipefail

cd "$(dirname "$0")"

APP_NAME="KeyFlowMac"
BUILD_DIR=".build/app"
APP_BUNDLE="$BUILD_DIR/$APP_NAME.app"
TARGET="arm64-apple-macos14.0"

rm -rf "$APP_BUNDLE"
mkdir -p "$BUILD_DIR"
mkdir -p "$APP_BUNDLE/Contents/MacOS"
mkdir -p "$APP_BUNDLE/Contents/Resources"
mkdir -p "$APP_BUNDLE/Contents/Frameworks"

echo "Compiling KeyFlowCore..."
swiftc Sources/KeyFlowCore/*.swift Sources/KeyFlowCore/Importer/*.swift \
    -module-name KeyFlowCore \
    -emit-module -emit-module-path "$BUILD_DIR/KeyFlowCore.swiftmodule" \
    -emit-library -o "$BUILD_DIR/libKeyFlowCore.dylib" \
    -Xlinker -install_name -Xlinker @rpath/libKeyFlowCore.dylib \
    -target "$TARGET" -swift-version 5 -Onone -g

APP_SOURCES=(App/*.swift App/Persistence/*.swift App/Service/*.swift App/UI/*.swift)

echo "Compiling App (${#APP_SOURCES[@]} files)..."
swiftc "${APP_SOURCES[@]}" \
    -o "$APP_BUNDLE/Contents/MacOS/$APP_NAME" \
    -I "$BUILD_DIR" -L "$BUILD_DIR" -lKeyFlowCore \
    -Xlinker -rpath -Xlinker @executable_path/../Frameworks \
    -target "$TARGET" -swift-version 5 \
    -parse-as-library \
    -Onone -g

cp "$BUILD_DIR/libKeyFlowCore.dylib" "$APP_BUNDLE/Contents/Frameworks/"
cp App/Resources/Info.plist "$APP_BUNDLE/Contents/Info.plist"

echo "Ad-hoc signing..."
codesign --force --deep --sign - "$APP_BUNDLE"

echo "Built $APP_BUNDLE"
