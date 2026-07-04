// swift/aesthetics.swift
// Score each image path's aesthetic quality on-device with Apple Vision, emit JSON to stdout:
//   [{"path": "...", "score": <Double -1..1>, "isUtility": <Bool>}, ...]
// Build:  swiftc -O -parse-as-library swift/aesthetics.swift -o .composerv/bin/aesthetics
import Foundation
import Vision
import ImageIO

@main
struct Aesthetics {
    static func main() async {
        let paths = Array(CommandLine.arguments.dropFirst())
        var out: [[String: Any]] = []
        for path in paths {
            guard let src = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil),
                  let cg = CGImageSourceCreateImageAtIndex(src, 0, nil) else { continue }
            if #available(macOS 15.0, *) {
                let request = CalculateImageAestheticsScoresRequest()
                do {
                    let obs = try await request.perform(on: cg)
                    out.append(["path": path,
                                "score": Double(obs.overallScore),
                                "isUtility": obs.isUtility])
                } catch {
                    FileHandle.standardError.write(Data("aesthetics err \(path): \(error)\n".utf8))
                }
            }
        }
        if let data = try? JSONSerialization.data(withJSONObject: out) {
            FileHandle.standardOutput.write(data)
        }
    }
}
