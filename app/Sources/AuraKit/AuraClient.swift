import Foundation

/// What the chat UI needs from the server. `AuraClient` is the real one; tests use a fake.
public protocol AuraTransport: Sendable {
    func chat(_ text: String) -> AsyncThrowingStream<AuraEvent, Error>
    func approve(_ approvals: [Bool]) -> AsyncThrowingStream<AuraEvent, Error>
    func newConversation() async throws
    func warmup() async throws
    func health() async throws -> Health
    func settings() async throws -> AppSettings
    func registerVoices(_ voices: [VoiceInfo]) async throws
    func updateVoice(_ patch: VoicePatch) async throws -> VoiceProfile
    func resetPersona() async throws -> PersonaInfo
    func voices() async throws -> [VoiceInfo]
    func speech(_ text: String, voice: String, speed: Double) async throws -> Data
}

/// Defaults so test fakes only implement what they exercise.
extension AuraTransport {
    public func settings() async throws -> AppSettings { throw AuraClientError.badResponse }
    public func registerVoices(_ voices: [VoiceInfo]) async throws {}
    public func updateVoice(_ patch: VoicePatch) async throws -> VoiceProfile { throw AuraClientError.badResponse }
    public func resetPersona() async throws -> PersonaInfo { throw AuraClientError.badResponse }
    public func voices() async throws -> [VoiceInfo] { [] }
    public func speech(_ text: String, voice: String, speed: Double) async throws -> Data { throw AuraClientError.badResponse }
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
