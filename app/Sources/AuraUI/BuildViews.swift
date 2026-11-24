import AuraKit
import SwiftUI

// MARK: - the Developer's builds in the Workshop (Sprint 6)

public extension EnvironmentValues {
    /// Set by snapshot tests: offscreen rendering draws nothing inside scroll views, so their
    /// content is laid out inline instead.
    @Entry var rendersOffscreen = false
}

/// A scroll view, or (offscreen) just its content.
struct Scrolling<Content: View>: View {
    var maxHeight: CGFloat?
    @ViewBuilder let content: Content
    @Environment(\.rendersOffscreen) private var offscreen

    var body: some View {
        if offscreen { content } else { ScrollView { content }.frame(maxHeight: maxHeight) }
    }
}

/// A build in the Workshop's sidebar.
struct BuildRow: View {
    let build: BuildInfo

    var body: some View {
        HStack {
            Image(systemName: build.improve ? "wand.and.stars" : "wrench.and.screwdriver")
                .foregroundStyle(.secondary).frame(width: 18)
            VStack(alignment: .leading, spacing: 2) {
                Text(build.skill).font(.body.weight(.medium))
                Text("\(EngineInfo.named(build.engine)?.label ?? build.engine) · \(build.stageLabel)")
                    .font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer()
            if build.isRunning { ProgressView().controlSize(.small) }
            else { BuildStageIcon(stage: build.stage) }
        }
    }
}

struct BuildStageIcon: View {
    let stage: String

    var body: some View {
        Image(systemName: icon).foregroundStyle(color)
    }

    private var icon: String {
        switch stage {
        case "ready": "checkmark.seal.fill"
        case "deployed": "checkmark.circle.fill"
        case "rejected", "stopped": "minus.circle"
        case "interrupted": "arrow.clockwise.circle"
        default: "xmark.octagon.fill"
        }
    }

    private var color: Color {
        switch stage {
        case "ready": .accentColor
        case "deployed": .green
        case "rejected", "stopped": .secondary
        case "interrupted": .orange
        default: .red
        }
    }
}

/// One build, live: the Developer's actions as they happen, each round's audit, and the result.
struct BuildView: View {
    let build: BuildInfo
    let onStop: () -> Void
    let onReview: () -> Void

    private var engine: EngineInfo? { EngineInfo.named(build.engine) }

    static func title(_ build: BuildInfo) -> String {
        if build.isRunning { return build.improve ? "Improving \(build.skill)" : "Building \(build.skill)" }
        if ["ready", "deployed", "rejected"].contains(build.stage) {
            return build.improve ? "Improved \(build.skill)" : "Built \(build.skill)"
        }
        return build.improve ? "Improvement of \(build.skill)" : "Build of \(build.skill)"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()
            Scrolling {
                VStack(alignment: .leading, spacing: 14) {
                    facts
                    feed
                    if !build.history.isEmpty { rounds }
                    Card {
                        DisclosureGroup("Show the specification (all \(engine?.label ?? "the Developer") received besides the format guide and an example)") {
                            SpecificationText(text: build.specification).padding(.top, 6)
                        }
                        .font(.callout)
                    } title: {
                        Label("Specification", systemImage: "doc.text").font(.headline)
                    }
                }
                .padding(20)
            }
            Divider()
            bottomBar
        }
    }

