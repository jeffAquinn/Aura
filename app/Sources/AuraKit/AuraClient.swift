import Foundation

/// What the chat UI needs from the server. `AuraClient` is the real one; tests use a fake.
public protocol AuraTransport: Sendable {
    func chat(_ text: String) -> AsyncThrowingStream<AuraEvent, Error>
    /// A message the user handed to a Developer in the chat box ("claude" or "codex"; nil: Aura decides).
    func chat(_ text: String, developer: String?) -> AsyncThrowingStream<AuraEvent, Error>
    func approve(_ approvals: [Bool]) -> AsyncThrowingStream<AuraEvent, Error>
    /// The same, where an answer can approve with edits (the Specification Approval card).
    func approve(decisions: [ApprovalDecision]) -> AsyncThrowingStream<AuraEvent, Error>
    func newConversation() async throws
    func warmup() async throws
    func health() async throws -> Health
    func settings() async throws -> AppSettings
    func models() async throws -> ModelCatalog
    func updateModels(_ update: ModelRoutingUpdate) async throws -> ModelCatalog
    func updateCodingEngine(_ engine: String) async throws -> ModelCatalog
    /// Refresh the account-visible Anthropic model list after an explicit user action.
    func syncCloudModels() async throws -> ModelCatalog
    func updateSleepMode(_ mode: SleepMode) async throws -> ModelCatalog
    /// A user-confirmed download through the local Ollama daemon; never an agent action.
    func pullLocalModel(_ model: String) async throws -> ModelCatalog
    /// An explicit model applies to this Aura turn only. Developer turns do not accept it.
    func chat(_ text: String, developer: String?, model: ModelChoice?) -> AsyncThrowingStream<AuraEvent, Error>
    func registerVoices(_ voices: [VoiceInfo]) async throws
    func updateVoice(_ patch: VoicePatch) async throws -> VoiceProfile
    func resetPersona() async throws -> PersonaInfo
    func voices() async throws -> [VoiceInfo]
    func speech(_ text: String, voice: String, speed: Double) async throws -> Data
    func skills() async throws -> SkillsOverview
    func agents() async throws -> AgentsOverview
    func createAgent(_ request: AgentCandidateRequest, imported: Bool) async throws -> AgentInfo
    func suspendAgent(_ name: String, reason: String) async throws -> AgentInfo
    func revalidateAgent(_ name: String) async throws -> TaskInfo
    func retireAgent(_ name: String, reason: String) async throws -> AgentInfo
    func toggleSkill(_ name: String, enabled: Bool) async throws
    func propose(_ skill: String) async throws -> TaskInfo
    func proposeAgent(_ name: String) async throws -> TaskInfo
    func tasks() async throws -> [TaskInfo]
    func task(_ id: String) async throws -> TaskInfo
    func decide(_ id: String, approve: Bool) async throws -> TaskInfo
    func cancelAudit(_ id: String) async throws -> TaskInfo
    func status() async throws -> SystemStatus
    func alerts() async throws -> AlertsInfo
    func uninstall(_ skill: String) async throws
    func keepFlagged(_ skill: String) async throws
    func auditNow() async throws
    func developer() async throws -> DeveloperInfo
    /// A user-requested, no-write verification of the signed-in Claude Code or Codex account.
    func checkDeveloper(_ engine: String) async throws -> EngineInfo
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
    func setChat(_ id: String, _ chat: ChatSwitch) async throws -> ConnectionInfo
    /// Live messages newer than `after`, waiting up to `wait` seconds for one (a long poll).
    func messages(after: Int, wait: Int) async throws -> MessagesPage
    /// Stop the turn being streamed. Returns once the server has cleaned it up (true), or false
    /// when nothing was stopped (the caller then closes the stream, which the server treats as a stop).
    func stopTurn() async throws -> Bool
    /// The conversation the server kept (after a restart: the history, and any approval still waiting).
    func conversation() async throws -> ConversationInfo
    func retryBuild(_ id: String) async throws -> BuildInfo
    func dismissRecovery(_ id: String) async throws
    func hol() async throws -> HOLOverview
    func startHOL(_ name: String, request: HOLStart) async throws -> HOLRun
    func createHOLSavepoint(_ label: String) async throws -> HOLSavepoint
    func restoreHOLSavepoint(_ id: String) async throws -> HOLSavepoint
    func restoreHOLOriginal() async throws
    func holTerminal(_ id: String, after: Int) async throws -> HOLTerminalState
    func sendHOLCommand(_ id: String, command: String) async throws -> HOLTerminalState
    func sendHOLMessage(_ id: String, message: String) async throws -> HOLMessageResult
    func decideHOLChanges(_ id: String, allowed: [Bool]) async throws -> HOLMessageResult
    func stopHOLTerminal(_ id: String) async throws -> HOLTerminalState
}

