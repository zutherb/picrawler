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

/// The compact logo (head on a violet tile), drawn natively.
struct RobotMark: View {
    var body: some View {
        GeometryReader { geo in
            let s = min(geo.size.width, geo.size.height) / 64
            ZStack {
                RoundedRectangle(cornerRadius: 15 * s)
                    .fill(LinearGradient(colors: [Color(hex: 0x3B1E7A), Theme.deep], startPoint: .topLeading, endPoint: .bottomTrailing))
                Group {
                    Capsule().fill(Color(hex: 0x2F6FE0)).frame(width: 9 * s, height: 19 * s).offset(x: -20.5 * s, y: 2.5 * s)
                    Capsule().fill(Color(hex: 0x2F6FE0)).frame(width: 9 * s, height: 19 * s).offset(x: 20.5 * s, y: 2.5 * s)
                    RoundedRectangle(cornerRadius: 17 * s).fill(.white).frame(width: 40 * s, height: 42 * s).offset(y: 2 * s)
                    RoundedRectangle(cornerRadius: 9.5 * s).fill(Color(hex: 0x0B0A26)).frame(width: 30 * s, height: 24 * s).offset(y: 1 * s)
                    Circle().stroke(Theme.accent, lineWidth: 2.6 * s).frame(width: 9.2 * s).offset(x: -6 * s, y: 0.5 * s)
                    Circle().stroke(Theme.accent, lineWidth: 2.6 * s).frame(width: 9.2 * s).offset(x: 6 * s, y: 0.5 * s)
                    Circle().fill(.white).frame(width: 6.8 * s).offset(x: 14 * s, y: -25.5 * s)
                }
            }
            .frame(width: geo.size.width, height: geo.size.height)
        }
    }
}
