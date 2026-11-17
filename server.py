"""
Ground truth session hub.

Receives synchronized heart rate, posture (and future respiration) data
from separate reference devices during a single experiment session, and
logs each modality to its own CSV under data/<participant_id>/<session>/.

See DESIGN.md for the overall architecture.
"""

from flask import Flask, request, jsonify
import argparse
import csv
import json
import os
from datetime import datetime
from multiprocessing import Process, Queue
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.animation as animation
import matplotlib.dates as mdates

DATA_ROOT = 'data'
PARTICIPANT_ID = 'unlabeled'

app = Flask(__name__)

# --- Session state -------------------------------------------------------
# Single active session at a time (one lab, one trial in progress) -
# matches how the original heart-rate-only server worked.
session_state = {
    'session_id': None,
    'session_dir': None,
    'modalities': {},        # name -> {'file':, 'writer':, 'columns': [...]}
    'heartrate_queue': None,  # feeds the live BPM plot
    'plot_process': None,
}


def live_plotter(data_queue, filename_base):
    """Runs in a separate process to show/save a live heart-rate plot."""
    try:
        data_points = []

        def animate(i):
            while not data_queue.empty():
                item = data_queue.get()
                if item == "STOP":
                    plt.savefig(f"{filename_base}.png")
                    print(f"Graph saved to {filename_base}.png")
                    plt.close()
                    return
                data_points.append(item)

            if not data_points:
                return

            df = pd.DataFrame(data_points, columns=['timestamp', 'bpm'])
            df['timestamp'] = (
                pd.to_datetime(df['timestamp'], unit='ms', utc=True)
                  .dt.tz_convert('Asia/Colombo')
                  .dt.tz_localize(None)
            )

            ax.clear()
            ax.plot(df['timestamp'], df['bpm'], marker='.', linestyle='-')
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S.%f'))
            ax.xaxis.set_major_locator(mdates.AutoDateLocator())
            fig.autofmt_xdate()
            ax.set_title('Live Heart Rate Variation')
            ax.set_xlabel('Time')
            ax.set_ylabel('Heart Rate (BPM)')
            ax.grid(True)

        fig, ax = plt.subplots(figsize=(15, 7))
        ani = animation.FuncAnimation(fig, animate, interval=1000)
        print("Starting live graph window...")
        plt.show()

    except Exception as e:
        print(f"Error in plotting process: {e}")


# --- Modality logging helpers --------------------------------------------

def _open_modality_log(name, columns):
    path = os.path.join(session_state['session_dir'], f'{name}.csv')
    f = open(path, 'w', newline='')
    writer = csv.writer(f)
    writer.writerow(columns)
    f.flush()
    session_state['modalities'][name] = {'file': f, 'writer': writer, 'columns': columns}


def _write_modality_rows(name, rows, default_columns):
    if name not in session_state['modalities']:
        _open_modality_log(name, default_columns)
    entry = session_state['modalities'][name]
    for row in rows:
        entry['writer'].writerow(row)
    entry['file'].flush()
    os.fsync(entry['file'].fileno())


# --- Session lifecycle -----------------------------------------------------

@app.route('/session/start', methods=['POST'])
def session_start():
    if session_state.get('session_dir'):
        close_session()

    payload = request.get_json(silent=True) or {}
    session_label = payload.get('session_label')

    timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')
    folder_name = f"{timestamp_str}_{session_label}" if session_label else timestamp_str
    session_dir = os.path.join(DATA_ROOT, PARTICIPANT_ID, folder_name)
    os.makedirs(session_dir, exist_ok=True)

    session_state['session_id'] = folder_name
    session_state['session_dir'] = session_dir
    session_state['modalities'] = {}

    with open(os.path.join(session_dir, 'session_meta.json'), 'w') as f:
        json.dump({
            'participant_id': PARTICIPANT_ID,
            'session_label': session_label,
            'started_at': datetime.now().isoformat(),
        }, f, indent=2)

    # Heart rate keeps its dedicated CSVs + live plot for continuity with
    # the original single-modality server.
    _open_modality_log('heartrate', ['date time', 'heartrate value'])
    _open_modality_log('heartrate_events', ['date time', 'event'])

    data_queue = Queue()
    plot_process = Process(target=live_plotter, args=(data_queue, os.path.join(session_dir, 'heartrate')))
    plot_process.start()
    session_state['heartrate_queue'] = data_queue
    session_state['plot_process'] = plot_process

    print(f"Session started for participant '{PARTICIPANT_ID}': {session_dir}")
    return jsonify({'session_id': folder_name, 'session_dir': session_dir}), 200


