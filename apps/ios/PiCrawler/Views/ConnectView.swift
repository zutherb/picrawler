import SwiftUI

/// Start screen: robots found via Bonjour, or join the robot's hotspot.
struct ConnectView: View {
    @EnvironmentObject var model: AppModel
    @State private var hotspotPassword = ""
    @State private var showManual = false
    @State private var manualHost = ""

    var body: some View {
        ScrollView {
            VStack(spacing: 20) {
                header
                RobotList(discovery: model.discovery)
                hotspotPanel
                if let message = model.message {
                    Text(message).font(.system(size: 13, design: .monospaced)).foregroundStyle(Theme.bad)
                }
                Button("Enter address manually") { showManual = true }
                    .font(.system(size: 13, design: .monospaced))
                    .foregroundStyle(Theme.muted)
            }
            .padding(20)
            .frame(maxWidth: 560)
            .frame(maxWidth: .infinity)
        }
        .alert("Robot address", isPresented: $showManual) {
            TextField("e.g. 192.168.178.42", text: $manualHost)
                .plainInput()
            Button("Connect") {
                let host = manualHost.trimmingCharacters(in: .whitespaces)
                guard !host.isEmpty else { return }
                model.choose(Robot(id: "manual-\(host)", name: host, host: host, port: 8080, tls: true))
            }
            Button("Cancel", role: .cancel) {}
        }
    }

    private var header: some View {
        VStack(spacing: 10) {
            RobotMark().frame(width: 96, height: 96)
            Theme.eyebrow("Robot control")
            Text("PiCrawler").font(.system(size: 34, weight: .bold)).foregroundStyle(Theme.text)
            HStack(spacing: 6) {
                Text("picrawler@robot:~$ find")
                Text("▌").foregroundStyle(Theme.accent)
            }
            .font(.system(size: 14, design: .monospaced))
            .foregroundStyle(Theme.muted)
        }
        .padding(.top, 30)
    }

    private var hotspotPanel: some View {
        Panel {
            Theme.eyebrow("Away from home")
            Text("No robot found? Join its own Wi-Fi \(Hotspot.ssid(robotID: model.robotID)).")
                .foregroundStyle(Theme.muted)
                .font(.system(size: 14))
            if model.savedHotspotPassphrase == nil {
                SecureField("Hotspot password", text: $hotspotPassword)
                    .textFieldStyle(.roundedBorder)
            }
            Button {
                let passphrase = model.savedHotspotPassphrase ?? hotspotPassword
                Task { await model.joinHotspot(passphrase: passphrase) }
            } label: {
                HStack {
                    if model.busy { ProgressView().tint(.black) }
                    Text(model.onHotspot ? "Connect via hotspot" : "Join \(Hotspot.ssid(robotID: model.robotID))")
                }
            }
            .buttonStyle(PrimaryButtonStyle())
            .disabled(model.busy || (model.savedHotspotPassphrase == nil && hotspotPassword.count < 8))
        }
    }
}

/// Robots found on the network; tap to connect.
struct RobotList: View {
    @EnvironmentObject var model: AppModel
    @ObservedObject var discovery: Discovery

    var body: some View {
        Panel {
            HStack {
                Theme.eyebrow("On this network")
                Spacer()
                if discovery.browsing { ProgressView().scaleEffect(0.8) }
            }
            ForEach(discovery.pending.keys.sorted(), id: \.self) { name in
                HStack {
                    RobotMark().frame(width: 36, height: 36).opacity(0.5)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(name).font(.system(size: 16, weight: .semibold)).foregroundStyle(Theme.muted)
                        Text(discovery.pending[name] ?? "").font(.system(size: 12, design: .monospaced))
                            .foregroundStyle(Theme.warn)
                    }
                    Spacer()
                }
                .padding(10)
            }
            if discovery.robots.isEmpty && discovery.pending.isEmpty {
                Text(discovery.error ?? "Looking for robots…")
                    .font(.system(size: 14, design: .monospaced))
                    .foregroundStyle(Theme.muted)
            }
            ForEach(discovery.robots) { robot in
                Button { model.choose(robot) } label: {
                    HStack {
                        RobotMark().frame(width: 36, height: 36)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(robot.name).font(.system(size: 16, weight: .semibold)).foregroundStyle(Theme.text)
                            Text(robot.displayAddress).font(.system(size: 12, design: .monospaced))
                                .foregroundStyle(Theme.muted)
                        }
                        Spacer()
                        Image(systemName: "chevron.right").foregroundStyle(Theme.accent)
                    }
                    .padding(10)
                    .background(Theme.accent.opacity(0.06), in: RoundedRectangle(cornerRadius: 10))
                }
            }
        }
    }
}

