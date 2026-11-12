import SwiftUI
#if canImport(UIKit)
import UIKit
#else
import AppKit
#endif

/// The remote control, like the web UI's Remote page: video with status
/// chips, joystick, speed, actions, STOP. Side by side on iPad / landscape.
struct RemoteView: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.horizontalSizeClass) private var sizeClass
    let robot: Robot
    @State private var speed = 70.0

    private let actions = ["stand", "sit", "wave", "dance", "push up", "look left", "look right", "look up"]

    var body: some View {
        VStack(spacing: 0) {
            topBar
            GeometryReader { geo in
                let wide = sizeClass == .regular || geo.size.width > geo.size.height
                ScrollView {
                    if wide {
                        HStack(alignment: .top, spacing: 18) {
                            cameraPanel.frame(maxWidth: .infinity)
                            controlsPanel.frame(width: min(420, geo.size.width * 0.4))
                        }
                        .padding(18)
                    } else {
                        VStack(spacing: 16) {
                            cameraPanel
                            controlsPanel
                        }
                        .padding(14)
                    }
                }
            }
        }
        .overlay(alignment: .bottom) {
            if let message = model.message {
                Text(message)
                    .font(.system(size: 13, design: .monospaced))
                    .foregroundStyle(Theme.text)
                    .padding(12)
                    .background(Theme.deep2, in: RoundedRectangle(cornerRadius: 10))
                    .overlay(RoundedRectangle(cornerRadius: 10).stroke(Theme.bad))
                    .padding()
                    .onTapGesture { model.message = nil }
            }
        }
    }

    private var topBar: some View {
        HStack(spacing: 10) {
            RobotMark().frame(width: 32, height: 32)
            VStack(alignment: .leading, spacing: 0) {
                Text("PiCrawler").font(.system(size: 16, weight: .bold)).foregroundStyle(Theme.text)
                HStack(spacing: 5) {
                    Circle().fill(model.linkUp ? Theme.accent : Theme.bad).frame(width: 7, height: 7)
                    Text("picrawler@\(robot.host)").font(.system(size: 11, design: .monospaced)).foregroundStyle(Theme.muted)
                }
            }
            Spacer()
            if let battery = model.battery {
                BatteryPill(percent: battery.percent, level: battery.level)
            }
            Menu {
                Button("Disconnect") { model.disconnect() }
                Button("Forget this robot", role: .destructive) { model.disconnect(forget: true) }
            } label: {
                Image(systemName: "ellipsis.circle").font(.system(size: 22)).foregroundStyle(Theme.muted)
            }
            Button("STOP") { model.stop() }
                .font(.system(size: 14, weight: .bold, design: .monospaced))
                .foregroundStyle(.white)
                .padding(.horizontal, 14).padding(.vertical, 8)
                .background(Theme.stop, in: RoundedRectangle(cornerRadius: 8))
        }
        .padding(.horizontal, 16).padding(.vertical, 10)
        .background(Theme.background.opacity(0.95))
        .overlay(alignment: .bottom) { Rectangle().fill(Theme.border).frame(height: 1) }
    }

    private var cameraPanel: some View {
        Panel {
            HStack {
                Theme.eyebrow("Camera")
                Text(model.motion)
                    .font(.system(size: 11, design: .monospaced))
                    .padding(.horizontal, 9).padding(.vertical, 2)
                    .background(Theme.accent.opacity(0.1), in: Capsule())
                    .overlay(Capsule().stroke(Theme.accent.opacity(0.35)))
                    .foregroundStyle(Theme.text)
            }
            VideoView(jpeg: model.frame, placeholder: model.videoError ?? "Connecting to the camera…")
            FlowChips(items: chips)
        }
    }

    private var chips: [Chip] {
        var list = [Chip(text: model.bodyOnline ? "body online" : "body offline", level: model.bodyOnline ? .ok : .bad)]
        if let cm = model.distanceCM {
            list.append(Chip(text: String(format: "%.0f cm ahead", cm), level: cm < 15 ? .bad : cm < 25 ? .warn : .ok))
        }
        if let b = model.battery {
            list.append(Chip(text: "battery \(b.percent) %", level: b.level == "critical" ? .bad : b.level == "low" ? .warn : .ok))
        }
        return list
    }

    private var controlsPanel: some View {
        Panel {
            Theme.eyebrow("Drive")
            JoystickView { x, y in model.drive(x: x, y: y, speed: Int(speed)) }
                .frame(maxWidth: 280)
                .frame(maxWidth: .infinity)
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 6) {
                    Text("SPEED").font(.system(size: 12, design: .monospaced)).foregroundStyle(Theme.muted)
                    Text("\(Int(speed))").font(.system(size: 12, weight: .bold, design: .monospaced)).foregroundStyle(Theme.text)
                }
                Slider(value: $speed, in: 20...100, step: 5)
            }
            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 4), spacing: 8) {
                ForEach(actions, id: \.self) { name in
                    Button(name.capitalized) { model.action(name, speed: Int(speed)) }
                        .buttonStyle(OutlineButtonStyle())
                        .lineLimit(2)
                        .minimumScaleFactor(0.8)
                }
            }
            Button("STOP") { model.stop() }.buttonStyle(StopButtonStyle())
            Text("Moves only while you hold the joystick. Let go, close the app or lose the connection and the crawler stops after the current step. The obstacle stop and the battery protection always apply.")
                .font(.system(size: 12))
                .foregroundStyle(Theme.muted)
        }
    }
}

