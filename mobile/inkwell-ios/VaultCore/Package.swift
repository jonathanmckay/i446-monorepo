// swift-tools-version:5.9
import PackageDescription

// Offline vault subset + capture outbox for the Inkwell iOS app's vault tab.
// Pure Foundation + the system SQLite (FTS5 is built into Apple's sqlite), so
// it builds on macOS with plain CommandLineTools: `swift build`, and the
// checks run as an executable: `swift run VaultCheck` (exit 0 = all pass).
let package = Package(
    name: "VaultCore",
    platforms: [.iOS(.v17), .macOS(.v13)],
    products: [.library(name: "VaultCore", targets: ["VaultCore"])],
    targets: [
        .target(name: "VaultCore", linkerSettings: [.linkedLibrary("sqlite3")]),
        .executableTarget(name: "VaultCheck", dependencies: ["VaultCore"]),
    ]
)
