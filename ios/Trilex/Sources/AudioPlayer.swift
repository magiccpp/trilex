import AVFoundation

/// Plays pronunciation clips straight from memory - they are ~3 KB MP3 blobs
/// in the audio pack, so nothing is written to disk and no network is used.
@MainActor
final class AudioPlayer {
    static let shared = AudioPlayer()
    private var player: AVAudioPlayer?

    func play(_ data: Data) {
        let session = AVAudioSession.sharedInstance()
        // Playback, not ambient: a pronunciation is something the user asked
        // to hear, so it should sound even with the ring switch off.
        try? session.setCategory(.playback, mode: .spokenAudio, options: [.duckOthers])
        try? session.setActive(true)
        player?.stop()
        player = try? AVAudioPlayer(data: data, fileTypeHint: AVFileType.mp3.rawValue)
        player?.play()
    }
}
