# Dreama integration v1 contract

BeefTV facade prefix: `/api/integrations/dreama/v1`; bridge prefix: `/integration/v1`. The facade authenticates the local workspace and obtains credentials ONLY from an enabled saved loopback Dreama channel. Frontend calls the existing `http` client and gets the normal unwrapped data. Bridge endpoints use its existing Bearer authentication and plain JSON.

- GET `/status`: schemaVersion, service, integrationVersion, executionMode, delaySeconds.
- GET `/projects`: `{items:[{id:number,name:string,promptMode:string,aspectRatio:string}]}`.
- GET `/series?projectId=N`: `{items:[{id:number,name:string,projectId:number}]}`.
- POST `/series`: `{projectId,name}` -> `{item:{id,name,projectId}}`; explicit UI create only.
- GET `/catalog?projectId=N` (project optional): `{schemaVersion:1,catalogVersion:string,channels:[{id:string,models:string[],modelProfiles:object[]}],models:object[]}`. Existing channel IDs remain stable. Profiles use existing BeefTV fields with explicit capability versions; model variants keep individual limits.
- GET `/tasks?canvasId=...`: `{items:[...]}`. Task public projection includes id, kind, status, stage, pointsEstimate, autoExecuteAt, cancelable, projectId, seriesId, canvasId, nodeId, error, taskId, resultReady. No credentials, raw request bytes or upstream signed URLs.
- POST `/tasks/{id}/cancel`: existing ledger cancellation semantics; do not pretend to cancel remote work already submitted.
- POST facade-only `/catalog/refresh`: `{channelId,expectedRevision}`; fetch all bridge channel profiles and merge only matching saved Dreama channels via existing config CAS. Preserve keys, defaults, unrelated channels and pinned history values. Return `{revision,updatedChannels}`.
- POST facade-only `/ensure`: ensure configured local bridge process is healthy, start hidden only from installed runtime config, never accept executable paths from browser.

All facade reads require `channelId` query; facade writes require `channelId` body. It is removed before calling the bridge. The local backend chooses allowlisted upstream paths, not arbitrary URLs.

Generation metadata: namespaced `providerOptions.dreama` projected by provider manifest to request body `dreama`. Shape `{schemaVersion:1,canvasId:string,nodeId:string,projectId:number,seriesId:number,promptMode:'drama',aspectRatio:string,bindingRevision:number}`. Add final request snapshot fields if needed, but backend resolves identity and verifies actual project/series. Legacy calls without it retain the proven historical binding and never inherit a new default. Every prepared job records an immutable validated binding snapshot; all gateway operations use that snapshot.

Canvas binding and episode groups are versioned persisted document metadata, never localStorage only. Existing voice binding format remains supported. Use actual group membership, not screen geometry. Frontend implementer chooses the narrowest existing document metadata container and reports it to root before finalizing. Snapshot compiler is the existing reference chain; no second independent reference ordering.

Versioned audio declarations allow `audio.voices.default: ""`: no voice is selected yet. Do not infer the first voice; validate the final explicit voice or character-bound voice after reference compilation. Persisted invalid voice, speed, duration, model and resolution choices remain visible and fail preflight; only absent choices receive defaults.

Ordinary video declares `video.ratioSource: "project"` with empty global `ratios/defaultRatio`. The per-canvas effective capability comes from its verified project binding. Do not save one project's ratio into the global model directory. Explicit saved ratio conflicts require correction; extension and enhancement use their source-video semantics and do not inherit this override.

Integration writes are available through the existing allowlisted business API and CLI, with the same workspace authorization. Dedicated releases install the versioned `dreama-modalities` plugin through that API before refreshing model profiles with CAS; merely replacing the Python source is insufficient.
