import CryptoKit
import Foundation

/// Talks to the robot's web service: login, live status (SSE), the camera
/// stream (MJPEG), remote control and stop. Same API as the browser UI.
final class RobotClient: NSObject, URLSessionDataDelegate {
    enum ClientError: LocalizedError {
        case loginFailed, rateLimited, notAdmin, certificateChanged, http(Int)

        var errorDescription: String? {
            switch self {
            case .loginFailed: return "Wrong user or password."
            case .rateLimited: return "Too many attempts, wait a minute."
            case .notAdmin: return "This user may not drive the robot (admin role needed)."
            case .certificateChanged: return "The robot's certificate changed. If you reinstalled it, forget the robot and connect again."
            case .http(let code): return "The robot answered with HTTP \(code)."
            }
        }
    }

    struct Me: Decodable { let user: String; let role: String; let csrf: String }

    let robot: Robot
    private(set) var csrf: String?
    private(set) var me: Me?
    private let lock = NSLock()
    private var streams: [Int: (parser: JPEGFrameParser, continuation: AsyncThrowingStream<Data, Error>.Continuation)] = [:]
    private var sessionCookie: String?      // "picrawler_session=…", one per robot
    private lazy var session: URLSession = {
        let config = URLSessionConfiguration.default
        // the session cookie is handled by hand: one robot, one session
        config.httpShouldSetCookies = false
        config.httpCookieAcceptPolicy = .never
        config.timeoutIntervalForRequest = 10
        config.timeoutIntervalForResource = 24 * 3600         // streams run long
        return URLSession(configuration: config, delegate: self, delegateQueue: nil)
    }()

    init(robot: Robot) {
        self.robot = robot
    }

    // MARK: login

    func login(user: String, password: String) async throws {
        var request = URLRequest(url: robot.baseURL.appending(path: "login"))
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        var form = URLComponents()
        form.queryItems = [URLQueryItem(name: "user", value: user), URLQueryItem(name: "password", value: password)]
        request.httpBody = form.percentEncodedQuery?.data(using: .utf8)
        let (_, response) = try await session.data(for: request)     // redirects are not followed
        // the server answers 302: to "/" with a session cookie, or to "/login?error=…"
        guard let http = response as? HTTPURLResponse else { throw ClientError.loginFailed }
        let location = http.value(forHTTPHeaderField: "Location") ?? ""
        if location.contains("error=rate") { throw ClientError.rateLimited }
        guard http.statusCode == 302, !location.contains("/login"),
              let cookie = http.value(forHTTPHeaderField: "Set-Cookie")?
                .components(separatedBy: ";").first(where: { $0.hasPrefix("picrawler_session=") }) else {
            throw ClientError.loginFailed
        }
        sessionCookie = cookie
        let me: Me = try await getJSON("api/me")
        guard me.role == "admin" else { throw ClientError.notAdmin }
        self.me = me
        self.csrf = me.csrf
    }

    // MARK: commands

    func remote(x: Double, y: Double, speed: Int, action: String? = nil) async throws {
        var body: [String: Any] = ["x": x, "y": y, "speed": speed]
        if let action { body["action"] = action }
        try await post("api/remote", body)
    }

    func stop(sit: Bool = false) async throws {
        try await post("api/stop", ["sit": sit])
    }

    /// Ask the arbiter to look around; the result arrives as "map" events.
    func scan() async throws {
        try await post("api/map/scan", [:])
    }

    // MARK: live data

