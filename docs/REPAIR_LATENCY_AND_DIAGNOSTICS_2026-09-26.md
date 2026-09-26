# Repair latency and validation diagnostics

Accepted repairs return directly to the normal gameplay startup gates. Failed repairs retain exponential backoff; acceptance, source identity, pending-action checks, stop requests and the original cutoff remain mandatory.

Verification subprocesses are polled at most every 250 ms, bounded by the configured polling interval and remaining deadline. This reduces avoidable waits after short Git and GitHub commands without skipping commands, shortening tests, caching results, or changing gameplay/repair polling. The full production-host test suite remains required for code repair.

Input-route failures retain their first structured validation stage (`route_schema`, `production_sites`, `commitment`, or `live_route`), an allowlisted source role when known, and a base exception class. Raw exception text and arbitrary source labels are not included in this diagnostic. Existing route evidence and native topology diagnostics remain available. The execution barrier, uncertainty status, pending work and commitments are unchanged; diagnostics do not clear native faults or authorize retries.

Validation is synthetic/offline; these changes do not establish native gameplay throughput improvements.

The offline Python/Lua suite passed: 2333 passed, 77 skipped. Browser validation runs separately in hosted CI.
