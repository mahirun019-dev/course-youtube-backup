import AppKit

// Separate optical masters at 16, 32, 128, 256 and 512 points.
// Retina representations render the matching master at 2x, never resize a PNG.
let destination = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: destination, withIntermediateDirectories: true)
let blue = NSColor(srgbRed: 0.10, green: 0.36, blue: 0.87, alpha: 1).cgColor
let white = NSColor.white.cgColor
let surface = NSColor(srgbRed: 0.98, green: 0.99, blue: 1, alpha: 1).cgColor

func polygon(_ c: CGContext, _ points: [CGPoint], _ color: CGColor) {
    c.beginPath(); c.move(to: points[0])
    for p in points.dropFirst() { c.addLine(to: p) }
    c.closePath(); c.setFillColor(color); c.fillPath()
}

func render(points size: Int, scale: Int, name: String) throws {
    let pixels = size * scale
    let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels,
        bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
        colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    let c = NSGraphicsContext(bitmapImageRep: bitmap)!.cgContext
    c.scaleBy(x: CGFloat(scale), y: CGFloat(scale))
    c.translateBy(x: 0, y: CGFloat(size)); c.scaleBy(x: 1, y: -1)
    c.setShouldAntialias(true)
    let s = CGFloat(size)
    let tile: CGRect, radius: CGFloat, panel: CGRect, panelRadius: CGFloat
    let apex: CGFloat, shoulder: CGFloat, halfHead: CGFloat, halfStem: CGFloat, stemEnd: CGFloat
    let play: CGRect
    if size == 16 {
        tile = CGRect(x: 1, y: 1, width: 14, height: 14); radius = 3
        panel = CGRect(x: 3, y: 8, width: 10, height: 5); panelRadius = 1.3
        apex = 3; shoulder = 6; halfHead = 3.5; halfStem = 1; stemEnd = 7
        play = CGRect(x: 7, y: 9, width: 3, height: 3)
    } else if size == 32 {
        tile = CGRect(x: 2, y: 2, width: 28, height: 28); radius = 6.2
        panel = CGRect(x: 6, y: 17, width: 20, height: 10); panelRadius = 2.4
        apex = 5; shoulder = 11; halfHead = 6; halfStem = 2; stemEnd = 15
        play = CGRect(x: 14, y: 19, width: 6, height: 6)
    } else {
        tile = CGRect(x: s * 0.0625, y: s * 0.0625, width: s * 0.875, height: s * 0.875)
        radius = s * 0.195
        // Enlarge the 128pt glyph relative to larger icons for legibility.
        let compact = size == 128
        panel = CGRect(x: s * (compact ? 0.203125 : 0.21875), y: s * 0.515625,
                       width: s * (compact ? 0.59375 : 0.5625), height: s * 0.296875)
        panelRadius = s * (compact ? 0.046875 : 0.048828125)
        apex = s * 0.2265625; shoulder = s * 0.3828125
        halfHead = s * (compact ? 0.16015625 : 0.15234375)
        halfStem = s * (compact ? 0.03515625 : 0.03125); stemEnd = s * 0.4609375
        play = CGRect(x: s * 0.4609375, y: s * 0.5859375, width: s * 0.140625, height: s * 0.15625)
    }
    c.setFillColor(surface)
    c.addPath(CGPath(roundedRect: tile, cornerWidth: radius, cornerHeight: radius, transform: nil)); c.fillPath()
    c.setFillColor(blue)
    c.addPath(CGPath(roundedRect: panel, cornerWidth: panelRadius, cornerHeight: panelRadius, transform: nil)); c.fillPath()
    let x = s / 2
    polygon(c, [CGPoint(x: x, y: apex), CGPoint(x: x + halfHead, y: shoulder),
        CGPoint(x: x + halfHead - halfStem * 1.35, y: shoulder + halfStem * 1.35),
        CGPoint(x: x + halfStem, y: apex + halfStem * 3.0), CGPoint(x: x + halfStem, y: stemEnd),
        CGPoint(x: x - halfStem, y: stemEnd), CGPoint(x: x - halfStem, y: apex + halfStem * 3.0),
        CGPoint(x: x - halfHead + halfStem * 1.35, y: shoulder + halfStem * 1.35),
        CGPoint(x: x - halfHead, y: shoulder)], blue)
    polygon(c, [CGPoint(x: play.minX, y: play.minY), CGPoint(x: play.maxX, y: play.midY),
                CGPoint(x: play.minX, y: play.maxY)], white)
    try bitmap.representation(using: .png, properties: [:])!.write(to: destination.appendingPathComponent(name))
}

for size in [16, 32, 128, 256, 512] {
    try render(points: size, scale: 1, name: "icon_\(size)x\(size).png")
    try render(points: size, scale: 2, name: "icon_\(size)x\(size)@2x.png")
}
