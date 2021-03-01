import argparse
import re
import os
import requests
import pandas as pd
import xml.etree.ElementTree as et
import time


def parse_xml(xml_file, df_cols):
    """Parse the input XML file and store the result in a pandas
    DataFrame with the given columns.

    Features will be parsed from the text content
    of each sub-element.

    based on:
    https://medium.com/@robertopreste/from-xml-to-pandas-dataframes-9292980b1c1c
    """
    xtree = et.parse(xml_file)
    xroot = xtree.getroot()
    rows = []

    for node in xroot:
        res = []
        for el in df_cols[0:]:
            if node is not None and node.find(el) is not None:
                res.append(node.find(el).text)
            else:
                res.append(None)
        rows.append({df_cols[i]: res[i]
                     for i, _ in enumerate(df_cols)})

    out_df = pd.DataFrame(rows, columns=df_cols)

    return out_df


def get_video_id(video_url):
    ids = re.findall('https://slideslive\\.(com|de)/([0-9]*)/([^/]*)(.*)', video_url)
    if len(ids) < 1:
        print('Error: {0} is not a correct url.'.format(video_url))
        exit()
    return ids[0][1], ids[0][2]


def download_save_file(url, save_path, headers, wait_time=0.2):
    r = requests.get(url, headers=headers)
    with open(save_path, 'wb') as f:
        f.write(r.content)
    time.sleep(wait_time)


def download_slides_xml(base_xml_url, video_id, video_name, headers, wait_time):
    folder_name = '{0}-{1}'.format(video_id, video_name)
    if not os.path.exists(folder_name):
        os.mkdir(folder_name)

    if os.path.isfile(folder_name):
        print('Error: {0} is a file, can\'t create a folder with that name'.format(folder_name))
        exit()

    file_path = '{0}/{1}.xml'.format(folder_name, video_id)
    if not os.path.exists(file_path):
        xml_url = '{0}{1}/v2/{1}.xml'.format(base_xml_url, video_id)
        print('downloading {}'.format(file_path))
        download_save_file(xml_url, file_path, headers, wait_time)

    return open(file_path, 'r')


def download_slides(video_id, video_name, df, base_img_url, size, headers, wait_time):
    folder_name = '{0}-{1}'.format(video_id, video_name)
    for index, row in df.iterrows():
        img_url = base_img_url.format(video_id, row['slideName'], size)
        file_path = '{0}/{3}-{1}-{2}.jpg'.format(folder_name, row['slideName'], size, row['time'])
        print('downloading {}'.format(file_path))
        download_save_file(img_url, file_path, headers, wait_time)


def create_ffmpeg_concat_file(video_id, video_name, df, size):
    folder_name = '{0}-{1}'.format(video_id, video_name)
    ffmpeg_file_path = '{0}/ffmpeg_concat.txt'.format(folder_name)
    if os.path.exists(ffmpeg_file_path):
        return
    with open(ffmpeg_file_path, 'a') as f:
        last_time = 0
        last_file_path = ''
        for index, row in df.iterrows():
            # if not first, write duration
            duration = int(row['timeSec']) - last_time
            if index != 0:
                f.write('duration {0}\n'.format(duration))
            file_path = '{3}-{1}-{2}.jpg'.format(folder_name, row['slideName'], size, row['time'])
            f.write("file '{0}'\n".format(file_path))
            last_time = int(row['timeSec'])
            last_file_path = file_path
        # add some time for the last slide, we have no information how long it should be shown.
        f.write('duration 30\n')
        # Due to a quirk, the last image has to be specified twice - the 2nd time without any duration directive
        # see: https://trac.ffmpeg.org/wiki/Slideshow
        # still not bug free
        f.write("file '{0}'\n".format(last_file_path))



parser = argparse.ArgumentParser()
parser.add_argument('url')
parser.add_argument('--size', default='big', help='medium or big')
parser.add_argument('--useragent', default='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Ubuntu Chromium/76.0.3809.100 Chrome/76.0.3809.100 Safari/537.36')
parser.add_argument('--basedataurl', default='https://d2ygwrecguqg66.cloudfront.net/data/presentations/')
parser.add_argument('--waittime', default='0.2', type=float, help='seconds to wait after each download')
args = parser.parse_args()

headers = {'User-Agent': args.useragent}
base_img_url = '{0}{1}'.format(args.basedataurl, '{0}/slides/{2}/{1}.jpg')

video_id, video_name = get_video_id(args.url)
xml = download_slides_xml(args.basedataurl, video_id, video_name, headers, args.waittime)
df = parse_xml(xml, ['orderId', 'timeSec', 'time', 'slideName'])
download_slides(video_id, video_name, df, base_img_url, args.size, headers, args.waittime)
create_ffmpeg_concat_file(video_id, video_name, df, args.size)
