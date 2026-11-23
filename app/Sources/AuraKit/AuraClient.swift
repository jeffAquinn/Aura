import Foundation

/// What the chat UI needs from the server. `AuraClient` is the real one; tests use a fake.
public protocol AuraTransport: Sendable {
    func chat(_ text: String) -> AsyncThrowingStream<AuraEvent, Error>
    func approve(_ approvals: [Bool]) -> AsyncThrowingStream<AuraEvent, Error>
    /// The same, where an answer can approve with edits (the Specification Approval card).
    func approve(decisions: [ApprovalDecision]) -> AsyncThrowingStream<AuraEvent, Error>
    func newConversation() async throws
    func warmup() async throws
    func health() async throws -> Health
    func settings() async throws -> AppSettings
    func registerVoices(_ voices: [VoiceInfo]) async throws
    func updateVoice(_ patch: VoicePatch) async throws -> VoiceProfile
    func resetPersona() async throws -> PersonaInfo
    func voices() async throws -> [VoiceInfo]
    func speech(_ text: String, voice: String, speed: Double) async throws -> Data
    func skills() async throws -> SkillsOverview
    func toggleSkill(_ name: String, enabled: Bool) async throws
    func propose(_ skill: String) async throws -> TaskInfo
    func tasks() async throws -> [TaskInfo]
    func task(_ id: String) async throws -> TaskInfo
    func decide(_ id: String, approve: Bool) async throws -> TaskInfo
    func status() async throws -> SystemStatus
    func alerts() async throws -> AlertsInfo
    func uninstall(_ skill: String) async throws
    func keepFlagged(_ skill: String) async throws
    func auditNow() async throws
    func developer() async throws -> DeveloperInfo
    func startBuild(_ request: BuildRequest) async throws -> BuildInfo
    func stopBuild(_ id: String) async throws -> BuildInfo
    func secrets() async throws -> [SecretInfo]
    func setSecret(_ name: String, value: String) async throws -> SecretInfo
    func deleteSecret(_ name: String) async throws
    func connections() async throws -> [ConnectionInfo]
    func saveConnectionCredentials(_ id: String, _ credentials: ConnectionCredentials) async throws -> ConnectionInfo
    func removeConnectionCredentials(_ id: String) async throws -> ConnectionInfo
    func connect(_ id: String, services: [String]) async throws -> SignInStart
    func checkConnection(_ id: String) async throws -> ConnectionInfo
    func disconnect(_ id: String) async throws -> ConnectionInfo
    func saveLiveTokens(_ id: String, _ tokens: LiveTokens) async throws -> ConnectionInfo
    func removeLiveTokens(_ id: String) async throws -> ConnectionInfo
    /// Live messages newer than `after`, waiting up to `wait` seconds for one (a long poll).
    func messages(after: Int, wait: Int) async throws -> MessagesPage
}

