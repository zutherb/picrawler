import SwiftUI

/// Login like the web UI's hero page.
struct LoginView: View {
    @EnvironmentObject var model: AppModel
    let robot: Robot
    @State private var user = "admin"
    @State private var password = ""
    @State private var remember = true

    var body: some View {
        ZStack {
            Theme.deep.ignoresSafeArea()
            RadialGradient(colors: [Theme.accent.opacity(0.35), .clear], center: .topTrailing, startRadius: 0, endRadius: 380)
                .ignoresSafeArea()
            VStack(alignment: .leading, spacing: 12) {
                RobotMark().frame(width: 72, height: 72)
                Theme.eyebrow("Robot control")
                Text("PiCrawler").font(.system(size: 32, weight: .bold)).foregroundStyle(Theme.text)
                Text("picrawler@\(robot.host):~$ login").font(.system(size: 13, design: .monospaced))
                    .foregroundStyle(Theme.muted)
                TextField("User", text: $user)
                    .plainInput()
                    .textContentType(.username)
                    .textFieldStyle(.roundedBorder)
                SecureField("Password", text: $password)
                    .textContentType(.password)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit(submit)
                Toggle("Remember on this device", isOn: $remember)
                    .font(.system(size: 14))
                    .foregroundStyle(Theme.muted)
                if let message = model.message {
                    Text(message).font(.system(size: 13, design: .monospaced)).foregroundStyle(Color(hex: 0xFF8A85))
                }
                Button(action: submit) {
                    HStack {
                        if model.busy { ProgressView().tint(.black) }
                        Text("Log in")
                    }
                }
                .buttonStyle(PrimaryButtonStyle())
                .disabled(model.busy || password.isEmpty)
                Button("Back") { model.disconnect() }
                    .font(.system(size: 13, design: .monospaced))
                    .foregroundStyle(Theme.muted)
                    .frame(maxWidth: .infinity)
            }
            .padding(24)
            .frame(maxWidth: 400)
        }
    }

    private func submit() {
        Task { await model.login(robot, user: user, password: password, remember: remember) }
    }
}
