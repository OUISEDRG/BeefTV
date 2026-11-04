# Character voice generation failure diagnosis and repair

Affected BeefTV tasks: b65e95d0855cab9afcac4e1bfa324c7c and fb46f209c586379037ad047172ebd74d. Both failed before paid video submission. Their bridge jobs had generationSubmitted=false and no upstream task ID.

The official createProjectLibraryWithImages API stored the parent voiceId but left child image voiceId at zero. CLI library list projected the parent hasVoice=true, while prompt assembly read the child and returned fileType=1. The bridge correctly rejected that as a voice mention (expected type 3), but previous mock tests missed the create/update distinction. Calling the official voice-selector updateProjectLibrary action synchronized the child and changed the exact same assembly call to fileType=3. New imports now perform that action and retain exact-ID voice-tag verification before generation.

The actual 5041-character user prompt contains 108 image mentions. Adding the official image/voice tags produced 43452 HTML characters and 45525 UTF-16 command-line units, above the Windows argv limit. New drafts are created with only a short name; their exact HTML, duration and produceType are saved via the official /storyboard/api/save JSON body and read back. No text rewriting or reference shortening is used.

The local task observer also stopped on raw status=failed while backend outcome was still retrievable. The subsequent no_result outcome never reached the canvas. The observer now continues read-only GETs until final outcome, and a new subscription refreshes stale terminal cache. It does not create/retry/recover tasks automatically.

The latest failed request's existing bridge-imported assets were re-saved with the same user-selected voice IDs. Its exact prompt was saved to draft 412088 and read back identically. Both imageTaskId and videoTaskId were zero. The failed ledger was not re-armed and no paid generation was called. Evidence: ../evidence/voice-generation-fix-live.json.

Validation: 154 backend tests pass with non-loopback external HTTP blocked; 40 targeted frontend tests, typecheck and native build pass. Earlier one legacy test initially missed mocking the new save transport and received upstream code 500 for synthetic board 300; it was corrected, and the final suite prohibits accidental external requests. No generation method was involved.

Deployment: codex-ui.3-voice-recovery, SHA256 adbd352889bddf01d840556ff2f066104eb061299bbd25edcdf7279fd89820c3. Native app relaunched to its home page; canvas click verification was blocked by CUA coordinate geometry/capture failures. Before restart, the original canvas was observed saved locally. Backup and deployment receipt: D:\BeefTV-Codex-UI-tools\voice-generation-fix-deployment.json.