/// Wrapping row of chips.
struct FlowChips: View {
    let items: [Chip]
    var body: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 8) { ForEach(items.indices, id: \.self) { items[$0] } }
            VStack(alignment: .leading, spacing: 8) { ForEach(items.indices, id: \.self) { items[$0] } }
        }
    }
}

struct BatteryPill: View {
    let percent: Int
    let level: String

    var body: some View {
        HStack(spacing: 5) {
            ZStack(alignment: .leading) {
                RoundedRectangle(cornerRadius: 3).stroke(color, lineWidth: 1.5).frame(width: 24, height: 12)
                RoundedRectangle(cornerRadius: 1.5).fill(color)
                    .frame(width: max(0, min(20, 20 * Double(percent) / 100)), height: 8).padding(.leading, 2)
            }
            Text("\(percent)%").font(.system(size: 12, design: .monospaced)).foregroundStyle(color)
        }
    }

    private var color: Color { level == "critical" ? Theme.bad : level == "low" ? Theme.warn : Theme.accent }
}

/// Shows the newest JPEG of the MJPEG stream.
struct VideoView: View {
    let jpeg: Data?
    let placeholder: String

    var body: some View {
        ZStack {
            Theme.deep2
            GridBackground().opacity(jpeg == nil ? 1 : 0)
            if let jpeg, let image = PlatformImage(data: jpeg) {
                #if canImport(UIKit)
                Image(uiImage: image).resizable().aspectRatio(contentMode: .fit)
                #else
                Image(nsImage: image).resizable().aspectRatio(contentMode: .fit)
                #endif
            } else {
                Text(placeholder).font(.system(size: 13, design: .monospaced)).foregroundStyle(Theme.muted)
            }
        }
        .aspectRatio(16 / 9, contentMode: .fit)
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.border))
    }
}

#if canImport(UIKit)
typealias PlatformImage = UIImage
#else
typealias PlatformImage = NSImage
#endif

struct GridBackground: View {
    var body: some View {
        Canvas { context, size in
            var path = Path()
            stride(from: 0, through: size.width, by: 36).forEach { x in
                path.move(to: CGPoint(x: x, y: 0)); path.addLine(to: CGPoint(x: x, y: size.height))
            }
            stride(from: 0, through: size.height, by: 36).forEach { y in
                path.move(to: CGPoint(x: 0, y: y)); path.addLine(to: CGPoint(x: size.width, y: y))
            }
            context.stroke(path, with: .color(.white.opacity(0.05)), lineWidth: 1)
        }
    }
}
