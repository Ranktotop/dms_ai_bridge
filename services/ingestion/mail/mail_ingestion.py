"""Entry point for the mail ingestion service.

All account config (IMAP server, port, SSL, username, folders, etc.) lives in
the YAML config file.  Only passwords are read from ENV.

Configuration:
    MAIL_INGESTION_CONFIG                 Path to mail_ingestion.yml (default: config/mail_ingestion.yml)
    MAIL_INGESTION_WATCH                  Watch mode: 'true' / '1' (default: false)
    MAIL_INGESTION_POLL_INTERVAL_SECONDS  Polling interval in watch mode in seconds (default: 300)
    MAIL_INGESTION_BATCH_SIZE             Max messages per run per folder (0 = no limit, default: 0)

Per account (account_id uppercased, must match 'id' field in YAML):
    MAIL_INGESTION_{ACCOUNT_ID}_PASSWORD
"""
import asyncio
import os
import sys

from dotenv import load_dotenv

from shared.logging.logging_setup import setup_logging
from shared.helper.HelperConfig import HelperConfig
from shared.clients.cache.CacheClientManager import CacheClientManager
from shared.clients.dms.DMSClientManager import DMSClientManager
from shared.clients.llm.LLMClientManager import LLMClientManager
from shared.clients.ocr.OCRClientManager import OCRClientManager
from shared.clients.prompt.PromptClientManager import PromptClientManager
from services.ingestion.mail.helper.MailAccountConfigHelper import MailAccountConfigHelper, MailAccountConfig
from services.ingestion.mail.MailIngestionService import MailIngestionService

load_dotenv()
logging = setup_logging()


async def _run_once(
    accounts: list[MailAccountConfig],
    services: dict[str, MailIngestionService],
    batch_size: int = 0,
) -> None:
    """Process each account/folder pair once and return.

    For each account the correct service is selected by ``dms_engine`` so that
    each DMS engine's client is reused across all accounts that share it.

    Args:
        accounts: List of resolved mail account configurations.
        services: Mapping of dms_engine name → MailIngestionService instance.
        batch_size: Maximum number of messages to process per folder per run.
            0 means no limit.
    """
    for account in accounts:
        service = services.get(account.dms_engine)
        if service is None:
            logging.error(
                "No service found for DMS engine '%s' on account '%s'. Skipping.",
                account.dms_engine, account.id
            )
            continue
        for folder in account.folders:
            try:
                await service.do_ingest_account_folder(
                    account=account, folder=folder, batch_size=batch_size,
                )
            except Exception as e:
                logging.error(
                    "Error ingesting folder '%s' on account '%s': %s",
                    folder.path, account.id, e
                )


async def _run_watch(
    accounts: list[MailAccountConfig],
    services: dict[str, MailIngestionService],
    batch_size: int = 0,
) -> None:
    """Repeatedly poll each account/folder at a configurable interval.

    Runs ``_run_once`` in a loop, sleeping ``MAIL_INGESTION_POLL_INTERVAL_SECONDS``
    between each full pass.  Continues indefinitely until the process is killed.

    Args:
        accounts: List of resolved mail account configurations.
        services: Mapping of dms_engine name → MailIngestionService instance.
        batch_size: Maximum number of messages to process per folder per run.
            0 means no limit.
    """
    poll_interval = int(os.getenv("MAIL_INGESTION_POLL_INTERVAL_SECONDS", "300").strip())
    logging.info("Mail ingestion watch mode — polling every %d second(s).", poll_interval)
    while True:
        try:
            await _run_once(accounts=accounts, services=services, batch_size=batch_size)
        except Exception as e:
            logging.error("Unexpected error during mail ingestion run: %s", e)
        logging.info("Mail ingestion run complete. Sleeping %d second(s)...", poll_interval)
        await asyncio.sleep(poll_interval)


async def run() -> None:
    watch = os.getenv("MAIL_INGESTION_WATCH", "false").strip().lower() in ("1", "true", "yes")
    batch_size = int(os.getenv("MAIL_INGESTION_BATCH_SIZE", "0").strip())
    helper_config = HelperConfig(logger=logging)

    dms_clients_list = DMSClientManager(helper_config=helper_config).get_clients()
    llm_client = LLMClientManager(helper_config=helper_config).get_client()
    cache_client = CacheClientManager(helper_config=helper_config).get_client()
    ocr_client = OCRClientManager(helper_config=helper_config).get_client()
    prompt_client = PromptClientManager(helper_config=helper_config).get_client()

    # boot regular clients
    for client in [*dms_clients_list, llm_client, cache_client, ocr_client]:
        await client.boot()
        await client.do_healthcheck()

    # boot prompt client — non-fatal if it fails, LLM prompts fall back to local defaults
    try:
        await prompt_client.boot()
        await prompt_client.do_healthcheck()
    except Exception as e:
        logging.error(
            "Failed to boot prompt client: %s. Prompt-based features will use local fallbacks. Error: %s",
            prompt_client._get_engine_name(), e
        )

    # load mail account configurations
    account_config_helper = MailAccountConfigHelper(helper_config=helper_config)
    accounts = account_config_helper.get_accounts()

    if not accounts:
        logging.error(
            "No mail accounts configured. "
            "Add account entries to the YAML config and set MAIL_INGESTION_{ACCOUNT_ID}_PASSWORD in ENV."
        )
        sys.exit(0)

    # build a mapping from dms_engine → MailIngestionService so each DMS client is shared
    dms_clients_by_engine: dict[str, object] = {
        client.get_engine_name(): client for client in dms_clients_list
    }
    services: dict[str, MailIngestionService] = {}
    for engine_name, dms_client in dms_clients_by_engine.items():
        services[engine_name] = MailIngestionService(
            helper_config=helper_config,
            dms_client=dms_client,
            llm_client=llm_client,
            cache_client=cache_client,
            ocr_client=ocr_client,
            prompt_client=prompt_client,
        )

    # fill DMS cache for all engines used by the configured accounts
    engines_needed = {a.dms_engine for a in accounts}
    for engine_name in engines_needed:
        client = dms_clients_by_engine.get(engine_name)
        if client is not None:
            await client.fill_cache()
        else:
            logging.warning(
                "No DMS client found for engine '%s' — some accounts may not work.", engine_name
            )

    if watch:
        await _run_watch(accounts=accounts, services=services, batch_size=batch_size)
    else:
        await _run_once(accounts=accounts, services=services, batch_size=batch_size)

    # close all clients cleanly
    for client in [*dms_clients_list, llm_client, cache_client, ocr_client]:
        await client.close()
    if prompt_client:
        await prompt_client.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