/// Defaults so test fakes only implement what they exercise.
extension AuraTransport {
    public func settings() async throws -> AppSettings { throw AuraClientError.badResponse }
    public func registerVoices(_ voices: [VoiceInfo]) async throws {}
    public func updateVoice(_ patch: VoicePatch) async throws -> VoiceProfile { throw AuraClientError.badResponse }
    public func resetPersona() async throws -> PersonaInfo { throw AuraClientError.badResponse }
    public func voices() async throws -> [VoiceInfo] { [] }
    public func speech(_ text: String, voice: String, speed: Double) async throws -> Data { throw AuraClientError.badResponse }
    public func skills() async throws -> SkillsOverview { SkillsOverview(installed: [], inbox: []) }
    public func toggleSkill(_ name: String, enabled: Bool) async throws {}
    public func propose(_ skill: String) async throws -> TaskInfo { throw AuraClientError.badResponse }
    public func tasks() async throws -> [TaskInfo] { [] }
    public func task(_ id: String) async throws -> TaskInfo { throw AuraClientError.badResponse }
    public func decide(_ id: String, approve: Bool) async throws -> TaskInfo { throw AuraClientError.badResponse }
    public func status() async throws -> SystemStatus { throw AuraClientError.badResponse }
    public func alerts() async throws -> AlertsInfo { AlertsInfo(alerts: [], continuousAudit: AuditClock()) }
    public func uninstall(_ skill: String) async throws {}
    public func keepFlagged(_ skill: String) async throws {}
    public func auditNow() async throws {}
    public func approve(decisions: [ApprovalDecision]) -> AsyncThrowingStream<AuraEvent, Error> {
        approve(decisions.map(\.approved))
    }
    public func developer() async throws -> DeveloperInfo { throw AuraClientError.badResponse }
    public func startBuild(_ request: BuildRequest) async throws -> BuildInfo { throw AuraClientError.badResponse }
    public func stopBuild(_ id: String) async throws -> BuildInfo { throw AuraClientError.badResponse }
    public func secrets() async throws -> [SecretInfo] { [] }
    public func setSecret(_ name: String, value: String) async throws -> SecretInfo { throw AuraClientError.badResponse }
    public func deleteSecret(_ name: String) async throws {}
    public func connections() async throws -> [ConnectionInfo] { [] }
    public func saveConnectionCredentials(_ id: String, _ credentials: ConnectionCredentials) async throws -> ConnectionInfo {
        throw AuraClientError.badResponse
    }
    public func removeConnectionCredentials(_ id: String) async throws -> ConnectionInfo { throw AuraClientError.badResponse }
    public func connect(_ id: String, services: [String]) async throws -> SignInStart { throw AuraClientError.badResponse }
    public func checkConnection(_ id: String) async throws -> ConnectionInfo { throw AuraClientError.badResponse }
    public func disconnect(_ id: String) async throws -> ConnectionInfo { throw AuraClientError.badResponse }
    public func saveLiveTokens(_ id: String, _ tokens: LiveTokens) async throws -> ConnectionInfo { throw AuraClientError.badResponse }
    public func removeLiveTokens(_ id: String) async throws -> ConnectionInfo { throw AuraClientError.badResponse }
    public func messages(after: Int, wait: Int) async throws -> MessagesPage { MessagesPage(messages: [], latest: after) }
}

public enum AuraClientError: Error, LocalizedError, Equatable {
    case http(status: Int, message: String)
    case badResponse

    public var errorDescription: String? {
        switch self {
        case let .http(status, message): "Aura Server said \(status): \(message)"
        case .badResponse: "Aura Server sent an unexpected response."
        }
    }
}

/// HTTP + SSE client for the local Aura Server. Every request carries the per-launch token.
public final class AuraClient: AuraTransport {
    public let baseURL: URL
    private let token: String
    private let session: URLSession

    public init(port: Int, token: String, session: URLSession = .shared) {
        baseURL = URL(string: "http://127.0.0.1:\(port)")!
        self.token = token
        self.session = session
    }

    public func chat(_ text: String) -> AsyncThrowingStream<AuraEvent, Error> {
        stream("/v1/chat", body: ["text": text])
    }

    public func approve(_ approvals: [Bool]) -> AsyncThrowingStream<AuraEvent, Error> {
        stream("/v1/approvals", body: ["approvals": approvals])
    }

    public func approve(decisions: [ApprovalDecision]) -> AsyncThrowingStream<AuraEvent, Error> {
        stream("/v1/approvals", body: ["approvals": decisions])
    }

    public func newConversation() async throws {
        _ = try await send(request("/v1/conversation/new", body: [String: String]()))
    }

    public func warmup() async throws {
        _ = try await send(request("/v1/warmup", body: [String: String]()))
    }

    public func health() async throws -> Health {
        let data = try await send(request("/v1/health"))
        let json = JSONDecoder()
        json.keyDecodingStrategy = .convertFromSnakeCase
        return try json.decode(Health.self, from: data)
    }

    public func settings() async throws -> AppSettings {
        try Self.decoder.decode(AppSettings.self, from: await send(request("/v1/settings")))
    }

    public func registerVoices(_ voices: [VoiceInfo]) async throws {
        struct Body: Encodable { let voices: [VoiceInfo] }
        _ = try await send(request("/v1/voices", body: Body(voices: voices)))
    }

    public func updateVoice(_ patch: VoicePatch) async throws -> VoiceProfile {
        var r = request("/v1/voice", body: patch)
        r.httpMethod = "PUT"
        return try Self.decoder.decode(VoiceProfile.self, from: await send(r))
    }

