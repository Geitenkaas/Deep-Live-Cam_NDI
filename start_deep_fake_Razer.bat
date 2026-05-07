ECHO off
ECHO Run deep fake using Razer webcam
venv\Scripts\python.exe run_deep_fake.py --width 960 --height 540 --execution-provider cuda --device "Razer Kiyo Webcam"
