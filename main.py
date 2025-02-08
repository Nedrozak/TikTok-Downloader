import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import winreg
from datetime import datetime

# Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Set up a stream handler to display logs on the console
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.flush = sys.stdout.flush
formatter = logging.Formatter("%(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
stream_handler.setFormatter(formatter)
logger.addHandler(stream_handler)
stream_handler.flush = sys.stdout.flush


exe_name = "yt-dlp"
exe_path = shutil.which(exe_name)
logging.debug(f"yt-dlp executable path: {exe_path}")


def is_valid_date(date_str):
    """Checks if the string is a valid date in YYYYMMDD format."""
    try:
        datetime.strptime(date_str, "%Y%m%d")
        return True
    except ValueError:
        return False


def get_user_folder(folder_name):
    """Returns the actual folder path set by the user in Windows registry."""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        ) as key:
            folder_path, _ = winreg.QueryValueEx(key, folder_name)
            return os.path.expandvars(folder_path)
    except Exception as e:
        logger.warning(f"Error detecting {folder_name} folder: {e}")
        return os.path.join(os.path.expanduser("~"), folder_name)  # Fallback


def get_downloads_folder():
    return get_user_folder("{374DE290-123F-4565-9164-39C4925E467B}")


def get_videos_folder():
    return os.path.join(get_user_folder("My Video"), "4K Tokkit")


def get_video_info(url):
    """Retrieves video metadata for custom filename and metadata embedding."""
    command = [exe_path, "--dump-json", url]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        metadata = json.loads(result.stdout)

        profile_name = metadata.get("uploader", "").replace(" ", "_")
        unique_id = metadata.get("id", "")
        upload_date = metadata.get("upload_date", "")

        if not profile_name or not unique_id or not upload_date:
            raise ValueError("Missing critical metadata.")

        formatted_date = f"{upload_date[:4]}{upload_date[4:6]}{upload_date[6:8]}"
        filename = f"{profile_name}_{formatted_date}_{unique_id}"
        return metadata, filename
    except Exception as e:
        logger.info(f"Failed to retrieve video info: {e}")
        return None, None


def embed_metadata(video_path, metadata):
    """Uses ffmpeg to embed metadata into the video file."""
    if not os.path.exists(video_path):
        logger.info(f"Error: Video file '{video_path}' not found.")
        return

    title = metadata.get("title", "Unknown Title")
    uploader = metadata.get("uploader", "Unknown Uploader")
    description = metadata.get("description", "").replace("\n", " ")
    upload_date = metadata.get("upload_date", "Unknown Date")

    metadata_cmd = [
        "ffmpeg",
        "-i",
        video_path,
        "-metadata",
        f"title={title}",
        "-metadata",
        f"artist={uploader}",
        "-metadata",
        f"description={description}",
        "-metadata",
        f"date={upload_date}",
        "-codec",
        "copy",
        video_path.replace(".mp4", "_meta.mp4"),
    ]

    subprocess.run(metadata_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.replace(video_path.replace(".mp4", "_meta.mp4"), video_path)
    logger.info(f"✅ Metadata embedded in: {video_path}")


def download_tiktok_video(url, output_folder, failed_urls):
    """Downloads a single TikTok video with metadata."""
    os.makedirs(output_folder, exist_ok=True)
    metadata, video_filename = get_video_info(url)

    if not metadata or not video_filename:
        logger.info(f"Skipping video due to missing metadata: {url}")
        failed_urls.append(url)
        return None

    output_path = os.path.join(output_folder, f"{video_filename}.mp4")

    command = [
        exe_path,
        "-f",
        "bestvideo+bestaudio/best",
        "-o",
        output_path,
        "--merge-output-format",
        "mp4",
        url,
    ]
    result = subprocess.run(command)
    if result.returncode != 0:
        logger.info(f"Failed to download video: {url}")
        failed_urls.append(url)
        return None

    embed_metadata(output_path, metadata)
    return metadata


def download_tiktok_profile(url):
    """Downloads all missing videos from a TikTok profile."""
    profile_name = url.split("/@")[1].split("/")[0]
    profile_folder = os.path.join(get_videos_folder(), profile_name)
    os.makedirs(profile_folder, exist_ok=True)

    # Create the failed videos file for profile download
    failed_urls_file = os.path.join(profile_folder, "failed_videos.txt")

    history_file = os.path.join(profile_folder, "downloaded_videos.txt")

    existing_videos = set()
    if os.path.exists(history_file):
        with open(history_file, "r") as file:
            # Regular expression to match YYYYMMDD date format
            date_pattern = re.compile(r"(^|\_)(\d{8})(\_|$)")

            # Check only the files in the main profile folder, not subfolders
            for line in file:
                video_id = line.strip()
                for filename in os.listdir(profile_folder):
                    file_path = os.path.join(profile_folder, filename)
                    # Ensure we're not going into subfolders by checking if it's a file
                    if os.path.isfile(file_path) and video_id in filename and filename.endswith(".mp4"):
                        # Look for date pattern
                        date_match = date_pattern.search(filename)
                        if date_match:
                            date_str = date_match.group(2)  # Extract the matched date (YYYYMMDD)
                            if is_valid_date(date_str):  # Validate the date
                                existing_videos.add(video_id)
                            else:
                                logger.info(f"File {filename} contains an invalid date, treating it as missing.")
                        else:
                            logger.debug(f"File {filename} is missing a valid date, treating it as missing.")

    command = [exe_path, "--flat-playlist", "--dump-json", url]
    result = subprocess.run(command, capture_output=True, text=True)

    if result.returncode != 0:
        logger.info("Failed to retrieve profile videos.")
        return

    videos = [json.loads(line) for line in result.stdout.splitlines()]

    failed_urls = []  # List to collect failed video URLs
    for video in videos:
        video_id = video.get("id")
        if video_id not in existing_videos:
            video_url = video.get("url")
            logger.info(f"Downloading new video: {video_url}")

            # Wait 0.5 seconds before downloading
            time.sleep(0.5)

            metadata = download_tiktok_video(video_url, profile_folder, failed_urls)
            if metadata:  # Only add to history if download is successful
                with open(history_file, "a") as file:
                    file.write(video_id + "\n")

    if failed_urls:
        with open(failed_urls_file, "w") as f:
            for url in failed_urls:
                f.write(url + "\n")


def main():
    parser = argparse.ArgumentParser(description="Download TikTok videos in full quality with metadata.")
    parser.add_argument("url", help="TikTok video or profile URL")
    parser.add_argument(
        "-o", "--output", help="Output folder (default: Downloads folder)", default=get_downloads_folder()
    )
    args = parser.parse_args()

    failed_urls = []  # List to collect failed video URLs

    if "/@" in args.url and not any(ext in args.url for ext in [".mp4", "video"]):
        logger.info("Detected a profile URL, downloading all missing videos...")
        download_tiktok_profile(args.url)
    else:
        download_tiktok_video(args.url, args.output, failed_urls)

    # If there are failed videos, logger.info them to the console
    if failed_urls:
        logger.info("\nFailed to download the following videos:")
        for url in failed_urls:
            logger.info(url)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("\nDownload interrupted.")
