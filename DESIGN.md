# Ground Truth Collection Pipeline — Design Plan

## Goal
Collect synchronized ground-truth heart rate, respiration rate, and posture
data from separate reference devices, organized so it can later be aligned
with the CSI-based vital-sign estimates (see `csi data capture pipline` /
`sleep-monitoring-study`).

**2026-11-16 update:** posture ground truth now comes from a USB webcam,
not the ESP32 IMU originally planned below - see "Posture: USB webcam
(decided, supersedes ESP32 IMU)". The IMU section is kept for history.

## Why a separate codebase
- Different language/lifecycle than the Wear OS app (`MobileApp`): this is a
  Python service that will grow independently as more device clients are
  added by different team members.
- The Wear OS app doesn't need to know about the other devices — it already
  just POSTs its stream to a server. That server is the piece that should
  generalize, not the watch app.

Proposed layout (new folder, sibling to `MobileApp`):
```
FYP/ground-truth-pipeline/
  server.py                  <- session hub (evolved from the current heart-rate server.py)
  clients/
    esp32_posture/            <- ESP32 IMU firmware (posture) - superseded, see below
    respiration_TBD/          <- placeholder until the belt's own recorder is documented here
  data/
    <participant_id>/
      <session_id>/
        heartrate.csv
        heartrate_events.csv
        belt.csv                (respiration - whatever the belt's recorder writes)
        camera.mp4              <- record_camera.py (../webcam/), 5 fps, no audio
        camera_times.csv        <- record_camera.py: frame, t
        calibration.csv         <- start, end, posture (typed, or via ../webcam/log_calibration.py)
        posture.csv             <- posture_from_video.py (../webcam/): one label per 10s window
        ground_truth.csv        <- merge_ground_truth.py (../webcam/): one row per second
        session_meta.json      <- participant_id, session_id, start/stop time, device list
```

## Core concept: session hub
One "session" = one participant + one trial, running concurrently across all
measurement devices. The server owns session lifecycle; each device is a
dumb data source that doesn't know about the others.

- `POST /session/start` `{participant_id, session_label}` — begins a session,
  creates the per-session folder, writes `session_meta.json`.
- `POST /<modality>/record_batch` — e.g. `/heartrate/record_batch`,
  `/posture/record_batch`, later `/respiration/record_batch`. Same generic
  contract for every modality: a JSON array of timestamped readings.
- `POST /session/stop` — finalizes and flushes all per-modality files.

This is a generalization of the endpoint pattern already working in the
current watch app (`/start`, `/record_batch`, `/stop`) — same idea, just
namespaced per modality so adding respiration later needs zero server
redesign, only a new route + client.

## Timestamp / sync convention
- Every device timestamps its own readings in epoch milliseconds (already
  how the watch app works).
