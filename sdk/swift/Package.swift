// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "Likyly",
    platforms: [.iOS(.v15), .macOS(.v12), .tvOS(.v15), .watchOS(.v8)],
    products: [
        .library(name: "Likyly", targets: ["Likyly"]),
    ],
    targets: [
        .target(name: "Likyly", path: "Sources/Likyly"),
        .testTarget(name: "LikylyTests", dependencies: ["Likyly"], path: "Tests/LikylyTests"),
    ]
)