    /// Server-sent events: (event name, JSON object).
    func events() -> AsyncThrowingStream<(String, [String: Any]), Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let (bytes, response) = try await session.bytes(for: request("events"))
                    try check(response)
                    var event = "message"
                    // The server sends one "data:" line per event; the line reader
                    // skips the blank separator, so each data line completes an event.
                    for try await line in bytes.lines {
                        if line.hasPrefix("event:") {
                            event = line.dropFirst(6).trimmingCharacters(in: .whitespaces)
                        } else if line.hasPrefix("data:") {
                            let payload = Data(line.dropFirst(5).trimmingCharacters(in: .whitespaces).utf8)
                            if let json = try? JSONSerialization.jsonObject(with: payload) as? [String: Any] {
                                continuation.yield((event, json))
                            }
                            event = "message"
                        }
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    /// Camera stream: JPEG frames cut out of the multipart MJPEG response.
    /// Uses a data task with delegate callbacks: chunks, not single bytes.
    func frames() -> AsyncThrowingStream<Data, Error> {
        AsyncThrowingStream(bufferingPolicy: .bufferingNewest(2)) { continuation in
            let task = session.dataTask(with: request("stream.mjpg"))
            lock.withLock { streams[task.taskIdentifier] = (JPEGFrameParser(), continuation) }
            continuation.onTermination = { [weak self] _ in
                task.cancel()
                self?.lock.withLock { _ = self?.streams.removeValue(forKey: task.taskIdentifier) }
            }
            task.resume()
        }
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            let entry = lock.withLock { streams.removeValue(forKey: dataTask.taskIdentifier) }
            entry?.continuation.finish(throwing: ClientError.http(http.statusCode))
            completionHandler(.cancel)
            return
        }
        completionHandler(.allow)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        lock.lock()
        guard var entry = streams[dataTask.taskIdentifier] else { lock.unlock(); return }
        let frames = entry.parser.feed([UInt8](data))
        streams[dataTask.taskIdentifier] = entry
        lock.unlock()
        frames.forEach { entry.continuation.yield($0) }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        let entry = lock.withLock { streams.removeValue(forKey: task.taskIdentifier) }
        if let error { entry?.continuation.finish(throwing: error) } else { entry?.continuation.finish() }
    }

    // MARK: HTTP helpers

    private func request(_ path: String) -> URLRequest {
        var request = URLRequest(url: robot.baseURL.appending(path: path))
        if let sessionCookie { request.setValue(sessionCookie, forHTTPHeaderField: "Cookie") }
        return request
    }

    private func getJSON<T: Decodable>(_ path: String) async throws -> T {
        let (data, response) = try await session.data(for: request(path))
        try check(response)
        return try JSONDecoder().decode(T.self, from: data)
    }

    private func post(_ path: String, _ body: [String: Any]) async throws {
        var request = request(path)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(csrf, forHTTPHeaderField: "X-CSRF-Token")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (_, response) = try await session.data(for: request)
        try check(response)
    }

    private func check(_ response: URLResponse) throws {
        guard let http = response as? HTTPURLResponse else { return }
        guard (200..<300).contains(http.statusCode) else { throw ClientError.http(http.statusCode) }
    }

    // MARK: redirects

    /// Never follow redirects: the login answer must be read as it is, and
    /// API calls do not redirect.
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }

    // MARK: trust on first use

    /// The robot uses a self-signed certificate. The first connection pins its
    /// fingerprint; later connections must present the same certificate.
    func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge,
                    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              let trust = challenge.protectionSpace.serverTrust,
              let chain = SecTrustCopyCertificateChain(trust) as? [SecCertificate],
              let leaf = chain.first else {
            completionHandler(.performDefaultHandling, nil)
            return
        }
        let fingerprint = SHA256.hash(data: SecCertificateCopyData(leaf) as Data)
            .map { String(format: "%02x", $0) }.joined()
        let key = "pin.\(robot.id)"
        if let pinned = UserDefaults.standard.string(forKey: key) {
            guard pinned == fingerprint else {
                completionHandler(.cancelAuthenticationChallenge, nil)
                return
            }
        } else {
            UserDefaults.standard.set(fingerprint, forKey: key)
        }
        completionHandler(.useCredential, URLCredential(trust: trust))
    }

    static func forget(robotID: String) {
        UserDefaults.standard.removeObject(forKey: "pin.\(robotID)")
    }
}

/// Cuts complete JPEGs (FFD8 … FFD9) out of a byte stream, independent of
/// the multipart boundaries around them.
struct JPEGFrameParser {
    private var buffer = [UInt8]()
    private let maxBuffer = 2 * 1024 * 1024

    mutating func feed(_ bytes: [UInt8]) -> [Data] {
        buffer.append(contentsOf: bytes)
        var frames = [Data]()
        while let start = find(0xFF, 0xD8, from: 0), let end = find(0xFF, 0xD9, from: start + 2) {
            frames.append(Data(buffer[start...end + 1]))
            buffer.removeSubrange(0...end + 1)
        }
        if buffer.count > maxBuffer { buffer.removeAll() }    // garbage, start over
        return frames
    }

    private func find(_ a: UInt8, _ b: UInt8, from: Int) -> Int? {
        guard buffer.count >= 2, from < buffer.count - 1 else { return nil }
        for i in from..<(buffer.count - 1) where buffer[i] == a && buffer[i + 1] == b {
            return i
        }
        return nil
    }
}
