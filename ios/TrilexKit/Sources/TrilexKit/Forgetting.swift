// Port of trilex/forgetting.py: forgetting curve and scheduling (FSRS-4.5).
//
// Each card carries two latent variables:
//
//     stability  S - days until recall probability falls to 90%
//     difficulty D - 1..10, how much harder this card is than average
//
// and retention decays along a power-law forgetting curve
//
//     R(t) = (1 + FACTOR * t / S) ^ DECAY
//
// with FACTOR and DECAY chosen so that R(S) = 0.90 exactly. The review queue
// is ordered by *lowest R first* - the words closest to being lost come back
// first. Weights are the published FSRS-4.5 defaults.
import Foundation

public enum Grade: Int, CaseIterable, Sendable {
    case again = 0, hard, good, easy

    public var name: String {
        switch self {
        case .again: return "Again"
        case .hard: return "Hard"
        case .good: return "Good"
        case .easy: return "Easy"
        }
    }

    /// FSRS speaks in 1..4; our buttons are 0..3.
    var fsrs: Int { rawValue + 1 }
}

public enum Forgetting {
    public static let DECAY = -0.5
    public static let FACTOR = 19.0 / 81.0          // makes R(S) == 0.9

    static let W: [Double] = [0.4872, 1.4003, 3.7145, 13.8206, 5.1618, 1.2298, 0.8975, 0.0310,
                              1.6474, 0.1367, 1.0461, 2.1072, 0.0793, 0.3246, 1.5870, 0.2272,
                              2.8755]

    public static let MIN_STABILITY = 0.1
    public static let MAX_STABILITY = 365.0 * 10
    public static let DEFAULT_RETENTION = 0.90

    static func clamp(_ x: Double, _ lo: Double, _ hi: Double) -> Double { max(lo, min(hi, x)) }

    // --------------------------------------------------------------- the curve
    /// Probability of recalling the card right now. 1.0 = just reviewed.
    public static func retrievability(_ elapsedDays: Double, _ stability: Double) -> Double {
        guard stability > 0 else { return 0 }
        return pow(1.0 + FACTOR * max(0, elapsedDays) / stability, DECAY)
    }

    /// Days until retention falls to `retention`. Equals S when retention=0.9.
    public static func intervalFor(_ stability: Double, retention: Double = DEFAULT_RETENTION) -> Double {
        let r = clamp(retention, 0.70, 0.99)
        return (stability / FACTOR) * (pow(r, 1.0 / DECAY) - 1.0)
    }

    /// Sample points for plotting the forgetting curve.
    public static func curve(_ stability: Double, days: Int = 60, step: Int = 1) -> [(Int, Double)] {
        stride(from: 0, through: days, by: step).map { ($0, retrievability(Double($0), stability)) }
    }

    // ------------------------------------------------------- state transitions
    static func initialStability(_ g: Grade) -> Double {
        clamp(W[g.fsrs - 1], MIN_STABILITY, MAX_STABILITY)
    }

    static func initialDifficulty(_ g: Grade) -> Double {
        clamp(W[4] - Double(g.fsrs - 3) * W[5], 1, 10)
    }

    static func nextDifficulty(_ difficulty: Double, _ g: Grade) -> Double {
        var d = difficulty - W[6] * Double(g.fsrs - 3)
        // Mean reversion pulls difficulty back toward the "easy first answer"
        // baseline, so one bad day does not permanently mark a card as hard.
        d = W[7] * initialDifficulty(.easy) + (1.0 - W[7]) * d
        return clamp(d, 1, 10)
    }

    static func stabilityAfterRecall(_ d: Double, _ s: Double, _ r: Double, _ g: Grade) -> Double {
        let hardPenalty = g == .hard ? W[15] : 1.0
        let easyBonus = g == .easy ? W[16] : 1.0
        let gain = exp(W[8]) * (11.0 - d) * pow(s, -W[9]) * (exp((1.0 - r) * W[10]) - 1.0)
            * hardPenalty * easyBonus
        return clamp(s * (1.0 + gain), MIN_STABILITY, MAX_STABILITY)
    }

    static func stabilityAfterLapse(_ d: Double, _ s: Double, _ r: Double) -> Double {
        let v = W[11] * pow(d, -W[12]) * (pow(s + 1.0, W[13]) - 1.0) * exp((1.0 - r) * W[14])
        return clamp(min(v, s), MIN_STABILITY, MAX_STABILITY)
    }

    /// Interval each button would produce, for the button labels.
    public static func preview(_ mem: Memory, elapsedDays: Double,
                               retention: Double = DEFAULT_RETENTION) -> [Grade: Double] {
        var out: [Grade: Double] = [:]
        for g in Grade.allCases {
            out[g] = mem.review(g, elapsedDays: elapsedDays).nextInterval(retention: retention)
        }
        return out
    }

    public static func fmtInterval(_ days: Double) -> String {
        if days < 1 { return "today" }
        if days < 30 { return String(format: "%.0fd", days) }
        if days < 365 { return String(format: "%.1fmo", days / 30.44) }
        return String(format: "%.1fy", days / 365.25)
    }

    public static func fmtRetention(_ r: Double) -> String {
        String(format: "%.0f%%", 100.0 * r)
    }
}

/// The learned state of one card.
public struct Memory: Equatable, Sendable {
    public var stability: Double = 0
    public var difficulty: Double = 0
    public var reps: Int = 0
    public var lapses: Int = 0

    public init(stability: Double = 0, difficulty: Double = 0, reps: Int = 0, lapses: Int = 0) {
        self.stability = stability; self.difficulty = difficulty
        self.reps = reps; self.lapses = lapses
    }

    public var isNew: Bool { reps == 0 || stability <= 0 }

    public func recall(_ elapsedDays: Double) -> Double {
        isNew ? 1.0 : Forgetting.retrievability(elapsedDays, stability)
    }

    /// Apply a grade and return the new memory state.
    public func review(_ g: Grade, elapsedDays: Double) -> Memory {
        if isNew {
            return Memory(stability: Forgetting.initialStability(g),
                          difficulty: Forgetting.initialDifficulty(g),
                          reps: 1, lapses: g == .again ? 1 : 0)
        }
        let r = recall(elapsedDays)
        let d = Forgetting.nextDifficulty(difficulty, g)
        if g == .again {
            return Memory(stability: Forgetting.stabilityAfterLapse(difficulty, stability, r),
                          difficulty: d, reps: reps + 1, lapses: lapses + 1)
        }
        return Memory(stability: Forgetting.stabilityAfterRecall(difficulty, stability, r, g),
                      difficulty: d, reps: reps + 1, lapses: lapses)
    }

    public func nextInterval(retention: Double = Forgetting.DEFAULT_RETENTION) -> Double {
        // Python's round(x, 2) rounds half to even.
        let iv = (Forgetting.intervalFor(stability, retention: retention) * 100)
            .rounded(.toNearestOrEven) / 100
        return max(1.0, iv)
    }
}
