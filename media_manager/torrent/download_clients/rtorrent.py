import logging
import shutil
from urllib.parse import quote

import pyrosimple

from media_manager.config import MediaManagerConfig
from media_manager.indexer.schemas import IndexerQueryResult
from media_manager.torrent.download_clients.abstract_download_client import (
    AbstractDownloadClient,
)
from media_manager.torrent.schemas import Torrent, TorrentStatus
from media_manager.torrent.utils import get_torrent_filepath, get_torrent_hash

log = logging.getLogger(__name__)


class RtorrentDownloadClient(AbstractDownloadClient):
    name = "rtorrent"

    def __init__(self) -> None:
        self.config = MediaManagerConfig().torrents.rtorrent

        scheme = "https" if self.config.https_enabled else "http"
        credentials = ""
        if self.config.username:
            credentials = f"{quote(self.config.username)}:{quote(self.config.password)}@"
        path = self.config.path if self.config.path.startswith("/") else f"/{self.config.path}"
        url = f"{scheme}://{credentials}{self.config.host}:{self.config.port}{path}"

        try:
            self._engine = pyrosimple.connect(url)
            # Test connection
            self._engine.rpc.system.client_version()
        except Exception:
            log.exception("Failed to connect to rTorrent")
            raise

    def download_torrent(self, indexer_result: IndexerQueryResult) -> Torrent:
        """
        Add a torrent to the rTorrent client and return the torrent object.

        :param indexer_result: The indexer query result of the torrent file to download.
        :return: The torrent object with calculated hash and initial status.
        """
        torrent_hash = get_torrent_hash(torrent=indexer_result)
        download_dir = (
            MediaManagerConfig().misc.torrent_directory / indexer_result.title
        )
        try:
            # rTorrent fetches the .torrent/magnet from the URL itself, like the
            # other clients. The empty first argument is the load target.
            self._engine.rpc.load.start(
                "",
                str(indexer_result.download_url),
                f'd.directory.set="{download_dir}"',
                f'd.custom1.set="{self.config.label}"',
            )

            log.info(f"Successfully added torrent to rTorrent: {indexer_result.title}")

        except Exception:
            log.exception("Failed to add torrent to rTorrent")
            raise

        torrent = Torrent(
            status=TorrentStatus.unknown,
            title=indexer_result.title,
            quality=indexer_result.quality,
            imported=False,
            hash=torrent_hash,
            usenet=False,
        )

        torrent.status = self.get_torrent_status(torrent)

        return torrent

    def remove_torrent(self, torrent: Torrent, delete_data: bool = False) -> None:
        """
        Remove a torrent from the rTorrent client.

        rTorrent does not delete downloaded files on erase, so when requested the
        download directory is removed from the (shared) filesystem afterwards.

        :param torrent: The torrent to remove.
        :param delete_data: Whether to delete the downloaded data.
        """
        try:
            self._engine.rpc.d.erase(torrent.hash.upper())
        except Exception:
            log.exception("Failed to remove torrent")
            raise

        if delete_data:
            download_dir = get_torrent_filepath(torrent=torrent)
            try:
                shutil.rmtree(download_dir)
            except FileNotFoundError:
                log.debug(f"Download directory not found, nothing to delete: {download_dir}")
            except OSError:
                log.exception(f"Failed to delete download directory: {download_dir}")

    def get_torrent_status(self, torrent: Torrent) -> TorrentStatus:
        """
        Get the status of a specific torrent.

        :param torrent: The torrent to get the status of.
        :return: The status of the torrent.
        """
        torrent_hash = torrent.hash.upper()
        try:
            # d.complete raises an XML-RPC fault if the hash is unknown to rTorrent
            try:
                complete = self._engine.rpc.d.complete(torrent_hash)
            except Exception:
                log.warning(f"Torrent not found in rTorrent: {torrent_hash}")
                return TorrentStatus.unknown

            # Check completion first: a finished torrent must always import, even
            # if it still carries a benign tracker message.
            if int(complete) == 1:
                return TorrentStatus.finished

            message = self._engine.rpc.d.message(torrent_hash)
            if message and "Tried all trackers" not in message:
                log.warning(f"Torrent {torrent.title} has error message: {message}")
                return TorrentStatus.error
        except Exception:
            log.exception("Failed to get torrent status")
            return TorrentStatus.error

        return TorrentStatus.downloading

    def pause_torrent(self, torrent: Torrent) -> None:
        """
        Pause a torrent download.

        :param torrent: The torrent to pause.
        """
        try:
            self._engine.rpc.d.pause(torrent.hash.upper())
            log.debug(f"Successfully paused torrent: {torrent.title}")
        except Exception:
            log.exception("Failed to pause torrent")
            raise

    def resume_torrent(self, torrent: Torrent) -> None:
        """
        Resume a torrent download.

        :param torrent: The torrent to resume.
        """
        try:
            self._engine.rpc.d.resume(torrent.hash.upper())
            log.debug(f"Successfully resumed torrent: {torrent.title}")
        except Exception:
            log.exception("Failed to resume torrent")
            raise
