// decisions_echo_helper — macOS Voice Processing I/O audio bridge.
//
// Capture and playback both go through AVAudioEngine with
// `setVoiceProcessingEnabled(true)`, which is Apple's OS-level acoustic echo
// cancellation (the same engine FaceTime/Zoom use). Playback sent on stdin is
// the AEC reference; captured mic audio (echo-cancelled) is written to stdout.
//
// Protocol (raw PCM, no framing):
//   stdout : int16 LE, mono, at the engine sample rate (printed to stderr)
//   stdin  : int16 LE, mono, same sample rate
//   stderr : "READY <sample_rate> <frame_samples>" on startup; errors otherwise
//
// One 20 ms frame per read/write. Exits when stdin closes.

import AVFoundation
import CoreAudio
import Foundation

func logErr(_ s: String) {
    FileHandle.standardError.write(("HELPER " + s + "\n").data(using: .utf8)!)
}

// VoiceProcessingIO needs capture and render on the SAME sample rate. The
// MacBook mic is 48 kHz while the built-in speakers default to 44.1 kHz, and
// that mismatch makes the voice-processing AudioUnit fail to initialise
// (kAudioUnitErr_FailedInitialization, -10875). Pin the default output device
// to `rate` before anything else.
func setDefaultOutputSampleRate(_ rate: Double) {
    var addr = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyDefaultOutputDevice,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var deviceID = AudioDeviceID(0)
    var size = UInt32(MemoryLayout<AudioDeviceID>.size)
    guard AudioObjectGetPropertyData(
        AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &deviceID
    ) == noErr, deviceID != 0 else {
        logErr("could not read default output device")
        return
    }
    var rateAddr = AudioObjectPropertyAddress(
        mSelector: kAudioDevicePropertyNominalSampleRate,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var value = rate
    let status = AudioObjectSetPropertyData(
        deviceID, &rateAddr, 0, nil, UInt32(MemoryLayout<Double>.size), &value
    )
    logErr("set default output rate to \(Int(rate)) (status=\(status))")
}

let engine = AVAudioEngine()
let player = AVAudioPlayerNode()
engine.attach(player)

// --- Enable Apple's Voice Processing I/O (OS-level AEC) ---------------------
// Enable on the input node only; enabling it on the output node as well makes
// the output AudioUnit fail to initialise (AVFoundation -10875) on macOS.
let noVP = CommandLine.arguments.contains("--no-vp")
if !noVP {
    if let micRate = engine.inputNode.inputFormat(forBus: 0).sampleRate as Double?, micRate > 0 {
        setDefaultOutputSampleRate(48000)
    }
    do {
        try engine.inputNode.setVoiceProcessingEnabled(true)
    } catch {
        logErr("ERROR voice-processing: \(error)")
        exit(2)
    }
} else {
    logErr("voice-processing DISABLED")
}

let inFormat = engine.inputNode.inputFormat(forBus: 0)
let sampleRate = inFormat.sampleRate
guard sampleRate > 0 else {
    logErr("ERROR invalid sample rate from input node")
    exit(2)
}

// Playback is fed as interleaved int16 mono; AVAudioEngine converts as needed.
let playFormat = AVAudioFormat(
    commonFormat: .pcmFormatInt16, sampleRate: sampleRate, channels: 1, interleaved: true
)!
engine.connect(player, to: engine.mainMixerNode, format: playFormat)

let frameSamples = Int(sampleRate * 0.02)   // 20 ms
let frameBytes = frameSamples * 2

// --- Capture: input tap -> stdout ------------------------------------------
let outQueue = DispatchQueue(label: "decisionsecho.out")
let stdoutHandle = FileHandle.standardOutput

engine.inputNode.installTap(onBus: 0, bufferSize: AVAudioFrameCount(frameSamples), format: inFormat) {
    buffer, _ in
    guard let ch = buffer.floatChannelData?[0] else { return }
    let n = Int(buffer.frameLength)
    guard n > 0 else { return }
    var data = Data(capacity: n * 2)
    data.withUnsafeMutableBytes { raw in
        let p = raw.bindMemory(to: Int16.self)
        for i in 0..<n {
            let s = max(-1.0, min(1.0, ch[i]))
            p[i] = Int16(s * 32767.0)
        }
    }
    outQueue.async { stdoutHandle.write(data) }
}

engine.prepare()
do {
    try engine.start()
} catch {
    logErr("ERROR engine-start: \(error)")
    exit(2)
}
player.play()

logErr("READY \(Int(sampleRate)) \(frameSamples)")

// --- Playback: stdin -> player ---------------------------------------------
var pcm = [Int16](repeating: 0, count: frameSamples)
while true {
    var got = 0
    var eof = false
    pcm.withUnsafeMutableBytes { raw in
        while got < frameBytes {
            let r = read(0, raw.baseAddress!.advanced(by: got), frameBytes - got)
            if r <= 0 { eof = true; break }
            got += r
        }
    }
    if eof && got == 0 { break }
    if got < frameBytes {
        // pad the final partial frame with silence
        pcm.withUnsafeMutableBytes { raw in
            memset(raw.baseAddress!.advanced(by: got), 0, frameBytes - got)
        }
    }
    guard let buf = AVAudioPCMBuffer(pcmFormat: playFormat, frameCapacity: AVAudioFrameCount(frameSamples)) else {
        continue
    }
    buf.frameLength = AVAudioFrameCount(frameSamples)
    if let dst = buf.int16ChannelData?[0] {
        memcpy(dst, &pcm, frameBytes)
    }
    player.scheduleBuffer(buf, completionHandler: nil)
}

player.stop()
engine.stop()
logErr("EOF")
