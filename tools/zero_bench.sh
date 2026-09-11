#!/bin/bash
# zero_bench.sh - which part of the video path costs the Pi Zero its CPU?
# Plays the same 40 s recording through four video chains and reports CPU use.
# Needs the transmitter on the air. Stops the receiver while it runs (~4 min).
# Queues are size-limited (not time-limited): with 1 s queues the audio fills
# while the video waits for a keyframe, and the player stalls for ever.
F=/tmp/bench.ts
Q="queue max-size-time=0 max-size-buffers=0 max-size-bytes=20000000"
AUD="d. ! audio/mpeg ! $Q ! decodebin ! audioconvert ! audioresample ! alsasink device=hdmi:CARD=vc4hdmi,DEV=0 async=false"
sudo systemctl stop t2rx
if [ -s $F ] && [ "$1" != "new" ]; then
  echo "Using the existing recording $F  (bash zero_bench.sh new  to record again)"
else
  echo "Recording 40 s of the live stream (1700 kHz, 436 MHz)..."
  sudo timeout 45 /opt/t2rx/t2rx -f 436000000 -b 1.7 -q > $F
fi
ls -lh $F
python3 - << 'PY'
import sys; sys.path.insert(0, "/opt/t2rx"); import osd
st = {"state": "LOCK", "sig": "-77.6", "cnr": "28.3", "rate": "1.10", "mod": "QPSK", "fec": "1/2", "gi": "1/8", "fft": "2K"}
info = {"preset": 1, "name": "70cm T2 1.7", "freq": 436.0, "bw": 1700, "callsign": "G8YTZ", "provider": "Portsdown 4"}
osd.render_osd(800, st, info, "full").save("/tmp/bench_osd.png")
PY
# Whole-system CPU busy % over the test, from /proc/stat (counts every process,
# however the player is started).
cpu() { awk '/^cpu /{print $2+$3+$4+$6+$7+$8, $5}' /proc/stat; }
run() {
  local name=$1 chain=$2 b0 i0 b1 i1 t0 t1
  echo "   playing $name ... listen to the sound"
  read b0 i0 <<< "$(cpu)"; t0=$(date +%s)
  sudo timeout 120 gst-launch-1.0 -q filesrc location=$F ! tsparse ! tsdemux name=d \
      d. ! video/x-h264 ! $Q ! h264parse ! $chain $AUD >/dev/null 2>&1
  read b1 i1 <<< "$(cpu)"; t1=$(date +%s)
  awk -v n="$name" -v b=$((b1 - b0)) -v i=$((i1 - i0)) -v r=$((t1 - t0)) \
    'BEGIN { printf "%-38s CPU %3.0f%%   (%d s)\n", n, b * 100 / (b + i), r }' >> /tmp/bench_result.txt
}
rm -f /tmp/bench_result.txt
echo
run "A  zero-copy (like r4)"            "v4l2h264dec ! kmssink"
run "B  ordinary frames, no OSD"        "v4l2h264dec ! video/x-raw,format=I420 ! kmssink"
run "C  ordinary frames + OSD panel"    "v4l2h264dec ! video/x-raw,format=I420 ! gdkpixbufoverlay location=/tmp/bench_osd.png offset-x=24 offset-y=20 ! kmssink"
run "D  C + pixel-shape fix"            "v4l2h264dec ! video/x-raw,format=I420 ! capssetter caps=video/x-raw,pixel-aspect-ratio=16/15 ! gdkpixbufoverlay location=/tmp/bench_osd.png offset-x=24 offset-y=20 ! kmssink"
echo
cat /tmp/bench_result.txt
echo
sudo systemctl start t2rx
echo "Receiver restarted."
