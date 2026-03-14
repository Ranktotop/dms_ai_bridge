from concurrent.futures import ThreadPoolExecutor
from shared.helper.HelperConfig import HelperConfig
from services.ingestion.mail.helper.MailAccountConfigHelper import MailAccountConfig, MailFolderConfig


class MailFetcher:
    """
    Helper class responsible for connecting to IMAP servers and fetching raw email messages as bytes.
    """

    ##########################################
    ############# LIFECYCLE ##################
    ##########################################

    def __init__(self, helper_config: HelperConfig) -> None:
        self.logging = helper_config.get_logger()
        # single-thread executor keeps IMAP connections off the event loop
        self._executor = ThreadPoolExecutor(max_workers=1)

    ##########################################
    ############### CORE #####################
    ##########################################

    def fetch_all_mails(
        self,
        account: MailAccountConfig,
        folder: MailFolderConfig,
    ) -> dict[int, bytes]:
        """
        Connect to the IMAP server and fetch all messages
        
        Args:
            account: Mail account configuration.
            folder: IMAP folder to read.

        Returns:
            Mapping of persistent UID → raw RFC 822 message bytes.
        """
        try:
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
        except Exception as e:
            self.logging.error(f"Error fetching mails for account {account.username} folder {folder.path}: {e}")
            return {}
        