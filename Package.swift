// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "KeyFlowCore",
    platforms: [.macOS(.v13)],
    products: [
        .library(name: "KeyFlowCore", targets: ["KeyFlowCore"]),
    ],
    targets: [
        .target(name: "KeyFlowCore"),
        .testTarget(name: "KeyFlowCoreTests", dependencies: ["KeyFlowCore"]),
        // Dependency-free runner for the same cases as KeyFlowCoreTests, runnable with
        // `swift run SmokeTests` on toolchains where swift-testing's macro plugin isn't wired up
        // (plain Command Line Tools, no full Xcode) — `swift test` remains the primary path.
        .executableTarget(name: "SmokeTests", dependencies: ["KeyFlowCore"]),
    ]
)
