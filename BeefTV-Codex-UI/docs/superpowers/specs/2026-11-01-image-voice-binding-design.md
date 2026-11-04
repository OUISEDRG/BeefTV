# Canvas picture voice binding

The user explicitly selected binding on ordinary canvas image nodes and using the binding for both video and independent speech, and asked to implement directly. Existing image IDs, original images, edges and prompt text remain intact. No real inference or paid-media test is authorized for installation.

## Approach

Add a native image-toolbar dialog for selecting/previewing a Dreama catalog voice or choosing/uploading an existing audio resource. Persist a versioned binding in image node metadata, with stable voice ID or local sample resource ID. Changing or removing the binding is explicit. Reuse the existing resource uploader and canvas persistence, not a new project or database. A binding is attached to this picture; copying a node carries its binding. This does not silently create a domain project or a shared character entity.

Alternatives inspected: project character cards already have versioned voice bindings but require a domain project and alter the current image-reference workflow; shared workspace characters would add unrelated character-management scope. The selected ordinary-picture entry avoids those prerequisites. Existing character cards remain available.

## Generation

Freeze binding metadata with each submitted task and pass it as structured provider options through the Dreama protocol. Video maps each bound voice to the final corresponding reference image slot, creates/uses the proper Dreama character reference, and verifies saved voice binding and reference tags before submission. Audio uses exactly one bound speaking character; conflicting/multiple bindings must give an actionable selection error rather than choose silently. Preserve prompt text and existing reference order. Do not append descriptions, rewrite dialogue or guess speakers.

Catalog selection and preview do not generate audio. Local audio stays local until the user explicitly starts generation. Reference/audio limits and upstream fields come from verified production code/catalogs. Unverified upstream contracts are not guessed; unsupported models fail before paid submission. Existing 5-second execution policy applies only to user-created generation tasks, not to binding or test actions.

## Delivery

Back up the installed executable, bridge and current settings before deployment. Preserve the native Codex reasoning controls and all current integrations. Test UI persistence, exact prompt preservation, reordered references, binding removal, duplicate/multiple voices, request mapping and unknown-submission guards using fake upstreams. Read-only live catalog verification and native UI inspection are permitted; no paid tests.
