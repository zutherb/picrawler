import Foundation
import Network

/// A PiCrawler announced on the local network (avahi, `_picrawler._tcp`).
struct Robot: Identifiable, Hashable {
    let id: String          // TXT "id" plus service name, stable per robot
    let name: String
    let host: String        // resolved IP address
    let port: UInt16
    let tls: Bool

    var baseURL: URL {
        // IPv6 literals need brackets in URLs
        let h = host.contains(":") ? "[\(host)]" : host
        return URL(string: "\(tls ? "https" : "http")://\(h):\(port)")!
    }

    /// The robot's own hotspot: the Pi is always at 10.42.0.1.
    static func hotspot(id: String = "1") -> Robot {
        Robot(id: "hotspot-\(id)", name: "PiCrawler \(id) (hotspot)", host: "10.42.0.1", port: 8080, tls: true)
    }
}

/// Browses for `_picrawler._tcp` and resolves each service to an address.
@MainActor
final class Discovery: ObservableObject {
    @Published private(set) var robots: [Robot] = []
    @Published private(set) var browsing = false
    @Published private(set) var error: String?

    private var browser: NWBrowser?
    private var resolvers: [String: NWConnection] = [:]

    func start() {
        stop()
        let parameters = NWParameters()
        parameters.includePeerToPeer = true
        let browser = NWBrowser(for: .bonjourWithTXTRecord(type: "_picrawler._tcp", domain: nil), using: parameters)
        browser.stateUpdateHandler = { [weak self] state in
            Task { @MainActor in
                switch state {
                case .ready: self?.browsing = true; self?.error = nil
                case .failed(let e): self?.browsing = false; self?.error = e.localizedDescription
                case .cancelled: self?.browsing = false
                default: break
                }
            }
        }
        browser.browseResultsChangedHandler = { [weak self] results, _ in
            Task { @MainActor in self?.update(results) }
        }
        browser.start(queue: .main)
        self.browser = browser
    }

    func stop() {
        browser?.cancel()
        browser = nil
        resolvers.values.forEach { $0.cancel() }
        resolvers.removeAll()
    }

    private func update(_ results: Set<NWBrowser.Result>) {
        let names = Set(results.compactMap { serviceName($0.endpoint) })
        robots.removeAll { robot in !names.contains(where: { robot.id.hasSuffix("|\($0)") }) }
        for result in results {
            guard let name = serviceName(result.endpoint) else { continue }
            var txt: [String: String] = [:]
            if case .bonjour(let record) = result.metadata {
                txt = record.dictionary
            }
            resolve(result.endpoint, name: name, txt: txt)
        }
    }

    private func serviceName(_ endpoint: NWEndpoint) -> String? {
        if case .service(let name, _, _, _) = endpoint { return name }
        return nil
    }

    /// Connect once to learn the address behind the service, then close.
    private func resolve(_ endpoint: NWEndpoint, name: String, txt: [String: String]) {
        guard resolvers[name] == nil else { return }
        let connection = NWConnection(to: endpoint, using: .tcp)
        resolvers[name] = connection
        connection.stateUpdateHandler = { [weak self] state in
            guard case .ready = state else {
                if case .failed = state { Task { @MainActor in self?.resolvers[name] = nil } }
                return
            }
            if case .hostPort(let host, let port) = connection.currentPath?.remoteEndpoint {
                let address: String
                switch host {
                case .ipv4(let a): address = "\(a)"
                case .ipv6(let a): address = "\(a)".components(separatedBy: "%").first ?? "\(a)"
                case .name(let n, _): address = n
                @unknown default: address = "\(host)"
                }
                let robot = Robot(id: "\(txt["id"] ?? "?")|\(name)", name: name, host: address,
                                  port: port.rawValue, tls: txt["tls"] != "0")
                Task { @MainActor in
                    guard let self else { return }
                    self.robots.removeAll { $0.id == robot.id }
                    self.robots.append(robot)
                    self.robots.sort { $0.name < $1.name }
                }
            }
            connection.cancel()
            Task { @MainActor in self?.resolvers[name] = nil }
        }
        connection.start(queue: .main)
    }
}