/// Defaults so test fakes only implement what they exercise.
extension AuraTransport {
    public func chat(_ text: String, developer: String?) -> AsyncThrowingStream<AuraEvent, Error> { chat(text) }
    public func settings() async throws -> AppSettings { throw AuraClientError.badResponse }
    public func models() async throws -> ModelCatalog { throw AuraClientError.badResponse }
    public func updateModels(_ update: ModelRoutingUpdate) async throws -> ModelCatalog { throw AuraClientError.badResponse }
    public func updateCodingEngine(_ engine: String) async throws -> ModelCatalog { throw AuraClientError.badResponse }
    public func syncCloudModels() async throws -> ModelCatalog { throw AuraClientError.badResponse }
    public func updateSleepMode(_ mode: SleepMode) async throws -> ModelCatalog { throw AuraClientError.badResponse }
    public func pullLocalModel(_ model: String) async throws -> ModelCatalog { throw AuraClientError.badResponse }
    public func chat(_ text: String, developer: String?, model: ModelChoice?) -> AsyncThrowingStream<AuraEvent, Error> {
        chat(text, developer: developer)
    }
    public func registerVoices(_ voices: [VoiceInfo]) async throws {}
    public func updateVoice(_ patch: VoicePatch) async throws -> VoiceProfile { throw AuraClientError.badResponse }
    public func resetPersona() async throws -> PersonaInfo { throw AuraClientError.badResponse }
    public func voices() async throws -> [VoiceInfo] { [] }
    public func speech(_ text: String, voice: String, speed: Double) async throws -> Data { throw AuraClientError.badResponse }
    public func skills() async throws -> SkillsOverview { SkillsOverview(installed: [], inbox: []) }
    public func agents() async throws -> AgentsOverview { AgentsOverview(installed: [], inbox: []) }
    public func createAgent(_ request: AgentCandidateRequest, imported: Bool) async throws -> AgentInfo { throw AuraClientError.badResponse }
    public func suspendAgent(_ name: String, reason: String) async throws -> AgentInfo { throw AuraClientError.badResponse }
    public func revalidateAgent(_ name: String) async throws -> TaskInfo { throw AuraClientError.badResponse }
    public func retireAgent(_ name: String, reason: String) async throws -> AgentInfo { throw AuraClientError.badResponse }
    public func toggleSkill(_ name: String, enabled: Bool) async throws {}
    public func propose(_ skill: String) async throws -> TaskInfo { throw AuraClientError.badResponse }
    public func proposeAgent(_ name: String) async throws -> TaskInfo { throw AuraClientError.badResponse }
    public func tasks() async throws -> [TaskInfo] { [] }
    public func task(_ id: String) async throws -> TaskInfo { throw AuraClientError.badResponse }
    public func decide(_ id: String, approve: Bool) async throws -> TaskInfo { throw AuraClientError.badResponse }
    public func cancelAudit(_ id: String) async throws -> TaskInfo { throw AuraClientError.badResponse }
    public func status() async throws -> SystemStatus { throw AuraClientError.badResponse }
    public func alerts() async throws -> AlertsInfo { AlertsInfo(alerts: [], continuousAudit: AuditClock()) }
    public func uninstall(_ skill: String) async throws {}
    public func keepFlagged(_ skill: String) async throws {}
    public func auditNow() async throws {}
    public func approve(decisions: [ApprovalDecision]) -> AsyncThrowingStream<AuraEvent, Error> {
        approve(decisions.map(\.approved))
    }
    public func developer() async throws -> DeveloperInfo { throw AuraClientError.badResponse }
    public func checkDeveloper(_ engine: String) async throws -> EngineInfo { throw AuraClientError.badResponse }
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
    public func setChat(_ id: String, _ chat: ChatSwitch) async throws -> ConnectionInfo { throw AuraClientError.badResponse }
    public func messages(after: Int, wait: Int) async throws -> MessagesPage { MessagesPage(messages: [], latest: after) }
    public func stopTurn() async throws -> Bool { false }
    public func conversation() async throws -> ConversationInfo { throw AuraClientError.badResponse }
    public func retryBuild(_ id: String) async throws -> BuildInfo { throw AuraClientError.badResponse }
    public func dismissRecovery(_ id: String) async throws {}
    public func hol() async throws -> HOLOverview { throw AuraClientError.badResponse }
    public func startHOL(_ name: String, request: HOLStart) async throws -> HOLRun { throw AuraClientError.badResponse }
    public func createHOLSavepoint(_ label: String) async throws -> HOLSavepoint { throw AuraClientError.badResponse }
    public func restoreHOLSavepoint(_ id: String) async throws -> HOLSavepoint { throw AuraClientError.badResponse }
    public func restoreHOLOriginal() async throws {}
    public func holTerminal(_ id: String, after: Int) async throws -> HOLTerminalState { throw AuraClientError.badResponse }
    public func sendHOLCommand(_ id: String, command: String) async throws -> HOLTerminalState { throw AuraClientError.badResponse }
    public func sendHOLMessage(_ id: String, message: String) async throws -> HOLMessageResult { throw AuraClientError.badResponse }
    public func decideHOLChanges(_ id: String, allowed: [Bool]) async throws -> HOLMessageResult { throw AuraClientError.badResponse }
    public func stopHOLTerminal(_ id: String) async throws -> HOLTerminalState { throw AuraClientError.badResponse }
}

