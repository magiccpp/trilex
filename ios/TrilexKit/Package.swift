// swift-tools-version:5.9
// The platform-neutral core of the iPhone app: search, forgetting curve,
// wordbook, sync and downloads. Nothing here imports UIKit or SwiftUI, so it
// builds and tests on Linux as well as on Apple platforms.
import PackageDescription

let package = Package(
    name: "TrilexKit",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "TrilexKit", targets: ["TrilexKit"]),
    ],
    targets: [
        // Apple SDKs ship an `SQLite3` module; Linux needs this shim instead.
        .systemLibrary(name: "CSQLite", path: "Sources/CSQLite",
                       providers: [.apt(["libsqlite3-dev"])]),
        .target(name: "TrilexKit",
                dependencies: [.target(name: "CSQLite",
                                       condition: .when(platforms: [.linux]))]),
        .testTarget(name: "TrilexKitTests", dependencies: ["TrilexKit"],
                    resources: [.copy("Fixtures")]),
    ]
)
