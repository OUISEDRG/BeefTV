# Successful empty write receipt handling

Affected task: BeefTV 4d137bece1e9bd02c2e331cee0005c21, bridge f65ad587-1ff1-5049-be83-156230ec4b4e. The real API returned HTTP 200, code 0, result null after updateProjectLibrary succeeded. DreamaGateway.post attempted result.get and raised AttributeError at bind:1. The original request had generationSubmitted=false and no paid task ID.

Fix: successful null and item:null writes return an empty acknowledgement; normal object/item-object data remains intact, malformed non-object receipts produce a BridgeError. Business errors remain errors. Actual write success continues to be verified by library/official mention-tag/board readback.

Regression tests first reproduced NoneType.get and nested-null failures, then passed. Final suite: 156 passing, all non-loopback HTTP blocked during offline tests.

The exact frozen original request was reconstructed from task input and original resource bytes, not manually approximated. Its digest exactly matched production: cb79dbb9a241849d74515bcdd176fb33ccaae45d30ebf14b171c8b85768ec903. An isolated Bridge.prepare ran real upload, 7 references, 4 voice bindings, prompt assembly, draft save/readback, 720P/30-second estimate (900 points), and check_ready. Draft 412094 has videoTaskId=0. HTTP produce/generate routes and CLI paid calls were explicitly disabled. The separate verification job was cancelled after verification; its ledger was never attached to the running server. No agent-paid generation was submitted.

While verification ran, a separate user-created 4-second ordinary video task (941471, BeefTV 8af016ca6e8b059d034af6989dcb2136) completed and its output was persisted by BeefTV. The service was reloaded only after this task finished. Post-deployment, the real null acknowledgement and the completed video endpoint were checked again successfully.

Evidence: ../evidence/voice-null-receipt-full-preflight.json and ../evidence/null-receipt-deployment.json. Original failed requests were not replayed. This proves pre-submission readiness; it does not claim that the 30-second voice-bound video has been generated or that its audio quality has been reviewed.
