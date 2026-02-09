import argparse
import os
import re
import sys
import time
import xml.etree.ElementTree as et

import requests


def fail(message):
    print('Error: {0}'.format(message))
    sys.exit(1)


def get_video_id(video_url):
    ids = re.findall(r'https?://slideslive\.(com|de)/([0-9]+)(?:/([^/?#]*))?.*', video_url)
    if len(ids) < 1:
        fail('{0} is not a correct url.'.format(video_url))
    video_id = ids[0][1]
    raw_name = ids[0][2] or 'presentation'
    video_name = re.sub(r'[^a-zA-Z0-9._-]+', '-', raw_name).strip('-')
    if video_name == '':
        video_name = 'presentation'
    return video_id, video_name


def ensure_output_folder(video_id, video_name):
    folder_name = '{0}-{1}'.format(video_id, video_name)
    if os.path.isfile(folder_name):
        fail('{0} is a file, cannot create output folder.'.format(folder_name))
    os.makedirs(folder_name, exist_ok=True)
    return folder_name


def download_save_file(session, url, save_path, headers, wait_time=0.2, timeout=60):
    response = session.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    with open(save_path, 'wb') as f:
        f.write(response.content)
    time.sleep(wait_time)


def get_quality_tag_from_file_name(file_name):
    name_without_ext = os.path.splitext(file_name)[0]
    if '-' not in name_without_ext:
        return None
    candidate = name_without_ext.rsplit('-', 1)[1].lower()
    if candidate.isdigit():
        return candidate
    if candidate in ('small', 'medium', 'big', 'hd', 'fullhd'):
        return candidate
    return None


def create_pdf_from_slides(folder_name, video_id, video_name, slides):
    if len(slides) == 0:
        return None

    try:
        from PIL import Image
    except ImportError:
        print('Warning: Pillow is not installed, skipped PDF generation.')
        return None

    slides_sorted = sorted(slides, key=lambda item: item['time_ms'])
    quality_tag = get_quality_tag_from_file_name(slides_sorted[0]['file_name'])
    if quality_tag is None:
        pdf_name = '{0}-{1}.pdf'.format(video_id, video_name)
    else:
        pdf_name = '{0}-{1}-{2}.pdf'.format(video_id, video_name, quality_tag)
    pdf_path = os.path.join(folder_name, pdf_name)

    images = []
    try:
        for slide in slides_sorted:
            image_path = os.path.join(folder_name, slide['file_name'])
            with Image.open(image_path) as image:
                images.append(image.convert('RGB'))

        first_image, remaining_images = images[0], images[1:]
        first_image.save(pdf_path, save_all=True, append_images=remaining_images, resolution=150.0)
    finally:
        for image in images:
            image.close()

    print('created {0}'.format(pdf_path))
    return pdf_path


def normalize_modern_size(size):
    size_map = {
        'small': '432',
        'medium': '540',
        'big': '1080',
        'hd': '1080',
        'fullhd': '1080',
    }
    normalized = str(size).strip().lower()
    if normalized in size_map:
        return size_map[normalized]
    if normalized.isdigit():
        return normalized
    print('Warning: unsupported --size "{0}" for modern endpoint, using 1080.'.format(size))
    return '1080'


def normalize_legacy_size(size):
    normalized = str(size).strip().lower()
    if normalized in ('medium', 'big'):
        return normalized
    if normalized.isdigit():
        return 'big' if int(normalized) >= 720 else 'medium'
    return 'big'


def extract_first_match(pattern, text):
    match = re.search(pattern, text, re.IGNORECASE)
    if match is None:
        return None
    return match.group(1)


def extract_all_matches(pattern, text):
    return re.findall(pattern, text, re.IGNORECASE)


def fetch_page_html(session, url, headers):
    response = session.get(url, headers=headers, timeout=60)
    response.raise_for_status()
    return response.text


def fetch_player_playlist(session, video_id, player_token, headers):
    player_url = 'https://slideslive.com/player/{0}?player_token={1}'.format(video_id, player_token)
    response = session.get(player_url, headers=headers, timeout=60)
    response.raise_for_status()
    return response.text


def parse_player_playlist_value(playlist_text, key):
    pattern = r'^{0}:(.+)$'.format(re.escape(key))
    match = re.search(pattern, playlist_text, re.MULTILINE)
    if match is None:
        return None
    return match.group(1).strip()


def download_modern_slides(session, args, video_id, video_name, folder_name, headers):
    html = fetch_page_html(session, args.url, headers)

    slides_json_candidates = extract_all_matches(
        r'(https://s\.slideslive\.com/{0}/v2/slides\.json(?:\?[^"\'\s<]+)?)'.format(video_id),
        html,
    )
    slides_json_url = slides_json_candidates[0] if len(slides_json_candidates) > 0 else None

    slide_host = extract_first_match(
        r'"slideslive_on_the_fly_resized_slides_host":"([^"]+)"',
        html,
    )
    if slide_host is None:
        slide_host = 'slideslive-slides.b-cdn.net'

    if slides_json_url is None:
        player_token = extract_first_match(r'player_token=([A-Za-z0-9\-_=\.]+)', html)
        if player_token is not None:
            playlist = fetch_player_playlist(session, video_id, player_token, headers)
            slides_json_url = parse_player_playlist_value(playlist, '#EXT-SL-VOD-SLIDES-JSON-URL')

    if slides_json_url is None:
        raise RuntimeError('could not locate modern slides.json URL')

    response = session.get(slides_json_url, headers=headers, timeout=60)
    response.raise_for_status()
    data = response.json()
    slides = data.get('slides', [])

    image_slides = []
    for slide in slides:
        if slide.get('type') != 'image':
            continue
        image = slide.get('image') or {}
        name = image.get('name')
        if not name:
            continue
        ext = image.get('extname') or '.png'
        if not ext.startswith('.'):
            ext = '.' + ext
        time_ms = int(slide.get('time') or 0)
        image_slides.append({
            'name': name,
            'ext': ext,
            'time_ms': time_ms,
        })

    if len(image_slides) == 0:
        raise RuntimeError('modern slides.json contains no image slides')

    quality = normalize_modern_size(args.size)
    downloaded = []
    for slide in image_slides:
        file_name = '{0}-{1}-{2}{3}'.format(slide['time_ms'], slide['name'], quality, slide['ext'])
        save_path = os.path.join(folder_name, file_name)
        image_url = 'https://{0}/{1}/slides/original/{2}{3}?class={4}'.format(
            slide_host,
            video_id,
            slide['name'],
            slide['ext'],
            quality,
        )
        print('downloading {0}'.format(save_path))
        download_save_file(session, image_url, save_path, headers, args.waittime)
        downloaded.append({
            'time_ms': slide['time_ms'],
            'file_name': file_name,
        })

    create_ffmpeg_concat_file(folder_name, downloaded)
    create_pdf_from_slides(folder_name, video_id, video_name, downloaded)
    return True


