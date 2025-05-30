import logging
import os
import shutil
import time
from pathlib import Path
from typing import Dict, Any

from botocore.exceptions import BotoCoreError, ClientError

import settings
from erieiron_common import common


class S3LocalCache:
    def __init__(self, s3_client):
        self.s3_client = s3_client
        self.max_disk_usage_percent = settings.S3_CACHE_MAX_DISK_USAGE

        self.cache_dir = Path(settings.S3_CACHE_DIR).resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_cache_path(self, bucket_name: str, s3_key: str) -> Path:
        cache_path = self.cache_dir / bucket_name / s3_key
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        return cache_path

    def _get_current_disk_usage(self) -> float:
        disk_stats = shutil.disk_usage(self.cache_dir)
        return (disk_stats.used / disk_stats.total) * 100.0

    def cleanup_cache(self) -> None:
        current_usage = self._get_current_disk_usage()

        if current_usage <= self.max_disk_usage_percent:
            return

        logging.info(f"disk usage ({current_usage:.1f}%) exceeds threshold ({self.max_disk_usage_percent:.1f}%). starting cleanup...")

        cache_files = []
        stale_files = []
        stale_threshold = time.time() - 24 * 3600  # 24 hours ago

        for root, _, files in os.walk(self.cache_dir):
            for file in files:
                file_path = Path(root) / file
                try:
                    mtime = file_path.stat().st_mtime
                    size = file_path.stat().st_size
                    cache_files.append((file_path, mtime, size))

                    # Check if the file is stale
                    if mtime < stale_threshold:
                        stale_files.append((file_path, size))
                except (OSError, FileNotFoundError) as e:
                    logging.warning(f"Error accessing file {file_path}: {str(e)}")

        # Remove stale files first
        for file_path, size in stale_files:
            try:
                os.remove(file_path)
                logging.info(f"Removed stale file: {file_path} ({size / 1024 / 1024:.2f} MB)")
            except (OSError, FileNotFoundError) as e:
                logging.warning(f"Error removing stale file {file_path}: {str(e)}")

        # Sort remaining files by last modified time (oldest first)
        cache_files.sort(key=lambda x: x[1])

        total_removed = 0
        for file_path, _, size in cache_files:
            current_usage = self._get_current_disk_usage()
            if current_usage <= self.max_disk_usage_percent:
                break

            try:
                os.remove(file_path)
                total_removed += 1
                logging.info(f"Removed cached file: {file_path} ({size / 1024 / 1024:.2f} MB)")
            except (OSError, FileNotFoundError) as e:
                logging.warning(f"Error removing file {file_path}: {str(e)}")

        new_usage = self._get_current_disk_usage()
        logging.info(f"cleanup complete. removed {total_removed} files. new disk usage: {new_usage:.1f}%")

    def file_exists_in_s3(self, bucket_name: str, key: str):
        try:
            self.s3_client.head_object(Bucket=bucket_name, Key=key)
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                return False
            else:
                raise e

    def put_file(
            self,
            file_path: Path,
            bucket_name: str,
            key: str,
            content_type: str,
    ) -> Dict[str, Any]:
        file_path = Path(file_path)
        assert file_path.exists(), f"{file_path} does not exist"

        try:
            with open(file_path, 'rb') as f:
                if common.is_not_empty(content_type):
                    response = self.s3_client.upload_fileobj(
                        f,
                        bucket_name,
                        key,
                        ExtraArgs={'ContentType': content_type}
                    )
                else:
                    response = self.s3_client.upload_fileobj(
                        f,
                        bucket_name,
                        key
                    )

            cache_path = self._get_cache_path(bucket_name, key)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file_path, cache_path)

            self.touch_file(bucket_name, key)

            self.cleanup_cache()

            return response
        except (BotoCoreError, ClientError) as e:
            logging.error(f"Error uploading file to S3: {str(e)}")
            raise

    def get_file(self, bucket_name, key: str) -> Path:
        if not key:
            raise ValueError(f"key is None when attempting to fetch from {bucket_name}")

        self.cleanup_cache()

        cache_path = self._get_cache_path(bucket_name, key)

        if not cache_path.exists():
            logging.debug(f"cache miss {bucket_name}/{key}")
            self.s3_client.download_file(
                bucket_name,
                key,
                str(cache_path)
            )
        else:
            logging.debug(f"cache hit {bucket_name}/{key}")

        self.touch_file(bucket_name, key)

        return cache_path

    def touch_file(self, bucket_name, key):
        cache_path = self._get_cache_path(bucket_name, key)
        if cache_path.exists():
            current_time = time.time()
            os.utime(cache_path, (current_time, current_time))
