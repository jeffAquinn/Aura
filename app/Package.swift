// swift-tools-version: 6.2
import PackageDescription

let package = Package(
    name: "Aura",
    platforms: [.macOS(.v26)],  // native glass (glassEffect / NSGlassEffectView)
    products: [
        .executable(name: "Aura", targets: ["Aura"]),
        .executable(name: "aura-smoke", targets: ["aura-smoke"]),
    ],
    targets: [
        // Networking, protocol, state and Markdown model: no UI, fully unit-tested.
        .target(name: "AuraKit"),
        // SwiftUI views (snapshot-tested).
        .target(name: "AuraUI", dependencies: ["AuraKit"]),
        // The menu bar app: AppKit shell around AuraUI.
        .executableTarget(name: "Aura", dependencies: ["AuraKit", "AuraUI"]),
        // Drives the real server through AuraKit from the command line (end-to-end check).
        .executableTarget(name: "aura-smoke", dependencies: ["AuraKit"]),
        .testTarget(name: "AuraKitTests", dependencies: ["AuraKit"]),
        .testTarget(name: "AuraUITests", dependencies: ["AuraUI", "AuraKit"]),
    ]
)
