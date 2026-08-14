# Ground Truth Collection Pipeline — Design Plan

## Goal
Collect synchronized ground-truth heart rate, respiration rate, and posture
data from separate reference devices, organized so it can later be aligned
with the CSI-based vital-sign estimates (see `csi data capture pipline` /
`sleep-monitoring-study`).

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
    esp32_posture/            <- ESP32 IMU firmware (posture)
    respiration_TBD/          <- placeholder until device is chosen
  data/
    <participant_id>/
      <session_id>/
        heartrate.csv
        heartrate_events.csv
        posture.csv
        respiration.csv        (once added)
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

## Posture: ESP32 IMU (decided)
- ESP32 + IMU (e.g. MPU6050/9250) samples at a fixed rate (e.g. 50–100 Hz —
  much higher than heart rate's ~1 Hz), batches readings (don't fire one
  HTTP request per sample), and POSTs to `/posture/record_batch` tagged
  with the active participant/session.
- Needs a small Arduino/ESP-IDF HTTP client — same shape as
  `NetworkSender.kt`, just in C/C++, plus a local ring buffer for
  network-outage resilience.
- Schema TBD with the team: raw accel/gyro vs. a fused orientation
  (quaternion/Euler angles) computed on-device.

## Respiration (not yet decided)
No server changes needed once the device is chosen — it plugs into the same
`/<modality>/record_batch` pattern. The only device-specific work is the
client (BLE, serial, or another app, depending on what the device exposes).

## Relationship to the CSI capture pipeline
Codebases stay separate — no merge with `ESP32-CSI-Tool-master`. Alignment
happens at analysis time (e.g. in `CSI_Fundamentals_Colab.ipynb`) by joining
on `participant_id` + `session_id` + timestamp, once all systems share the
same session start marker.

## Suggested build order
1. Generalize `server.py` into the session hub with `/heartrate` (already
   working) + `/posture` endpoints; move it into its own
   `ground-truth-pipeline` folder.
2. Update the Wear OS app's routes from `/start` / `/record_batch` / `/stop`
   to the namespaced `/heartrate/...` equivalents (small, mechanical rename).
3. Build the ESP32 posture client once the IMU model + mounting location is
   finalized.
4. Add the respiration modality (endpoint + client) once the device is
   chosen.
5. Add `session_meta.json` writing on start/stop for cross-pipeline
   traceability.
