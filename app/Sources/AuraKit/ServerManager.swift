import Foundation

/// Launches and watches the Python Aura Server (`uv run aura-server`).
///
/// The per-launch token goes to the server on stdin, never in arguments or the environment
/// (which other processes can read). The stdin pipe stays open: when this process quits or
/// crashes the pipe closes, and the server shuts itself down.
@MainActor
public final class ServerManager {
    public enum State: Equatable, Sendable {
        case stopped
        case starting
        case running(port: Int)
        case failed(String)
    }

    public private(set) var state: State = .stopped {
        didSet { onStateChange?(state) }
    }
    /// Called on every state change, including automatic restarts (which get a new port).
    public var onStateChange: ((State) -> Void)?
    public let token: String

    private let projectDir: URL
    private let uv: URL
    private var process: Process?
    private var stdin: FileHandle?
    private var stopping = false
    private var restarts = 0

    public init(projectDir: URL) throws {
        self.projectDir = projectDir
        uv = try Self.findUV()
        token = Self.makeToken()
    }

    /// Start the server and return its port once it is listening.
    @discardableResult
    public func start() async throws -> Int {
        stopping = false
        state = .starting
        do {
            let port = try await launch()
            state = .running(port: port)
            return port
        } catch {
            state = .failed(error.localizedDescription)
            throw error
        }
    }

    /// For app termination: tell the server to exit (stdin EOF) without waiting for it.
    public func shutDownWithoutWaiting() {
        stopping = true
        try? stdin?.close()
        stdin = nil
    }

    /// Ask the server to exit (close its stdin), then make sure it has. Returns its exit status.
    @discardableResult
    public func stop() async -> Int32? {
        stopping = true
        try? stdin?.close()
        stdin = nil
        guard let process else { state = .stopped; return nil }
        for _ in 0..<50 where process.isRunning {  // up to 5 s for a graceful exit
            try? await Task.sleep(for: .milliseconds(100))
        }
        if process.isRunning { process.terminate() }
        process.waitUntilExit()
        self.process = nil
        state = .stopped
        return process.terminationStatus
    }

    // MARK: - launching

    private func launch() async throws -> Int {
        let p = Process()
        p.executableURL = uv
        p.arguments = ["run", "--quiet", "--project", projectDir.path, "aura-server"]
        p.currentDirectoryURL = projectDir
        p.environment = Self.environment()
        let input = Pipe(), output = Pipe()
        p.standardInput = input
        p.standardOutput = output
        p.standardError = try Self.stderrLog()
        p.terminationHandler = { [weak self] proc in
            let status = proc.terminationStatus
            Task { @MainActor in self?.exited(proc, status: status) }
        }
        try p.run()
        process = p
        stdin = input.fileHandleForWriting
        input.fileHandleForWriting.write(Data((token + "\n").utf8))

        return try await withThrowingTaskGroup(of: Int.self) { group in
            group.addTask {
                for try await line in output.fileHandleForReading.bytes.lines {
                    if line.hasPrefix("AURA_READY port="), let port = Int(line.dropFirst(16)) {
                        return port
                    }
                }
                throw ServerError.exitedEarly(Self.logPath.path)
            }
            group.addTask {
                try await Task.sleep(for: .seconds(120))  // first run may install dependencies
                throw ServerError.timedOut
            }
            let port = try await group.next()!
            group.cancelAll()
            return port
        }
    }

    private func exited(_ proc: Process, status: Int32) {
        guard proc === process, !stopping else { return }
        process = nil
        stdin = nil
        restarts += 1
        let delay = min(30.0, pow(2.0, Double(restarts - 1)))  // 1, 2, 4 … 30 s
        state = .failed("Aura Server stopped (status \(status)); restarting in \(Int(delay)) s.")
        Task { @MainActor in
            try? await Task.sleep(for: .seconds(delay))
            guard !stopping else { return }
            if (try? await start()) != nil {
                try? await Task.sleep(for: .seconds(60))
                if case .running = state { restarts = 0 }  // healthy for a minute
            }
        }
    }

    // MARK: - environment

    public enum ServerError: Error, LocalizedError {
        case uvNotFound, exitedEarly(String), timedOut
        public var errorDescription: String? {
            switch self {
            case .uvNotFound: "Couldn't find `uv`; it is needed to run the Aura Server."
            case let .exitedEarly(log): "The Aura Server exited during startup. See \(log)."
            case .timedOut: "The Aura Server didn't start within 2 minutes."
            }
        }
    }

    nonisolated static let home = FileManager.default.homeDirectoryForCurrentUser
    nonisolated static let logPath = home.appending(path: "Library/Logs/Aura/server-stderr.log")

    nonisolated static func findUV() throws -> URL {
        let candidates = [".local/bin/uv", ".cargo/bin/uv"].map { home.appending(path: $0).path }
            + ["/opt/homebrew/bin/uv", "/usr/local/bin/uv"]
        guard let path = candidates.first(where: { FileManager.default.isExecutableFile(atPath: $0) }) else {
            throw ServerError.uvNotFound
        }
        return URL(fileURLWithPath: path)
    }

    /// Apps launched from Finder get a minimal PATH; add where uv, docker and ollama live.
    nonisolated static func environment() -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        env.removeValue(forKey: "AURA_SERVER_TOKEN")
        let extra = [".local/bin", ".docker/bin"].map { home.appending(path: $0).path }
            + ["/opt/homebrew/bin", "/usr/local/bin"]
        env["PATH"] = (extra + [env["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin"]).joined(separator: ":")
        return env
    }

    nonisolated static func stderrLog() throws -> FileHandle {
        try FileManager.default.createDirectory(
            at: logPath.deletingLastPathComponent(), withIntermediateDirectories: true)
        if !FileManager.default.fileExists(atPath: logPath.path) {
            FileManager.default.createFile(atPath: logPath.path, contents: nil)
        }
        let handle = try FileHandle(forWritingTo: logPath)
        handle.seekToEndOfFile()
        return handle
    }

    /// 256 random bits, hex-encoded. SystemRandomNumberGenerator is cryptographically secure.
    nonisolated static func makeToken() -> String {
        var rng = SystemRandomNumberGenerator()
        return (0..<32).map { _ in String(format: "%02x", UInt8.random(in: .min ... .max, using: &rng)) }
            .joined()
    }
}
