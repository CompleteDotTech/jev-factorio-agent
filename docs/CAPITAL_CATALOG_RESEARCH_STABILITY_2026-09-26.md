# Capital catalog stability across research and restart

The native catalog includes force-specific recipe `enabled` bits. A controller caches that catalog while fresh observations update researched technologies. A capital investment can therefore be valid after a research unlock even though its cached recipe bit remains false. Restarting refreshes the bit and formerly changed the frozen catalog digest.

New schema-2 investment specifications exclude only recipe `enabled` from the digest. Recipe structure, ingredients, products, energy, hidden state, game version, machine capabilities and hand categories remain bound. Existing current capability checks remain mandatory.

Schema-1 specifications retain their original digest, key, deadline and progress. If the exact legacy digest does not match, compatibility reconstructs only currently true enabled bits whose unlocking technology is observed researched. It searches at most eight bits (256 combinations) and requires exact equality with the saved legacy digest. Any other structural difference or an over-budget mismatch fails closed. No checkpoint migration or automatic fault clearing occurs.

Investment admission checks semantic and reconstructed legacy key aliases against existing failure counts, without rewriting those counts. Expiring an old investment therefore cannot obtain a fresh budget through a schema-2 key. Optional admission fails closed when the alias search exceeds its budget; active intent keeps its existing validation and deadline.

Incident evidence independently found a seven-recipe native graph where changing only `assembling-machine-1.enabled` back to false reproduced the saved legacy hash exactly. The source regression tests are synthetic; no native gameplay throughput improvement is claimed.

The saved digest `1ec200ec9e1f00ca006929578dabdb269f732a92a47ae63423cb1a27c50b1939` was reproduced from the fresh catalog by changing only `assembling-machine-1.enabled` from true to false; no structural difference was needed.

This was a separate incident from the preceding ENOSPC failure. Storage was recovered by expanding the existing VM disk online from 256 to 320 GiB, without deleting files. The original prepared gather was verified under its unchanged attempt at tick 788625; the catalog guard then stopped the controller at tick 789157. Catalog compatibility does not repair storage, replay the gather, or extend the expired capital deadline.

Validation: 82 focused capital tests passed; the full offline Python/Lua suite passed 2350 tests with 77 skips. Hosted CI separately validates browser behavior.
