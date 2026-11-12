import SwiftUI
#if canImport(UIKit)
import UIKit
#endif

/// Virtual joystick like the web remote: drag the teal thumb. Reports the
/// stick (x, y in -1…1, up is negative) every 150 ms while held, and one
/// centered report on release, so the robot works as a dead man's switch.
struct JoystickView: View {
    let onStick: (Double, Double) -> Void

    @State private var offset: CGSize = .zero
    @State private var limit: CGFloat = 100      // how far the thumb can move, in points
    @State private var active = false
    private let sendEvery: Duration = .milliseconds(150)
    private let deadzone = 0.2

    var body: some View {
        GeometryReader { geo in
            let size = min(geo.size.width, geo.size.height)
            let radius = size / 2
            let thumb = size * 0.27
            ZStack {
                Circle()
                    .fill(Theme.deep2)
                    .overlay(Circle().fill(RadialGradient(colors: [Theme.accent.opacity(active ? 0.22 : 0.12), .clear],
                                                          center: .center, startRadius: 0, endRadius: radius)))
                    .overlay(Circle().stroke(active ? Theme.accent.opacity(0.5) : Color.white.opacity(0.12)))
                    .shadow(color: active ? Theme.accent.opacity(0.4) : .clear, radius: 24)
                Circle().stroke(style: StrokeStyle(lineWidth: 1, dash: [4, 4]))
                    .foregroundStyle(.white.opacity(0.18))
                    .padding(size * 0.22)
                label("forward").offset(y: -radius + 16)
                label("back").offset(y: radius - 16)
                label("left").offset(x: -radius + 26)
                label("right").offset(x: radius - 28)
                Circle()
                    .fill(Theme.accentGradient)
                    .frame(width: thumb, height: thumb)
                    .shadow(color: Theme.accent.opacity(0.6), radius: 12, y: 6)
                    .offset(offset)
            }
            .frame(width: size, height: size)
            .contentShape(Circle())
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { value in
                        let limit = radius - thumb / 2
                        self.limit = limit
                        var dx = value.location.x - radius
                        var dy = value.location.y - radius
                        let length = (dx * dx + dy * dy).squareRoot()
                        if length > limit { dx *= limit / length; dy *= limit / length }
                        offset = CGSize(width: dx, height: dy)
                        if !active { begin() }
                    }
                    .onEnded { _ in end() }
            )
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .aspectRatio(1, contentMode: .fit)
        .onDisappear { end() }
        .task(id: active) {
            // while held: report the stick every 150 ms (dead man's switch)
            while active && !Task.isCancelled {
                let (x, y) = stick()
                if x != 0 || y != 0 { onStick(x, y) }
                try? await Task.sleep(for: sendEvery)
            }
        }
    }

    private func label(_ text: String) -> some View {
        Text(text.uppercased())
            .font(.system(size: 10, design: .monospaced))
            .tracking(1)
            .foregroundStyle(Theme.muted)
    }

    private func stick() -> (Double, Double) {
        let x = max(-1, min(1, offset.width / max(1, limit)))
        let y = max(-1, min(1, offset.height / max(1, limit)))
        return ((x * x + y * y).squareRoot() < deadzone) ? (0, 0) : (x, y)
    }

    private func begin() {
        active = true
        haptic(.medium)
    }

    private func end() {
        guard active else { return }
        active = false
        withAnimation(.spring(response: 0.25, dampingFraction: 0.6)) { offset = .zero }
        onStick(0, 0)
        haptic(.light)
    }

    private func haptic(_ style: HapticStyle) {
        #if canImport(UIKit)
        UIImpactFeedbackGenerator(style: style == .medium ? .medium : .light).impactOccurred()
        #endif
    }

    private enum HapticStyle { case light, medium }
}
