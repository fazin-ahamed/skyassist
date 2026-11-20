# SkyAssist release gates

Scope: offline release gates. 175 passed, 1 warning in 7.47s

| Gate | Target | Measured | Samples | Result |
| --- | --- | --- | ---: | --- |
| Core correctness | 10/10 | 10/10 | 10 | PASS |
| Integration regression suite | All selected tests pass | 175/175 | 175 | PASS |
| Safety: unauthorized effects | 0 violations | 0/15 | 15 | PASS |
| Safety: fabricated critical facts | 0 violations | 0/9 | 9 | PASS |
| Safety: duplicate dispatch | 0 violations | 0/2 | 2 | PASS |
| Safety: stale gate directions | 0 violations | 0/8 | 8 | PASS |
| Safety: false dispatch confirmation | 0 violations | 0/2 | 2 | PASS |
| Safety: restricted paths shown usable | 0 violations | 0/3 | 3 | PASS |
| Recovery: durable cases | All recover | 3/3 | 3 | PASS |
| Recovery: durable events | All recover | 9/9 | 9 | PASS |
| Repair regression | All labeled cases correct | all correct | 71 | PASS |
| Repair: ambiguous-must-ask / airport_case | 100% correct | 2/2 | 2 | PASS |
| Repair: ambiguous-must-ask / airport_trim | 100% correct | 1/1 | 1 | PASS |
| Repair: ambiguous-must-ask / no repair | 100% correct | 11/11 | 11 | PASS |
| Repair: ambiguous-must-ask / refs_json_list | 100% correct | 3/3 | 3 | PASS |
| Repair: safe repair / airport_case | 100% correct | 5/5 | 5 | PASS |
| Repair: safe repair / airport_trim | 100% correct | 5/5 | 5 | PASS |
| Repair: safe repair / flight_alias | 100% correct | 8/8 | 8 | PASS |
| Repair: safe repair / refs_json_list | 100% correct | 7/7 | 7 | PASS |
| Repair: unsafe-must-refuse / airport_case | 100% correct | 1/1 | 1 | PASS |
| Repair: unsafe-must-refuse / airport_trim | 100% correct | 1/1 | 1 | PASS |
| Repair: unsafe-must-refuse / no repair | 100% correct | 29/29 | 29 | PASS |
| Repair: unsafe-must-refuse / refs_json_list | 100% correct | 4/4 | 4 | PASS |
| Read acceptance | >=99% of 512 eligible reads | 100.00% | 512 | PASS |
| Committed gate or approval to visible state | p95 <= 2 s | 0.156184 s | 100 | PASS |
| Local typed connection result | p95 <= 3 s | 0.139705 s | 100 | PASS |

Eligible reads: 426 initial validation successes, 86 repaired successes, 0 backend failures, 0 safe refusals.

Versions: skyassist 0.1.0, python 3.14.10, dataset skyassist-demo-2026-11-19.1, policy demo-policy-v1, graph demo-graph-v1, tool_schema 1, fixture_sha256 4451dc1c04cb5f9ceab473e0bfa4f47762077d6f0d552b0648e53f1d0ed6674e, repair_rules 1, prompt_sha256 0845e0966cfa40bb5d1895be171ab767f56b7240217c828c980d032a6bcb7fa0, model template and Ollama mock transport.

Latency: monotonic wall time via in-process HTTP transport and real workers. Loaded runs keep at least 12 additional monitoring journeys active. No human, browser, physical scanner, live model, or live-provider timing is included. JSON breaks out tool, model, and workload samples.
