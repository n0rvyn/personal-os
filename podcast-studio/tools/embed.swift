// tools/embed.swift — Chinese text → NLContextualEmbedding vector (JSON to stdout).
//
// Reads the entire stdin as a single string, computes a Chinese contextual
// embedding via Apple's NLContextualEmbedding, mean-pools the per-token
// vectors into a single document vector, and prints `{"vector": [Double, ...]}`
// to stdout. Exit codes:
//
//   0  : vector emitted (zero-length only if no tokens were produced;
//        callers must check the JSON and fall back to n-gram on empty)
//   2  : stdin not valid UTF-8
//   3  : embedder init returned nil / assets unavailable (caller → n-gram)
//   4  : load() failed
//   5  : embeddingResult(for:) failed
//   6  : JSON serialization failed
//
// This is a thin platform-API passthrough — the Python side
// (`lib.embed.embed_text`) handles parse + n-gram fallback. Per the Phase 2
// threat model: text travels via stdin (never argv), no shell, no eval.

import Foundation
import NaturalLanguage

let pipe = FileHandle.standardInput
let data = pipe.readDataToEndOfFile()
let text: String
if let s = String(data: data, encoding: .utf8) {
    text = s
} else {
    FileHandle.standardError.write(Data("stdin was not valid UTF-8\n".utf8))
    exit(2)
}

if text.isEmpty {
    // Match the Python contract: empty text → empty vector (caller falls back
    // to n-gram). Emit an empty vector so the JSON shape stays stable.
    let empty: [String: [Double]] = ["vector": []]
    if let out = try? JSONSerialization.data(withJSONObject: empty, options: []) {
        FileHandle.standardOutput.write(out)
        FileHandle.standardOutput.write(Data("\n".utf8))
    }
    exit(0)
}

// NLContextualEmbedding(language:) is a FAILABLE initializer — must unwrap.
guard let embedder = NLContextualEmbedding(language: .simplifiedChinese) else {
    FileHandle.standardError.write(Data(
        "NLContextualEmbedding init returned nil for simplifiedChinese\n".utf8
    ))
    exit(3)
}

if !embedder.hasAvailableAssets {
    FileHandle.standardError.write(Data(
        "NLContextualEmbedding assets unavailable for simplifiedChinese\n".utf8
    ))
    exit(3)
}

do {
    try embedder.load()
} catch {
    FileHandle.standardError.write(Data(
        "NLContextualEmbedding load() failed: \(error)\n".utf8
    ))
    exit(4)
}

// NLContextualEmbedding yields PER-TOKEN vectors; mean-pool them into one
// document vector so the Python side can cosine two documents.
let result: NLContextualEmbeddingResult
do {
    result = try embedder.embeddingResult(for: text, language: .simplifiedChinese)
} catch {
    FileHandle.standardError.write(Data(
        "NLContextualEmbedding.embeddingResult(for:) failed: \(error)\n".utf8
    ))
    exit(5)
}

let dim = embedder.dimension
var sum = [Double](repeating: 0.0, count: dim)
var count = 0
result.enumerateTokenVectors(in: text.startIndex..<text.endIndex) { (vector, _) -> Bool in
    if vector.count == dim {
        for i in 0..<dim { sum[i] += Double(vector[i]) }
        count += 1
    }
    return true
}

let pooled: [Double] = count > 0 ? sum.map { $0 / Double(count) } : []

let payload: [String: [Double]] = ["vector": pooled]
do {
    let out = try JSONSerialization.data(withJSONObject: payload, options: [])
    FileHandle.standardOutput.write(out)
    FileHandle.standardOutput.write(Data("\n".utf8))
} catch {
    FileHandle.standardError.write(Data(
        "JSONSerialization failed: \(error)\n".utf8
    ))
    exit(6)
}
