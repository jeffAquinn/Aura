import Foundation

/// Follows the server's live feed for as long as `transport` returns one: each frame goes to
/// `apply`, and a dropped connection is retried quietly from the last frame seen, waiting
/// `retry` seconds at first and doubling up to 5. The returned task stops it.
@MainActor
func followFeed(from start: @escaping @MainActor () -> Int, retry: Double,
                transport: @escaping @MainActor () -> (any AuraTransport)?,
                apply: @escaping @MainActor (MirrorFrame) -> Void) -> Task<Void, Never> {
    Task {
        var backoff = retry
        while !Task.isCancelled, let feed = transport() {
            do {
                for try await frame in feed.mirror(after: start()) {
                    apply(frame)
                    backoff = retry
                }
            } catch {}
            if Task.isCancelled { return }
            try? await Task.sleep(for: .seconds(backoff))
            backoff = min(backoff * 2, 5)
        }
    }
}
