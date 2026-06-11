// teamstap — capture the audio OUTPUT of a running app (default: Microsoft
// Teams) to a WAV file via ScreenCaptureKit, immune to output-device churn
// (AirPods A2DP/HFP flapping, BT resets, Teams pinning its own devices).
//
// SCK matches audio by application (helper processes included) and resamples
// internally to the configured rate, so the file format never changes even
// when the render device drops to 24 kHz HFP mid-call.
//
// Usage: teamstap --out /path/to/file.wav [--bundle com.microsoft.teams2]
//                 [--max-seconds N] [--channels 1|2]
// Stop with SIGINT/SIGTERM; the WAV is finalized on stop.
// Prints a one-line RMS/peak stat to stderr every 5s for liveness checks.

import Foundation
import ScreenCaptureKit
import AVFoundation
import CoreMedia

func log(_ s: String) { FileHandle.standardError.write((s + "\n").data(using: .utf8)!) }

// ---- args ----
var outPath: String? = nil
var bundleSubstr = "com.microsoft.teams"
var maxSeconds: Double = 0
var channels = 1
var args = Array(CommandLine.arguments.dropFirst())
while !args.isEmpty {
    let a = args.removeFirst()
    switch a {
    case "--out": outPath = args.isEmpty ? nil : args.removeFirst()
    case "--bundle": bundleSubstr = args.isEmpty ? bundleSubstr : args.removeFirst()
    case "--max-seconds": maxSeconds = Double(args.isEmpty ? "0" : args.removeFirst()) ?? 0
    case "--channels": channels = Int(args.isEmpty ? "1" : args.removeFirst()) ?? 1
    default: log("unknown arg: \(a)"); exit(64)
    }
}
guard let outPath else { log("usage: teamstap --out file.wav [--bundle id-substring] [--max-seconds N] [--channels 1|2]"); exit(64) }

final class Recorder: NSObject, SCStreamOutput, SCStreamDelegate {
    let file: AVAudioFile
    var frames: Int64 = 0
    var lastRMS: Float = 0
    var lastPeak: Float = 0
    var stream: SCStream?

    init(url: URL, sampleRate: Double, channels: Int) throws {
        file = try AVAudioFile(forWriting: url, settings: [
            AVFormatIDKey: kAudioFormatLinearPCM,
            AVSampleRateKey: sampleRate,
            AVNumberOfChannelsKey: channels,
            AVLinearPCMBitDepthKey: 16,
            AVLinearPCMIsFloatKey: false,
            AVLinearPCMIsNonInterleaved: false,
        ], commonFormat: .pcmFormatFloat32, interleaved: false)
        super.init()
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sb: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio, sb.isValid, CMSampleBufferGetNumSamples(sb) > 0 else { return }
        guard let fmtDesc = CMSampleBufferGetFormatDescription(sb),
              var asbd = CMAudioFormatDescriptionGetStreamBasicDescription(fmtDesc)?.pointee,
              let format = AVAudioFormat(streamDescription: &asbd) else { return }
        let n = CMSampleBufferGetNumSamples(sb)
        guard let pcm = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(n)) else { return }
        pcm.frameLength = AVAudioFrameCount(n)
        let status = CMSampleBufferCopyPCMDataIntoAudioBufferList(sb, at: 0, frameCount: Int32(n), into: pcm.mutableAudioBufferList)
        guard status == noErr else { return }
        do { try file.write(from: pcm) } catch { log("write error: \(error)"); return }
        frames += Int64(n)
        if let ch = pcm.floatChannelData {
            var sum: Float = 0, peak: Float = 0
            let cnt = Int(pcm.frameLength)
            for i in 0..<cnt { let v = ch[0][i]; sum += v * v; peak = max(peak, abs(v)) }
            lastRMS = cnt > 0 ? (sum / Float(cnt)).squareRoot() : 0
            lastPeak = peak
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        log("stream stopped with error: \(error)")
        exit(70)
    }
}

let url = URL(fileURLWithPath: outPath)
let sampleRate = 48000.0

Task {
    do {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
        let apps = content.applications.filter { $0.bundleIdentifier.lowercased().contains(bundleSubstr.lowercased()) }
        guard !apps.isEmpty else {
            log("no running app matches bundle substring '\(bundleSubstr)'")
            log("running apps: " + content.applications.map(\.bundleIdentifier).filter { !$0.isEmpty }.sorted().joined(separator: ", "))
            exit(69)
        }
        guard let display = content.displays.first else { log("no display"); exit(69) }
        log("tapping audio of: " + apps.map(\.bundleIdentifier).joined(separator: ", "))

        let filter = SCContentFilter(display: display, including: apps.flatMap { app in
            content.windows.filter { $0.owningApplication?.bundleIdentifier == app.bundleIdentifier }
        })
        let cfg = SCStreamConfiguration()
        cfg.capturesAudio = true
        cfg.excludesCurrentProcessAudio = true
        cfg.sampleRate = Int(sampleRate)
        cfg.channelCount = channels
        cfg.width = 2
        cfg.height = 2
        cfg.minimumFrameInterval = CMTime(value: 1, timescale: 1)

        let rec = try Recorder(url: url, sampleRate: sampleRate, channels: channels)
        let stream = SCStream(filter: filter, configuration: cfg, delegate: rec)
        rec.stream = stream
        try stream.addStreamOutput(rec, type: .audio, sampleHandlerQueue: DispatchQueue(label: "teamstap.audio"))
        try await stream.startCapture()
        log("capturing → \(outPath) (48k, \(channels)ch, 16-bit)")

        // graceful stop on SIGINT/SIGTERM
        signal(SIGINT, SIG_IGN); signal(SIGTERM, SIG_IGN)
        let stop: () -> Void = {
            Task {
                try? await stream.stopCapture()
                rec.file.close()  // finalize WAV header before exit
                log("stopped. \(rec.frames) frames (\(Double(rec.frames)/sampleRate)s) → \(outPath)")
                exit(0)
            }
        }
        let sigint = DispatchSource.makeSignalSource(signal: SIGINT, queue: .main)
        sigint.setEventHandler(handler: stop); sigint.resume()
        let sigterm = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
        sigterm.setEventHandler(handler: stop); sigterm.resume()

        // liveness stats every 5s
        let timer = DispatchSource.makeTimerSource(queue: .main)
        timer.schedule(deadline: .now() + 5, repeating: 5)
        timer.setEventHandler {
            log(String(format: "stat: frames=%lld rms=%.6f peak=%.6f", rec.frames, rec.lastRMS, rec.lastPeak))
        }
        timer.resume()

        if maxSeconds > 0 {
            DispatchQueue.main.asyncAfter(deadline: .now() + maxSeconds) { stop() }
        }
        // keep timer/signal sources alive
        withExtendedLifetime((sigint, sigterm, timer)) {}
    } catch {
        log("fatal: \(error)")
        exit(70)
    }
}

dispatchMain()
