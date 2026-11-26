import Combine
import Darwin
import Foundation

/// Small, local-only process telemetry for the Chat header. macOS exposes CPU time for this
/// process, but has no supported public API for per-process GPU utilisation, so the UI keeps that
/// distinction explicit rather than presenting a made-up GPU number.
@MainActor
final class AuraRuntimeUsage: ObservableObject {
    @Published private(set) var cpuPercent = 0.0
    @Published private(set) var storageBytes: Int64 = 0

    private var timer: Timer?
    private var previousCPUSeconds: Double?
    private var previousSample = Date()
    private var lastStorageSample = Date.distantPast

    func start() {
        guard timer == nil else { return }
        sample()
        timer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in self?.sample() }
        }
    }

    var cpuText: String { String(format: "%.1f%%", cpuPercent) }
    var storageText: String { ByteCountFormatter.string(fromByteCount: storageBytes, countStyle: .file) }

    private func sample() {
        let now = Date()
        if let cpu = cpuSeconds(), let previous = previousCPUSeconds {
            let elapsed = now.timeIntervalSince(previousSample)
            if elapsed > 0 { cpuPercent = max(0, (cpu - previous) / elapsed * 100) }
            previousCPUSeconds = cpu
        } else {
            previousCPUSeconds = cpuSeconds()
        }
        previousSample = now

        // This is deliberately infrequent: storage is useful context, not a reason to wake the
        // disk or repeatedly traverse Aura's data folders while someone is typing.
        if now.timeIntervalSince(lastStorageSample) >= 30 {
            storageBytes = AuraRuntimeUsage.storageBytes()
            lastStorageSample = now
        }
    }

    private func cpuSeconds() -> Double? {
        var usage = rusage()
        guard getrusage(RUSAGE_SELF, &usage) == 0 else { return nil }
        func seconds(_ value: timeval) -> Double {
            Double(value.tv_sec) + Double(value.tv_usec) / 1_000_000
        }
        return seconds(usage.ru_utime) + seconds(usage.ru_stime)
    }

    private static func storageBytes() -> Int64 {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let roots = [
            Bundle.main.bundleURL,
            home.appending(path: "Library/Application Support/Aura", directoryHint: .isDirectory),
            home.appending(path: "Library/Logs/Aura", directoryHint: .isDirectory),
        ]
        let keys: Set<URLResourceKey> = [.isRegularFileKey, .fileAllocatedSizeKey, .totalFileAllocatedSizeKey]
        return roots.reduce(0) { total, root in
            guard let files = FileManager.default.enumerator(at: root, includingPropertiesForKeys: Array(keys),
                                                              options: [.skipsHiddenFiles])
            else { return total }
            var bytes = total
            for case let url as URL in files {
                guard let values = try? url.resourceValues(forKeys: keys), values.isRegularFile == true else { continue }
                bytes += Int64(values.totalFileAllocatedSize ?? values.fileAllocatedSize ?? 0)
            }
            return bytes
        }
    }
}