/// The ids that bind what the user does to what the server is doing: the turn being streamed
/// (for Stop) and the approval card on screen (so an answer can never approve a different request).
final class TurnIDs: @unchecked Sendable {
    private let lock = NSLock()
    private var _turn: String?
    private var _approval: String?

    var turn: String? {
        get { lock.withLock { _turn } }
        set { lock.withLock { _turn = newValue } }
    }

    var approval: String? {
        get { lock.withLock { _approval } }
        set { lock.withLock { _approval = newValue } }
    }
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
    private let ids = TurnIDs()

    public init(port: Int, token: String, session: URLSession = .shared) {
        baseURL = URL(string: "http://127.0.0.1:\(port)")!
        self.token = token
        self.session = session
    }

    public func chat(_ text: String) -> AsyncThrowingStream<AuraEvent, Error> {
        stream("/v1/chat", body: ["text": text])
    }

    public func chat(_ text: String, developer: String?) -> AsyncThrowingStream<AuraEvent, Error> {
        chat(text, developer: developer, model: nil)
    }

    public func chat(_ text: String, developer: String?, model: ModelChoice?) -> AsyncThrowingStream<AuraEvent, Error> {
        struct Body: Encodable, Sendable { let text: String; let developer: String?; let model: ModelChoice? }
        return stream("/v1/chat", body: Body(text: text, developer: developer, model: model))
    }

    struct Answer<A: Encodable & Sendable>: Encodable, Sendable {
        let approvals: A
        let approval_id: String?
    }

    public func approve(_ approvals: [Bool]) -> AsyncThrowingStream<AuraEvent, Error> {
        stream("/v1/approvals", body: Answer(approvals: approvals, approval_id: ids.approval))
    }