/// The compact logo (ball robot with four legs on a dark tile), drawn
/// natively; same geometry as logo-mark.svg (64-unit grid, offsets from the
/// centre 32/32).
struct RobotMark: View {
    var body: some View {
        GeometryReader { geo in
            let s = min(geo.size.width, geo.size.height) / 64
            ZStack {
                RoundedRectangle(cornerRadius: 15 * s)
                    .fill(LinearGradient(colors: [Color(hex: 0x232631), Theme.deep2], startPoint: .topLeading, endPoint: .bottomTrailing))
                // back legs behind the ball, ball, front legs with amber joints
                ForEach([-1.0, 1.0], id: \.self) { side in
                    Capsule().fill(Color(hex: 0xD3CEC4)).frame(width: 4.4 * s, height: 13.5 * s)
                        .rotationEffect(.degrees(-9.5 * side)).offset(x: 5.75 * side * s, y: 18.5 * s)
                }
                Ellipse().fill(Color(hex: 0xFAF8F3)).frame(width: 44 * s, height: 41 * s).offset(y: -3 * s)
                ForEach([-1.0, 1.0], id: \.self) { side in
                    Capsule().fill(.white).frame(width: 5.2 * s, height: 17.2 * s)
                        .rotationEffect(.degrees(-17 * side)).offset(x: 12.25 * side * s, y: 18.75 * s)
                    Circle().fill(Theme.accent).frame(width: 5.4 * s).offset(x: 10.5 * side * s, y: 13 * s)
                    // side camera lenses
                    Ellipse().fill(Theme.accent).frame(width: 5.2 * s, height: 10.4 * s).offset(x: 21.5 * side * s, y: -2 * s)
                }
                // camera ring on top
                Ellipse().fill(Theme.accent).frame(width: 10 * s, height: 4.4 * s).offset(y: -22.8 * s)
                Ellipse().fill(Theme.background).frame(width: 6 * s, height: 2.6 * s).offset(y: -23 * s)
                // screen face
                RoundedRectangle(cornerRadius: 9.5 * s).fill(Color(hex: 0x0D0F14)).frame(width: 30 * s, height: 24 * s).offset(y: -2.5 * s)
                // comic eyes like logo-mark.svg: white eye, dark pupil, highlight
                ForEach([-6.5, 6.5], id: \.self) { x in
                    Circle().fill(.white).frame(width: 10 * s).offset(x: x * s, y: -4 * s)
                    Circle().fill(Theme.background).frame(width: 5.8 * s).offset(x: x * s, y: -4 * s)
                    Circle().fill(.white.opacity(0.9)).frame(width: 1.8 * s).offset(x: (x - 1) * s, y: -5.2 * s)
                    Ellipse().fill(Color(hex: 0xFF8C8C)).frame(width: 4 * s, height: 2.5 * s).offset(x: x * 1.754 * s, y: 4.6 * s)
                }
                // smile
                Path { p in
                    p.move(to: CGPoint(x: 29.2 * s, y: 35.8 * s))
                    p.addQuadCurve(to: CGPoint(x: 34.8 * s, y: 35.8 * s), control: CGPoint(x: 32 * s, y: 38.4 * s))
                }
                .stroke(Theme.accent, style: StrokeStyle(lineWidth: 1.4 * s, lineCap: .round))
                .frame(width: 64 * s, height: 64 * s)
            }
            .frame(width: geo.size.width, height: geo.size.height)
        }
    }
}