def parse_legacy_xml_file(xml_file_path):
    xtree = et.parse(xml_file_path)
    xroot = xtree.getroot()
    slides = []

    candidates = xroot.findall('.//slide')
    if len(candidates) == 0:
        candidates = list(xroot)

    for node in candidates:
        slide_name_node = node.find('slideName')
        if slide_name_node is None or slide_name_node.text is None:
            continue

        time_sec_node = node.find('timeSec')
        time_node = node.find('time')

        if time_sec_node is not None and time_sec_node.text is not None:
            time_ms = int(float(time_sec_node.text) * 1000.0)
        elif time_node is not None and time_node.text is not None:
            time_ms = int(float(time_node.text))
        else:
            time_ms = 0

        time_label = str(time_ms)
        slides.append({
            'slide_name': slide_name_node.text,
            'time_ms': time_ms,
            'time_label': time_label,
        })

    return slides


def download_legacy_slides(session, args, video_id, video_name, folder_name, headers):
    xml_path = os.path.join(folder_name, '{0}.xml'.format(video_id))
    if not os.path.exists(xml_path):
        xml_url = '{0}{1}/{1}.xml'.format(args.basedataurl, video_id)
        print('downloading {0}'.format(xml_path))
        download_save_file(session, xml_url, xml_path, headers, args.waittime)

    slides = parse_legacy_xml_file(xml_path)
    if len(slides) == 0:
        raise RuntimeError('legacy XML contains no slides')

    legacy_size = normalize_legacy_size(args.size)
    downloaded = []
    for slide in slides:
        image_url = '{0}{1}/slides/{2}/{3}.jpg'.format(
            args.basedataurl,
            video_id,
            legacy_size,
            slide['slide_name'],
        )
        file_name = '{0}-{1}-{2}.jpg'.format(
            slide['time_label'],
            slide['slide_name'],
            legacy_size,
        )
        save_path = os.path.join(folder_name, file_name)
        print('downloading {0}'.format(save_path))
        download_save_file(session, image_url, save_path, headers, args.waittime)
        downloaded.append({
            'time_ms': slide['time_ms'],
            'file_name': file_name,
        })

    create_ffmpeg_concat_file(folder_name, downloaded)
    create_pdf_from_slides(folder_name, video_id, video_name, downloaded)
    return True


def create_ffmpeg_concat_file(folder_name, slides):
    if len(slides) == 0:
        return

    slides_sorted = sorted(slides, key=lambda item: item['time_ms'])
    ffmpeg_file_path = os.path.join(folder_name, 'ffmpeg_concat.txt')

    with open(ffmpeg_file_path, 'w', encoding='utf-8') as f:
        previous = None
        for current in slides_sorted:
            if previous is not None:
                duration_ms = max(1, current['time_ms'] - previous['time_ms'])
                f.write('duration {0:.3f}\n'.format(duration_ms / 1000.0))
            f.write("file '{0}'\n".format(current['file_name']))
            previous = current

        # We do not have end time for the last slide. Keep it visible for 30 seconds.
        f.write('duration 30\n')
        f.write("file '{0}'\n".format(slides_sorted[-1]['file_name']))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('url')
    parser.add_argument('--size', default='big', help='legacy: medium/big, modern: medium/big or numeric quality like 540/1080')
    parser.add_argument(
        '--useragent',
        default='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                '(KHTML, like Gecko) Ubuntu Chromium/76.0.3809.100 Chrome/76.0.3809.100 Safari/537.36',
    )
    parser.add_argument('--basedataurl', default='https://d2ygwrecguqg66.cloudfront.net/data/presentations/')
    parser.add_argument('--waittime', default='0.2', type=float, help='seconds to wait after each download')
    args = parser.parse_args()

    headers = {'User-Agent': args.useragent}
    session = requests.Session()

    video_id, video_name = get_video_id(args.url)
    folder_name = ensure_output_folder(video_id, video_name)

    try:
        download_modern_slides(session, args, video_id, video_name, folder_name, headers)
        print('done: modern endpoint')
        return
    except Exception as modern_error:
        print('Warning: modern endpoint failed ({0}). Trying legacy XML fallback.'.format(modern_error))

    try:
        download_legacy_slides(session, args, video_id, video_name, folder_name, headers)
        print('done: legacy endpoint')
    except Exception as legacy_error:
        fail('download failed. modern and legacy paths both failed: {0}'.format(legacy_error))


if __name__ == '__main__':
    main()
