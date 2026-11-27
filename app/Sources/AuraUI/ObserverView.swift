import AuraKit
import SwiftUI

/// The Observer (docs/observer.md): a live, read-only log of Aura's own activity. Newest at the
/// bottom; it follows new events unless paused. Click a row for its details.
public struct ObserverView: View {
    let store: ObserverStore
    @State private var expanded: Set<Int> = []

    public init(store: ObserverStore) {
        self.store = store
    }

    public var body: some View {
        VStack(spacing: 0) {
            toolbar
            Divider()
            if store.visible.isEmpty {
                ContentUnavailableView("Nothing yet", systemImage: "eye",
                                       description: Text("Aura's activity appears here as it happens: turns, tools and approvals, files, containers and searches."))
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                rows
            }
        }
        .frame(minWidth: 520, minHeight: 360)
    }

    private var toolbar: some View {
        HStack(spacing: 10) {
            Button {
                store.paused ? store.resume() : store.pause()
            } label: {
                Label(store.paused ? "Resume" : "Pause", systemImage: store.paused ? "play.fill" : "pause.fill")
            }
            .help(store.paused ? "Show what arrived while paused, and follow again" : "Hold the view to read; new events wait")
            if store.paused && !store.held.isEmpty {
                Text("\(store.held.count) new").font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Menu {
                ForEach(ObserverEvent.Category.allCases, id: \.self) { kind in
                    Toggle(kind.rawValue, isOn: Binding(
                        get: { store.shown.contains(kind) },
                        set: { on in if on { store.shown.insert(kind) } else { store.shown.remove(kind) } }))
                }
                Divider()
                Button("Show All") { store.shown = Set(ObserverEvent.Category.allCases) }
            } label: {
                Label(store.shown.count == ObserverEvent.Category.allCases.count ? "All kinds" : "\(store.shown.count) kinds",
                      systemImage: "line.3.horizontal.decrease.circle")
            }
            .fixedSize()
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }

    private var rows: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    ForEach(store.visible) { event in
                        ObserverRow(event: event, expanded: expanded.contains(event.id)) {
                            if expanded.contains(event.id) { expanded.remove(event.id) } else { expanded.insert(event.id) }
                        }
                        .id(event.id)
                        Divider().opacity(0.4)
                    }
                }
            }
            .onChange(of: store.visible.last?.id) { _, last in
                if let last, !store.paused { proxy.scrollTo(last, anchor: .bottom) }
            }
            .onAppear { if let last = store.visible.last?.id { proxy.scrollTo(last, anchor: .bottom) } }
        }
    }
}

struct ObserverRow: View {
    let event: ObserverEvent
    let expanded: Bool
    let toggle: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(event.clock).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                    .frame(width: 58, alignment: .leading)
                Label(event.category.rawValue, systemImage: Self.symbol(event.category))
                    .font(.caption.weight(.medium))
                    .foregroundStyle(Self.color(event.category))
                    .frame(width: 112, alignment: .leading)
                Text(event.title).font(.callout).lineLimit(expanded ? nil : 1).textSelection(.enabled)
                Spacer(minLength: 0)
                if !event.details.isEmpty {
                    Image(systemName: expanded ? "chevron.down" : "chevron.right").font(.caption2).foregroundStyle(.tertiary)
                }
            }
            if expanded {
                ForEach(Array(event.details.enumerated()), id: \.offset) { _, field in
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text(field.key).font(.caption.monospaced()).foregroundStyle(.secondary).frame(width: 120, alignment: .trailing)
                        Text(field.value).font(.caption.monospaced()).textSelection(.enabled)
                    }
                }
                .padding(.leading, 66)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
        .contentShape(Rectangle())
        .onTapGesture(perform: toggle)
        .accessibilityElement(children: .combine)
    }

    static func symbol(_ kind: ObserverEvent.Category) -> String {
        switch kind {
        case .turns: "bubble.left.and.bubble.right"
        case .actions: "hammer"
        case .vault: "folder"
        case .containers: "shippingbox"
        case .egress: "network"
        case .connections: "link"
        case .alerts: "exclamationmark.triangle"
        case .other: "circle"
        }
    }

    static func color(_ kind: ObserverEvent.Category) -> Color {
        switch kind {
        case .turns: .blue
        case .actions: .green
        case .vault: .purple
        case .containers: .teal
        case .egress: .orange
        case .connections: .indigo
        case .alerts: .yellow
        case .other: .secondary
        }
    }
}