    public func resetPersona() async throws -> PersonaInfo {
        try Self.decoder.decode(PersonaInfo.self, from: await send(request("/v1/persona/reset", body: [String: String]())))
    }

    public func voices() async throws -> [VoiceInfo] {
        try Self.decoder.decode([VoiceInfo].self, from: await send(request("/v1/voices")))
    }

    /// One sentence of audio (WAV) in one of Aura's neural voices.
    public func speech(_ text: String, voice: String, speed: Double) async throws -> Data {
        struct Body: Encodable { let text: String; let voice: String; let speed: Double }
        return try await send(request("/v1/speech", body: Body(text: text, voice: voice, speed: speed)))
    }

    // MARK: - the Workshop

    public func skills() async throws -> SkillsOverview {
        try Self.decoder.decode(SkillsOverview.self, from: await send(request("/v1/skills")))
    }

    public func toggleSkill(_ name: String, enabled: Bool) async throws {
        var r = request("/v1/skills/\(name)", body: ["enabled": enabled])
        r.httpMethod = "PATCH"
        _ = try await send(r)
    }

    public func propose(_ skill: String) async throws -> TaskInfo {
        try Self.decoder.decode(TaskInfo.self, from: await send(request("/v1/tasks", body: ["skill": skill])))
    }

    public func tasks() async throws -> [TaskInfo] {
        try Self.decoder.decode([TaskInfo].self, from: await send(request("/v1/tasks")))
    }

    public func task(_ id: String) async throws -> TaskInfo {
        try Self.decoder.decode(TaskInfo.self, from: await send(request("/v1/tasks/\(id)")))
    }

    public func decide(_ id: String, approve: Bool) async throws -> TaskInfo {
        try Self.decoder.decode(TaskInfo.self, from: await send(request("/v1/tasks/\(id)/decision", body: ["approve": approve])))
    }

    public func status() async throws -> SystemStatus {
        try Self.decoder.decode(SystemStatus.self, from: await send(request("/v1/status")))
    }

    // MARK: - the Continuous Audit

    public func alerts() async throws -> AlertsInfo {
        try Self.decoder.decode(AlertsInfo.self, from: await send(request("/v1/alerts")))
    }

    public func uninstall(_ skill: String) async throws {
        var r = request("/v1/skills/\(skill)")
        r.httpMethod = "DELETE"
        _ = try await send(r)
    }

    public func keepFlagged(_ skill: String) async throws {
        _ = try await send(request("/v1/skills/\(skill)/acknowledge", body: [String: String]()))
    }

    public func auditNow() async throws {
        _ = try await send(request("/v1/audit", body: [String: String]()))
    }

    // MARK: - the Developer and keys (Sprint 6)

    public func developer() async throws -> DeveloperInfo {
        try Self.decoder.decode(DeveloperInfo.self, from: await send(request("/v1/developer")))
    }

    public func startBuild(_ request: BuildRequest) async throws -> BuildInfo {
        try Self.decoder.decode(BuildInfo.self, from: await send(self.request("/v1/builds", body: request)))
    }

    public func stopBuild(_ id: String) async throws -> BuildInfo {
        try Self.decoder.decode(BuildInfo.self, from: await send(request("/v1/builds/\(id)/stop", body: [String: String]())))
    }

    public func secrets() async throws -> [SecretInfo] {
        try Self.decoder.decode([SecretInfo].self, from: await send(request("/v1/secrets")))
    }

    /// The value goes to the local server only, which stores it in the macOS Keychain.
    public func setSecret(_ name: String, value: String) async throws -> SecretInfo {
        var r = request("/v1/secrets/\(name)", body: ["value": value])
        r.httpMethod = "PUT"
        return try Self.decoder.decode(SecretInfo.self, from: await send(r))
    }

    public func deleteSecret(_ name: String) async throws {
        var r = request("/v1/secrets/\(name)")
        r.httpMethod = "DELETE"
        _ = try await send(r)
    }

    // MARK: - connections (Settings → Connections)

    public func connections() async throws -> [ConnectionInfo] {
        try Self.decoder.decode([ConnectionInfo].self, from: await send(request("/v1/connections")))
    }

