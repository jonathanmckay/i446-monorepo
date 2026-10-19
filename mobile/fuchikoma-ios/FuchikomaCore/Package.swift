// swift-tools-version:5.9
import PackageDescription

// Pure Foundation code shared by the iOS app (and any future widget /
// Live Activity extension). Builds on macOS with plain CommandLineTools.
// CommandLineTools ships neither XCTest nor swift-testing, so the checks
// live in a tiny executable: `swift run CoreCheck` (exit 0 = all pass).
let package = Package(
    name: "FuchikomaCore",
    platforms: [.iOS(.v17), .macOS(.v13)],
    products: [.library(name: "FuchikomaCore", targets: ["FuchikomaCore"])],
    targets: [
        .target(name: "FuchikomaCore"),
        .executableTarget(name: "CoreCheck", dependencies: ["FuchikomaCore"]),
    ]
)