- The server should also record its own receipt-time timestamp alongside
  the device-reported one for every batch. Rationale: device timestamps are
  more precise but drift against each other (no shared clock between watch
  and ESP32); receipt timestamps share one clock (the server's) but add
  network jitter. Keeping both lets you choose the right one during
  analysis instead of guessing after the fact.
- `session_meta.json` should log a wall-clock start marker so trials can be
  matched against the CSI capture tool's own logs even without perfect
  clock sync across systems.

## Durability
Carry over the same principle used for the heart-rate fix: each device
should keep a local durable buffer and the server should flush+fsync after
every batch, so a dropped Wi-Fi connection or a mid-session crash doesn't
silently lose a trial. (ESP32 has far less storage than the watch, so a
small ring buffer rather than a full local CSV is more realistic there.)

## Posture: USB webcam (decided, supersedes ESP32 IMU)
No accelerometer on the body at all — the participant wears only the
respiration belt and the watch. A 720p USB webcam records the bed (head at
the top of frame, dim room light, no blanket) at 5 fps; `camera.mp4` +
`camera_times.csv` are written straight into the session folder by
`record_camera.py`, started alongside the other recorders same as any other
modality here. After the session, `posture_from_video.py` runs MediaPipe
Pose over the video, trains a nearest-neighbour classifier on the
calibration-minute frames (`calibration.csv`: start, end, posture —
supine/left/right/prone), and writes `posture.csv` (one label per 10 s
window). A person checks the labels against the video
(`review_posture_labels.py`) before `merge_ground_truth.py` folds them into
`ground_truth.csv`, with calibration/protocol-instructed windows winning
over the camera's guess where they apply.

All four scripts live in `../webcam/` (own README there) — kept out of this
package since they don't touch the Flask session hub at all; they only read
and write files in the session folder it creates.

Full rationale, camera mounting, session procedure changes, and known weak
points (front/back confusion, labels during a turn): "Camera Posture
Integration Plan" (2026-11-16).

### Posture: ESP32 IMU (superseded, kept for history)
- ESP32 + IMU (e.g. MPU6050/9250) samples at a fixed rate (e.g. 50–100 Hz —
  much higher than heart rate's ~1 Hz), batches readings (don't fire one
  HTTP request per sample), and POSTs to `/posture/record_batch` tagged
  with the active participant/session.
- Needs a small Arduino/ESP-IDF HTTP client — same shape as
  `NetworkSender.kt`, just in C/C++, plus a local ring buffer for
  network-outage resilience.
- Schema TBD with the team: raw accel/gyro vs. a fused orientation
  (quaternion/Euler angles) computed on-device.
- `/posture/record_batch` is left in `server.py` in case this (or another
  IMU-style device) is ever wired back in — it's schema-flexible and costs
  nothing to keep — but nothing currently posts to it.

## Respiration: Vernier belt (decided)
A Vernier respiration belt, via its own recorder (Bluetooth or USB), writes
`belt.csv` into the session folder — same contract as every other recorder
here: whatever columns it writes, as long as every row carries a
laptop-clock time. `merge_ground_truth.py` (`../webcam/`) auto-detects the
time column (`t`/`timestamp`/`time`/`date time`/`datetime`) when folding it
into `ground_truth.csv`, so no server changes were needed — it never POSTs
to `server.py` at all, it's a file dropped straight into the session folder
like the camera. If a Flask-side `/respiration/record_batch` endpoint is
ever needed for a different respiration device, it plugs into the same
`/<modality>/record_batch` pattern the heart rate and (superseded) IMU
posture endpoints already use.

## Relationship to the CSI capture pipeline
Codebases stay separate — no merge with `ESP32-CSI-Tool-master`. Alignment
happens at analysis time (e.g. in `CSI_Fundamentals_Colab.ipynb`) by joining
on `participant_id` + `session_id` + timestamp, once all systems share the
same session start marker.

## Suggested build order
1. ~~Generalize `server.py` into the session hub with `/heartrate` (already
   working) + `/posture` endpoints; move it into its own
   `ground-truth-pipeline` folder.~~ Done.
2. ~~Update the Wear OS app's routes from `/start` / `/record_batch` / `/stop`
   to the namespaced `/heartrate/...` equivalents (small, mechanical rename).~~ Done.
3. ~~Build the ESP32 posture client once the IMU model + mounting location is
   finalized.~~ Superseded — posture comes from the camera instead (see
   "Posture: USB webcam" above); no IMU client was built.
4. ~~Add the respiration modality (endpoint + client) once the device is
   chosen.~~ Device decided (Vernier belt); it writes `belt.csv` directly
   into the session folder rather than through a server endpoint, so no
   server change was needed.
5. ~~Add `session_meta.json` writing on start/stop for cross-pipeline
   traceability.~~ Done.
6. Camera posture pipeline (`../webcam/`): `record_camera.py`,
   `log_calibration.py`, `posture_from_video.py`,
   `review_posture_labels.py`, `merge_ground_truth.py`. Done, smoke-tested;
   not yet run on a real session — see that folder's README.
7. Update Form B, the information sheet and the consent form for video
   before recording anyone beyond a test/team member (ethics — see the
   plan's "Privacy and ethics" section; not done here, it's a paperwork
   step outside this codebase).