@app.route('/session/stop', methods=['POST'])
def session_stop():
    close_session()
    return "Session stopped", 200


def close_session():
    if session_state.get('heartrate_queue'):
        session_state['heartrate_queue'].put("STOP")

    for entry in session_state.get('modalities', {}).values():
        if entry['file'] and not entry['file'].closed:
            entry['file'].close()

    if session_state.get('plot_process'):
        session_state['plot_process'].join(timeout=5)
        if session_state['plot_process'].is_alive():
            session_state['plot_process'].terminate()
            print("Plotting process was terminated.")

    print(f"Session '{session_state.get('session_id')}' closed.")
    session_state['session_id'] = None
    session_state['session_dir'] = None
    session_state['modalities'] = {}
    session_state['heartrate_queue'] = None
    session_state['plot_process'] = None


# --- Heart rate (watch app) ------------------------------------------------

@app.route('/heartrate/record_batch', methods=['POST'])
def heartrate_record_batch():
    if 'heartrate' not in session_state['modalities']:
        return "Not recording", 400
    if not request.is_json:
        return "Invalid data format", 400

    rows = []
    for point in request.get_json():
        bpm = point.get('bpm')
        timestamp = point.get('timestamp')
        readable_time = datetime.fromtimestamp(timestamp / 1000.0).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        rows.append([readable_time, bpm])
        session_state['heartrate_queue'].put((timestamp, bpm))

    _write_modality_rows('heartrate', rows, ['date time', 'heartrate value'])
    return "Data batch recorded", 200


@app.route('/heartrate/status', methods=['POST'])
def heartrate_status():
    if 'heartrate_events' not in session_state['modalities']:
        return "Not recording", 400
    if not request.is_json:
        return "Invalid data format", 400

    payload = request.get_json()
    status = payload.get('status')
    timestamp = payload.get('timestamp')
    readable_time = datetime.fromtimestamp(timestamp / 1000.0).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]

    _write_modality_rows('heartrate_events', [[readable_time, status]], ['date time', 'event'])
    print(f"Heart rate status event: {status} at {readable_time}")
    return "Status recorded", 200


# --- Posture (ESP32 IMU) -----------------------------------------------------
# Superseded by the camera-based posture pipeline (see
# ../webcam/README.md and DESIGN.md's "Posture: USB webcam" section) -
# posture ground truth now comes from record_camera.py + posture_from_video.py
# writing straight into the session folder, not through this endpoint.
# Left in place (schema-flexible, costs nothing to keep) in case an IMU-style
# device is ever wired back in; nothing currently posts to it.

@app.route('/posture/record_batch', methods=['POST'])
def posture_record_batch():
    """
    Schema-flexible: whatever keys the ESP32 sends per reading become CSV
    columns (e.g. ax, ay, az, gx, gy, gz - exact fields TBD by the team).
    Every reading must include 'timestamp' (device clock, epoch ms).
    'server_received_at' (server clock, epoch ms) is always added so
    device/server clock drift can be accounted for during analysis.
    """
    if not session_state.get('session_dir'):
        return "Not recording", 400
    if not request.is_json:
        return "Invalid data format", 400

    data_points = request.get_json()
    if not data_points:
        return "Empty batch", 200

    server_received_at = int(datetime.now().timestamp() * 1000)
    device_columns = list(data_points[0].keys())
    columns = ['server_received_at'] + device_columns
    rows = [[server_received_at] + [point.get(k) for k in device_columns] for point in data_points]

    existing = session_state['modalities'].get('posture')
    if existing and existing['columns'] != columns:
        # Device schema changed mid-session - close out and start a fresh
        # file with the new header rather than silently misaligning columns.
        existing['file'].close()
        del session_state['modalities']['posture']

    _write_modality_rows('posture', rows, columns)
    return "Data batch recorded", 200


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--participant', '-p', default=None,
                         help="Participant ID (used to group output files)")
    args = parser.parse_args()

    PARTICIPANT_ID = args.participant
    if not PARTICIPANT_ID:
        PARTICIPANT_ID = input("Enter participant ID (Enter to skip): ").strip() or "unlabeled"

    print(f"Ground truth session hub starting for participant '{PARTICIPANT_ID}'...")
    print("Please ensure 'flask', 'matplotlib', and 'pandas' are installed.")
    app.run(host='0.0.0.0', port=5000)
