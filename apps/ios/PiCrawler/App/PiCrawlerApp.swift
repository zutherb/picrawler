import SwiftUI

@main
struct PiCrawlerApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(model)
                .preferredColorScheme(.dark)
                .tint(Theme.accent)
                .onAppear { model.start() }
        }
    }
}

struct RootView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        ZStack {
            Theme.background.ignoresSafeArea()
            switch model.phase {
            case .searching:
                ConnectView()
            case .login(let robot):
                LoginView(robot: robot)
            case .connected(let robot):
                RemoteView(robot: robot)
            }
        }
        .animation(.easeInOut(duration: 0.2), value: model.phase)
    }
}
