import SwiftUI

/// Colors and type of the web UI (after bernd-zuther.de): deep violet, teal
/// accent, monospaced labels.
enum Theme {
    static let deep = Color(hex: 0x24104F)
    static let deep2 = Color(hex: 0x160A33)
    static let background = Color(hex: 0x120A26)
    static let panel = Color(hex: 0x1E1138)
    static let text = Color(hex: 0xF1EDF9)
    static let muted = Color(hex: 0xA79BC9)
    static let accent = Color(hex: 0x30D5C0)
    static let accent2 = Color(hex: 0xA0F3E3)
    static let warn = Color(hex: 0xFEBC2E)
    static let bad = Color(hex: 0xFF5F57)
    static let stop = Color(hex: 0xE5484D)
    static let border = Color.white.opacity(0.10)

    static let accentGradient = LinearGradient(colors: [accent, accent2], startPoint: .leading, endPoint: .trailing)

    /// Section label like the web UI's eyebrows: CAMERA, DRIVE …
    static func eyebrow(_ text: String) -> some View {
        Text(text.uppercased())
            .font(.system(size: 12, weight: .semibold, design: .monospaced))
            .tracking(1.5)
            .foregroundStyle(accent)
    }
}

extension Color {
    init(hex: UInt32) {
        self.init(red: Double((hex >> 16) & 0xFF) / 255, green: Double((hex >> 8) & 0xFF) / 255,
                  blue: Double(hex & 0xFF) / 255)
    }
}

extension View {
    /// No auto-capitalization or autocorrect (user names, addresses).
    func plainInput() -> some View {
        #if os(iOS)
        return self.textInputAutocapitalization(.never).autocorrectionDisabled()
        #else
        return self.autocorrectionDisabled()
        #endif
    }
}

/// Card like `.panel` in the web UI.
struct Panel<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 14) { content }
            .padding(18)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.panel, in: RoundedRectangle(cornerRadius: 14))
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(Theme.border))
    }
}

/// Pill with a status dot, like `.chip` on the remote page.
struct Chip: View {
    enum Level { case ok, warn, bad, neutral }
    let text: String
    var level: Level = .neutral

    var body: some View {
        HStack(spacing: 7) {
            Circle().fill(color).frame(width: 8, height: 8)
            Text(text).font(.system(size: 12, design: .monospaced))
        }
        .foregroundStyle(Theme.text)
        .padding(.horizontal, 11).padding(.vertical, 5)
        .overlay(Capsule().stroke(Color.white.opacity(0.2)))
    }

    private var color: Color {
        switch level {
        case .ok: return Theme.accent
        case .warn: return Theme.warn
        case .bad: return Theme.bad
        case .neutral: return Theme.muted
        }
    }
}

struct OutlineButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 15, weight: .medium))
            .foregroundStyle(Theme.text)
            .frame(maxWidth: .infinity, minHeight: 44)
            .background(configuration.isPressed ? Theme.accent.opacity(0.15) : .clear,
                        in: RoundedRectangle(cornerRadius: 8))
            .overlay(RoundedRectangle(cornerRadius: 8)
                .stroke(configuration.isPressed ? Theme.accent : Color.white.opacity(0.2)))
    }
}

struct PrimaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 16, weight: .semibold))
            .foregroundStyle(Color(hex: 0x101018))
            .frame(maxWidth: .infinity, minHeight: 48)
            .background(Theme.accent.opacity(configuration.isPressed ? 0.8 : 1), in: RoundedRectangle(cornerRadius: 8))
    }
}

struct StopButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 20, weight: .bold, design: .monospaced))
            .tracking(2)
            .foregroundStyle(.white)
            .frame(maxWidth: .infinity, minHeight: 56)
            .background(Theme.stop.opacity(configuration.isPressed ? 0.85 : 1), in: RoundedRectangle(cornerRadius: 10))
    }
}
