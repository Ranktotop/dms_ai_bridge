"""IMAP client for fetching unprocessed mail messages."""
import asyncio
from concurrent.futures import ThreadPoolExecutor

from shared.clients.cache.CacheClientInterface import CacheClientInterface
from shared.helper.HelperConfig import HelperConfig
from services.ingestion.mail.helper.MailAccountConfigHelper import MailAccountConfig, MailFolderConfig


class MailFetcher:
    """Fetches messages from an IMAP folder that have not yet been processed.

    Uses ``imapclient`` (synchronous, high-level) wrapped in a thread executor
    so the event loop is not blocked.  All message IDs are persistent IMAP UIDs
    (``use_uid=True``), so cache entries remain valid even if other messages in
    the folder are deleted.

    Cache key format: ``mail_ingestion:{dms_engine}:{uid}``
    """

    ##########################################
    ############# LIFECYCLE ##################
    ##########################################

    def __init__(self, helper_config: HelperConfig, cache_client: CacheClientInterface) -> None:
        self.logging = helper_config.get_logger()
        self._cache_client = cache_client
        # single-thread executor keeps IMAP connections off the event loop
        self._executor = ThreadPoolExecutor(max_workers=1)

    ##########################################
    ############### CORE #####################
    ##########################################

    async def fetch_unprocessed(
        self,
        account: MailAccountConfig,
        folder: MailFolderConfig,
        batch_size: int = 0,
    ) -> tuple[list[tuple[str, bytes]], int]:
        """Fetch messages from an IMAP folder that have not yet been processed.

        Always checks every UID against the cache to obtain the true total count
        of unprocessed messages, then slices to batch_size for the returned list.
        This allows callers to display accurate progress against the full backlog.

        Args:
            account: Mail account configuration including IMAP credentials.
            folder: The folder configuration specifying which IMAP path to read.
            batch_size: Maximum number of unprocessed messages to return.
                0 means no limit (all unprocessed messages are returned).

        Returns:
            Tuple of:
              - list of (message_id, raw_bytes) tuples to process this run
              - total number of unprocessed messages across the full folder
            Returns ([], 0) on any IMAP error.
        """
        loop = asyncio.get_event_loop()
        try:
            # run blocking IMAP fetch in a thread so the event loop stays free
            uid_to_raw: dict[int, bytes] = await loop.run_in_executor(
                self._executor,
                self._fetch_all_sync,
                account,
                folder,
            )
        except Exception as e:
            self.logging.error(
                "IMAP error for account '%s', folder '%s': %s",
                account.id, folder.path, e
            )
            return [], 0

        if not uid_to_raw:
            self.logging.info(
                "No messages found in folder '%s' on account '%s'.",
                folder.path, account.id
            )
            return [], 0

        self.logging.info(
            "Found %d message(s) in folder '%s' on account '%s'. Checking cache...",
            len(uid_to_raw), folder.path, account.id
        )

        # always check every UID against the cache — we need the full unprocessed count
        # so the progress display reflects the total work remaining, not just this batch
        all_unprocessed: list[tuple[str, bytes]] = []
        for uid, raw_bytes in uid_to_raw.items():
            message_id = self._get_cache_key(account.dms_engine, uid)
            if await self._cache_client.do_exists(message_id):
                self.logging.debug(
                    "Skipping UID %d in folder '%s': already in cache.", uid, folder.path
                )
                continue
            all_unprocessed.append((message_id, raw_bytes))
            self.logging.debug(
                "Queued UID %d (%d bytes) from folder '%s'.", uid, len(raw_bytes), folder.path
            )

        # slice to batch_size after counting so the caller knows the total;
        # 0 means no limit — return everything
        batch = all_unprocessed[:batch_size] if batch_size > 0 else all_unprocessed
        return batch, len(all_unprocessed)

    ##########################################
    ############# HELPERS ####################
    ##########################################

    def _fetch_all_sync(
        self,
        account: MailAccountConfig,
        folder: MailFolderConfig,
    ) -> dict[int, bytes]:
        """Connect to the IMAP server and fetch all messages synchronously.

        Called from a thread executor — must not use async/await.

        Args:
            account: Mail account configuration.
            folder: IMAP folder to read.

        Returns:
            Mapping of persistent UID → raw RFC 822 message bytes.

        Raises:
            Exception: Any IMAP or network error — handled by the caller.
        """
        import imapclient

        with imapclient.IMAPClient(
            host=account.imap_server,
            port=account.imap_port,
            ssl=account.imap_ssl,
            use_uid=True,       # all IDs returned are persistent UIDs, not sequence numbers
        ) as client:
            client.login(account.username, account.password)
            # readonly=True avoids marking messages as \Seen during fetch
            client.select_folder(folder.path, readonly=True)

            uids = client.search(["ALL"])
            if not uids:
                return {}

            # fetch all RFC 822 bodies in one round trip
            response = client.fetch(uids, ["RFC822"])

        # response is {uid: {b'RFC822': <bytes>, b'SEQ': <int>}}
        return {
            uid: data[b"RFC822"]
            for uid, data in response.items()
            if b"RFC822" in data
        }

    def _get_cache_key(self, dms_engine: str, uid: int) -> str:
        """Return the cache key for a processed message.

        Format: ``mail_ingestion:{dms_engine}:{uid}``

        Args:
            dms_engine: The DMS engine name (e.g. ``postgresql``).
            uid: The persistent IMAP UID of the message.

        Returns:
            Cache key string.
        """
        return "mail_ingestion:%s:%d" % (dms_engine, uid)
