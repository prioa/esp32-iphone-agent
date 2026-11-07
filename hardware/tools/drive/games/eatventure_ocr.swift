// Prints the text in a PNG, one line per piece: x y w h text (pixels, y from the top). macOS's own Vision framework.
// Build: swiftc -O eatventure_ocr.swift -o ocr. See eatventure_bot.py.
import Foundation
import Vision
import CoreGraphics
import ImageIO

let url = URL(fileURLWithPath: CommandLine.arguments[1]) as CFURL
guard let src = CGImageSourceCreateWithURL(url, nil), let img = CGImageSourceCreateImageAtIndex(src, 0, nil) else { exit(1) }
let w = Double(img.width), h = Double(img.height)
let req = VNRecognizeTextRequest()
req.recognitionLevel = .accurate
req.recognitionLanguages = ["de-DE", "en-US"]
try VNImageRequestHandler(cgImage: img).perform([req])
for r in req.results ?? [] {
    guard let t = r.topCandidates(1).first else { continue }
    let b = r.boundingBox
    print(Int(b.minX * w), Int((1 - b.maxY) * h), Int(b.width * w), Int(b.height * h), t.string)
}
