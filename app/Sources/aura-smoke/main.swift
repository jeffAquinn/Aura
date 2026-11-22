// aura-smoke: exercise the real client-server loop from the command line.
//
//   aura-smoke [--approve] "message" ["message" ...]
//
// Launches the Aura Server through ServerManager (exactly as the app does), checks auth,
// streams each message through AuraClient with per-event timings, answers approval cards
// through ChatStore (Deny unless --approve), then shuts the server down.

import AVFoundation
import AuraKit
import Foundation

@MainActor
func smoke() async throws {
    let args = Array(CommandLine.arguments.dropFirst())
    let approve = args.contains("--approve")
    let messages = args.filter { !$0.hasPrefix("--") }
    let project = URL(fileURLWithPath: ProcessInfo.processInfo.environment["AURA_PROJECT_DIR"]
        ?? FileManager.default.currentDirectoryPath)
    let start = Date()
    func log(_ line: String) {
        let ms = Int(Date().timeIntervalSince(start) * 1000)
        print("\(String(repeating: " ", count: max(0, 6 - String(ms).count)))\(ms) ms  \(line)")
    }

    let server = try ServerManager(projectDir: project)
    let port = try await server.start()
    log("server ready on 127.0.0.1:\(port)")

    let client = AuraClient(port: port, token: server.token)
    let health = try await client.health()
    log("health: model=\(health.model) loaded=\(health.modelLoaded.map(String.init) ?? "?") search=\(health.searchMode)")
    do {
        _ = try await AuraClient(port: port, token: String(repeating: "0", count: 64)).health()
        log("!! a wrong token was accepted")
    } catch {
        log("wrong token refused: \(error.localizedDescription)")
    }

    let store = ChatStore()
    store.transport = client
    for message in messages {
        log("you › \(message)")
        var turnStart = Date(), sawText = false
        // Stream the turn event by event (timings), through the same client the app uses.
        func show(_ events: AsyncThrowingStream<AuraEvent, Error>) async throws -> [PendingAction]? {
            var pending: [PendingAction]?
            for try await event in events {
                let ms = Int(Date().timeIntervalSince(turnStart) * 1000)
                switch event {
                case .text(let t) where sawText: _ = t
                case .text(let t): sawText = true; log("  +\(ms) ms  first text: \(t.debugDescription)")
                case .approval(let actions): pending = actions; log("  +\(ms) ms  approval: \(actions.map(\.description))")
                default: log("  +\(ms) ms  \(event)")
                }
            }
            return pending
        }
        if let actions = try await show(client.chat(message)) {
            // Answer through ChatStore, the way the panel's approval card does.
            store.transport = client
            let decision = Array(repeating: approve, count: actions.count)
            log("  answering the card: \(approve ? "Allow" : "Deny")")
            turnStart = Date(); sawText = false
            _ = try await show(client.approve(decision))
        }
    }

    // Aura's neural voice through the app's own client: a real sentence, checked, never played.
    let started = Date()
    if let wav = try? await client.speech("Good evening. This is Heart, one of Aura's voices.", voice: "kokoro:af_heart", speed: 1.0),
       let player = try? AVAudioPlayer(data: wav) {
        log("neural voice: \(String(format: "%.1f", player.duration)) s of audio in \(Int(Date().timeIntervalSince(started) * 1000)) ms (\(wav.count / 1024) KB WAV)")
    } else {
        log("!! neural voice failed")
    }

    // ChatStore end to end against the real server: one turn, then its transcript.
    log("ChatStore turn:")
    store.send("In one short sentence, what can you do?")
    await store.turnTask?.value
    for item in store.items { log("  [\(item.role)] \(item.text.prefix(140))") }

    let status = await server.stop()
    log("server stopped (exit status \(status.map(String.init) ?? "?"))")
}

do {
    try await smoke()
} catch {
    print("smoke failed: \(error.localizedDescription)")
    exit(1)
}
