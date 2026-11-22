import AppKit
import AuraKit
import AuraUI
import Carbon.HIToolbox
import SwiftUI

@main
struct AuraApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    var body: some Scene { Settings { EmptyView() } }  // menu-bar only: no windows of its own
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private let store = ChatStore()
    private var server: ServerManager?
    private var statusItem: StatusItemController!
    private var panel: AuraPanel!
    private var hotKey: HotKey?
    private var warmTimer: Timer?
    private lazy var workshop = WorkshopStore { [weak self] in self?.store.transport }
    private var workshopWindow: NSWindow?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        let avatar = Bundle.main.image(forResource: "Avatar").map { Image(nsImage: $0) }
        let starfield = Bundle.main.image(forResource: "Starfield").map { Image(nsImage: $0) }
        // Aura's neural voices (Kokoro, via the local server) or macOS voices, chosen per voice.
        let neural = NeuralSpeechEngine { [weak self] in
            guard let transport = self?.store.transport else { return nil }
            return { text, profile in try await transport.speech(text, voice: profile.voice ?? "", speed: profile.speed) }
        }
        store.speech = SpeechController(engine: RoutingSpeechEngine(system: SystemSpeechEngine(), neural: neural))
        let chat = ChatView(store: store, avatar: avatar, backdrop: starfield) { [weak self] in
            self?.restartServer()
        }
        .environment(\.openWorkshop, OpenWorkshopAction { [weak self] taskID in self?.openWorkshop(taskID) })
        panel = AuraPanel(rootView: chat)
        panel.onHide = { [weak self] in self?.panelHidden() }
        hotKey = HotKey(keyCode: UInt32(kVK_Space), modifiers: UInt32(optionKey)) { [weak self] in
            self?.togglePanel()
        }
        AppLog.write(hotKey == nil ? "hotkey ⌥Space: taken by another app" : "hotkey ⌥Space: registered")
        statusItem = StatusItemController(menu: makeMenu()) { [weak self] in self?.togglePanel() }
        startServer()
        // Opening Aura should visibly open it: show the panel once the menu bar icon is placed.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
            guard let self else { return }
            AppLog.write("menu bar icon: \(self.statusItem.diagnostics)")
            self.showPanel()
        }
    }

    /// Opening Aura again while it runs (Finder, Spotlight, `open`) brings the panel back.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        AppLog.write("reopened")
        showPanel()
        return false
    }

    func applicationWillTerminate(_ notification: Notification) {
        server?.shutDownWithoutWaiting()  // the server exits when its stdin closes
    }

    // MARK: - server

    private var projectDir: URL? {
        if let dir = ProcessInfo.processInfo.environment["AURA_PROJECT_DIR"] {
            return URL(fileURLWithPath: dir)
        }
        return (Bundle.main.object(forInfoDictionaryKey: "AuraProjectDir") as? String)
            .map { URL(fileURLWithPath: $0) }
    }

    private func startServer() {
        guard let projectDir else {
            AppLog.write("no AuraProjectDir in Info.plist")
            store.connection = .offline("Aura can't find its files. Please rebuild Aura.")
            return
        }
        do {
            let server = try ServerManager(projectDir: projectDir)
            self.server = server
            server.onStateChange = { [weak self] state in self?.serverChanged(state) }
            launch(server)
        } catch {
            AppLog.write("server manager: \(error)")
            store.connection = .offline(FriendlyMessage.describe(error))
        }
    }

    private func launch(_ server: ServerManager) {
        Task {
            do {
                try await server.start()
            } catch {
                AppLog.write("server start failed: \(error)")
                store.connectionLost(FriendlyMessage.describe(error))
            }
        }
    }

    private func serverChanged(_ state: ServerManager.State) {
        AppLog.write("server: \(state)")
        switch state {
        case .starting:
            store.connection = .starting
        case let .running(port):
            guard let server else { return }
            store.transport = AuraClient(port: port, token: server.token)
            if panel.isVisible { warm() }
            Task {
                // Tell the server which voices this Mac has, then mirror voice + persona settings.
                await store.registerVoices(SystemSpeechEngine.catalog())
                await store.loadSettings()
                AppLog.write("voices registered: \(store.voices.count); speak=\(store.voice.speak); persona=\(store.persona.name)")
            }
        case .failed:  // technical reason is in app.log; the server restarts itself
            store.connectionLost(FriendlyMessage.reconnecting)
        case .stopped:
            store.connectionLost(FriendlyMessage.offline)
        }
    }

    // MARK: - panel + Smart-Warm

    private func togglePanel() {
        panel.isVisible ? panel.hide() : showPanel()
    }

    private func showPanel() {
        panel.show(below: statusItem.buttonFrame)
        store.focusRequests += 1
        store.speech?.warmUp()  // load the voice now, so the first sentence isn't late
        warm()  // the user is about to type: load the model now
        warmTimer?.invalidate()
        warmTimer = Timer.scheduledTimer(withTimeInterval: 600, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.warm() }  // keep it loaded while the panel is open
        }
    }

    private func panelHidden() {
        warmTimer?.invalidate()
        warmTimer = nil
        warm()  // restart the 30-minute countdown from the moment the panel closed
    }

    private func warm() {
        guard let transport = store.transport else { return }
        Task { try? await transport.warmup() }
    }

    // MARK: - menu

    private func makeMenu() -> NSMenu {
        let menu = NSMenu()
        func add(_ title: String, _ action: Selector) {
            let item = NSMenuItem(title: title, action: action, keyEquivalent: "")
            item.target = self
            menu.addItem(item)
        }
        add(hotKey == nil ? "Open Aura" : "Open Aura (⌥Space)", #selector(openPanel))
        add("New Conversation", #selector(newConversation))
        add("Open Workshop…", #selector(openWorkshopFromMenu))
        menu.addItem(.separator())
        add("Open Vault in Finder", #selector(openVault))
        add("Open Audit Log", #selector(openAuditLog))
        add("Restart Aura Server", #selector(restartServer))
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Quit Aura", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        return menu
    }

    @objc private func openPanel() { showPanel() }

    @objc private func openWorkshopFromMenu() { openWorkshop(nil) }

    /// The Workshop: a normal, resizable window (code review needs room), optionally on a task.
    func openWorkshop(_ taskID: String?) {
        if workshopWindow == nil {
            let window = NSWindow(
                contentRect: NSRect(x: 0, y: 0, width: 1040, height: 720),
                styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                backing: .buffered, defer: false)
            window.title = "Aura Workshop"
            window.isReleasedWhenClosed = false
            window.contentView = NSHostingView(rootView: WorkshopView(store: workshop))
            window.setFrameAutosaveName("AuraWorkshop")
            if window.frame.origin == .zero { window.center() }
            workshopWindow = window
        }
        if let taskID { workshop.selectedTaskID = taskID }
        NSApp.activate()
        workshopWindow?.makeKeyAndOrderFront(nil)
        Task { await workshop.refresh() }
        AppLog.write("workshop opened\(taskID.map { " on task \($0)" } ?? "")")
    }

    @objc private func newConversation() {
        Task { await store.newConversation() }
    }

    @objc private func openVault() {
        Task {
            let vault = (try? await store.transport?.health())?.vault
                ?? FileManager.default.homeDirectoryForCurrentUser.appending(path: "Documents/Aura_Vault").path
            NSWorkspace.shared.open(URL(fileURLWithPath: vault))
        }
    }

    @objc private func openAuditLog() {
        let log = FileManager.default.homeDirectoryForCurrentUser.appending(path: "Library/Logs/Aura/actions.jsonl")
        NSWorkspace.shared.open(log)
    }

    @objc private func restartServer() {
        guard let server else { return startServer() }
        Task {
            await server.stop()
            launch(server)
        }
    }
}