    /// The client secret goes to the local server only, which stores it in the macOS Keychain.
    public func saveConnectionCredentials(_ id: String, _ credentials: ConnectionCredentials) async throws -> ConnectionInfo {
        var r = request("/v1/connections/\(id)/credentials", body: credentials)
        r.httpMethod = "PUT"
        return try Self.decoder.decode(ConnectionInfo.self, from: await send(r))
    }

    public func removeConnectionCredentials(_ id: String) async throws -> ConnectionInfo {
        var r = request("/v1/connections/\(id)/credentials")
        r.httpMethod = "DELETE"
        return try Self.decoder.decode(ConnectionInfo.self, from: await send(r))
    }

    public func connect(_ id: String, services: [String]) async throws -> SignInStart {
        try Self.decoder.decode(SignInStart.self, from: await send(request("/v1/connections/\(id)/connect", body: ["services": services])))
    }

    public func checkConnection(_ id: String) async throws -> ConnectionInfo {
        try Self.decoder.decode(ConnectionInfo.self, from: await send(request("/v1/connections/\(id)/check", body: [String: String]())))
    }

    public func disconnect(_ id: String) async throws -> ConnectionInfo {
        try Self.decoder.decode(ConnectionInfo.self, from: await send(request("/v1/connections/\(id)/disconnect", body: [String: String]())))
    }

    public func saveLiveTokens(_ id: String, _ tokens: LiveTokens) async throws -> ConnectionInfo {
        var r = request("/v1/connections/\(id)/live", body: tokens)
        r.httpMethod = "PUT"
        return try Self.decoder.decode(ConnectionInfo.self, from: await send(r))
    }

    public func removeLiveTokens(_ id: String) async throws -> ConnectionInfo {
        var r = request("/v1/connections/\(id)/live")
        r.httpMethod = "DELETE"
        return try Self.decoder.decode(ConnectionInfo.self, from: await send(r))
    }

    public func messages(after: Int, wait: Int) async throws -> MessagesPage {
        var r = request("/v1/messages")
        r.url = r.url?.appending(queryItems: [URLQueryItem(name: "after", value: String(after)),
                                              URLQueryItem(name: "wait", value: String(wait))])
        r.timeoutInterval = TimeInterval(wait + 30)  // the server holds the request open while it waits
        return try Self.decoder.decode(MessagesPage.self, from: await send(r))
    }

    private static var decoder: JSONDecoder {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }

    // MARK: - plumbing

    func request(_ path: String, body: (some Encodable)? = String?.none) -> URLRequest {
        var r = URLRequest(url: baseURL.appending(path: path))
        r.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        r.timeoutInterval = 300  // idle time between bytes: covers a cold model load
        if let body {
            r.httpMethod = "POST"
            r.setValue("application/json", forHTTPHeaderField: "Content-Type")
            r.httpBody = try? JSONEncoder().encode(body)
        }
        return r
    }

    private func send(_ request: URLRequest) async throws -> Data {
        let (data, response) = try await session.data(for: request)
        try Self.check(response, data)
        return data
    }

    private static func check(_ response: URLResponse, _ body: Data) throws {
        guard let http = response as? HTTPURLResponse else { throw AuraClientError.badResponse }
        guard (200..<300).contains(http.statusCode) else {
            struct ErrorBody: Decodable { let error: String }
            let message = (try? JSONDecoder().decode(ErrorBody.self, from: body))?.error
                ?? String(decoding: body, as: UTF8.self)
            throw AuraClientError.http(status: http.statusCode, message: message)
        }
    }

    private func stream(_ path: String, body: some Encodable & Sendable) -> AsyncThrowingStream<AuraEvent, Error> {
        let request = request(path, body: body)
        let session = session
        return AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let (bytes, response) = try await session.bytes(for: request)
                    if (response as? HTTPURLResponse)?.statusCode != 200 {
                        var data = Data()
                        for try await byte in bytes { data.append(byte) }
                        try Self.check(response, data)
                    }
                    var parser = SSEParser()
                    for try await line in bytes.lines {
                        guard let event = parser.feed(line) else { continue }
                        continuation.yield(event)
                        if event == .done { break }
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            // Stop (or a dropped consumer) cancels the request; the server then frees the turn.
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}
