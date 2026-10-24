Echo is an Imprint particle sculpture derived from MediaPipe's canonical face
model, copyright the MediaPipe Authors, licensed under Apache-2.0 (LICENSE).

Source: https://github.com/google-ai-edge/mediapipe/blob/a9c3cf246ecd002a4cdc779516c4b8df742403fd/mediapipe/modules/face_geometry/data/canonical_face_model.obj

Original OBJ SHA256: 8bac80443397e113f41a8b565ea72c59390bc031d9defab289dba7bc0c54e618

Imprint modifications: rescaled and repositioned facial coordinates; deterministic
surface sampling; authored skull, neck and dissolving particles; luminous vertex
colors and contours; jawOpen and blink deformation targets; glTF export and live
rendering. The OBJ is a geometric template, not an executable or face tracker.
No MediaPipe Python/JS runtime, tracking model, camera or external asset service
is required. The live avatar does not identify or copy the user's face.

Regenerate with `node scripts/build-echo-avatar.mjs` from imprint/ui. The output
is public/models/imprint-echo.glb. Point size, bloom appearance and the depth mask
are renderer choices; other glTF viewers may display points differently.