    public func approve(decisions: [ApprovalDecision]) -> AsyncThrowingStream<AuraEvent, Error> {
        stream("/v1/approvals", body: Answer(approvals: decisions, approval_id: ids.approval))
    }

    public func stopTurn() async throws -> Bool {
        struct Body: Encodable { let turn_id: String? }
        struct Reply: Decodable { let stopped: Bool }
        guard let turn = ids.turn else { return false }
        var r = request("/v1/stop", body: Body(turn_id: turn))
        r.timeoutInterval = 45  // the server waits for the turn's cleanup (up to 30 s)
        return try Self.decoder.decode(Reply.self, from: await send(r)).stopped
    }

    public func conversation() async throws -> ConversationInfo {
        let info = try Self.decoder.decode(ConversationInfo.self, from: await send(request("/v1/conversation")))
        ids.approval = info.pending?.approvalId
        return info
    }

    public func retryBuild(_ id: String) async throws -> BuildInfo {
        try Self.decoder.decode(BuildInfo.self, from: await send(request("/v1/builds/\(id)/retry", body: [String: String]())))
    }

    public func dismissRecovery(_ id: String) async throws {
        _ = try await send(request("/v1/recovery/\(id)/dismiss", body: [String: String]()))
    }

    public func hol() async throws -> HOLOverview {
        try Self.decoder.decode(HOLOverview.self, from: await send(request("/v1/hol")))
    }

    public func startHOL(_ name: String, request body: HOLStart) async throws -> HOLRun {
        try Self.decoder.decode(HOLRun.self, from: await send(request("/v1/hol/\(name)/start", body: body)))
    }

    public func createHOLSavepoint(_ label: String) async throws -> HOLSavepoint {
        struct Body: Encodable { let label: String }
        return try Self.decoder.decode(HOLSavepoint.self, from: await send(request("/v1/hol/savepoints", body: Body(label: label))))
    }

    public func restoreHOLSavepoint(_ id: String) async throws -> HOLSavepoint {
        try Self.decoder.decode(HOLSavepoint.self, from: await send(request("/v1/hol/savepoints/\(id)/restore", body: [String: String]())))
    }

    public func restoreHOLOriginal() async throws {
        _ = try await send(request("/v1/hol/restore-original", body: ["confirmation": "RESTORE"]))
    }

    public func holTerminal(_ id: String, after: Int) async throws -> HOLTerminalState {
        var r = request("/v1/hol/terminals/\(id)")
        r.url = r.url?.appending(queryItems: [URLQueryItem(name: "after", value: String(after))])
        return try Self.decoder.decode(HOLTerminalState.self, from: await send(r))
    }

    public func sendHOLCommand(_ id: String, command: String) async throws -> HOLTerminalState {
        try Self.decoder.decode(HOLTerminalState.self, from: await send(request("/v1/hol/terminals/\(id)/command", body: ["command": command])))
    }

    public func sendHOLMessage(_ id: String, message: String) async throws -> HOLMessageResult {
        try Self.decoder.decode(HOLMessageResult.self, from: await send(request("/v1/hol/terminals/\(id)/message", body: ["message": message])))
    }

    public func decideHOLChanges(_ id: String, allowed: [Bool]) async throws -> HOLMessageResult {
        try Self.decoder.decode(HOLMessageResult.self, from: await send(request("/v1/hol/terminals/\(id)/decide", body: ["allowed": allowed])))
    }