    private var header: some View {
        HStack(alignment: .center, spacing: 12) {
            Image(systemName: build.improve ? "wand.and.stars" : "wrench.and.screwdriver.fill")
                .font(.title2).foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 2) {
                Text(BuildView.title(build)).font(.title3.weight(.semibold))
                Text(build.message).font(.callout).foregroundStyle(.secondary).lineLimit(2)
            }
            Spacer()
            if build.isRunning { ProgressView().controlSize(.small) }
            else { BuildStageIcon(stage: build.stage).font(.title2) }
        }
        .padding(20)
    }

    private var facts: some View {
        HStack(spacing: 24) {
            fact("Developer", engine?.label ?? build.engine)
            fact("Round", "\(max(build.round, 1)) of \(build.maxRounds)")
            fact("Cost", build.costLabel)
            fact("Sent to", engine?.vendor ?? "the vendor")
        }
    }

    private func fact(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.callout.weight(.medium))
        }
    }

    private var feed: some View {
        Card {
            ScrollViewReader { proxy in
                Scrolling(maxHeight: 300) {
                    VStack(alignment: .leading, spacing: 3) {
                        if build.feed.isEmpty {
                            Text("Waiting for the Developer…").foregroundStyle(.secondary)
                        }
                        ForEach(Array(build.feed.enumerated()), id: \.offset) { i, line in
                            HStack(alignment: .firstTextBaseline, spacing: 8) {
                                Text(line.time.map { $0.formatted(date: .omitted, time: .standard) } ?? "")
                                    .foregroundStyle(.tertiary)
                                Text(line.text).textSelection(.enabled)
                                    .foregroundStyle(line.text.hasPrefix("“") ? Color.secondary : Color.primary)
                            }
                            .id(i)
                        }
                    }
                    .font(.system(.caption, design: .monospaced))
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(minHeight: 160, alignment: .top)
                .onChange(of: build.feed.count, initial: true) { _, count in
                    if count > 0 { proxy.scrollTo(count - 1, anchor: .bottom) }
                }
            }
        } title: {
            HStack {
                Label("Live feed", systemImage: "dot.radiowaves.left.and.right").font(.headline)
                Spacer()
                Text("Isolated container · sees only this build's workspace · reaches only \(engine?.vendor ?? "its vendor")")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private var rounds: some View {
        Card {
            VStack(alignment: .leading, spacing: 8) {
                ForEach(build.history, id: \.self) { r in
                    VStack(alignment: .leading, spacing: 3) {
                        HStack {
                            Image(systemName: r.passed ? "checkmark.circle.fill" : "arrow.uturn.backward.circle.fill")
                                .foregroundStyle(r.passed ? .green : .orange)
                            Text(r.passed ? "Round \(r.round): passed every check" : "Round \(r.round): didn't pass, wiped and sent back")
                                .font(.callout.weight(.medium))
                            if let risk = r.risk, r.passed { RiskBadge(risk: risk, compact: true) }
                        }
                        ForEach(r.findings, id: \.self) { f in
                            Text(f).font(.caption.monospaced()).foregroundStyle(.secondary)
                                .lineLimit(4).textSelection(.enabled).padding(.leading, 24)
                        }
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } title: {
            Label("Security Governor, per round", systemImage: "checkmark.shield").font(.headline)
        }
    }

    @ViewBuilder private var bottomBar: some View {
        HStack {
            switch build.stage {
            case _ where build.isRunning:
                Text("Nothing is installed: a passing build goes to Code Review first.")
                    .font(.callout).foregroundStyle(.secondary)
                Spacer()
                Button("Stop", role: .destructive, action: onStop)
            case "ready":
                Text("Passed every check. Review the code before anything is installed.")
                    .font(.callout).foregroundStyle(.secondary)
                Spacer()
                Button("Review the Audit Card", action: onReview).buttonStyle(.borderedProminent)
            case "deployed", "rejected":
                Text(build.stage == "deployed" ? "You approved and deployed it." : "You rejected it. Nothing was installed.")
                    .font(.callout).foregroundStyle(.secondary)
                Spacer()
                Button("Open the Audit Card", action: onReview)
            default:
                Label(build.message, systemImage: "info.circle").font(.callout).foregroundStyle(.secondary)
                Spacer()
            }
        }
        .padding(16)
    }
}

/// On a built skill's Audit Card: who wrote it, how many rounds it took, what it cost, and what it
/// was asked to do.
struct BuiltByCard: View {
    let origin: TaskOrigin

    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: 6) {
                Text("\(origin.engine), round \(origin.round)" + (origin.costUsd > 0 ? String(format: " · $%.2f", origin.costUsd) : ""))
                    .font(.callout)
                Text("A zero-trust Developer: written in an isolated container, then audited like any other skill.")
                    .font(.caption).foregroundStyle(.secondary)
                DisclosureGroup("Specification") {
                    SpecificationText(text: origin.specification).padding(.top, 6)
                }
                .font(.callout)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } title: {
            Label("Built by", systemImage: "wrench.and.screwdriver").font(.headline)
        }
    }
}

struct SpecificationText: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.system(.caption, design: .monospaced))
            .textSelection(.enabled)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(8)
            .background(Color.primary.opacity(0.04), in: .rect(cornerRadius: 8))
    }
}

// MARK: - New Skill / Improve

/// What the New Skill sheet opens for: a new skill, or an installed one to improve.
struct BuildSheetRequest: Identifiable {
    let improve: String?
    var id: String { improve ?? "new" }
}

/// The Workshop's way to commission a skill. Clicking Build, with the specification, the
/// Developer, the limits and exactly what leaves this Mac in view, is the approval.
struct NewBuildSheet: View {
    let store: WorkshopStore
    let improve: String?
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var engine = "claude"
    @State private var spec = Specification.template
    @State private var starting = false

    private var chosen: EngineInfo? { store.engines.first { $0.name == engine } }
    private var problems: [String] {
        var out = Specification.problems(spec)
        if improve == nil, !Specification.validName(name) {
            out.insert("the name must be lowercase letters, digits and underscores (2–40, starting with a letter)", at: 0)
        }
        if improve == nil, store.skills.contains(where: { $0.name == name }) {
            out.insert("'\(name)' is already installed: improve it instead", at: 0)
        }
        return out
    }
    private var canBuild: Bool { problems.isEmpty && chosen?.problem == nil && !starting }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(improve.map { "Improve \($0)" } ?? "New Skill").font(.title2.weight(.semibold))
            if improve == nil {
                LabeledContent("Name") {
                    TextField("e.g. pdf_text", text: $name).textFieldStyle(.roundedBorder).frame(maxWidth: 260)
                }
            }
            LabeledContent("Developer") {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(store.engines) { e in EngineChoice(engine: e, selected: engine == e.name) { engine = e.name } }
                }
            }
            VStack(alignment: .leading, spacing: 4) {
                Text("Technical Specification").font(.headline)
                Text("Six sections: \(Specification.sections.joined(separator: ", ")). It's all the Developer is told about the task.")
                    .font(.caption).foregroundStyle(.secondary)
                TextEditor(text: $spec)
                    .font(.system(.callout, design: .monospaced))
                    .frame(minHeight: 240)
                    .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(Color.secondary.opacity(0.3)))
            }
            disclosure
            ForEach(problems, id: \.self) { p in
                Label(p.prefix(1).uppercased() + p.dropFirst(), systemImage: "exclamationmark.triangle")
                    .font(.caption).foregroundStyle(.orange)
            }
            if let error = store.buildError {
                Label("Not started: \(error)", systemImage: "xmark.octagon").font(.callout).foregroundStyle(.red)
            }
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                Button(improve == nil ? "Build" : "Build Improvement") { build() }
                    .disabled(!canBuild)  // deliberately not the default button: Return doesn't build
            }
        }
        .padding(20)
        .frame(width: 620)
        .onAppear {
            store.clearBuildError()
            if let ready = store.engines.first(where: { $0.problem == nil }) { engine = ready.name }
        }
    }

    /// Exactly what leaves this Mac, to whom, and the limits.
    private var disclosure: some View {
        VStack(alignment: .leading, spacing: 4) {
            if let e = chosen, let d = store.developer {
                Label("Sent to \(e.vendor): this specification, the skill format guide and an example skill"
                      + (improve == nil ? "" : ", and the skill's current files") + ". Nothing from your vault or a conversation.",
                      systemImage: "arrow.up.forward.circle")
                Label("Limits: \(d.limits(for: e)). \(e.costNote)", systemImage: "gauge.with.dots.needle.33percent")
            }
            Label("The result must pass the Security Governor, and you review it before anything is installed.",
                  systemImage: "checkmark.shield")
        }
        .font(.caption).foregroundStyle(.secondary)
        .fixedSize(horizontal: false, vertical: true)
    }

    private func build() {
        starting = true
        let request = BuildRequest(skill: improve ?? name, specification: spec, engine: engine, improve: improve != nil)
        Task {
            if await store.startBuild(request) { dismiss() }
            starting = false
        }
    }
}

struct EngineChoice: View {
    let engine: EngineInfo
    let selected: Bool
    let onSelect: () -> Void

    var body: some View {
        Button(action: onSelect) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Image(systemName: selected ? "largecircle.fill.circle" : "circle")
                    .foregroundStyle(selected ? Color.accentColor : .secondary)
                VStack(alignment: .leading, spacing: 1) {
                    Text("\(engine.label) (\(engine.vendor))")
                    if let problem = engine.problem {
                        Text(problem).font(.caption).foregroundStyle(.orange)
                    }
                }
            }
        }
        .buttonStyle(.plain)
        .disabled(engine.problem != nil)
    }
}
