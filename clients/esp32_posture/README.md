# ESP32 Posture Client (placeholder)

Not implemented yet — waiting on the final IMU model and mounting location.

When ready, this should be a small Arduino/ESP-IDF sketch that:
1. Samples the IMU at a fixed rate (e.g. 50-100 Hz).
2. Batches readings (don't send one HTTP request per sample).
3. `POST`s each batch as a JSON array to `http://<server>:5000/posture/record_batch`,
   where each reading is a flat object with a `timestamp` (epoch ms) plus
   whatever sensor fields you choose (e.g. `ax`, `ay`, `az`, `gx`, `gy`, `gz`).
   The server infers CSV columns from whatever keys you send - no server
   changes needed once you land on a schema.
4. Keeps a small local ring buffer so a brief Wi-Fi drop doesn't lose data
   (ESP32 flash is limited, so this doesn't need to hold a full session
   like the watch app's local backup does - just enough to ride out short
   gaps).