    public func stopHOLTerminal(_ id: String) async throws -> HOLTerminalState {
        try Self.decoder.decode(HOLTerminalState.self, from: await send(request("/v1/hol/terminals/\(id)/stop", body: [String: String]())))
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

    public func models() async throws -> ModelCatalog {
        try Self.decoder.decode(ModelCatalog.self, from: await send(request("/v1/models")))
    }

    public func updateModels(_ update: ModelRoutingUpdate) async throws -> ModelCatalog {
        var r = request("/v1/models", body: update)
        r.httpMethod = "PUT"
        return try Self.decoder.decode(ModelCatalog.self, from: await send(r))
    }

    public func updateCodingEngine(_ engine: String) async throws -> ModelCatalog {
        struct Body: Encodable { let engine: String }
        var r = request("/v1/models/coding-engine", body: Body(engine: engine))
        r.httpMethod = "PUT"
        return try Self.decoder.decode(ModelCatalog.self, from: await send(r))
    }

    public func syncCloudModels() async throws -> ModelCatalog {
        try Self.decoder.decode(ModelCatalog.self, from: await send(request("/v1/models/sync-cloud", body: [String: String]())))
    }

    public func updateSleepMode(_ mode: SleepMode) async throws -> ModelCatalog {
        var r = request("/v1/models/sleep", body: mode)
        r.httpMethod = "PUT"
        return try Self.decoder.decode(ModelCatalog.self, from: await send(r))
    }

    public func pullLocalModel(_ model: String) async throws -> ModelCatalog {
        struct Body: Encodable { let model: String }
        var r = request("/v1/models/pull", body: Body(model: model))
        r.timeoutInterval = 3_600  // a first local model download can be large
        return try Self.decoder.decode(ModelCatalog.self, from: await send(r))
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

    public func agents() async throws -> AgentsOverview {
        try Self.decoder.decode(AgentsOverview.self, from: await send(request("/v1/agents")))
    }

    public func createAgent(_ candidate: AgentCandidateRequest, imported: Bool) async throws -> AgentInfo {
        let route = imported ? "/v1/agents/imports" : "/v1/agents/candidates"
        return try Self.decoder.decode(AgentInfo.self, from: await send(request(route, body: candidate)))
    }

    public func suspendAgent(_ name: String, reason: String) async throws -> AgentInfo {
        try Self.decoder.decode(AgentInfo.self, from: await send(request("/v1/agents/\(name)/suspend", body: ["reason": reason])))
    }

    public func revalidateAgent(_ name: String) async throws -> TaskInfo {
        try Self.decoder.decode(TaskInfo.self, from: await send(request("/v1/agents/\(name)/revalidate", body: [String: String]())))
    }

    public func retireAgent(_ name: String, reason: String) async throws -> AgentInfo {
        try Self.decoder.decode(AgentInfo.self, from: await send(request("/v1/agents/\(name)/retire", body: ["reason": reason])))
    }

    public func toggleSkill(_ name: String, enabled: Bool) async throws {
        var r = request("/v1/skills/\(name)", body: ["enabled": enabled])
        r.httpMethod = "PATCH"
        _ = try await send(r)
    }

    public func propose(_ skill: String) async throws -> TaskInfo {
        try Self.decoder.decode(TaskInfo.self, from: await send(request("/v1/tasks", body: ["skill": skill])))
    }

    public func proposeAgent(_ name: String) async throws -> TaskInfo {
        try Self.decoder.decode(TaskInfo.self, from: await send(request("/v1/agents/\(name)/audit", body: [String: String]())))
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

    public func cancelAudit(_ id: String) async throws -> TaskInfo {
        try Self.decoder.decode(TaskInfo.self, from: await send(request("/v1/tasks/\(id)/cancel", body: [String: String]())))
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

    public func checkDeveloper(_ engine: String) async throws -> EngineInfo {
        var r = request("/v1/developer/\(engine)/check", body: [String: String]())
        r.timeoutInterval = 120  // runs a single answer through the isolated Developer container
        return try Self.decoder.decode(EngineInfo.self, from: await send(r))
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

    public func setChat(_ id: String, _ chat: ChatSwitch) async throws -> ConnectionInfo {
        var r = request("/v1/connections/\(id)/chat", body: chat)
        r.httpMethod = "PUT"
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
        let ids = ids
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
                        if event == .thinking { ids.turn = parser.turnID }
                        if case .approval = event { ids.approval = parser.approvalID }
                        if event == .done { ids.turn = nil }
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
