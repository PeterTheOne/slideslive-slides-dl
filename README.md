slideslive-slides-dl
====================

a script to download the slides of a presentation on slideslive.com.
This software only downloads slides and not the video, 
use youtube-dl for that. Be aware of copyright law. Archival and 
education use is encouraged.

install and run
---------------

- `pip3 install -r requirements.txt`
- `python3 slideslive-slides.dl.py https://slideslive.de/38919334/technical-seo-and-modern-javascript-web-apps`

create a video file out of slides
---------------------------------

see: https://trac.ffmpeg.org/wiki/Slideshow

- install ffmpeg

- change directory to folder with the images:
`cd 00000000-sometitle` 

- use ffmpeg to create a video:
`ffmpeg -f concat -i ffmpeg_concat.txt -vsync vfr -pix_fmt yuv420p slides-video.mp4`
