"""Helper for loading and resolving mail account configuration."""
import os
import yaml
from dataclasses import dataclass

from shared.helper.HelperConfig import HelperConfig


@dataclass
class MailFolderConfig:
    """Configuration for a single IMAP folder to ingest from."""
    path: str
    document_type: str | None
    tags: list[str]


@dataclass
class MailAccountConfig:
    """Full configuration for a single mail account, including IMAP credentials."""
    id: str
    dms_engine: str
    default_owner_id: int
    recipient_mapping: dict[str, int]       # email address → DMS owner_id
    attachment_extensions: frozenset[str]   # lowercase extensions without dot
    ingest_body: bool
    folders: list[MailFolderConfig]
    imap_server: str
    imap_port: int
    imap_ssl: bool
    username: str
    password: str


class MailAccountConfigHelper:
    """Loads mail account configuration from YAML and reads only passwords from ENV.

    All structural config (IMAP server, port, SSL, username, folders, recipient
    mapping, etc.) lives in the YAML file.  Only the password for each account
    is read from ENV so that secrets never end up in version-controlled config.

    ENV per account (account_id uppercased):
        MAIL_INGESTION_{ACCOUNT_ID}_PASSWORD

    Global:
        MAIL_INGESTION_CONFIG     Path to the YAML config file
                                  (default: config/mail_ingestion.yml)
    """

    ##########################################
    ############# LIFECYCLE ##################
    ##########################################

    def __init__(self, helper_config: HelperConfig) -> None:
        self.logging = helper_config.get_logger()
        self._helper_config = helper_config
        self._accounts: list[MailAccountConfig] = self._load_accounts()

    def _load_accounts(self) -> list[MailAccountConfig]:
        """Load and validate all accounts defined in the YAML config file.

        Reads every entry under ``accounts`` in the YAML.  For each account the
        password is pulled from ENV (``MAIL_INGESTION_{ACCOUNT_ID}_PASSWORD``).
        Accounts whose password ENV var is missing are skipped with an error log.
        """
        config_path = os.environ.get("MAIL_INGESTION_CONFIG", "config/mail_ingestion.yml").strip()
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)
        except FileNotFoundError:
            self.logging.error("Mail ingestion config file not found at '%s'.", config_path)
            return []
        except Exception as e:
            self.logging.error("Failed to parse mail ingestion config at '%s': %s", config_path, e)
            return []

        raw_accounts = (raw or {}).get("accounts", []) or []
        accounts: list[MailAccountConfig] = []

        for raw_account in raw_accounts:
            account_id = (raw_account.get("id") or "").strip().lower()
            if not account_id:
                self.logging.warning("Skipping mail account entry with missing 'id' field.")
                continue

            # read non-secret connection config from YAML — server/port/ssl/username are not secrets
            imap_server = (raw_account.get("imap_server") or "").strip()
            imap_port_raw = str(raw_account.get("imap_port") or "993").strip()
            imap_ssl_raw = str(raw_account.get("imap_ssl", True)).strip().lower()
            username = (raw_account.get("username") or "").strip()

            if not imap_server:
                self.logging.error(
                    "Skipping account '%s': 'imap_server' is not set in config.", account_id
                )
                continue
            if not username:
                self.logging.error(
                    "Skipping account '%s': 'username' is not set in config.", account_id
                )
                continue

            try:
                imap_port = int(imap_port_raw)
            except ValueError:
                self.logging.error(
                    "Skipping account '%s': 'imap_port' value '%s' is not a valid integer.",
                    account_id, imap_port_raw
                )
                continue

            imap_ssl = imap_ssl_raw in ("true", "1", "yes")

            # only the password comes from ENV — it is the sole secret
            account_key = account_id.upper()
            password = os.environ.get("MAIL_INGESTION_%s_PASSWORD" % account_key, "")
            if not password:
                self.logging.error(
                    "Skipping account '%s': MAIL_INGESTION_%s_PASSWORD is not set.",
                    account_id, account_key
                )
                continue

            # parse folder configs
            folders: list[MailFolderConfig] = []
            for raw_folder in (raw_account.get("folders") or []):
                folder_path = (raw_folder.get("path") or "").strip()
                if not folder_path:
                    self.logging.warning(
                        "Account '%s': skipping folder entry with missing 'path' field.", account_id
                    )
                    continue
                folders.append(MailFolderConfig(
                    path=folder_path,
                    document_type=raw_folder.get("document_type") or None,
                    tags=[str(t) for t in (raw_folder.get("tags") or [])],
                ))

            # parse recipient mapping — keys are email addresses, values are owner IDs
            raw_mapping = raw_account.get("recipient_mapping") or {}
            recipient_mapping: dict[str, int] = {}
            for email_addr, owner_id in raw_mapping.items():
                try:
                    recipient_mapping[str(email_addr).strip().lower()] = int(owner_id)
                except (ValueError, TypeError):
                    self.logging.warning(
                        "Account '%s': invalid owner_id '%s' for email '%s', skipping.",
                        account_id, owner_id, email_addr
                    )

            # parse attachment extensions — stored as lowercase without leading dot
            raw_extensions = raw_account.get("attachment_extensions") or []
            attachment_extensions = frozenset(
                str(ext).strip().lower().lstrip(".") for ext in raw_extensions if ext
            )

            accounts.append(MailAccountConfig(
                id=account_id,
                dms_engine=(raw_account.get("dms_engine") or "").strip(),
                default_owner_id=int(raw_account.get("default_owner_id") or 0),
                recipient_mapping=recipient_mapping,
                attachment_extensions=attachment_extensions,
                ingest_body=bool(raw_account.get("ingest_body", True)),
                folders=folders,
                imap_server=imap_server,
                imap_port=imap_port,
                imap_ssl=imap_ssl,
                username=username,
                password=password,
            ))
            self.logging.info(
                "Loaded mail account '%s' — %d folder(s), dms_engine='%s', owner_id=%d.",
                account_id, len(folders), raw_account.get("dms_engine", ""), raw_account.get("default_owner_id", 0)
            )

        return accounts

    ##########################################
    ############## GETTER ####################
    ##########################################

    def get_accounts(self) -> list[MailAccountConfig]:
        """Return all successfully loaded mail account configurations."""
        return self._accounts

    ##########################################
    ############### CORE #####################
    ##########################################

    def resolve_owner_id(self, account: MailAccountConfig, recipients: list[str]) -> int:
        """Resolve the DMS owner_id for the given list of recipient email addresses.

        Iterates ``recipients`` in order and returns the first match found in
        ``account.recipient_mapping``.  Falls back to ``account.default_owner_id``
        if no recipient matches.

        Args:
            account: The mail account config containing the mapping.
            recipients: List of recipient email address strings (may be empty).

        Returns:
            Resolved DMS owner_id as an integer.
        """
        for recipient in recipients:
            # normalise to lowercase for comparison — email addresses are case-insensitive
            normalised = recipient.strip().lower()
            if normalised in account.recipient_mapping:
                return account.recipient_mapping[normalised]
        # no match found — use the account-level default
        return account.default_owner_id
