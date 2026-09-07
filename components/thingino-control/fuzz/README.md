# Thingino Control parser fuzzing

This standalone `cargo-fuzz` package compiles the production parser source files directly. Its
dependencies and lockfile are isolated from the Rust 1.95.0, standard-library-only production
crate.

Copy `corpus/` to task-owned scratch so libFuzzer does not grow the checked-in seed tree. Then run
each target with a bounded input and time budget from this directory, replacing
`$FUZZ_CORPUS_ROOT` with that scratch copy:
The 1 GiB RSS ceiling includes libFuzzer and sanitizer runtime overhead.

```sh
cargo fuzz run json_parse "$FUZZ_CORPUS_ROOT/json_parse" -- -dict=dictionaries/json.dict -max_total_time=60 -max_len=131072 -timeout=2 -rss_limit_mb=1024
cargo fuzz run http_request "$FUZZ_CORPUS_ROOT/http_request" -- -dict=dictionaries/http.dict -max_total_time=60 -max_len=12289 -timeout=2 -rss_limit_mb=1024
cargo fuzz run decoders "$FUZZ_CORPUS_ROOT/decoders" -- -dict=dictionaries/decoders.dict -max_total_time=60 -max_len=8194 -timeout=2 -rss_limit_mb=1024
cargo fuzz run motion_datagram "$FUZZ_CORPUS_ROOT/motion_datagram" -- -dict=dictionaries/motion.dict -max_total_time=60 -max_len=513 -timeout=2 -rss_limit_mb=1024
```

The targets check deterministic parsing, successful parse invariants, and JSON parse/serialize
round trips. Decoder fuzzing is tagged with `p` for path percent-decoding, `f` for form
percent-decoding, `b` for Base64, `h` for configuration SSID hex, and `w` for WPA SSID escaping.
