import SwiftUI

/// The arbiter's look-around scan (map/local), as sent on the "map" event.
struct LocalMap: Equatable {
    struct Sample: Equatable { let headingDeg: Double; let cm: Double? }

    let status: String               // scanning | done | aborted | refused
    let message: String?
    let done: Int
    let total: Int
    let samples: [Sample]
    let grid: [String]               // "?" unknown, "." free, "#" obstacle; row 0 = far ahead
    let radiusCM: Double
    let nearest: (headingDeg: Double, cm: Double)?
    let clearestDeg: Double?
    let time: Date

    init?(_ json: [String: Any]) {
        guard let status = json["status"] as? String else { return nil }
        self.status = status
        message = json["message"] as? String
        let progress = json["progress"] as? [Int] ?? [0, 0]
        done = progress.first ?? 0
        total = progress.count > 1 ? progress[1] : 0
        samples = (json["samples"] as? [[String: Any]] ?? []).compactMap { s in
            guard let heading = (s["heading_deg"] as? NSNumber)?.doubleValue else { return nil }
            return Sample(headingDeg: heading, cm: (s["cm"] as? NSNumber)?.doubleValue)
        }
        grid = json["grid"] as? [String] ?? []
        radiusCM = (json["radius_cm"] as? NSNumber)?.doubleValue ?? 150
        if let n = json["nearest"] as? [NSNumber], n.count == 2 {
            nearest = (n[0].doubleValue, n[1].doubleValue)
        } else {
            nearest = nil
        }
        clearestDeg = (json["clearest_deg"] as? NSNumber)?.doubleValue
        time = Date(timeIntervalSince1970: (json["ts"] as? NSNumber)?.doubleValue ?? Date().timeIntervalSince1970)
    }

    var scanning: Bool { status == "scanning" }

    static func == (a: LocalMap, b: LocalMap) -> Bool {
        a.status == b.status && a.done == b.done && a.time == b.time && a.message == b.message
    }
}

/// Grid, measurement rays, range rings every 50 cm and the crawler in the
/// middle, like the web UI's Surroundings panel. Heading 0 is up, positive
/// headings turn left (counter-clockwise).
struct MapCanvas: View {
    let map: LocalMap?

    var body: some View {
        Canvas { context, size in
            let side = min(size.width, size.height)
            let center = CGPoint(x: size.width / 2, y: size.height / 2)
            let radius = map?.radiusCM ?? 150
            let scale = side / (2 * radius)
            let origin = CGPoint(x: center.x - side / 2, y: center.y - side / 2)

            if let grid = map?.grid, !grid.isEmpty {
                let cell = side / CGFloat(grid.count)
                for (r, row) in grid.enumerated() {
                    for (c, ch) in row.enumerated() where ch != "?" {
                        let rect = CGRect(x: origin.x + CGFloat(c) * cell, y: origin.y + CGFloat(r) * cell,
                                          width: cell + 0.5, height: cell + 0.5)
                        context.fill(Path(rect), with: .color(ch == "#" ? Theme.warn : Theme.accent.opacity(0.28)))
                    }
                }
            }
            for cm in stride(from: 50.0, through: radius, by: 50) {
                let r = cm * scale
                context.stroke(Path(ellipseIn: CGRect(x: center.x - r, y: center.y - r, width: 2 * r, height: 2 * r)),
                               with: .color(.white.opacity(0.12)))
                context.draw(Text("\(Int(cm)) cm").font(.system(size: 10, design: .monospaced))
                                .foregroundColor(.white.opacity(0.45)),
                             at: CGPoint(x: center.x + 4, y: center.y - r + 8), anchor: .leading)
            }
            for sample in map?.samples ?? [] {
                let a = sample.headingDeg * .pi / 180
                let d = min(sample.cm ?? radius, radius) * scale
                var ray = Path()
                ray.move(to: center)
                ray.addLine(to: CGPoint(x: center.x - sin(a) * d, y: center.y - cos(a) * d))
                context.stroke(ray, with: .color(sample.cm == nil ? .white.opacity(0.15) : Theme.accent2.opacity(0.55)))
            }
            var crawler = Path()
            crawler.move(to: CGPoint(x: center.x, y: center.y - 11))
            crawler.addLine(to: CGPoint(x: center.x - 8, y: center.y + 8))
            crawler.addLine(to: CGPoint(x: center.x + 8, y: center.y + 8))
            crawler.closeSubpath()
            context.fill(crawler, with: .color(Theme.accent))
        }
        .aspectRatio(1, contentMode: .fit)
        .background(Theme.deep2, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.border))
        .accessibilityLabel("Map around the crawler")
    }
}

/// The Surroundings panel: map, status, summary and the Scan button.
struct MapPanel: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        Panel {
            HStack {
                Theme.eyebrow("Surroundings")
                Text(statusText)
                    .font(.system(size: 11, design: .monospaced))
                    .padding(.horizontal, 9).padding(.vertical, 2)
                    .background((warn ? Theme.warn : Theme.accent).opacity(0.1), in: Capsule())
                    .overlay(Capsule().stroke((warn ? Theme.warn : Theme.accent).opacity(0.35)))
                    .foregroundStyle(Theme.text)
            }
            MapCanvas(map: model.localMap)
                .frame(maxWidth: 480)
                .frame(maxWidth: .infinity)
            Text(infoText)
                .font(.system(size: 12))
                .foregroundStyle(Theme.muted)
            HStack(spacing: 14) {
                legend(Theme.accent.opacity(0.35), "free")
                legend(Theme.warn, "obstacle")
                legend(Theme.deep2, "unknown")
                Spacer()
                Button("Scan") { model.scan() }
                    .buttonStyle(OutlineButtonStyle())
                    .frame(width: 110)
                    .disabled(model.localMap?.scanning == true)
            }
        }
    }

    private var warn: Bool { ["aborted", "refused"].contains(model.localMap?.status ?? "") }

    private var statusText: String {
        guard let map = model.localMap else { return "no scan yet" }
        return map.scanning ? "scanning \(map.done)/\(map.total)" : map.status
    }

    private var infoText: String {
        guard let map = model.localMap else {
            return "The crawler turns once on the spot and measures with the ultrasonic sensor after every step."
        }
        if let message = map.message { return message }
        if map.scanning { return "Turning and measuring…" }
        var parts = [map.time.formatted(date: .omitted, time: .standard)]
        if let n = map.nearest { parts.append("nearest \(Int(n.cm.rounded())) cm at \(Int(n.headingDeg.rounded()))°") }
        if let c = map.clearestDeg { parts.append("most room at \(Int(c.rounded()))°") }
        return parts.joined(separator: " · ") + ". Only valid where it was scanned."
    }

    private func legend(_ color: Color, _ text: String) -> some View {
        HStack(spacing: 5) {
            RoundedRectangle(cornerRadius: 2).fill(color).frame(width: 10, height: 10)
                .overlay(RoundedRectangle(cornerRadius: 2).stroke(Color.white.opacity(0.2)))
            Text(text).font(.system(size: 11)).foregroundStyle(Theme.muted)
        }
    }
}
