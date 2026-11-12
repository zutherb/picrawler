import Foundation
import SwiftUI

/// App state: finding the robot, logging in, and the live session.
@MainActor
final class AppModel: ObservableObject {
    enum Phase: Equatable {
        case searching
        case login(Robot)
        case connected(Robot)
    }

    @Published var phase: Phase = .searching
    @Published var message: String?
    @Published var busy = false
    @Published var onHotspot = false

    // live data
    @Published var frame: Data?
    @Published var videoError: String?
    @Published var motion = "unknown"
    @Published var bodyOnline = false
    @Published var distanceCM: Double?
    @Published var battery: (percent: Int, level: String)?
    @Published var linkUp = false

    let discovery = Discovery()
    private(set) var client: RobotClient?
    private var liveTasks: [Task<Void, Never>] = []

    var robotID: String { UserDefaults.standard.string(forKey: "robotID") ?? "1" }

    // MARK: finding the robot

    /// Bonjour first; if nothing shows up and the phone is on a PiCrawler
    /// hotspot, use the hotspot address directly.
    func start() {
        discovery.start()
        Task {
            onHotspot = await Hotspot.onRobotHotspot()
            try? await Task.sleep(for: .seconds(3))
            if case .searching = phase, discovery.robots.isEmpty, onHotspot {
                choose(.hotspot(id: robotID))
            } else if case .searching = phase, discovery.robots.count == 1, let only = discovery.robots.first,
                      credentials(for: only) != nil {
                choose(only)       // exactly one robot and we know the login: go
            }
        }
    }

    func choose(_ robot: Robot) {
        if let (user, password) = credentials(for: robot) {
            Task { await login(robot, user: user, password: password, remember: true) }
        } else {
            phase = .login(robot)
        }
    }

    func joinHotspot(passphrase: String) async {
        busy = true
        defer { busy = false }
        do {
            try await Hotspot.join(robotID: robotID, passphrase: passphrase)
            Keychain.set(passphrase, for: "hotspot-\(robotID)")
            onHotspot = true
            try? await Task.sleep(for: .seconds(2))       // let DHCP finish
            choose(.hotspot(id: robotID))
        } catch {
            message = "Could not join \(Hotspot.ssid(robotID: robotID)): \(error.localizedDescription)"
        }
    }

    var savedHotspotPassphrase: String? { Keychain.get("hotspot-\(robotID)") }

    // MARK: login

    func login(_ robot: Robot, user: String, password: String, remember: Bool) async {
        busy = true
        defer { busy = false }
        let client = RobotClient(robot: robot)
        do {
            try await client.login(user: user, password: password)
            if remember {
                UserDefaults.standard.set(user, forKey: "user.\(robot.id)")
                Keychain.set(password, for: "login.\(robot.id)")
            }
            self.client = client
            message = nil
            phase = .connected(robot)
            startLive()
        } catch {
            message = error.localizedDescription
            phase = .login(robot)
        }
    }

    func credentials(for robot: Robot) -> (String, String)? {
        #if DEBUG
        // simulator screenshots: PICRAWLER_AUTOLOGIN="user:password" (debug builds only)
        if let auto = ProcessInfo.processInfo.environment["PICRAWLER_AUTOLOGIN"],
           let colon = auto.firstIndex(of: ":") {
            return (String(auto[..<colon]), String(auto[auto.index(after: colon)...]))
        }
        #endif
        guard let user = UserDefaults.standard.string(forKey: "user.\(robot.id)"),
              let password = Keychain.get("login.\(robot.id)") else { return nil }
        return (user, password)
    }

    func disconnect(forget: Bool = false) {
        liveTasks.forEach { $0.cancel() }
        liveTasks.removeAll()
        if forget, let robot = client?.robot {
            UserDefaults.standard.removeObject(forKey: "user.\(robot.id)")
            Keychain.delete("login.\(robot.id)")
            RobotClient.forget(robotID: robot.id)
        }
        client = nil
        frame = nil
        phase = .searching
        start()
    }

    // MARK: live session

    private func startLive() {
        guard let client else { return }
        liveTasks.forEach { $0.cancel() }
        liveTasks = [
            Task { [weak self] in
                while !Task.isCancelled {
                    do {
                        for try await (event, data) in client.events() {
                            self?.linkUp = true
                            if event == "status" { self?.apply(status: data) }
                        }
                    } catch {}
                    self?.linkUp = false
                    try? await Task.sleep(for: .seconds(2))     // reconnect
                }
            },
            Task { [weak self] in
                while !Task.isCancelled {
                    do {
                        for try await jpeg in client.frames() {
                            self?.frame = jpeg
                            self?.videoError = nil
                        }
                    } catch {
                        self?.videoError = "No video. Is the vision service running?"
                    }
                    try? await Task.sleep(for: .seconds(3))
                }
            },
        ]
    }

    private func apply(status: [String: Any]) {
        bodyOnline = (status["body"] as? [String: Any])?["status"] as? String == "online"
        if let m = status["motion"] as? [String: Any], let s = m["status"] as? String {
            motion = s == "running" ? "running \(m["name"] as? String ?? "")" : s
        }
        distanceCM = status["distance_cm"] as? Double
        if let b = status["battery"] as? [String: Any], b["present"] as? Bool == true,
           let percent = b["percent"] as? Int {
            battery = (percent, b["level"] as? String ?? "ok")
        } else {
            battery = nil
        }
    }

    // MARK: driving

    func drive(x: Double, y: Double, speed: Int) {
        guard let client else { return }
        Task { try? await client.remote(x: x, y: y, speed: speed) }
    }

    func action(_ name: String, speed: Int) {
        guard let client else { return }
        Task {
            do { try await client.remote(x: 0, y: 0, speed: speed, action: name) }
            catch { message = error.localizedDescription }
        }
    }

    func stop() {
        guard let client else { return }
        Task {
            do { try await client.stop() }
            catch { message = "Stop failed: \(error.localizedDescription)" }
        }
    }
}
