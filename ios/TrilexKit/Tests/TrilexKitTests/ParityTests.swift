// The iPhone port must answer exactly as the desktop does. expected.json is
// written by make_fixture.py running the Python code over the same fixture;
// regenerate it whenever the Python ranking changes, then make these pass.
import XCTest
@testable import TrilexKit

final class ParityTests: XCTestCase {
    func testLookupsMatchDesktop() throws {
        let lex = try Fixture.lexicon()
        for want in Fixture.expected["lookups"]!.array! {
            let q = want["query"]!.string!
            assertJSONEqual(json(lex.lookup(q)), want, path: "lookup(\(q.debugDescription))")
        }
    }

    func testSuggestMatchesDesktop() throws {
        let lex = try Fixture.lexicon()
        for pair in Fixture.expected["suggest"]!.array! {
            let prefix = pair.array![0].string!
            let got: JSONValue = .array(lex.suggest(prefix).map { .array([.string($0.word), .string($0.lang)]) })
            assertJSONEqual(got, pair.array![1], path: "suggest(\(prefix.debugDescription))")
        }
    }

    func testNormMatchesDesktop() {
        for row in Fixture.expected["norm"]!.array! {
            let r = row.array!
            let s = r[0].string!
            XCTAssertEqual(Norm.norm(s), r[1].string!, "norm(\(s.debugDescription))")
            XCTAssertEqual(Norm.loose(s), r[2].string!, "loose(\(s.debugDescription))")
            XCTAssertEqual(Norm.isCJK(s), r[3] == .bool(true), "isCJK(\(s.debugDescription))")
        }
    }

    func testForgettingCurveMatchesDesktop() {
        let e = Fixture.expected
        var mem = Memory()
        for step in e["forgetting"]!.array! {
            let g = Grade(rawValue: Int(step["grade"]!.number!))!
            mem = mem.review(g, elapsedDays: step["elapsed"]!.number!)
            XCTAssertEqual(mem.stability, step["stability"]!.number!, accuracy: 1e-9)
            XCTAssertEqual(mem.difficulty, step["difficulty"]!.number!, accuracy: 1e-9)
            XCTAssertEqual(mem.reps, Int(step["reps"]!.number!))
            XCTAssertEqual(mem.lapses, Int(step["lapses"]!.number!))
            XCTAssertEqual(mem.nextInterval(), step["next"]!.number!, accuracy: 1e-9)
            XCTAssertEqual(mem.nextInterval(retention: 0.85), step["next_85"]!.number!, accuracy: 1e-9)
            let pv = Forgetting.preview(mem, elapsedDays: 5)
            for (k, v) in step["preview"]!.object! {
                XCTAssertEqual(pv[Grade(rawValue: Int(k)!)!]!, v.number!, accuracy: 1e-9, "preview \(k)")
            }
        }
        for row in e["retrievability"]!.array! {
            let r = row.array!.map { $0.number! }
            XCTAssertEqual(Forgetting.retrievability(r[0], r[1]), r[2], accuracy: 1e-12)
        }
        for row in e["fmt_interval"]!.array! {
            XCTAssertEqual(Forgetting.fmtInterval(row.array![0].number!), row.array![1].string!)
        }
    }
}
