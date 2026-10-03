# Engineering Round 63 — seven-child calculus collaboration validation

No source code changed in this validation round.

A real DeepSeek run used `spawn_agents` once to create seven independent calculus workers. The parent waited, read every child to `next_offset=null`, and reviewed every child with its actual result digest.

Five children returned values satisfying the explicit `{problem:string, answer:string}` contract and were included. Two children attempted `spawn_agents` despite their contract requiring `delegation.max_children=0`; the host rejected those nested calls with `Current task child quota reached`. Their results lacked the required fields, host contract status was violation, and the parent excluded them from the final list. No unreviewed or invalid answer was silently included.

This proves bounded batch scheduling, complete collection, per-child review and safe exclusion for one seven-child task. It does not prove that models will always obey the no-nested-delegation instruction, nor that the five returned calculus answers are factually verified; claims remain unverified.

