ECHO off

ECHO Overwrite image of previous deep fake
cp templates\einstein.jpg images\temp.jpg

ECHO Run deep fake
venv\Scripts\python.exe run_deep_fake.py --source images/temp.jpg --width 960 --height 540 --execution-provider cuda --device 3
