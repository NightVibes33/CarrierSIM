import Foundation
import AVFAudio
import Network

@MainActor
final class KeepAlive: NSObject {
    private let engine = AVAudioEngine()
    private let player = AVAudioPlayerNode()
    private var running = false

    func start() {
        guard !running else { return }
        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playback, mode: .default, options: [.mixWithOthers])
            try session.setActive(true)

            if player.engine == nil { engine.attach(player) }
            let format = engine.outputNode.inputFormat(forBus: 0)
            guard format.sampleRate > 0, format.channelCount > 0 else { return }
            engine.connect(player, to: engine.mainMixerNode, format: format)

            let frames = max(1024, AVAudioFrameCount(format.sampleRate))
            guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames) else { return }
            buffer.frameLength = frames
            if let channels = buffer.floatChannelData {
                for channel in 0..<Int(format.channelCount) {
                    memset(channels[channel], 0, Int(frames) * MemoryLayout<Float>.size)
                }
            }
            if !engine.isRunning { try engine.start() }
            player.scheduleBuffer(buffer, at: nil, options: .loops)
            player.play()
            running = true
        } catch {
            running = false
        }
    }

    func stop() {
        guard running else { return }
        running = false
        player.stop()
        if engine.isRunning { engine.stop() }
        if player.engine != nil { engine.detach(player) }
        try? AVAudioSession.sharedInstance().setActive(false, options: [.notifyOthersOnDeactivation])
    }
}

@MainActor
final class LocalNetworkAuthorization {
    private var browser: NWBrowser?
    private var listener: NWListener?
    private var continuation: CheckedContinuation<Bool, Never>?

    func request(timeout: TimeInterval = 1.5) async -> Bool {
        await withCheckedContinuation { continuation in
            self.continuation = continuation

            let parameters = NWParameters.tcp
            parameters.includePeerToPeer = true

            let listener = try? NWListener(using: parameters)
            listener?.service = NWListener.Service(name: "CarrierSIMProbe", type: "_carriersimprobe._tcp")
            listener?.newConnectionHandler = { $0.cancel() }
            self.listener = listener

            let browser = NWBrowser(for: .bonjour(type: "_carriersimprobe._tcp", domain: nil), using: parameters)
            self.browser = browser

            listener?.start(queue: .main)
            browser.start(queue: .main)

            DispatchQueue.main.asyncAfter(deadline: .now() + timeout) { [weak self] in
                self?.finish(true)
            }
        }
    }

    private func finish(_ value: Bool) {
        guard let continuation else { return }
        self.continuation = nil
        continuation.resume(returning: value)
        browser?.cancel()
        browser = nil
        listener?.cancel()
        listener = nil
    }
}
