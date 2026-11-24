import AppKit
import AuraKit
import AuraUI
import Carbon.HIToolbox
import SwiftUI
import UserNotifications

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
    private var alertTimer: Timer?
    private var menu: NSMenu!
    private var shownAlerts: [String] = []
    private var shownBuilds: [String] = []  // builds ready for review, in the menu
    private var buildCheck: Timer?
    private lazy var workshop = WorkshopStore { [weak self] in self?.store.transport }
    private var workshopWindow: NSWindow?
    private lazy var connections = ConnectionsStore(transport: { [weak self] in self?.store.transport },
                                                    openURL: { NSWorkspace.shared.open($0) })  // the user's browser
    private var connectionsWindow: NSWindow?
    private let feed = MessageFeed()  // live Slack and Discord messages
    private let notifier = Notifier()

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
        .environment(\.openConnections, OpenConnectionsAction { [weak self] id in self?.openConnections(id) })
        panel = AuraPanel(rootView: chat)
        panel.onHide = { [weak self] in self?.panelHidden() }
        hotKey = HotKey(keyCode: UInt32(kVK_Space), modifiers: UInt32(optionKey)) { [weak self] in
            self?.togglePanel()
        }
        AppLog.write(hotKey == nil ? "hotkey ⌥Space: taken by another app" : "hotkey ⌥Space: registered")
        AppLog.write("main menu: \(NSApp.mainMenu?.items.map(\.title) ?? [])")
        menu = makeMenu()
        statusItem = StatusItemController(menu: menu) { [weak self] in self?.togglePanel() }
        workshop.onAlertsChanged = { [weak self] in self?.checkAlerts() }
        store.onBuildStarted = { [weak self] _ in self?.checkAlerts() }
        feed.onMessages = { [weak self] in self?.messagesArrived($0) }
        notifier.onOpen = { [weak self] in self?.showPanel() }
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
            let client = AuraClient(port: port, token: server.token)
            store.transport = client
            feed.start(client)  // a new server: follow its live messages from the start
            if panel.isVisible { warm() }
            alertTimer?.invalidate()  // the Continuous Audit runs in the server; show its alerts here
            alertTimer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in
                MainActor.assumeIsolated { self?.checkAlerts() }
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 15) { [weak self] in self?.checkAlerts() }  // after the startup check
            Task {
                // Tell the server which voices this Mac has, then mirror voice + persona settings.
                await store.registerVoices(SystemSpeechEngine.catalog())
                await store.loadSettings()
                await store.restoreConversation()  // the conversation (and any waiting approval) the server kept
                AppLog.write("voices registered: \(store.voices.count); speak=\(store.voice.speak); persona=\(store.persona.name)")
            }
        case .failed:  // technical reason is in app.log; the server restarts itself
            feed.stop()
            store.connectionLost(FriendlyMessage.reconnecting)
        case .stopped:
            feed.stop()
            store.connectionLost(FriendlyMessage.offline)
        }
    }

    // MARK: - live messages

    /// New Slack or Discord messages: a card in the chat for each. A Mac notification only when
    /// Aura answers the user in a messaging app (the user's choice), unless the panel is in front.
    private func messagesArrived(_ messages: [IncomingMessage]) {
        AppLog.write("live messages: \(messages.count)")  // never their text
        store.receive(messages)
        let answers = messages.filter { $0.kind == "reply" }
        guard !answers.isEmpty, !(panel.isVisible && panel.isKeyWindow) else { return }
        for answer in answers.suffix(3) { notifier.post(answer) }  // a burst doesn't flood the screen
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

    // MARK: - Continuous Audit alerts

    private func checkAlerts() {
        guard let transport = store.transport else { return }
        Task {
            guard let info = try? await transport.alerts() else { return }
            showAlerts(SkillAlert.pending(info.alerts))
            showBuilds(info.builds ?? [])
        }
    }

    /// The Developer's builds: while one runs, check every few seconds; when one is ready, say so
    /// (spoken, if spoken replies are on) and offer it in the menu.
    private func showBuilds(_ builds: [BuildAlert]) {
        buildCheck?.invalidate()
        if builds.contains(where: { BuildInfo.runningStages.contains($0.stage) }) {
            buildCheck = Timer.scheduledTimer(withTimeInterval: 5, repeats: false) { [weak self] _ in
                MainActor.assumeIsolated { self?.checkAlerts() }
            }
        }
        let ready = builds.filter { $0.stage == "ready" }
        guard ready.map(\.id) != shownBuilds else { return }
        for build in ready where !shownBuilds.contains(build.id) {
            AppLog.write("build ready for review: \(build.skill) (\(build.id))")
            store.speech?.announce("The \(build.skill) skill is ready for your review in the Workshop.")
        }
        shownBuilds = ready.map(\.id)
        for item in menu.items where item.tag == Self.buildTag { menu.removeItem(item) }
        guard !ready.isEmpty else { return }
        let at = menu.items.firstIndex { $0.tag != Self.alertTag } ?? 0  // below any security alerts
        let separator = NSMenuItem.separator()
        separator.tag = Self.buildTag
        menu.insertItem(separator, at: at)
        for build in ready.reversed() {
            let item = NSMenuItem(title: build.menuTitle, action: #selector(openBuildFromMenu(_:)), keyEquivalent: "")
            item.target = self
            item.tag = Self.buildTag
            item.representedObject = build.id
            item.toolTip = build.message
            menu.insertItem(item, at: at)
        }
    }

    @objc private func openBuildFromMenu(_ sender: NSMenuItem) {
        openWorkshop(sender.representedObject as? String)
    }

    private func showAlerts(_ pending: [SkillAlert]) {
        let names = pending.map(\.skill)
        guard names != shownAlerts else { return }
        if !names.isEmpty { AppLog.write("continuous audit alert: \(names.joined(separator: ", "))") }
        shownAlerts = names
        statusItem.setAlert(pending.first?.message)
        for item in menu.items where item.tag == Self.alertTag { menu.removeItem(item) }
        guard !pending.isEmpty else { return }
        let separator = NSMenuItem.separator()
        separator.tag = Self.alertTag
        menu.insertItem(separator, at: 0)
        for alert in pending.reversed() {
            let item = NSMenuItem(title: alert.menuTitle, action: #selector(openWorkshopFromMenu), keyEquivalent: "")
            item.target = self
            item.tag = Self.alertTag
            item.toolTip = alert.message
            menu.insertItem(item, at: 0)
        }
    }

    private static let alertTag = 900
    private static let buildTag = 901

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
        add("Connections…", #selector(openConnectionsFromMenu))
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
        if let taskID { workshop.select(taskID) }  // an audit's id, or a build's ("b-…")
        NSApp.activate()
        workshopWindow?.makeKeyAndOrderFront(nil)
        Task { await workshop.refresh() }
        AppLog.write("workshop opened\(taskID.map { " on task \($0)" } ?? "")")
    }

    @objc private func openConnectionsFromMenu() { openConnections(nil) }

    /// Settings → Connections: a normal window, where each account is set up step by step.
    func openConnections(_ id: String?) {
        if connectionsWindow == nil {
            let window = NSWindow(
                contentRect: NSRect(x: 0, y: 0, width: 900, height: 700),
                styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                backing: .buffered, defer: false)
            window.title = "Aura Connections"
            window.isReleasedWhenClosed = false
            window.contentView = NSHostingView(rootView: ConnectionsView(store: connections))
            window.setFrameAutosaveName("AuraConnections")
            if window.frame.origin == .zero { window.center() }
            connectionsWindow = window
        }
        if let id { connections.selectedID = id }
        panel.hide()  // the floating panel would sit on top of it
        NSApp.activate()
        connectionsWindow?.makeKeyAndOrderFront(nil)
        Task { await connections.refresh() }
        AppLog.write("connections opened\(id.map { " on \($0)" } ?? "")")
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
