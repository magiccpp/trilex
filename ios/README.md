# Þríauga for iPhone

A native SwiftUI app that reads the same `dict.db` as the desktop: search in any
of the three languages, pinyin and inflected forms, typo suggestions,
etymology, inflection tables, examples, illustrations and pronunciation audio,
the wordbook and forgetting-curve review, and sync with the desktop's wordbook.

Requires **iOS 17** and **Xcode 16**.

## Build and run

```bash
brew install xcodegen
cd ios
xcodegen generate          # writes Trilex.xcodeproj from project.yml
open Trilex.xcodeproj
```

Pick an iPhone simulator and press Run. To install on a phone, choose your
team under *Signing & Capabilities* (and change `PRODUCT_BUNDLE_IDENTIFIER` in
`project.yml` if `io.xiaodong.thriauga` is taken).

The Xcode project is generated rather than committed, so it never conflicts;
edit `project.yml` instead.

## Where the dictionary comes from

On first launch the app reads `manifest.json` from the latest GitHub release
and downloads the dictionary once, verifying its SHA-256. After that it is
fully offline; only the optional sync uses the network.

It prefers `dictionary_mobile` — the trimmed build from
`python -m trilex.build.trim` — and falls back to the desktop's `dict.db.xz`
when a release does not carry one. To publish the trimmed build:

```bash
python -m trilex.build.pack      # media packs, manifest, dict-core.db
python -m trilex.build.trim      # dict-mobile.db(.xz), added to the manifest
python -m trilex.build.publish   # uploads dict-mobile.db.xz with the rest
```

For an offline build that needs no download at all, copy a dictionary to
`ios/Trilex/Resources/dict.db` before `xcodegen generate`; the app opens a
bundled `dict.db` read-only when none has been downloaded. (It is git-ignored —
the file is hundreds of megabytes.)

Illustrations and audio are the same add-on packs the desktop uses, under
**Settings → Add-ons**. Downloads resume after an interruption.

## Layout

```
ios/
  project.yml          XcodeGen spec
  Trilex/Sources/      SwiftUI app: search, result page, wordbook, review, settings
  Trilex/Resources/    asset catalog, privacy manifest
  TrilexKit/           Swift package with no UI code
    Lexicon.swift        port of search.py   (lookup, autocomplete, fuzzy, inflections)
    Norm.swift           port of norm.py     (lookup keys must match byte for byte)
    Forgetting.swift     port of forgetting.py (FSRS-4.5)
    Wordbook.swift       port of srs.py and the user.db schema
    Sync.swift           port of sync.py     (same server, same last-write-wins rules)
    Packs.swift          port of packs.py    (manifest, resumable downloads, xz)
    Presentation.swift   the section decisions of render.py, as data
```

`TrilexKit` builds and tests on Linux too, so the core can be checked without
a Mac:

```bash
swift test --package-path ios/TrilexKit       # Linux needs libsqlite3-dev
```

## Keeping the port honest

The phone must answer exactly as the desktop does. `make_fixture.py` builds a
small dictionary, runs the **Python** search, normalisation and FSRS code over
it, and records the answers in `Fixtures/expected.json`; the parity tests hold
the Swift port to them. After changing ranking or normalisation in Python:

```bash
pip install rapidfuzz
python ios/TrilexKit/Tests/make_fixture.py
swift test --package-path ios/TrilexKit
```

and port the change until the tests pass. The `iOS` GitHub workflow runs the
package tests on Linux and macOS and builds the app for the simulator.
