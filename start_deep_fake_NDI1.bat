ECHO off
ECHO Run deep fake using NDI 1
venv\Scripts\python.exe run_deep_fake.py --width 960 --height 540 --execution-provider cuda --device "NDI Webcam Video 1" --ndi --ndi-name "DeepLiveCam"
